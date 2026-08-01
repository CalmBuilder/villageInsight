from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
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
from village_insight.templates.baseline_sources import normalize_baseline_sources
from village_insight.templates.catalog_snapshot import create_snapshot

_DIRECTORY_SHA = "1" * 64
_DUMP_SHA = "2" * 64


def _seed(database: Session) -> None:
    field = SemanticField(code="person.name", published_version=1)
    field.versions.append(
        SemanticFieldVersion(
            version=1,
            name="姓名",
            layer="base",
            data_type="text",
            status=TemplateStatus.PUBLISHED,
            source="codex",
        )
    )
    region = RegionTemplate(code="person.roster", published_version=1)
    region.versions.append(
        RegionTemplateVersion(
            version=1,
            name="人员表",
            domain="population",
            record_type="person",
            record_grain="one_row_per_person",
            region_kind="table",
            region_fingerprint="3" * 64,
            status=TemplateStatus.PUBLISHED,
            source="bootstrap",
        )
    )
    sheet = SheetComposition(code="person.sheet", published_version=1)
    sheet.versions.append(
        SheetCompositionVersion(
            version=1,
            name="人员表页",
            composition_fingerprint="4" * 64,
            status=TemplateStatus.PUBLISHED,
            source="manual",
        )
    )
    route = WorkbookRoute(code="person.workbook", published_version=1)
    route.versions.append(
        WorkbookRouteVersion(
            version=1,
            name="人员文件",
            route_fingerprint="5" * 64,
            status=TemplateStatus.PUBLISHED,
            source="migration",
        )
    )
    database.add_all([field, region, sheet, route])
    database.flush()


def test_exact_restored_catalog_is_normalized_with_legacy_source_evidence() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        _seed(database)
        snapshot = create_snapshot(database)
        counts = normalize_baseline_sources(
            database,
            snapshot=snapshot,
            baseline_directory_sha256=_DIRECTORY_SHA,
            catalog_dump_sha256=_DUMP_SHA,
        )

        assert counts["semantic_field_versions"] == 1
        for model, legacy_source in (
            (SemanticFieldVersion, "codex"),
            (RegionTemplateVersion, "bootstrap"),
            (SheetCompositionVersion, "manual"),
            (WorkbookRouteVersion, "migration"),
        ):
            version = database.query(model).one()
            assert version.source == "validated_baseline"
            assert version.source_metadata["legacy_source"] == legacy_source
            assert version.source_metadata["parent_snapshot_sha256"] == snapshot[
                "snapshot_sha256"
            ]
    Base.metadata.drop_all(engine)


def test_normalization_rejects_current_database_with_extra_catalog_object() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        _seed(database)
        snapshot = create_snapshot(database)
        database.add(SemanticField(code="polluted.extra"))
        database.flush()

        try:
            normalize_baseline_sources(
                database,
                snapshot=snapshot,
                baseline_directory_sha256=_DIRECTORY_SHA,
                catalog_dump_sha256=_DUMP_SHA,
            )
        except ValueError as exc:
            assert "restored catalog is not exact" in str(exc)
        else:
            raise AssertionError("polluted catalog must not be normalized")
    Base.metadata.drop_all(engine)
