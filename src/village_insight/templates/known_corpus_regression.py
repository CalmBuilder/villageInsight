from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select

from village_insight.db.models import (
    DocumentProfile,
    FieldMatch,
    IngestionItem,
    RegionTemplateMatch,
)
from village_insight.db.session import get_session_factory
from village_insight.parsing.profile_storage import load_workbook_profile
from village_insight.templates.matching import match_profile


def evaluate_known_corpus() -> dict[str, Any]:
    database = get_session_factory()()
    try:
        items = list(database.scalars(select(IngestionItem).order_by(IngestionItem.id)))
        file_rows: list[dict[str, Any]] = []
        for item in items:
            profile_record = database.get(DocumentProfile, item.id)
            if profile_record is None:
                raise ValueError(f"item {item.id} has no stored document profile")
            match = match_profile(
                database,
                item_id=item.id,
                profile=load_workbook_profile(profile_record),
            )
            region_counts = Counter(
                str(match_type)
                for match_type in database.scalars(
                    select(RegionTemplateMatch.match_type).where(
                        RegionTemplateMatch.item_id == item.id
                    )
                )
            )
            field_counts = Counter(
                str(match_type)
                for match_type in database.scalars(
                    select(FieldMatch.match_type).where(FieldMatch.item_id == item.id)
                )
            )
            file_rows.append(
                {
                    "item_id": str(item.id),
                    "source_sha256": item.source_sha256,
                    "requires_hermes": match.requires_hermes,
                    "region_counts": dict(region_counts),
                    "field_counts": dict(field_counts),
                }
            )
        total_regions = sum(sum(row["region_counts"].values()) for row in file_rows)
        exact_regions = sum(row["region_counts"].get("exact", 0) for row in file_rows)
        total_fields = sum(sum(row["field_counts"].values()) for row in file_rows)
        exact_fields = sum(row["field_counts"].get("exact", 0) for row in file_rows)
        no_hermes = sum(not row["requires_hermes"] for row in file_rows)
        return {
            "contract_version": "known-corpus-template-regression/v1",
            "file_count": len(file_rows),
            "metrics": {
                "no_hermes_file_count": no_hermes,
                "exact_region_count": exact_regions,
                "total_region_count": total_regions,
                "exact_field_count": exact_fields,
                "total_field_count": total_fields,
            },
            "acceptance": {
                "all_files_match_without_hermes": no_hermes == len(file_rows),
                "all_regions_match_exactly": exact_regions == total_regions,
                "all_fields_match_exactly": exact_fields == total_fields,
            },
            "files": file_rows,
        }
    finally:
        database.rollback()
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = evaluate_known_corpus()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["metrics"], ensure_ascii=False))
    print(json.dumps(report["acceptance"], ensure_ascii=False))


if __name__ == "__main__":
    main()
