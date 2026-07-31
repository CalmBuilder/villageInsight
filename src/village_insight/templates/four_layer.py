from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    DocumentProfile,
    DocumentTemplate,
    IngestionItem,
    RegionTemplate,
    RegionTemplateMatch,
    RegionTemplateVersion,
    SemanticField,
    SemanticFieldVersion,
    SheetComposition,
    SheetCompositionRegionSlot,
    SheetCompositionVersion,
    TemplateMatch,
    TemplateRegionComponent,
    TemplateStatus,
    TemplateVersion,
    WorkbookRoute,
    WorkbookRouteSheetSlot,
    WorkbookRouteVersion,
)
from village_insight.parsing.profile_storage import load_workbook_profile
from village_insight.templates.field_variants import build_field_variant


def _add_variant_if_absent(
    version: SemanticFieldVersion,
    values: dict[str, Any],
) -> bool:
    variant = build_field_variant(values)
    if any(existing.variant_key == variant.variant_key for existing in version.variants):
        return False
    version.variants.append(variant)
    return True


def backfill_four_layer_foundation(database: Session) -> dict[str, int]:
    """Convert published legacy evidence into immutable field and Region seeds."""
    field_versions = {
        field.code: version
        for field, version in database.execute(
            select(SemanticField, SemanticFieldVersion).where(
                SemanticField.id == SemanticFieldVersion.field_id,
                SemanticField.published_version == SemanticFieldVersion.version,
                SemanticFieldVersion.status == TemplateStatus.PUBLISHED,
            )
        )
    }
    variants_created = 0
    for code, version in field_versions.items():
        for alias in [version.name, *version.aliases]:
            if not alias:
                continue
            variants_created += _add_variant_if_absent(
                version,
                {
                    "kind": "alias",
                    "alias": alias,
                    "source": "migration",
                    "confidence_basis_points": 10_000,
                    "evidence": {
                        "semantic_field_code": code,
                        "semantic_field_version": version.version,
                    },
                },
            )

    published_versions = list(
        database.scalars(
            select(TemplateVersion)
            .join(DocumentTemplate)
            .where(
                TemplateVersion.status == TemplateStatus.PUBLISHED,
                DocumentTemplate.published_version == TemplateVersion.version,
            )
        )
    )
    regions_created = 0
    regions_existing = 0
    for template_version in published_versions:
        definition = template_version.definition
        context = {
            "domain": str(definition.get("domain") or ""),
            "record_type": str(definition.get("record_type") or ""),
        }
        bindings = definition.get("field_bindings", [])
        for binding in bindings:
            code = str(binding.get("semantic_field_code") or "")
            field_version = field_versions.get(code)
            if field_version is None:
                continue
            path = [str(part) for part in binding.get("header_path", []) if str(part)]
            if not path:
                continue
            variants_created += _add_variant_if_absent(
                field_version,
                {
                    "kind": "role_context" if binding.get("role") else "header_path",
                    "header_path": path,
                    "role": binding.get("role"),
                    **context,
                    "observed_data_type": field_version.data_type,
                    "unit_dimension": binding.get("unit"),
                    "source": "migration",
                    "confidence_basis_points": 10_000,
                    "evidence": {
                        "legacy_template_id": str(template_version.template_id),
                        "legacy_template_version": template_version.version,
                    },
                },
            )

        components = list(
            database.scalars(
                select(TemplateRegionComponent)
                .where(TemplateRegionComponent.template_version_id == template_version.id)
                .order_by(TemplateRegionComponent.source_decision_index)
            )
        )
        snapshot = (template_version.source_metadata or {}).get(
            "layout_projection_snapshot",
            {},
        )
        snapshot_decisions = snapshot.get("decisions", []) if isinstance(snapshot, dict) else []
        for component in components:
            code = f"region.{template_version.template.code}.{component.region_fingerprint[:12]}"
            existing = database.scalar(select(RegionTemplate).where(RegionTemplate.code == code))
            if existing is not None:
                for existing_version in existing.versions:
                    if existing_version.region_kind not in {
                        "table",
                        "form",
                        "matrix",
                    }:
                        existing_version.source_metadata = {
                            **existing_version.source_metadata,
                            "legacy_region_kind": existing_version.region_kind,
                        }
                        existing_version.region_kind = "table"
                regions_existing += 1
                continue
            component_bindings = [
                bindings[index]
                for index in component.field_binding_indexes
                if 0 <= index < len(bindings)
            ]
            layout_rules = (
                snapshot_decisions[component.source_decision_index]
                if component.source_decision_index < len(snapshot_decisions)
                and isinstance(
                    snapshot_decisions[component.source_decision_index],
                    dict,
                )
                else {}
            )
            region = RegionTemplate(code=code, published_version=1)
            region.versions.append(
                RegionTemplateVersion(
                    version=1,
                    name=(f"{template_version.name} Region {component.source_decision_index + 1}"),
                    status=TemplateStatus.PUBLISHED,
                    domain=str(definition.get("domain") or "unknown"),
                    record_type=str(definition.get("record_type") or "record"),
                    record_grain=str(definition.get("record_grain") or "one_row_per_record"),
                    region_kind=(
                        str(
                            component.signature.get("kind")
                            or definition.get("region_kind")
                            or "table"
                        )
                        if str(
                            component.signature.get("kind")
                            or definition.get("region_kind")
                            or "table"
                        )
                        in {"table", "form", "matrix"}
                        else "table"
                    ),
                    region_fingerprint=component.region_fingerprint,
                    header_signature=[
                        [str(part) for part in path]
                        for path in component.signature.get("headers", [])
                        if isinstance(path, list)
                    ],
                    layout_rules=dict(layout_rules),
                    field_bindings=component_bindings,
                    identity_policy={
                        "field_codes": definition.get(
                            "identity_field_codes",
                            [],
                        )
                    },
                    quality_rules=[],
                    source="migration",
                    source_metadata={
                        "legacy_template_id": str(template_version.template_id),
                        "legacy_template_version": template_version.version,
                        "legacy_component_id": str(component.id),
                    },
                )
            )
            database.add(region)
            regions_created += 1
    database.flush()
    return {
        "field_variants_created": variants_created,
        "region_templates_created": regions_created,
        "region_templates_existing": regions_existing,
    }


def _catalog_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def seed_composition_route_from_verified_item(
    database: Session,
    *,
    item_id: uuid.UUID,
) -> dict[str, int]:
    """Seed upper layers from one already verified, exactly matched workbook."""
    item = database.get(IngestionItem, item_id)
    profile = database.get(DocumentProfile, item_id)
    summary = database.get(TemplateMatch, item_id)
    if (
        item is None
        or profile is None
        or summary is None
        or summary.requires_hermes
        or summary.match_type != "exact"
        or item.status != "imported"
    ):
        raise ValueError("upper-layer seeds require one imported exact-match workbook")
    workbook_profile = load_workbook_profile(profile)
    matches = list(
        database.scalars(
            select(RegionTemplateMatch)
            .where(RegionTemplateMatch.item_id == item_id)
            .order_by(
                RegionTemplateMatch.sheet_id,
                RegionTemplateMatch.region_id,
            )
        )
    )
    region_versions = {
        (version.region_template_id, version.version): version
        for version in database.scalars(
            select(RegionTemplateVersion).where(
                RegionTemplateVersion.status == TemplateStatus.PUBLISHED
            )
        )
    }
    by_sheet: dict[str, list[RegionTemplateMatch]] = {}
    for match in matches:
        if (
            match.region_template_id is None
            or match.region_template_version is None
            or match.match_type != "exact"
        ):
            raise ValueError("every source Region must resolve exactly")
        by_sheet.setdefault(match.sheet_id, []).append(match)

    composition_by_sheet: dict[str, SheetComposition] = {}
    compositions_created = 0
    for sheet in workbook_profile.sheets:
        sheet_id = sheet.id
        sheet_matches = by_sheet.get(sheet_id, [])
        if not sheet_matches:
            continue
        signature = [
            {
                "region_template_id": str(match.region_template_id),
                "region_template_version": match.region_template_version,
            }
            for match in sheet_matches
        ]
        fingerprint = _catalog_fingerprint(signature)
        code = f"sheet.verified.{fingerprint[:20]}"
        composition = database.scalar(select(SheetComposition).where(SheetComposition.code == code))
        if composition is None:
            composition = SheetComposition(
                code=code,
                published_version=1,
            )
            version = SheetCompositionVersion(
                version=1,
                name=f"已验证 Sheet 组合 {fingerprint[:8]}",
                status=TemplateStatus.PUBLISHED,
                composition_fingerprint=fingerprint,
                source="bootstrap",
                source_metadata={
                    "verified_source_item_id": str(item_id),
                    "verified_source_sha256": item.source_sha256,
                },
            )
            for ordinal, match in enumerate(sheet_matches):
                if match.region_template_id is None or match.region_template_version is None:
                    raise ValueError("verified Region match lost its template reference")
                region_version = region_versions[
                    (
                        match.region_template_id,
                        match.region_template_version,
                    )
                ]
                version.region_slots.append(
                    SheetCompositionRegionSlot(
                        slot_key=f"region_{ordinal + 1}",
                        region_template_id=match.region_template_id,
                        region_template_version=(match.region_template_version),
                        ordinal=ordinal,
                        materialize=bool(
                            region_version.layout_rules.get(
                                "materialize",
                                True,
                            )
                        ),
                    )
                )
            composition.versions.append(version)
            database.add(composition)
            database.flush()
            compositions_created += 1
        composition_by_sheet[sheet_id] = composition

    route_signature = [
        {
            "sheet_composition_id": str(composition_by_sheet[sheet.id].id),
            "sheet_composition_version": 1,
        }
        for sheet in workbook_profile.sheets
        if sheet.id in composition_by_sheet
    ]
    route_fingerprint = _catalog_fingerprint(route_signature)
    route_code = f"workbook.verified.{route_fingerprint[:20]}"
    route = database.scalar(select(WorkbookRoute).where(WorkbookRoute.code == route_code))
    route_created = 0
    if route is None:
        route = WorkbookRoute(
            code=route_code,
            published_version=1,
        )
        route_version = WorkbookRouteVersion(
            version=1,
            name=f"已验证工作簿路由 {route_fingerprint[:8]}",
            status=TemplateStatus.PUBLISHED,
            route_fingerprint=route_fingerprint,
            source="bootstrap",
            source_metadata={
                "verified_source_item_id": str(item_id),
                "verified_source_sha256": item.source_sha256,
            },
        )
        for ordinal, sheet in enumerate(workbook_profile.sheets):
            composition = composition_by_sheet.get(sheet.id)
            if composition is None:
                continue
            route_version.sheet_slots.append(
                WorkbookRouteSheetSlot(
                    slot_key=f"sheet_{ordinal + 1}",
                    sheet_composition_id=composition.id,
                    sheet_composition_version=1,
                    ordinal=ordinal,
                )
            )
        route.versions.append(route_version)
        database.add(route)
        route_created = 1
    database.flush()
    return {
        "sheet_compositions_created": compositions_created,
        "workbook_routes_created": route_created,
    }
