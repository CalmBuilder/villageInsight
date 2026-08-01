from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import DateTime, Uuid, delete, func, select, update
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.session import get_session_factory
from village_insight.templates.catalog_snapshot import create_snapshot

CONTRACT_VERSION = "four-layer-catalog-bundle/v1"

TABLE_NAMES = (
    "semantic_fields",
    "semantic_field_versions",
    "semantic_field_variants",
    "semantic_field_review_events",
    "region_templates",
    "region_template_versions",
    "region_template_review_events",
    "sheet_compositions",
    "sheet_composition_versions",
    "sheet_composition_region_slots",
    "sheet_composition_review_events",
    "workbook_routes",
    "workbook_route_versions",
    "workbook_route_sheet_slots",
    "workbook_route_review_events",
)

BEHAVIOR_CHILDREN = {
    "semantic_field_variants": "field_version_id",
    "sheet_composition_region_slots": "sheet_composition_version_id",
    "workbook_route_sheet_slots": "workbook_route_version_id",
}

ACTOR_EVENT_TABLES = {
    "semantic_field_review_events",
    "region_template_review_events",
    "sheet_composition_review_events",
    "workbook_route_review_events",
}


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _serialize(value: Any) -> Any:
    if isinstance(value, (datetime, uuid.UUID)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


def _deserialize_row(table_name: str, row: dict[str, Any]) -> dict[str, Any]:
    table = Base.metadata.tables[table_name]
    result: dict[str, Any] = {}
    for column in table.columns:
        value = row[column.name]
        if value is not None and isinstance(column.type, Uuid):
            value = uuid.UUID(str(value))
        elif value is not None and isinstance(column.type, DateTime):
            value = datetime.fromisoformat(str(value))
        result[column.name] = value
    return result


def create_catalog_bundle(database: Session) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table_name in TABLE_NAMES:
        table = Base.metadata.tables[table_name]
        primary_keys = list(table.primary_key.columns)
        statement = select(table)
        if primary_keys:
            statement = statement.order_by(*primary_keys)
        rows = database.execute(statement).mappings()
        tables[table_name] = [
            {key: _serialize(value) for key, value in row.items()} for row in rows
        ]
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "restore_policy": (
            "restore complete baseline catalog rows; preserve post-baseline versions and "
            "audit events; remove only extra behavior children attached to baseline versions; "
            "never write business tables"
        ),
        "catalog_snapshot": create_snapshot(database),
        "table_counts": {name: len(rows) for name, rows in tables.items()},
        "tables": tables,
    }
    payload["bundle_sha256"] = _canonical_sha256(payload)
    return payload


def write_catalog_bundle(path: Path, bundle: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode()
    if path.suffix == ".gz":
        with path.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                compressed.write(encoded)
        return
    path.write_bytes(encoded)


def read_catalog_bundle(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            raw = json.load(stream)
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("four-layer catalog bundle must be a JSON object")
    return cast(dict[str, Any], raw)


def validate_catalog_bundle(bundle: dict[str, Any]) -> None:
    if bundle.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("unsupported four-layer catalog bundle contract")
    expected_hash = bundle.get("bundle_sha256")
    payload = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    if expected_hash != _canonical_sha256(payload):
        raise ValueError("four-layer catalog bundle checksum mismatch")
    raw_tables = bundle.get("tables")
    if not isinstance(raw_tables, dict) or set(raw_tables) != set(TABLE_NAMES):
        raise ValueError("four-layer catalog bundle table set mismatch")
    counts = bundle.get("table_counts")
    if not isinstance(counts, dict):
        raise ValueError("four-layer catalog bundle counts are missing")
    for name in TABLE_NAMES:
        rows = raw_tables[name]
        if not isinstance(rows, list) or counts.get(name) != len(rows):
            raise ValueError(f"four-layer catalog bundle count mismatch: {name}")
        columns = set(Base.metadata.tables[name].columns.keys())
        for row in rows:
            if not isinstance(row, dict) or set(row) != columns:
                raise ValueError(f"four-layer catalog bundle row mismatch: {name}")
    snapshot = bundle.get("catalog_snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("four-layer catalog bundle snapshot is missing")


def _effective_rows(
    database: Session,
    *,
    table_name: str,
    serialized_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [_deserialize_row(table_name, row) for row in serialized_rows]
    if table_name not in ACTOR_EVENT_TABLES:
        return rows
    actor_ids = {
        row["actor_user_id"] for row in rows if row.get("actor_user_id") is not None
    }
    if not actor_ids:
        return rows
    users = Base.metadata.tables["users"]
    existing = set(
        database.scalars(select(users.c.id).where(users.c.id.in_(actor_ids)))
    )
    for row in rows:
        if row.get("actor_user_id") not in existing:
            row["actor_user_id"] = None
    return rows


def _sync_table(
    database: Session,
    *,
    table_name: str,
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    table = Base.metadata.tables[table_name]
    primary_keys = list(table.primary_key.columns)
    if len(primary_keys) != 1:
        raise ValueError(f"catalog recovery requires one primary key: {table_name}")
    primary_key = primary_keys[0]
    existing = {
        row[primary_key.name]: dict(row)
        for row in database.execute(select(table)).mappings()
    }
    inserted = 0
    updated = 0
    unchanged = 0
    for row in rows:
        identity = row[primary_key.name]
        current = existing.get(identity)
        if current is None:
            database.execute(table.insert().values(**row))
            inserted += 1
            continue
        changed = any(current[column.name] != row[column.name] for column in table.columns)
        if changed:
            values = {key: value for key, value in row.items() if key != primary_key.name}
            database.execute(
                update(table).where(primary_key == identity).values(**values)
            )
            updated += 1
        else:
            unchanged += 1
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


def _remove_extra_behavior_children(
    database: Session,
    *,
    table_name: str,
    parent_column_name: str,
    rows: list[dict[str, Any]],
) -> int:
    table = Base.metadata.tables[table_name]
    parent_ids = {row[parent_column_name] for row in rows}
    expected_ids = {row["id"] for row in rows}
    if not parent_ids:
        return 0
    statement = select(func.count()).select_from(table).where(
        table.c[parent_column_name].in_(parent_ids)
    )
    if expected_ids:
        statement = statement.where(table.c.id.not_in(expected_ids))
    removed = int(database.scalar(statement) or 0)
    if removed:
        delete_statement = delete(table).where(
            table.c[parent_column_name].in_(parent_ids)
        )
        if expected_ids:
            delete_statement = delete_statement.where(table.c.id.not_in(expected_ids))
        database.execute(delete_statement)
    return removed


def apply_catalog_bundle(
    database: Session,
    *,
    bundle: dict[str, Any],
) -> dict[str, dict[str, int]]:
    validate_catalog_bundle(bundle)
    serialized_tables = cast(dict[str, list[dict[str, Any]]], bundle["tables"])
    effective_tables = {
        name: _effective_rows(
            database,
            table_name=name,
            serialized_rows=serialized_tables[name],
        )
        for name in TABLE_NAMES
    }
    result: dict[str, dict[str, int]] = {}
    for table_name in TABLE_NAMES:
        removed_extra = 0
        parent_column = BEHAVIOR_CHILDREN.get(table_name)
        if parent_column is not None:
            removed_extra = _remove_extra_behavior_children(
                database,
                table_name=table_name,
                parent_column_name=parent_column,
                rows=effective_tables[table_name],
            )
        result[table_name] = _sync_table(
            database,
            table_name=table_name,
            rows=effective_tables[table_name],
        )
        if parent_column is not None:
            result[table_name]["removed_extra"] = removed_extra
    database.flush()
    database.expire_all()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a complete portable four-layer template catalog bundle."
    )
    parser.add_argument("operation", choices=("create",))
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    with get_session_factory()() as database:
        bundle = create_catalog_bundle(database)
    write_catalog_bundle(arguments.output, bundle)
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                "file_sha256": hashlib.sha256(arguments.output.read_bytes()).hexdigest(),
                "file_size_bytes": arguments.output.stat().st_size,
                "bundle_sha256": bundle["bundle_sha256"],
                "snapshot_sha256": bundle["catalog_snapshot"]["snapshot_sha256"],
                "counts": bundle["catalog_snapshot"]["counts"],
                "table_counts": bundle["table_counts"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
