"""Import the official English ESCO CSV occupation, skill, and relation files."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import career_recommendations
import main


DEFAULT_VERSION = "1.2.1"
SOURCE_PAGE = "https://esco.ec.europa.eu/en/use-esco/download"
SOURCE_API = "https://ec.europa.eu/esco/api/doc/esco_api_doc.html"
SOURCE_NAMES = {
    "occupations": ("occupations_en.csv", "occupations.csv"),
    "skills": ("skills_en.csv", "skills.csv"),
    "relations": ("occupationSkillRelations_en.csv", "occupationSkillRelations.csv"),
}
REQUIRED_HEADERS = {
    "occupations": {"conceptUri", "preferredLabel"},
    "skills": {"conceptUri", "preferredLabel"},
    "relations": {"occupationUri", "skillUri", "relationType"},
}


def _find_source_file(source_dir: Path, dataset: str) -> Path:
    candidates = SOURCE_NAMES[dataset]
    for candidate in candidates:
        path = source_dir / candidate
        if path.is_file():
            return path
    names = {name.casefold() for name in candidates}
    for path in source_dir.rglob("*.csv"):
        if path.name.casefold() in names:
            return path
    raise ValueError(
        f"Missing English ESCO {dataset} CSV. Expected one of "
        f"{', '.join(candidates)} under {source_dir}."
    )


def _read_csv(path: Path, dataset: str) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            headers = set(reader.fieldnames or [])
            missing = REQUIRED_HEADERS[dataset] - headers
            if missing:
                raise ValueError(
                    f"{path.name} is missing required ESCO columns: {', '.join(sorted(missing))}."
                )
            return list(reader)
    except OSError as exc:
        raise ValueError(f"Could not read ESCO source file {path}.") from exc


def _split_labels(value: str) -> list[str]:
    return list(dict.fromkeys(
        label.strip()
        for label in value.replace("\r", "\n").replace("\n", "|").split("|")
        if label.strip()
    ))


def _description(row: dict[str, str]) -> str:
    parts = [
        row.get(key, "").strip()
        for key in ("description", "definition", "scopeNote")
        if row.get(key, "").strip()
    ]
    return " ".join(dict.fromkeys(parts))


def _build_rows(source_dir: Path):
    source_paths = {
        dataset: _find_source_file(source_dir, dataset)
        for dataset in ("occupations", "skills", "relations")
    }
    source_rows = {
        dataset: _read_csv(path, dataset)
        for dataset, path in source_paths.items()
    }

    occupations_by_uri: dict[str, dict[str, object]] = {}
    for row in source_rows["occupations"]:
        uri = row.get("conceptUri", "").strip()
        label = row.get("preferredLabel", "").strip()
        if not uri or not label:
            continue
        occupations_by_uri[uri] = {
            "uri": uri,
            "preferred_label": label,
            "alt_labels": json.dumps(_split_labels(row.get("altLabels", "")), ensure_ascii=False),
            "description": _description(row),
            "isco_group": row.get("iscoGroup", "").strip(),
        }

    skills_by_uri: dict[str, dict[str, object]] = {}
    for row in source_rows["skills"]:
        uri = row.get("conceptUri", "").strip()
        label = row.get("preferredLabel", "").strip()
        if not uri or not label:
            continue
        skills_by_uri[uri] = {
            "uri": uri,
            "preferred_label": label,
            "alt_labels": json.dumps(_split_labels(row.get("altLabels", "")), ensure_ascii=False),
            "description": _description(row),
            "skill_type": row.get("skillType", "").strip(),
        }

    relations_by_key: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in source_rows["relations"]:
        occupation_uri = row.get("occupationUri", "").strip()
        skill_uri = row.get("skillUri", "").strip()
        relation_type = row.get("relationType", "").strip().casefold()
        if (
            occupation_uri not in occupations_by_uri
            or skill_uri not in skills_by_uri
            or relation_type not in {"essential", "optional"}
        ):
            continue
        key = (occupation_uri, skill_uri, relation_type)
        relations_by_key[key] = {
            "occupation_uri": occupation_uri,
            "skill_uri": skill_uri,
            "relation_type": relation_type,
            "skill_type": row.get("skillType", "").strip(),
        }

    occupations = list(occupations_by_uri.values())
    skills = list(skills_by_uri.values())
    relations = list(relations_by_key.values())
    if not occupations or not skills or not relations:
        raise ValueError(
            "The ESCO files contained no usable English occupations, skills, and "
            "essential/optional relations. Check that the files are from the same release."
        )
    return occupations, skills, relations


def import_catalog(
    database_path: Path,
    source_dir: Path,
    *,
    version: str = DEFAULT_VERSION,
    precompute_embeddings: bool = True,
    model=None,
    device: str | None = None,
) -> dict[str, int]:
    occupations, skills, relations = _build_rows(source_dir)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        main._initialize_esco_catalog_schema(connection)
        with connection:
            connection.execute("DELETE FROM esco_occupation_skill_relations")
            connection.execute("DELETE FROM esco_skills")
            connection.execute("DELETE FROM esco_occupations")
            connection.executemany(
                """
                INSERT INTO esco_occupations(
                    uri, preferred_label, alt_labels, description, isco_group
                ) VALUES (
                    :uri, :preferred_label, :alt_labels, :description, :isco_group
                )
                """,
                occupations,
            )
            connection.executemany(
                """
                INSERT INTO esco_skills(
                    uri, preferred_label, alt_labels, description, skill_type
                ) VALUES (
                    :uri, :preferred_label, :alt_labels, :description, :skill_type
                )
                """,
                skills,
            )
            connection.executemany(
                """
                INSERT INTO esco_occupation_skill_relations(
                    occupation_uri, skill_uri, relation_type, skill_type
                ) VALUES (
                    :occupation_uri, :skill_uri, :relation_type, :skill_type
                )
                """,
                relations,
            )
            metadata = {
                "version": version,
                "source": f"{SOURCE_PAGE}; {SOURCE_API}",
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "occupation_count": str(len(occupations)),
                "skill_count": str(len(skills)),
                "relation_count": str(len(relations)),
                "embeddings_ready": "false",
            }
            connection.executemany(
                """
                INSERT INTO esco_catalog_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                metadata.items(),
            )
        counts = {
            "occupations": len(occupations),
            "skills": len(skills),
            "relations": len(relations),
        }
        if precompute_embeddings:
            counts.update(
                career_recommendations.precompute_catalog_embeddings(
                    connection,
                    model,
                    device=device,
                )
            )
        return counts
    finally:
        connection.close()


def main_cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path(__file__).parent / "data" / f"esco-{DEFAULT_VERSION}",
        help="Directory containing the three official English ESCO CSV exports.",
    )
    parser.add_argument("--version", default=DEFAULT_VERSION, help="ESCO release label.")
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Import CSV data only; run prepare_esco_embeddings.py before app use.",
    )
    parser.add_argument(
        "--device",
        choices=("cpu", "mps", "cuda"),
        default="cpu",
        help="Embedding device. CPU is the reliable default for offline catalog initialization.",
    )
    parser.add_argument("--database", type=Path, default=main.DATABASE_PATH)
    args = parser.parse_args()
    counts = import_catalog(
        args.database,
        args.source_dir,
        version=args.version,
        precompute_embeddings=not args.skip_embeddings,
        device=args.device,
    )
    for dataset, count in counts.items():
        print(f"{dataset}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
