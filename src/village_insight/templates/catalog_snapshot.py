from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from village_insight.db.models import (
    RegionTemplate,
    RegionTemplateVersion,
    SemanticField,
    SemanticFieldVersion,
    SheetComposition,
    SheetCompositionVersion,
    TemplateStatus,
    WorkbookRoute,
    WorkbookRouteVersion,
)
from village_insight.db.session import get_session_factory

CONTRACT_VERSION = "four-layer-catalog-snapshot/v2"
LEGACY_CONTRACT_VERSION = "four-layer-catalog-snapshot/v1"


def _sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _legacy_field_payload(version: SemanticFieldVersion) -> dict[str, Any]:
    return {
        "name": version.name,
        "description": version.description,
        "layer": version.layer,
        "data_type": version.data_type,
        "unit_dimension": version.unit_dimension,
        "aliases": version.aliases,
        "validators": version.validators,
        "variants": sorted(
            [
                {
                    "variant_key": variant.variant_key,
                    "kind": variant.kind,
                    "normalized_value": variant.normalized_value,
                    "alias": variant.alias,
                    "header_path": variant.header_path,
                    "parent_path": variant.parent_path,
                    "role": variant.role,
                    "domain": variant.domain,
                    "record_type": variant.record_type,
                    "observed_data_type": variant.observed_data_type,
                    "unit_dimension": variant.unit_dimension,
                    "source": variant.source,
                    "confidence_basis_points": variant.confidence_basis_points,
                    "evidence": variant.evidence,
                }
                for variant in version.variants
            ],
            key=lambda item: item["variant_key"],
        ),
    }


def _field_payload(version: SemanticFieldVersion) -> dict[str, Any]:
    return {
        **_legacy_field_payload(version),
        "source": version.source,
        "source_metadata": version.source_metadata,
    }


def _region_payload(version: RegionTemplateVersion) -> dict[str, Any]:
    return {
        "name": version.name,
        "description": version.description,
        "domain": version.domain,
        "record_type": version.record_type,
        "record_grain": version.record_grain,
        "region_kind": version.region_kind,
        "region_fingerprint": version.region_fingerprint,
        "header_signature": version.header_signature,
        "layout_rules": version.layout_rules,
        "field_bindings": version.field_bindings,
        "identity_policy": version.identity_policy,
        "quality_rules": version.quality_rules,
        "source": version.source,
        "source_metadata": version.source_metadata,
    }


def _sheet_payload(version: SheetCompositionVersion) -> dict[str, Any]:
    return {
        "name": version.name,
        "description": version.description,
        "composition_fingerprint": version.composition_fingerprint,
        "matching_rules": version.matching_rules,
        "source": version.source,
        "source_metadata": version.source_metadata,
        "region_slots": [
            {
                "slot_key": slot.slot_key,
                "region_template_id": str(slot.region_template_id),
                "region_template_version": slot.region_template_version,
                "ordinal": slot.ordinal,
                "required": slot.required,
                "cardinality": slot.cardinality,
                "materialize": slot.materialize,
                "match_hints": slot.match_hints,
            }
            for slot in version.region_slots
        ],
    }


def _route_payload(version: WorkbookRouteVersion) -> dict[str, Any]:
    return {
        "name": version.name,
        "description": version.description,
        "route_fingerprint": version.route_fingerprint,
        "matching_rules": version.matching_rules,
        "source": version.source,
        "source_metadata": version.source_metadata,
        "sheet_slots": [
            {
                "slot_key": slot.slot_key,
                "sheet_composition_id": str(slot.sheet_composition_id),
                "sheet_composition_version": slot.sheet_composition_version,
                "ordinal": slot.ordinal,
                "required": slot.required,
                "cardinality": slot.cardinality,
                "materialize": slot.materialize,
                "match_hints": slot.match_hints,
            }
            for slot in version.sheet_slots
        ],
    }


def _snapshot_rows(
    objects: list[Any],
    *,
    payload_builder: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(objects, key=lambda value: value.code):
        version = next(
            (
                candidate
                for candidate in item.versions
                if candidate.version == item.published_version
            ),
            None,
        )
        rows.append(
            {
                "id": str(item.id),
                "code": item.code,
                "published_version": item.published_version,
                "content_sha256": (
                    _sha256(payload_builder(version)) if version is not None else None
                ),
            }
        )
    return rows


def create_snapshot(database: Session) -> dict[str, Any]:
    fields = list(
        database.scalars(
            select(SemanticField).options(
                selectinload(SemanticField.versions).selectinload(
                    SemanticFieldVersion.variants
                )
            )
        )
    )
    regions = list(
        database.scalars(
            select(RegionTemplate).options(selectinload(RegionTemplate.versions))
        )
    )
    sheets = list(
        database.scalars(
            select(SheetComposition).options(
                selectinload(SheetComposition.versions).selectinload(
                    SheetCompositionVersion.region_slots
                )
            )
        )
    )
    routes = list(
        database.scalars(
            select(WorkbookRoute).options(
                selectinload(WorkbookRoute.versions).selectinload(
                    WorkbookRouteVersion.sheet_slots
                )
            )
        )
    )
    layers = {
        "semantic_fields": _snapshot_rows(fields, payload_builder=_field_payload),
        "region_templates": _snapshot_rows(regions, payload_builder=_region_payload),
        "sheet_compositions": _snapshot_rows(sheets, payload_builder=_sheet_payload),
        "workbook_routes": _snapshot_rows(routes, payload_builder=_route_payload),
    }
    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "restore_policy": (
            "restore published pointers and statuses; preserve all versions and "
            "ingested records; disable catalog objects created after this snapshot"
        ),
        "layers": layers,
        "counts": {name: len(rows) for name, rows in layers.items()},
    }
    snapshot["snapshot_sha256"] = _sha256(snapshot)
    return snapshot


def _restore_layer(
    *,
    objects: list[Any],
    rows: list[dict[str, Any]],
    payload_builder: Any,
) -> dict[str, int]:
    expected = {str(row["code"]): row for row in rows}
    current = {str(item.code): item for item in objects}
    missing = sorted(set(expected) - set(current))
    if missing:
        raise ValueError(
            "snapshot catalog objects are missing from the database: "
            + ", ".join(missing[:10])
        )
    restored = 0
    disabled = 0
    unchanged = 0
    for code, item in current.items():
        row = expected.get(code)
        if row is None:
            changed = item.published_version is not None
            for version in item.versions:
                if version.status == TemplateStatus.PUBLISHED:
                    version.status = TemplateStatus.DEPRECATED
                    changed = True
            item.published_version = None
            disabled += int(changed)
            unchanged += int(not changed)
            continue
        target_number = row["published_version"]
        target = next(
            (
                version
                for version in item.versions
                if version.version == target_number
            ),
            None,
        )
        if target_number is not None and target is None:
            raise ValueError(f"snapshot version is missing: {code}@{target_number}")
        if target is not None:
            actual_hash = _sha256(payload_builder(target))
            if actual_hash != row["content_sha256"]:
                raise ValueError(
                    f"snapshot version content changed: {code}@{target_number}; "
                    "use the PostgreSQL disaster-recovery dump"
                )
        changed = item.published_version != target_number
        for version in item.versions:
            desired = (
                TemplateStatus.PUBLISHED
                if version.version == target_number
                else TemplateStatus.DEPRECATED
                if version.status == TemplateStatus.PUBLISHED
                else version.status
            )
            if version.status != desired:
                version.status = desired
                changed = True
        item.published_version = target_number
        restored += int(changed)
        unchanged += int(not changed)
    return {
        "restored": restored,
        "disabled_post_snapshot": disabled,
        "unchanged": unchanged,
    }


def restore_snapshot(
    database: Session,
    *,
    snapshot: dict[str, Any],
) -> dict[str, dict[str, int]]:
    contract_version = snapshot.get("contract_version")
    if contract_version not in {CONTRACT_VERSION, LEGACY_CONTRACT_VERSION}:
        raise ValueError("unsupported four-layer catalog snapshot contract")
    expected_hash = snapshot.get("snapshot_sha256")
    payload = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    if expected_hash != _sha256(payload):
        raise ValueError("four-layer catalog snapshot checksum mismatch")
    if database.get_bind().dialect.name == "postgresql":
        database.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('village_insight_four_layer_restore'))"
            )
        )
    return {
        "semantic_fields": _restore_layer(
            objects=list(
                database.scalars(
                    select(SemanticField).options(
                        selectinload(SemanticField.versions).selectinload(
                            SemanticFieldVersion.variants
                        )
                    )
                )
            ),
            rows=snapshot["layers"]["semantic_fields"],
            payload_builder=(
                _legacy_field_payload
                if contract_version == LEGACY_CONTRACT_VERSION
                else _field_payload
            ),
        ),
        "region_templates": _restore_layer(
            objects=list(
                database.scalars(
                    select(RegionTemplate).options(
                        selectinload(RegionTemplate.versions)
                    )
                )
            ),
            rows=snapshot["layers"]["region_templates"],
            payload_builder=_region_payload,
        ),
        "sheet_compositions": _restore_layer(
            objects=list(
                database.scalars(
                    select(SheetComposition).options(
                        selectinload(SheetComposition.versions).selectinload(
                            SheetCompositionVersion.region_slots
                        )
                    )
                )
            ),
            rows=snapshot["layers"]["sheet_compositions"],
            payload_builder=_sheet_payload,
        ),
        "workbook_routes": _restore_layer(
            objects=list(
                database.scalars(
                    select(WorkbookRoute).options(
                        selectinload(WorkbookRoute.versions).selectinload(
                            WorkbookRouteVersion.sheet_slots
                        )
                    )
                )
            ),
            rows=snapshot["layers"]["workbook_routes"],
            payload_builder=_route_payload,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create or restore a non-destructive four-layer catalog snapshot."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--output", type=Path, required=True)
    restore = subparsers.add_parser("restore")
    restore.add_argument("--input", type=Path, required=True)
    restore.add_argument("--confirm", action="store_true")
    restore.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()

    with get_session_factory()() as database:
        if arguments.operation == "create":
            snapshot = create_snapshot(database)
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                json.dumps(
                    {
                        "output": str(arguments.output),
                        "snapshot_sha256": snapshot["snapshot_sha256"],
                        "counts": snapshot["counts"],
                    },
                    ensure_ascii=False,
                )
            )
            return
        if not arguments.confirm and not arguments.dry_run:
            parser.error("restore requires --confirm; use --dry-run to validate only")
        snapshot = json.loads(arguments.input.read_text(encoding="utf-8"))
        try:
            result = restore_snapshot(database, snapshot=snapshot)
            if arguments.dry_run:
                database.rollback()
            else:
                database.commit()
        except Exception:
            database.rollback()
            raise
        print(
            json.dumps(
                {
                    "input": str(arguments.input),
                    "dry_run": arguments.dry_run,
                    "result": result,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
