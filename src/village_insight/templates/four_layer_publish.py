from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from village_insight.db.models import (
    RegionTemplate,
    RegionTemplateReviewEvent,
    RegionTemplateVersion,
    SemanticField,
    SemanticFieldReviewEvent,
    SemanticFieldVersion,
    SheetComposition,
    SheetCompositionRegionSlot,
    SheetCompositionReviewEvent,
    SheetCompositionVersion,
    TemplateStatus,
    WorkbookRoute,
    WorkbookRouteReviewEvent,
    WorkbookRouteSheetSlot,
    WorkbookRouteVersion,
)
from village_insight.db.session import get_session_factory
from village_insight.templates.field_variants import build_field_variant

ACTOR = "system:codex-full-corpus"


def _next_version(versions: list[Any]) -> int:
    return max((int(version.version) for version in versions), default=0) + 1


def stage_published_package(
    database: Session,
    *,
    package: dict[str, Any],
) -> dict[str, int]:
    generation = str(package["generation_sha256"])
    fields_by_code: dict[str, tuple[SemanticField, SemanticFieldVersion]] = {}
    counts: dict[str, int] = {
        "fields_created": 0,
        "fields_reused": 0,
        "regions_created": 0,
        "regions_reused": 0,
        "sheets_created": 0,
        "sheets_reused": 0,
        "routes_created": 0,
        "routes_reused": 0,
    }
    for definition in package["semantic_fields"]:
        code = str(definition["code"])
        field = database.scalar(select(SemanticField).where(SemanticField.code == code))
        current_field_version = (
            next(
                (
                    version
                    for version in field.versions
                    if version.version == field.published_version
                ),
                None,
            )
            if field is not None
            else None
        )
        if field is not None and current_field_version is not None:
            fields_by_code[code] = (field, current_field_version)
            counts["fields_reused"] += 1
            continue
        if field is None:
            field = SemanticField(code=code)
            database.add(field)
        field_version = SemanticFieldVersion(
            version=_next_version(field.versions),
            name=str(definition["name"]),
            description=str(definition.get("description") or ""),
            layer=str(definition.get("layer") or "domain"),
            data_type=str(definition.get("data_type") or "text"),
            unit_dimension=definition.get("unit_dimension"),
            aliases=[str(alias) for alias in definition.get("aliases", [])],
            validators=[],
            status=TemplateStatus.PUBLISHED,
        )
        variant_keys: set[str] = set()
        variant_values = [
            {
                "kind": "alias",
                "alias": alias,
                "source": "codex",
                "confidence_basis_points": 10_000,
                "evidence": {"generation_sha256": generation},
            }
            for alias in [field_version.name, *field_version.aliases]
        ]
        variant_values.extend(
            {
                "kind": "header_path",
                "header_path": [str(part) for part in header_path],
                "source": "codex",
                "confidence_basis_points": 10_000,
                "evidence": {"generation_sha256": generation},
            }
            for header_path in definition.get("header_paths", [])
        )
        for values in variant_values:
            variant = build_field_variant(values)
            if variant.variant_key in variant_keys:
                continue
            variant_keys.add(variant.variant_key)
            field_version.variants.append(variant)
        field.versions.append(field_version)
        field.published_version = field_version.version
        database.add(
            SemanticFieldReviewEvent(
                field_version=field_version,
                action="codex_bulk_publish",
                from_status=TemplateStatus.DRAFT,
                to_status=TemplateStatus.PUBLISHED,
                actor=ACTOR,
                actor_type="system",
                comment=f"全量真实文件四层模板发布 {generation}",
            )
        )
        database.flush()
        fields_by_code[code] = (field, field_version)
        counts["fields_created"] += 1

    regions_by_code: dict[str, tuple[RegionTemplate, RegionTemplateVersion]] = {}
    for definition in package["region_templates"]:
        code = str(definition["code"])
        template = database.scalar(
            select(RegionTemplate).where(RegionTemplate.code == code)
        )
        current_region_version = (
            next(
                (
                    version
                    for version in template.versions
                    if version.version == template.published_version
                    and version.region_fingerprint
                    == definition["region_fingerprint"]
                ),
                None,
            )
            if template is not None
            else None
        )
        if template is not None and current_region_version is not None:
            regions_by_code[code] = (template, current_region_version)
            counts["regions_reused"] += 1
            continue
        if template is None:
            template = RegionTemplate(code=code)
            database.add(template)
        region_version = RegionTemplateVersion(
            version=_next_version(template.versions),
            name=str(definition["name"]),
            description="Codex 基于全量真实文件确认的业务表模板",
            status=TemplateStatus.PUBLISHED,
            domain=str(definition["domain"]),
            record_type=str(definition["record_type"]),
            record_grain=str(definition["record_grain"]),
            region_kind=str(definition["region_kind"]),
            region_fingerprint=str(definition["region_fingerprint"]),
            header_signature=definition["header_signature"],
            layout_rules=definition["layout_rules"],
            field_bindings=[
                {
                    **binding,
                    "semantic_field_version": fields_by_code[
                        str(binding["semantic_field_code"])
                    ][1].version,
                }
                for binding in definition["field_bindings"]
            ],
            identity_policy={},
            quality_rules=[],
            source="codex",
            source_metadata={
                "generation_sha256": generation,
                "evidence": definition["evidence"],
                "header_variants": definition.get("header_variants", []),
            },
        )
        for old_region_version in template.versions:
            if old_region_version.status == TemplateStatus.PUBLISHED:
                old_region_version.status = TemplateStatus.DEPRECATED
        template.versions.append(region_version)
        template.published_version = region_version.version
        database.add(
            RegionTemplateReviewEvent(
                region_template_version=region_version,
                action="codex_bulk_publish",
                from_status=TemplateStatus.DRAFT,
                to_status=TemplateStatus.PUBLISHED,
                actor=ACTOR,
                actor_type="system",
                comment=f"全量真实文件四层模板发布 {generation}",
            )
        )
        database.flush()
        regions_by_code[code] = (template, region_version)
        counts["regions_created"] += 1

    sheets_by_code: dict[str, tuple[SheetComposition, SheetCompositionVersion]] = {}
    for definition in package["sheet_compositions"]:
        code = str(definition["code"])
        composition = database.scalar(
            select(SheetComposition).where(SheetComposition.code == code)
        )
        current_sheet_version = (
            next(
                (
                    version
                    for version in composition.versions
                    if version.version == composition.published_version
                    and version.composition_fingerprint
                    == definition["composition_fingerprint"]
                ),
                None,
            )
            if composition is not None
            else None
        )
        if composition is not None and current_sheet_version is not None:
            sheets_by_code[code] = (composition, current_sheet_version)
            counts["sheets_reused"] += 1
            continue
        if composition is None:
            composition = SheetComposition(code=code)
            database.add(composition)
        sheet_version = SheetCompositionVersion(
            version=_next_version(composition.versions),
            name=str(definition["name"]),
            description="真实文件中的 Sheet 与业务表组合",
            status=TemplateStatus.PUBLISHED,
            composition_fingerprint=str(definition["composition_fingerprint"]),
            matching_rules={},
            source="codex",
            source_metadata={"generation_sha256": generation},
        )
        for slot in definition["region_slots"]:
            region, region_version = regions_by_code[
                str(slot["region_template_code"])
            ]
            sheet_version.region_slots.append(
                SheetCompositionRegionSlot(
                    slot_key=str(slot["slot_key"]),
                    region_template_id=region.id,
                    region_template_version=region_version.version,
                    ordinal=int(slot["ordinal"]),
                    required=bool(slot["required"]),
                    cardinality=str(slot["cardinality"]),
                    materialize=bool(slot["materialize"]),
                    match_hints={},
                )
            )
        for old_sheet_version in composition.versions:
            if old_sheet_version.status == TemplateStatus.PUBLISHED:
                old_sheet_version.status = TemplateStatus.DEPRECATED
        composition.versions.append(sheet_version)
        composition.published_version = sheet_version.version
        database.add(
            SheetCompositionReviewEvent(
                sheet_composition_version=sheet_version,
                action="codex_bulk_publish",
                from_status=TemplateStatus.DRAFT,
                to_status=TemplateStatus.PUBLISHED,
                actor=ACTOR,
                actor_type="system",
                comment=f"全量真实文件四层模板发布 {generation}",
            )
        )
        database.flush()
        sheets_by_code[code] = (composition, sheet_version)
        counts["sheets_created"] += 1

    for definition in package["workbook_routes"]:
        code = str(definition["code"])
        route = database.scalar(select(WorkbookRoute).where(WorkbookRoute.code == code))
        current_route_version = (
            next(
                (
                    version
                    for version in route.versions
                    if version.version == route.published_version
                    and version.route_fingerprint == definition["route_fingerprint"]
                    and version.source_metadata.get("generation_sha256")
                    == generation
                ),
                None,
            )
            if route is not None
            else None
        )
        if current_route_version is not None:
            counts["routes_reused"] += 1
            continue
        if route is None:
            route = WorkbookRoute(code=code)
            database.add(route)
        route_version = WorkbookRouteVersion(
            version=_next_version(route.versions),
            name=str(definition["name"]),
            description="真实文件的多 Sheet 快速路由",
            status=TemplateStatus.PUBLISHED,
            route_fingerprint=str(definition["route_fingerprint"]),
            matching_rules={},
            source="codex",
            source_metadata={
                "generation_sha256": generation,
                "members": definition["members"],
                "ignored_regions": definition.get("ignored_regions", []),
            },
        )
        for slot in definition["sheet_slots"]:
            composition, composition_version = sheets_by_code[
                str(slot["sheet_composition_code"])
            ]
            route_version.sheet_slots.append(
                WorkbookRouteSheetSlot(
                    slot_key=str(slot["slot_key"]),
                    sheet_composition_id=composition.id,
                    sheet_composition_version=composition_version.version,
                    ordinal=int(slot["ordinal"]),
                    required=bool(slot["required"]),
                    cardinality=str(slot["cardinality"]),
                    materialize=bool(slot["materialize"]),
                    match_hints={},
                )
            )
        for old_route_version in route.versions:
            if old_route_version.status == TemplateStatus.PUBLISHED:
                old_route_version.status = TemplateStatus.DEPRECATED
        route.versions.append(route_version)
        route.published_version = route_version.version
        database.add(
            WorkbookRouteReviewEvent(
                workbook_route_version=route_version,
                action="codex_bulk_publish",
                from_status=TemplateStatus.DRAFT,
                to_status=TemplateStatus.PUBLISHED,
                actor=ACTOR,
                actor_type="system",
                comment=f"全量真实文件四层模板发布 {generation}",
            )
        )
        database.flush()
        counts["routes_created"] += 1
    return counts


def read_publishable_package(directory: Path) -> dict[str, Any]:
    validation = json.loads(
        (directory / "validation-report.json").read_text(encoding="utf-8")
    )
    if not validation.get("safe_to_publish"):
        raise ValueError(
            "four-layer package is not safe to publish: "
            + ", ".join(validation.get("publication_blockers", []))
        )
    coverage = json.loads(
        (directory / "coverage-manifest.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (directory / "generation-manifest.json").read_text(encoding="utf-8")
    )
    return {
        **manifest,
        "semantic_fields": json.loads(
            (directory / "semantic-fields.json").read_text(encoding="utf-8")
        ),
        "region_templates": json.loads(
            (directory / "region-templates.json").read_text(encoding="utf-8")
        ),
        "sheet_compositions": json.loads(
            (directory / "sheet-compositions.json").read_text(encoding="utf-8")
        ),
        "workbook_routes": json.loads(
            (directory / "workbook-routes.json").read_text(encoding="utf-8")
        ),
        "coverage": coverage["coverage"],
        "holdout_validation": coverage["holdout_validation"],
    }


def publish_directory(
    directory: Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    package = read_publishable_package(directory)
    with get_session_factory()() as database:
        try:
            if database.get_bind().dialect.name == "postgresql":
                database.execute(
                    text(
                        "SELECT pg_advisory_xact_lock("
                        "hashtext('village_insight_four_layer_publish'))"
                    )
                )
            counts = stage_published_package(database, package=package)
            if dry_run:
                database.rollback()
            else:
                database.commit()
        except Exception:
            database.rollback()
            raise
    return {
        "generation_sha256": package["generation_sha256"],
        "dry_run": dry_run,
        **counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    result = publish_directory(
        arguments.directory,
        dry_run=arguments.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
