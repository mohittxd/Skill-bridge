"""Export an English ESCO release from the official API into importer CSV files."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import gzip
import http.client
import json
import math
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator


API_BASE = "https://ec.europa.eu/esco/api"
DEFAULT_VERSION = "1.2.1"
DEFAULT_OCCUPATION_PAGE_SIZE = 100
DEFAULT_SKILL_PAGE_SIZE = 500
DEFAULT_WORKERS = 3
MAX_ATTEMPTS = 4
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
OCCUPATIONS_SCHEME = "http://data.europa.eu/esco/concept-scheme/member-occupations"
SKILLS_SCHEME = "http://data.europa.eu/esco/concept-scheme/skills"


def _fetch_page(
    endpoint: str,
    scheme: str,
    page: int,
    version: str,
    page_size: int,
) -> dict[str, Any]:
    query = urllib.parse.urlencode({
        "isInScheme": scheme,
        "language": "en",
        "offset": page,
        "limit": page_size,
        "viewObsolete": "false",
        "selectedVersion": f"v{version.lstrip('v')}",
    })
    url = f"{API_BASE}/resource/{endpoint}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "SkillBridge-ESCO-catalog-import/1.0",
        },
    )
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = response.read()
                if response.headers.get("Content-Encoding", "").casefold() == "gzip":
                    body = gzip.decompress(body)
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("_embedded"), dict):
                raise ValueError(f"ESCO API returned an invalid {endpoint} page.")
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUSES or attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError(
                    f"ESCO API returned HTTP {exc.code} for {endpoint} page {page}."
                ) from exc
        except (
            urllib.error.URLError,
            socket.timeout,
            TimeoutError,
            gzip.BadGzipFile,
            EOFError,
            http.client.IncompleteRead,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            if attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError(
                    f"Could not retrieve ESCO {endpoint} page {page}: {exc}"
                ) from exc
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"Could not retrieve ESCO {endpoint} page {page}.")


def _collection_pages(
    endpoint: str,
    scheme: str,
    version: str,
    page_size: int,
    workers: int,
) -> tuple[int, Iterator[dict[str, Any]]]:
    first_page = _fetch_page(endpoint, scheme, 0, version, page_size)
    total = int(first_page.get("total", 0))
    if total < 1:
        raise ValueError(f"The ESCO API returned no {endpoint} for release {version}.")
    page_count = math.ceil(total / page_size)
    if len(first_page["_embedded"]) == 0:
        raise ValueError(f"The ESCO API returned an empty first page for {endpoint}.")

    def pages() -> Iterator[dict[str, Any]]:
        yield first_page
        if page_count == 1:
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            requests = [
                executor.submit(
                    _fetch_page, endpoint, scheme, page, version, page_size
                )
                for page in range(1, page_count)
            ]
            for page, future in enumerate(requests, start=1):
                payload = future.result()
                if int(payload.get("total", -1)) != total:
                    raise ValueError(
                        f"The ESCO {endpoint} total changed while page {page} was downloading."
                    )
                yield payload

    return total, pages()


def _english_text(value: Any) -> str:
    if isinstance(value, dict):
        localized = value.get("en") or value.get("en-us") or ""
        if isinstance(localized, dict):
            return str(localized.get("literal", "") or "").strip()
        return str(localized or "").strip()
    return str(value or "").strip()


def _english_labels(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    labels = value.get("en") or value.get("en-us") or []
    if isinstance(labels, str):
        labels = [labels]
    if not isinstance(labels, list):
        return []
    return list(dict.fromkeys(str(label).strip() for label in labels if str(label).strip()))


def _occupation_row(item: dict[str, Any]) -> dict[str, str]:
    code = str(item.get("code", "") or "").strip()
    return {
        "conceptUri": str(item.get("uri", "") or "").strip(),
        "preferredLabel": _english_text(item.get("preferredLabel")) or str(item.get("title", "")).strip(),
        "altLabels": "|".join(_english_labels(item.get("alternativeLabel"))),
        "description": _english_text(item.get("description")),
        "iscoGroup": code.split(".", 1)[0],
    }


def _skill_row(item: dict[str, Any]) -> dict[str, str]:
    return {
        "conceptUri": str(item.get("uri", "") or "").strip(),
        "preferredLabel": _english_text(item.get("preferredLabel")) or str(item.get("title", "")).strip(),
        "altLabels": "|".join(_english_labels(item.get("alternativeLabel"))),
        "description": _english_text(item.get("description")),
        "skillType": "",
    }


def _export_collection(
    endpoint: str,
    scheme: str,
    version: str,
    page_size: int,
    workers: int,
    output_path: Path,
    fieldnames: list[str],
    row_builder,
) -> tuple[int, int]:
    total, pages = _collection_pages(endpoint, scheme, version, page_size, workers)
    count = 0
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for page_number, payload in enumerate(pages, start=1):
            items = payload["_embedded"].values()
            page_count = 0
            for item in items:
                row = row_builder(item)
                if row["conceptUri"] and row["preferredLabel"]:
                    writer.writerow(row)
                    count += 1
                    page_count += 1
            print(f"{endpoint}: page {page_number}, {page_count} records", flush=True)
    if count != total:
        raise ValueError(
            f"ESCO API reported {total} {endpoint} but exported {count} records."
        )
    return count, total


def _export_occupations_and_relations(
    version: str,
    page_size: int,
    workers: int,
    occupation_path: Path,
    relation_path: Path,
) -> tuple[int, int]:
    total, pages = _collection_pages(
        "occupation",
        OCCUPATIONS_SCHEME,
        version,
        page_size,
        workers,
    )
    occupation_count = 0
    relation_count = 0
    seen_relations: set[tuple[str, str, str]] = set()
    with ExitStack() as stack:
        occupation_stream = stack.enter_context(
            occupation_path.open("w", encoding="utf-8", newline="")
        )
        relation_stream = stack.enter_context(
            relation_path.open("w", encoding="utf-8", newline="")
        )
        occupation_writer = csv.DictWriter(
            occupation_stream,
            fieldnames=["conceptUri", "preferredLabel", "altLabels", "description", "iscoGroup"],
        )
        relation_writer = csv.DictWriter(
            relation_stream,
            fieldnames=["occupationUri", "skillUri", "relationType", "skillType"],
        )
        occupation_writer.writeheader()
        relation_writer.writeheader()
        for page_number, payload in enumerate(pages, start=1):
            page_occupation_count = 0
            page_relation_count = 0
            for item in payload["_embedded"].values():
                occupation_row = _occupation_row(item)
                occupation_uri = occupation_row["conceptUri"]
                if occupation_uri and occupation_row["preferredLabel"]:
                    occupation_writer.writerow(occupation_row)
                    occupation_count += 1
                    page_occupation_count += 1

                links = item.get("_links", {})
                for api_field, relation_type in (
                    ("hasEssentialSkill", "essential"),
                    ("hasOptionalSkill", "optional"),
                ):
                    for relation in links.get(api_field, []):
                        skill_uri = str(relation.get("uri", "") or "").strip()
                        key = (occupation_uri, skill_uri, relation_type)
                        if not occupation_uri or not skill_uri or key in seen_relations:
                            continue
                        seen_relations.add(key)
                        relation_writer.writerow({
                            "occupationUri": occupation_uri,
                            "skillUri": skill_uri,
                            "relationType": relation_type,
                            "skillType": str(relation.get("skillType", "") or ""),
                        })
                        relation_count += 1
                        page_relation_count += 1
            print(
                f"occupations: page {page_number}, {page_occupation_count} records; "
                f"relations: {page_relation_count}",
                flush=True,
            )
    if occupation_count != total:
        raise ValueError(
            f"ESCO API reported {total} occupations but exported {occupation_count}."
        )
    if relation_count < 1:
        raise ValueError("The ESCO API returned no occupation-skill relations.")
    return occupation_count, relation_count


def export_catalog(
    destination: Path,
    *,
    version: str = DEFAULT_VERSION,
    occupation_page_size: int = DEFAULT_OCCUPATION_PAGE_SIZE,
    skill_page_size: int = DEFAULT_SKILL_PAGE_SIZE,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, int]:
    if occupation_page_size < 1 or occupation_page_size > 500:
        raise ValueError("occupation_page_size must be between 1 and 500.")
    if skill_page_size < 1 or skill_page_size > 500:
        raise ValueError("skill_page_size must be between 1 and 500.")
    if workers < 1 or workers > 6:
        raise ValueError("workers must be between 1 and 6.")

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".esco-api-export-", dir=destination.parent) as temp_dir:
        staging = Path(temp_dir)
        occupation_path = staging / "occupations_en.csv"
        skill_path = staging / "skills_en.csv"
        relation_path = staging / "occupationSkillRelations.csv"

        occupations, relation_count = _export_occupations_and_relations(
            version,
            occupation_page_size,
            workers,
            occupation_path,
            relation_path,
        )
        skills, _ = _export_collection(
            "skill",
            SKILLS_SCHEME,
            version,
            skill_page_size,
            workers,
            skill_path,
            ["conceptUri", "preferredLabel", "altLabels", "description", "skillType"],
            _skill_row,
        )

        destination.mkdir(parents=True, exist_ok=True)
        for filename in (
            "occupations_en.csv",
            "skills_en.csv",
            "occupationSkillRelations.csv",
        ):
            os.replace(staging / filename, destination / filename)

    return {
        "occupations": occupations,
        "skills": skills,
        "relations": relation_count,
    }


def main_cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(__file__).parent / "data" / f"esco-{DEFAULT_VERSION}",
        help="Directory to receive the three CSV files accepted by import_esco.py.",
    )
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument(
        "--occupation-page-size",
        type=int,
        default=DEFAULT_OCCUPATION_PAGE_SIZE,
    )
    parser.add_argument(
        "--skill-page-size",
        type=int,
        default=DEFAULT_SKILL_PAGE_SIZE,
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()
    counts = export_catalog(
        args.destination,
        version=args.version,
        occupation_page_size=args.occupation_page_size,
        skill_page_size=args.skill_page_size,
        workers=args.workers,
    )
    for dataset, count in counts.items():
        print(f"{dataset}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
