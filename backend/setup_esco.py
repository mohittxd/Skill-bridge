"""Idempotent ESCO catalog setup for SkillBridge.

Run from the backend folder: python setup_esco.py

Downloads the project's pinned ESCO release if source CSVs are missing,
imports its relationships, and builds the embeddings needed by the API.
The SQLite database should be on durable storage in production.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

import main
import download_esco_api
import import_esco

LOG = logging.getLogger("skillbridge.esco")


def catalog_status(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"ready": False, "reason": "Database does not exist yet"}
    with sqlite3.connect(path) as conn:
        try:
            metadata = dict(conn.execute("SELECT key, value FROM esco_catalog_metadata"))
            counts = {
                name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in (
                    "esco_occupations",
                    "esco_skills",
                    "esco_occupation_skill_relations",
                    "esco_concept_embeddings",
                )
            }
        except sqlite3.OperationalError:
            return {"ready": False, "reason": "ESCO schema is missing"}
    ready = (
        all(counts.values())
        and metadata.get("version") == import_esco.DEFAULT_VERSION
        and metadata.get("embeddings_ready") == "true"
        and metadata.get("embedding_model") == import_esco.career_recommendations.MODEL_NAME
        and counts["esco_concept_embeddings"] >= (
            counts["esco_occupations"] + counts["esco_skills"]
        )
    )
    return {"ready": bool(ready), "version": metadata.get("version"), "counts": counts}


def main_cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=main.DATABASE_PATH)
    parser.add_argument(
        "--source-dir", type=Path,
        default=Path(__file__).resolve().parent / "data" / f"esco-{import_esco.DEFAULT_VERSION}",
    )
    parser.add_argument("--force", action="store_true", help="Reimport an already complete catalog")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db = args.database.resolve()
    source = args.source_dir.resolve()
    before = catalog_status(db)
    if before["ready"] and not args.force:
        LOG.info("ESCO catalog already ready: %s", before)
        return 0

    try:
        for dataset in ("occupations", "skills", "relations"):
            import_esco._find_source_file(source, dataset)
    except ValueError:
        LOG.info("Downloading official ESCO %s English classification...", import_esco.DEFAULT_VERSION)
        download_esco_api.export_catalog(source, version=import_esco.DEFAULT_VERSION)

    LOG.info("Importing ESCO rows and building cached sentence embeddings...")
    counts = import_esco.import_catalog(db, source, version=import_esco.DEFAULT_VERSION)
    after = catalog_status(db)
    if not after["ready"]:
        raise RuntimeError(f"ESCO import finished but catalog is not ready: {after}")
    LOG.info("ESCO catalog ready: %s", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
