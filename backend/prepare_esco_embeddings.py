"""Precompute or refresh local ESCO Sentence Transformer embeddings."""

from __future__ import annotations

import argparse

import career_recommendations
import main


def main_cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device",
        choices=("cpu", "mps", "cuda"),
        default="cpu",
        help="Embedding device. CPU is the reliable default for offline catalog initialization.",
    )
    args = parser.parse_args()
    with main.database_connection() as connection:
        counts = career_recommendations.precompute_catalog_embeddings(
            connection,
            device=args.device,
        )
    for dataset, count in counts.items():
        print(f"{dataset}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
