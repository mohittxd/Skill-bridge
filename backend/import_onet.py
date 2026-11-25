"""Import the current official O*NET occupation and skill CSV files into SQLite."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import tempfile
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import main
from main import _initialize_onet_catalog_schema
from onet_matching import canonical_skill_label, occupation_category, skill_key


RELEASE = "31.0"
BASE_URL = "https://www.onetcenter.org/dl_files/database/db_31_0_csv"
SOURCE_FILES = {
    "occupation_data.csv": {
        "url": f"{BASE_URL}/occupation_data.csv",
        "headers": {"O*NET-SOC Code", "Title", "Description"},
    },
    "essential_skills.csv": {
        "url": f"{BASE_URL}/essential_skills.csv",
        "headers": {
            "O*NET-SOC Code", "Element Name", "Scale ID", "Data Value",
            "Recommend Suppress", "Not Relevant",
        },
    },
    "transferable_skills.csv": {
        "url": f"{BASE_URL}/transferable_skills.csv",
        "headers": {
            "O*NET-SOC Code", "Element Name", "Scale ID", "Data Value",
            "Recommend Suppress", "Not Relevant",
        },
    },
    "software_skills.csv": {
        "url": f"{BASE_URL}/software_skills.csv",
        "headers": {
            "O*NET-SOC Code", "Workplace Example", "Hot Technology", "In Demand",
        },
    },
}

SOURCE_PAGE = "https://www.onetcenter.org/database.html"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"


def _read_rows(path: Path, required_headers: set[str]) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            actual_headers = set(reader.fieldnames or [])
            if not required_headers.issubset(actual_headers):
                raise ValueError(f"{path.name} is missing required O*NET columns.")
            return list(reader)
    except OSError as exc:
        raise ValueError(f"Could not read required O*NET source file: {path}") from exc


def _rating_rows(path: Path, domain: str) -> dict[tuple[str, str], dict[str, object]]:
    rows = _read_rows(path, SOURCE_FILES[f"{domain}_skills.csv"]["headers"])
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        code = row["O*NET-SOC Code"].strip()
        name = row["Element Name"].strip()
        if not code or not name:
            continue
        key = (code, skill_key(name))
        item = grouped.setdefault(
            key,
            {
                "skill_name": canonical_skill_label(name),
                "domain": domain,
                "importance": None,
                "required_level": None,
                "not_relevant": False,
            },
        )
        if row.get("Not Relevant", "").strip().upper() == "Y":
            item["not_relevant"] = True
        if row.get("Recommend Suppress", "").strip().upper() == "Y":
            continue
        scale = row.get("Scale ID", "").strip().upper()
        value_text = row.get("Data Value", "").strip()
        if not value_text or scale not in {"IM", "LV"}:
            continue
        value = float(value_text)
        field = "importance" if scale == "IM" else "required_level"
        previous = item[field]
        item[field] = value if previous is None else max(float(previous), value)
    return {
        key: item
        for key, item in grouped.items()
        if not item["not_relevant"]
        and (item["importance"] is not None or item["required_level"] is not None)
    }


def _merge_skill(
    destination: dict[str, dict[str, object]],
    key: str,
    item: dict[str, object],
) -> None:
    current = destination.get(key)
    if current is None:
        destination[key] = dict(item)
        destination[key]["domains"] = {str(item["domain"])}
        return
    current_domains = current["domains"]
    assert isinstance(current_domains, set)
    current_domains.add(str(item["domain"]))
    for field in ("importance", "required_level"):
        value = item.get(field)
        old_value = current.get(field)
        if value is not None:
            current[field] = value if old_value is None else max(float(old_value), float(value))
    current["hot_technology"] = int(bool(current.get("hot_technology")) or bool(item.get("hot_technology")))
    current["in_demand"] = int(bool(current.get("in_demand")) or bool(item.get("in_demand")))


def _build_catalog(source_dir: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    occupations = _read_rows(
        source_dir / "occupation_data.csv", SOURCE_FILES["occupation_data.csv"]["headers"]
    )
    titles = {
        row["O*NET-SOC Code"].strip(): row
        for row in occupations
        if row["O*NET-SOC Code"].strip() and row["Title"].strip()
    }

    skills_by_code: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    for domain in ("essential", "transferable"):
        for (code, key), item in _rating_rows(source_dir / f"{domain}_skills.csv", domain).items():
            if code in titles:
                _merge_skill(skills_by_code[code], key, item)

    software_rows = _read_rows(
        source_dir / "software_skills.csv", SOURCE_FILES["software_skills.csv"]["headers"]
    )
    for row in software_rows:
        code = row["O*NET-SOC Code"].strip()
        example = row["Workplace Example"].strip()
        if not code or code not in titles or not example:
            continue
        _merge_skill(
            skills_by_code[code],
            skill_key(example),
            {
                "skill_name": canonical_skill_label(example),
                "domain": "software",
                "importance": None,
                "required_level": None,
                "hot_technology": row.get("Hot Technology", "").strip().upper() == "Y",
                "in_demand": row.get("In Demand", "").strip().upper() == "Y",
            },
        )

    eligible_codes = sorted(code for code, skills in skills_by_code.items() if skills)
    if not eligible_codes:
        raise ValueError("The O*NET files did not contain any occupations with usable skill data.")

    occupation_rows: list[dict[str, object]] = []
    skill_rows: list[dict[str, object]] = []
    for code in eligible_codes:
        occupation = titles[code]
        category_code, category_name = occupation_category(code)
        occupation_rows.append(
            {
                "code": code,
                "title": occupation["Title"].strip(),
                "description": occupation["Description"].strip(),
                "category_code": category_code,
                "category_name": category_name,
                "skill_count": len(skills_by_code[code]),
            }
        )
        for key, item in skills_by_code[code].items():
            domains = item["domains"]
            assert isinstance(domains, set)
            skill_rows.append(
                {
                    "occupation_code": code,
                    "skill_key": key,
                    "skill_name": str(item["skill_name"]),
                    "domains": ",".join(sorted(domains)),
                    "importance": item.get("importance"),
                    "required_level": item.get("required_level"),
                    "hot_technology": int(bool(item.get("hot_technology"))),
                    "in_demand": int(bool(item.get("in_demand"))),
                }
            )
    return occupation_rows, skill_rows


def import_catalog(database_path: Path, source_dir: Path) -> dict[str, int]:
    occupation_rows, skill_rows = _build_catalog(source_dir)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30)
    try:
        with connection:
            _initialize_onet_catalog_schema(connection)
            connection.execute("DELETE FROM onet_occupation_skills")
            connection.execute("DELETE FROM onet_occupations")
            connection.executemany(
                """
                INSERT INTO onet_occupations(
                    code, title, description, category_code, category_name, skill_count
                ) VALUES (:code, :title, :description, :category_code, :category_name, :skill_count)
                """,
                occupation_rows,
            )
            connection.executemany(
                """
                INSERT INTO onet_occupation_skills(
                    occupation_code, skill_key, skill_name, domains, importance,
                    required_level, hot_technology, in_demand
                ) VALUES (
                    :occupation_code, :skill_key, :skill_name, :domains, :importance,
                    :required_level, :hot_technology, :in_demand
                )
                """,
                skill_rows,
            )
            metadata = {
                "release": RELEASE,
                "source_url": SOURCE_PAGE,
                "license_url": LICENSE_URL,
                "occupation_count": str(len(occupation_rows)),
                "skill_row_count": str(len(skill_rows)),
                "imported_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            connection.executemany(
                """
                INSERT INTO onet_catalog_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                metadata.items(),
            )
    finally:
        connection.close()
    return {"occupations": len(occupation_rows), "skills": len(skill_rows)}


def _download_sources(destination: Path) -> None:
    for filename, details in SOURCE_FILES.items():
        request = urllib.request.Request(
            str(details["url"]),
            headers={"User-Agent": "SkillBridge O*NET local catalog importer"},
        )
        target = destination / filename
        try:
            with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as output:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        except OSError as exc:
            raise RuntimeError(f"Could not download official O*NET file {filename}.") from exc


def main_cli() -> int:
    parser = argparse.ArgumentParser(
        description="Import O*NET 31.0 occupation skills into the local SQLite database."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=main.DATABASE_PATH,
        help="SQLite database path (default: DATABASE_PATH or backend/skill_gap.db).",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Use the four official CSV files in this directory instead of downloading them.",
    )
    args = parser.parse_args()

    if args.source_dir:
        counts = import_catalog(args.database, args.source_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="onet-31-import-") as directory:
            source_dir = Path(directory)
            _download_sources(source_dir)
            counts = import_catalog(args.database, source_dir)
    print(
        f"Imported O*NET {RELEASE}: {counts['occupations']} occupations, "
        f"{counts['skills']} occupation-skill records into {args.database}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
