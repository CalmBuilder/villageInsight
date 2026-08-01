from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from village_insight.db.models import (
    ApprovedImportPlan,
    DatasetRecord,
    HermesRecognitionCache,
    IngestionItem,
    Job,
    QualityIssue,
    RecordIndexValue,
    RecordValueLineage,
    SemanticFieldVersion,
)
from village_insight.db.session import get_session_factory

REPORT_CONTRACT = "ingestion-four-layer-regression/v2"
RECORD_BATCH_SIZE = 200
LINEAGE_ID_BATCH_SIZE = 5_000


def _value(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row[name]
    return getattr(row, name)


def _chunks(values: Sequence[Any], size: int) -> list[Sequence[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _typed_value(index: RecordIndexValue) -> Any:
    values = {
        "text": _value(index, "text_value"),
        "integer": _value(index, "integer_value"),
        "decimal": _value(index, "decimal_value"),
        "boolean": _value(index, "boolean_value"),
        "date": _value(index, "date_value"),
        "datetime": _value(index, "datetime_value"),
    }
    return values.get(_value(index, "data_type"))


def _values_equal(data_type: str, semantic_value: Any, index_value: Any) -> bool:
    if semantic_value is None or index_value is None:
        return semantic_value is None and index_value is None
    if data_type == "decimal":
        return Decimal(str(semantic_value)) == Decimal(str(index_value))
    if data_type == "date" and isinstance(index_value, date):
        return str(semantic_value) == index_value.isoformat()
    if data_type == "datetime" and isinstance(index_value, datetime):
        semantic = str(semantic_value).replace("Z", "+00:00")
        return datetime.fromisoformat(semantic) == index_value
    return bool(semantic_value == index_value)


def _record_violations(
    record: Any,
    indices: list[Any],
    lineage_by_index_id: dict[Any, Any],
) -> tuple[list[str], int, int]:
    violations: set[str] = set()
    raw_data = _value(record, "raw_data")
    semantic_data = _value(record, "semantic_data")
    raw_columns = raw_data.get("columns")
    if raw_data.get("contract_version") != "dataset-record-raw/v1" or not isinstance(
        raw_columns, dict
    ):
        violations.add("RAW_CONTRACT_INVALID")
        raw_columns = {}
    semantic_fields = semantic_data.get("fields")
    if semantic_data.get("contract_version") != "dataset-record-semantic/v1" or not isinstance(
        semantic_fields, dict
    ):
        violations.add("SEMANTIC_CONTRACT_INVALID")
        semantic_fields = {}

    semantic_entries: dict[tuple[str, str], dict[str, Any]] = {}
    for field_code, roles in semantic_fields.items():
        if not isinstance(roles, dict):
            violations.add("SEMANTIC_ROLE_MAP_INVALID")
            continue
        for role_key, entry in roles.items():
            if not isinstance(entry, dict):
                violations.add("SEMANTIC_ENTRY_INVALID")
                continue
            semantic_entries[(str(field_code), "" if role_key == "$value" else role_key)] = entry
    index_entries = {
        (_value(entry, "semantic_field_code"), _value(entry, "role")): entry for entry in indices
    }
    if set(semantic_entries) != set(index_entries):
        violations.add("SEMANTIC_INDEX_KEY_MISMATCH")

    raw_cells = {
        str(column.get("source_cell", {}).get("id")): column.get("source_cell", {})
        for column in raw_columns.values()
        if isinstance(column, dict) and isinstance(column.get("source_cell"), dict)
    }
    for key, index in index_entries.items():
        semantic = semantic_entries.get(key)
        lineage = lineage_by_index_id.get(_value(index, "id"))
        if semantic is None:
            continue
        if not _values_equal(
            _value(index, "data_type"), semantic.get("value"), _typed_value(index)
        ):
            violations.add("SEMANTIC_INDEX_VALUE_MISMATCH")
        if lineage is None:
            violations.add("INDEX_LINEAGE_MISSING")
            continue
        if (
            _value(lineage, "source_sha256") != raw_data.get("source_sha256")
            or _value(lineage, "sheet_id") != _value(record, "sheet_id")
            or _value(lineage, "source_cell_id") != semantic.get("source_cell_id")
            or _value(lineage, "coordinate") != semantic.get("coordinate")
        ):
            violations.add("LINEAGE_SEMANTIC_EVIDENCE_MISMATCH")
        raw_cell = raw_cells.get(_value(lineage, "source_cell_id"))
        if raw_cell is None:
            violations.add("LINEAGE_RAW_CELL_MISSING")
        elif (
            raw_cell.get("coordinate") != _value(lineage, "coordinate")
            or raw_cell.get("raw_value") != _value(lineage, "raw_value")
            or raw_cell.get("display_value") != _value(lineage, "display_value")
        ):
            violations.add("LINEAGE_RAW_EVIDENCE_MISMATCH")
    return sorted(violations), len(raw_columns), len(semantic_entries)


def build_report(database: Session, *, run_id: str) -> dict[str, Any]:
    # Regression databases are intentionally frozen before unrelated schema
    # work is allowed to advance the application model. Read only the columns
    # this report owns so a newer optional feature cannot make historical
    # evidence unreadable before its migration is applied to the frozen DB.
    items = list(
        database.scalars(
            select(IngestionItem)
            .options(
                load_only(
                    IngestionItem.id,
                    IngestionItem.source_sha256,
                    IngestionItem.relative_path,
                    IngestionItem.original_name,
                    IngestionItem.status,
                    IngestionItem.formal_import_status,
                    IngestionItem.error_code,
                    IngestionItem.created_at,
                )
            )
            .order_by(IngestionItem.created_at)
        )
    )
    jobs = list(database.scalars(select(Job)))
    plans = list(
        database.scalars(
            select(ApprovedImportPlan).options(
                load_only(
                    ApprovedImportPlan.plan_source,
                    ApprovedImportPlan.field_mappings,
                )
            )
        )
    )
    issues = list(database.scalars(select(QualityIssue)))
    caches = list(database.scalars(select(HermesRecognitionCache)))
    field_versions = list(database.scalars(select(SemanticFieldVersion)))
    violation_counts: Counter[str] = Counter()
    item_rows: list[dict[str, Any]] = []
    raw_column_count = 0
    semantic_entry_count = 0
    record_count = 0
    index_value_count = 0
    lineage_count = 0
    for item in items:
        item_violations: Counter[str] = Counter()
        item_raw_columns = 0
        item_semantic_entries = 0
        item_indices = 0
        item_lineages = 0
        item_record_count = 0
        mapping_status_counts: Counter[str] = Counter()
        records = database.execute(
            select(
                DatasetRecord.id,
                DatasetRecord.sheet_id,
                DatasetRecord.raw_data,
                DatasetRecord.semantic_data,
                DatasetRecord.mapping_status,
            )
            .where(DatasetRecord.item_id == item.id)
            .order_by(DatasetRecord.id)
            .execution_options(yield_per=RECORD_BATCH_SIZE)
        ).mappings()
        for record_batch in records.partitions(RECORD_BATCH_SIZE):
            record_ids = [record["id"] for record in record_batch]
            index_rows = list(
                database.execute(
                    select(
                        RecordIndexValue.id,
                        RecordIndexValue.record_id,
                        RecordIndexValue.semantic_field_code,
                        RecordIndexValue.role,
                        RecordIndexValue.data_type,
                        RecordIndexValue.text_value,
                        RecordIndexValue.integer_value,
                        RecordIndexValue.decimal_value,
                        RecordIndexValue.boolean_value,
                        RecordIndexValue.date_value,
                        RecordIndexValue.datetime_value,
                    ).where(RecordIndexValue.record_id.in_(record_ids))
                ).mappings()
            )
            indices_by_record: dict[Any, list[Any]] = defaultdict(list)
            for index_row in index_rows:
                indices_by_record[index_row["record_id"]].append(index_row)
            lineage_by_index_id: dict[Any, Any] = {}
            index_ids = [index_row["id"] for index_row in index_rows]
            for index_id_batch in _chunks(index_ids, LINEAGE_ID_BATCH_SIZE):
                lineage_by_index_id.update(
                    {
                        lineage["record_index_value_id"]: lineage
                        for lineage in database.execute(
                            select(
                                RecordValueLineage.record_index_value_id,
                                RecordValueLineage.source_sha256,
                                RecordValueLineage.sheet_id,
                                RecordValueLineage.source_cell_id,
                                RecordValueLineage.coordinate,
                                RecordValueLineage.raw_value,
                                RecordValueLineage.display_value,
                            ).where(RecordValueLineage.record_index_value_id.in_(index_id_batch))
                        ).mappings()
                    }
                )
            for record in record_batch:
                record_indices = indices_by_record[record["id"]]
                violations, raw_count, semantic_count = _record_violations(
                    record,
                    record_indices,
                    lineage_by_index_id,
                )
                item_violations.update(violations)
                item_raw_columns += raw_count
                item_semantic_entries += semantic_count
                item_indices += len(record_indices)
                item_lineages += sum(index["id"] in lineage_by_index_id for index in record_indices)
                item_record_count += 1
                mapping_status_counts[str(record["mapping_status"])] += 1
        violation_counts.update(item_violations)
        raw_column_count += item_raw_columns
        semantic_entry_count += item_semantic_entries
        record_count += item_record_count
        index_value_count += item_indices
        lineage_count += item_lineages
        item_rows.append(
            {
                "source_sha256": item.source_sha256,
                "relative_path": item.relative_path or item.original_name,
                "status": item.status,
                "formal_import_status": item.formal_import_status,
                "error_code": item.error_code,
                "record_count": item_record_count,
                "raw_column_count": item_raw_columns,
                "semantic_entry_count": item_semantic_entries,
                "index_value_count": item_indices,
                "lineage_count": item_lineages,
                "mapping_status_counts": dict(sorted(mapping_status_counts.items())),
                "violation_counts": dict(sorted(item_violations.items())),
            }
        )
    job_counts = Counter(str(job.status) for job in jobs)
    job_kind_status_counts = Counter(f"{job.kind}:{job.status}" for job in jobs)
    job_attempt_counts = Counter(str(job.attempts) for job in jobs)
    status_counts = Counter(str(item.formal_import_status) for item in items)
    plan_source_counts = Counter(plan.plan_source for plan in plans)
    mapping_source_counts: Counter[str] = Counter()
    review_mapping_count = 0
    duplicate_role_count = 0
    mapping_count = 0
    for plan in plans:
        for mapping in plan.field_mappings:
            mapping_count += 1
            mapping_source_counts[str(mapping.get("mapping_source") or "template")] += 1
            review_mapping_count += int(bool(mapping.get("requires_review")))
            duplicate_role_count += int(str(mapping.get("role") or "").startswith("duplicate"))
    cache_contract_counts = Counter(
        f"{cache.prompt_version}|{cache.schema_version}" for cache in caches
    )
    report = {
        "contract_version": REPORT_CONTRACT,
        "run_id": run_id,
        "summary": {
            "item_count": len(items),
            "formal_import_status_counts": dict(sorted(status_counts.items())),
            "job_status_counts": dict(sorted(job_counts.items())),
            "job_kind_status_counts": dict(sorted(job_kind_status_counts.items())),
            "job_attempt_counts": dict(sorted(job_attempt_counts.items())),
            "record_count": record_count,
            "raw_column_count": raw_column_count,
            "semantic_entry_count": semantic_entry_count,
            "index_value_count": index_value_count,
            "lineage_count": lineage_count,
            "four_layer_violation_counts": dict(sorted(violation_counts.items())),
            "four_layer_closed": not violation_counts
            and semantic_entry_count == index_value_count == lineage_count,
            "approved_plan_count": len(plans),
            "plan_source_counts": dict(sorted(plan_source_counts.items())),
            "field_mapping_count": mapping_count,
            "mapping_source_counts": dict(sorted(mapping_source_counts.items())),
            "review_mapping_count": review_mapping_count,
            "duplicate_role_count": duplicate_role_count,
            "quality_issue_code_counts": dict(
                sorted(Counter(issue.code for issue in issues).items())
            ),
            "semantic_field_version_source_counts": dict(
                sorted(
                    Counter(str(version.source or "unknown") for version in field_versions).items()
                )
            ),
            "hermes_cache_contract_counts": dict(sorted(cache_contract_counts.items())),
        },
        "items": item_rows,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a value-safe four-layer ingestion regression report."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    with get_session_factory()() as database:
        report = build_report(database, run_id=arguments.run_id)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
