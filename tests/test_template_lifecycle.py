import hashlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.api.routes.catalog import get_field_details
from village_insight.db.base import Base
from village_insight.db.models import (
    DocumentTemplate,
    RegionTemplate,
    RegionTemplateReviewEvent,
    RegionTemplateVersion,
    SemanticField,
    SemanticFieldVariant,
    SemanticFieldVersion,
    SheetComposition,
    SheetCompositionRegionSlot,
    SheetCompositionVersion,
    TemplateReviewEvent,
    TemplateStatus,
    TemplateVersion,
    WorkbookRoute,
    WorkbookRouteSheetSlot,
    WorkbookRouteVersion,
)
from village_insight.templates.contracts import TemplateDefinition
from village_insight.templates.lifecycle import (
    LifecycleError,
    publish_field,
    publish_region_template,
    publish_sheet_composition,
    publish_workbook_route,
    transition_template,
)


def template_definition() -> TemplateDefinition:
    return TemplateDefinition(
        domain="population",
        region_kind="table",
        record_type="person",
        record_grain="one_row_per_person",
        field_bindings=[
            {
                "source_column_id": "sheet:0:region:0:column:1",
                "header_path": ["姓名"],
                "semantic_field_code": "person.name",
                "semantic_field_version": 1,
            }
        ],
    )


def test_template_lifecycle_requires_published_fields_and_records_events() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        field = SemanticField(code="person.name")
        field_version = SemanticFieldVersion(
            version=1,
            name="姓名",
            layer="base",
            data_type="text",
        )
        field.versions.append(field_version)
        template = DocumentTemplate(code="person_roster")
        version = TemplateVersion(
            version=1,
            name="人员名册",
            layout_fingerprint=hashlib.sha256(b"layout").hexdigest(),
            definition=template_definition().model_dump(mode="json"),
            source="manual",
        )
        template.versions.append(version)
        database.add_all([field, template])
        database.flush()

        transition_template(
            database,
            template=template,
            version=version,
            action="confirm",
            actor="user",
            comment="字段已核对",
        )
        transition_template(
            database,
            template=template,
            version=version,
            action="submit_review",
            actor="user",
            comment="提交审核",
        )
        with pytest.raises(LifecycleError, match="not published"):
            transition_template(
                database,
                template=template,
                version=version,
                action="approve",
                actor="admin",
                comment="",
            )

        publish_field(
            database,
            field=field,
            version=field_version,
            actor="admin",
            comment="基础字段",
        )
        transition_template(
            database,
            template=template,
            version=version,
            action="approve",
            actor="admin",
            comment="通过",
        )
        database.commit()

        assert version.status == TemplateStatus.PUBLISHED
        assert template.published_version == 1
        events = database.query(TemplateReviewEvent).order_by(TemplateReviewEvent.created_at)
        assert [event.action for event in events] == [
            "confirm",
            "submit_review",
            "approve",
        ]


def test_template_lifecycle_reject_requires_reason_and_blocks_invalid_jump() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        template = DocumentTemplate(code="synthetic")
        version = TemplateVersion(
            version=1,
            name="合成模板",
            layout_fingerprint=hashlib.sha256(b"synthetic").hexdigest(),
            definition=TemplateDefinition(
                domain="test",
                region_kind="table",
                record_type="row",
                record_grain="one_row_per_record",
            ).model_dump(mode="json"),
            source="manual",
        )
        template.versions.append(version)
        database.add(template)
        database.flush()

        with pytest.raises(LifecycleError, match="cannot approve"):
            transition_template(
                database,
                template=template,
                version=version,
                action="approve",
                actor="admin",
                comment="",
            )
        transition_template(
            database,
            template=template,
            version=version,
            action="confirm",
            actor="user",
            comment="",
        )
        transition_template(
            database,
            template=template,
            version=version,
            action="submit_review",
            actor="user",
            comment="",
        )
        with pytest.raises(LifecycleError, match="requires a comment"):
            transition_template(
                database,
                template=template,
                version=version,
                action="reject",
                actor="admin",
                comment="",
            )


def test_region_template_publishes_only_with_published_field_versions() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        field = SemanticField(code="person.name")
        field_version = SemanticFieldVersion(
            version=1,
            name="姓名",
            layer="base",
            data_type="text",
        )
        field_version.variants.append(
            SemanticFieldVariant(
                variant_key=hashlib.sha256(b"person-name").hexdigest(),
                kind="header_path",
                normalized_value="家庭成员姓名",
                alias=None,
                header_path=["家庭成员", "姓名"],
                parent_path=["家庭成员"],
                role=None,
                domain="population",
                record_type="person",
                observed_data_type="text",
                unit_dimension=None,
                source="codex",
                confidence_basis_points=9800,
                evidence={"source_count": 3},
            )
        )
        field.versions.append(field_version)
        template = RegionTemplate(code="population.person_roster")
        version = RegionTemplateVersion(
            version=1,
            name="人口明细",
            domain="population",
            record_type="person",
            record_grain="one_row_per_person",
            region_kind="table",
            region_fingerprint=hashlib.sha256(b"person-region").hexdigest(),
            header_signature=[["家庭成员", "姓名"]],
            field_bindings=[
                {
                    "source_column_id": "column:1",
                    "header_path": ["家庭成员", "姓名"],
                    "semantic_field_code": "person.name",
                    "semantic_field_version": 1,
                    "required": True,
                }
            ],
        )
        template.versions.append(version)
        database.add_all([field, template])
        database.flush()

        with pytest.raises(LifecycleError, match="not published"):
            publish_region_template(
                database,
                template=template,
                version=version,
                actor="admin",
                comment="",
            )

        publish_field(
            database,
            field=field,
            version=field_version,
            actor="admin",
            comment="字段和上下文已核验",
        )
        publish_region_template(
            database,
            template=template,
            version=version,
            actor="admin",
            comment="Region证据已核验",
        )
        database.commit()

        assert template.published_version == 1
        assert version.status == TemplateStatus.PUBLISHED
        assert field_version.variants[0].evidence == {"source_count": 3}
        assert database.query(RegionTemplateReviewEvent).one().action == "publish"


def test_field_details_include_versions_and_region_template_references() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        field = SemanticField(code="person.name", published_version=1)
        field.versions.extend(
            [
                SemanticFieldVersion(
                    version=1,
                    name="姓名",
                    description="人员姓名",
                    layer="base",
                    data_type="text",
                    status=TemplateStatus.PUBLISHED,
                    aliases=["成员姓名"],
                ),
                SemanticFieldVersion(
                    version=2,
                    name="姓名",
                    description="人员姓名",
                    layer="base",
                    data_type="text",
                    status=TemplateStatus.DRAFT,
                ),
            ]
        )
        template = RegionTemplate(code="population.person")
        template.versions.append(
            RegionTemplateVersion(
                version=1,
                name="人口明细",
                domain="population",
                record_type="person",
                record_grain="one_row_per_person",
                region_kind="table",
                region_fingerprint=hashlib.sha256(b"population-person").hexdigest(),
                field_bindings=[
                    {
                        "source_column_id": "column:1",
                        "header_path": ["姓名"],
                        "semantic_field_code": "person.name",
                        "semantic_field_version": 1,
                    }
                ],
            )
        )
        database.add_all([field, template])
        database.commit()

        detail = get_field_details(field.id, database)

        assert detail.field.code == "person.name"
        assert [version.version for version in detail.versions] == [2, 1]
        assert detail.versions[1].alias_count == 1
        assert len(detail.referenced_by) == 1
        assert detail.referenced_by[0].template_code == "population.person"
        assert detail.referenced_by[0].template_version == 1


def test_sheet_and_workbook_publish_validate_exact_dependency_versions() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        region = RegionTemplate(code="population.people", published_version=1)
        region.versions.append(
            RegionTemplateVersion(
                version=1,
                name="人口表",
                status=TemplateStatus.PUBLISHED,
                domain="population",
                record_type="person",
                record_grain="one_row_per_person",
                region_kind="table",
                region_fingerprint=hashlib.sha256(b"region").hexdigest(),
                header_signature=[["姓名"]],
            )
        )
        database.add(region)
        database.flush()
        composition = SheetComposition(code="population.sheet")
        composition_version = SheetCompositionVersion(
            version=1,
            name="人口Sheet",
            composition_fingerprint=hashlib.sha256(b"sheet").hexdigest(),
        )
        composition_version.region_slots.append(
            SheetCompositionRegionSlot(
                slot_key="people",
                region_template_id=region.id,
                region_template_version=2,
                ordinal=0,
                required=True,
                cardinality="one",
            )
        )
        composition.versions.append(composition_version)
        database.add(composition)
        database.flush()
        route = WorkbookRoute(code="population.workbook")
        route_version = WorkbookRouteVersion(
            version=1,
            name="人口工作簿",
            route_fingerprint=hashlib.sha256(b"workbook-route").hexdigest(),
        )
        route_version.sheet_slots.append(
            WorkbookRouteSheetSlot(
                slot_key="people_sheet",
                sheet_composition_id=composition.id,
                sheet_composition_version=1,
                ordinal=0,
                required=True,
                cardinality="one",
            )
        )
        route.versions.append(route_version)
        database.add(route)
        database.flush()

        with pytest.raises(LifecycleError, match="Region slot people"):
            publish_sheet_composition(
                database,
                composition=composition,
                version=composition_version,
                actor="admin",
                comment="",
            )
        composition_version.region_slots[0].region_template_version = 1
        publish_sheet_composition(
            database,
            composition=composition,
            version=composition_version,
            actor="admin",
            comment="依赖闭包通过",
        )
        publish_workbook_route(
            database,
            route=route,
            version=route_version,
            actor="admin",
            comment="依赖闭包通过",
        )

        assert composition.published_version == 1
        assert route.published_version == 1
