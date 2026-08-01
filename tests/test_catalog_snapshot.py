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
from village_insight.templates.catalog_snapshot import (
    create_snapshot,
    restore_snapshot,
)


def test_catalog_snapshot_restores_pointers_and_disables_new_objects() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        field = SemanticField(code="person.name", published_version=1)
        field.versions.append(
            SemanticFieldVersion(
                version=1,
                name="姓名",
                layer="base",
                data_type="text",
                status=TemplateStatus.PUBLISHED,
            )
        )
        region = RegionTemplate(code="region.people", published_version=1)
        region.versions.append(
            RegionTemplateVersion(
                version=1,
                name="人员表",
                status=TemplateStatus.PUBLISHED,
                domain="population",
                record_type="person",
                record_grain="one_row_per_person",
                region_kind="table",
                region_fingerprint="a" * 64,
                source="codex",
            )
        )
        sheet = SheetComposition(code="sheet.people", published_version=1)
        sheet.versions.append(
            SheetCompositionVersion(
                version=1,
                name="人员 Sheet",
                status=TemplateStatus.PUBLISHED,
                composition_fingerprint="b" * 64,
                source="codex",
            )
        )
        route = WorkbookRoute(code="workbook.people", published_version=1)
        route.versions.append(
            WorkbookRouteVersion(
                version=1,
                name="人员文件",
                status=TemplateStatus.PUBLISHED,
                route_fingerprint="c" * 64,
                source="codex",
            )
        )
        database.add_all([field, region, sheet, route])
        database.flush()
        snapshot = create_snapshot(database)
        assert snapshot["contract_version"] == "four-layer-catalog-snapshot/v2"
        assert snapshot["layers"]["semantic_fields"][0]["content_sha256"]

        field.published_version = None
        field.versions[0].status = TemplateStatus.DEPRECATED
        new_field = SemanticField(code="future.field", published_version=1)
        new_field.versions.append(
            SemanticFieldVersion(
                version=1,
                name="后续字段",
                layer="domain",
                data_type="text",
                status=TemplateStatus.PUBLISHED,
            )
        )
        database.add(new_field)
        database.flush()

        result = restore_snapshot(database, snapshot=snapshot)

        assert field.published_version == 1
        assert field.versions[0].status == TemplateStatus.PUBLISHED
        assert new_field.published_version is None
        assert new_field.versions[0].status == TemplateStatus.DEPRECATED
        assert result["semantic_fields"] == {
            "restored": 1,
            "disabled_post_snapshot": 1,
            "unchanged": 0,
        }
    Base.metadata.drop_all(engine)
