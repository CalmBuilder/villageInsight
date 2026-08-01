from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    RegionTemplate,
    RegionTemplateReviewEvent,
    RegionTemplateVersion,
    SemanticField,
    SemanticFieldReviewEvent,
    SemanticFieldVariant,
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
from village_insight.templates.catalog_bundle import (
    apply_catalog_bundle,
    create_catalog_bundle,
    read_catalog_bundle,
    validate_catalog_bundle,
    write_catalog_bundle,
)
from village_insight.templates.catalog_snapshot import restore_snapshot


def _seed(database: Session) -> None:
    field = SemanticField(code="person.name", published_version=1)
    field_version = SemanticFieldVersion(
        version=1,
        name="姓名",
        layer="base",
        data_type="text",
        status=TemplateStatus.PUBLISHED,
        source="validated_corpus",
    )
    field_version.variants.append(
        SemanticFieldVariant(
            variant_key="name",
            kind="alias",
            normalized_value="姓名",
            source="validated_corpus",
            confidence_basis_points=10000,
        )
    )
    field_version.review_events.append(
        SemanticFieldReviewEvent(
            action="publish",
            from_status=TemplateStatus.USER_CONFIRMED,
            to_status=TemplateStatus.PUBLISHED,
            actor="recovery-test",
        )
    )
    field.versions.append(field_version)

    region = RegionTemplate(code="person.roster", published_version=1)
    region_version = RegionTemplateVersion(
        version=1,
        name="人员表",
        domain="population",
        record_type="person",
        record_grain="one_row_per_person",
        region_kind="table",
        region_fingerprint="3" * 64,
        field_bindings=[{"semantic_field_code": "person.name"}],
        status=TemplateStatus.PUBLISHED,
        source="validated_corpus",
    )
    region_version.review_events.append(
        RegionTemplateReviewEvent(
            action="publish",
            from_status=TemplateStatus.USER_CONFIRMED,
            to_status=TemplateStatus.PUBLISHED,
            actor="recovery-test",
        )
    )
    region.versions.append(region_version)

    sheet = SheetComposition(code="person.sheet", published_version=1)
    sheet_version = SheetCompositionVersion(
        version=1,
        name="人员页",
        composition_fingerprint="4" * 64,
        status=TemplateStatus.PUBLISHED,
        source="validated_corpus",
    )
    sheet.versions.append(sheet_version)

    route = WorkbookRoute(code="person.workbook", published_version=1)
    route_version = WorkbookRouteVersion(
        version=1,
        name="人员文件",
        route_fingerprint="5" * 64,
        status=TemplateStatus.PUBLISHED,
        source="validated_corpus",
    )
    route.versions.append(route_version)
    database.add_all([field, region, sheet, route])
    database.flush()

    sheet_version.region_slots.append(
        SheetCompositionRegionSlot(
            slot_key="people",
            region_template_id=region.id,
            region_template_version=1,
            ordinal=0,
        )
    )
    sheet_version.review_events.append(
        SheetCompositionReviewEvent(
            action="publish",
            from_status=TemplateStatus.USER_CONFIRMED,
            to_status=TemplateStatus.PUBLISHED,
            actor="recovery-test",
        )
    )
    route_version.sheet_slots.append(
        WorkbookRouteSheetSlot(
            slot_key="people",
            sheet_composition_id=sheet.id,
            sheet_composition_version=1,
            ordinal=0,
        )
    )
    route_version.review_events.append(
        WorkbookRouteReviewEvent(
            action="publish",
            from_status=TemplateStatus.USER_CONFIRMED,
            to_status=TemplateStatus.PUBLISHED,
            actor="recovery-test",
        )
    )
    database.flush()


def test_complete_catalog_bundle_restores_an_empty_database(tmp_path: Path) -> None:
    source_engine = create_engine("sqlite://")
    Base.metadata.create_all(source_engine)
    with Session(source_engine) as source:
        _seed(source)
        bundle = create_catalog_bundle(source)
    bundle_path = tmp_path / "catalog.json.gz"
    write_catalog_bundle(bundle_path, bundle)
    loaded = read_catalog_bundle(bundle_path)
    validate_catalog_bundle(loaded)

    target_engine = create_engine("sqlite://")
    Base.metadata.create_all(target_engine)
    with target_engine.begin() as connection:
        connection.execute(text("CREATE TABLE business_sentinel (value INTEGER)"))
        connection.execute(text("INSERT INTO business_sentinel VALUES (7)"))
    with Session(target_engine) as target:
        result = apply_catalog_bundle(target, bundle=loaded)
        restore_snapshot(target, snapshot=loaded["catalog_snapshot"])
        target.commit()
        assert result["semantic_fields"]["inserted"] == 1
        assert target.query(SemanticField).count() == 1
        assert target.query(SemanticFieldVariant).count() == 1
        assert target.query(RegionTemplate).count() == 1
        assert target.query(SheetCompositionRegionSlot).count() == 1
        assert target.query(WorkbookRouteSheetSlot).count() == 1
        assert target.scalar(text("SELECT value FROM business_sentinel")) == 7
    Base.metadata.drop_all(source_engine)
    Base.metadata.drop_all(target_engine)


def test_catalog_bundle_repairs_corruption_and_removes_extra_behavior_rows() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        _seed(database)
        bundle = create_catalog_bundle(database)
        field = database.query(SemanticField).one()
        version = database.query(SemanticFieldVersion).one()
        version.name = "损坏名称"
        field.published_version = None
        original_variant = database.query(SemanticFieldVariant).one()
        database.delete(original_variant)
        database.flush()
        version.variants.append(
            SemanticFieldVariant(
                variant_key="corrupt-extra",
                kind="alias",
                normalized_value="错误",
                source="manual",
                confidence_basis_points=1,
            )
        )
        database.flush()

        result = apply_catalog_bundle(database, bundle=bundle)
        restore_snapshot(database, snapshot=bundle["catalog_snapshot"])

        database.expire_all()
        assert database.query(SemanticFieldVersion).one().name == "姓名"
        assert database.query(SemanticField).one().published_version == 1
        assert database.query(SemanticFieldVariant).one().variant_key == "name"
        assert result["semantic_field_versions"]["updated"] == 1
        assert result["semantic_field_variants"]["inserted"] == 1
        assert result["semantic_field_variants"]["removed_extra"] == 1
    Base.metadata.drop_all(engine)
