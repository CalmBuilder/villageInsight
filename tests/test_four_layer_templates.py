import hashlib

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    DocumentTemplate,
    RegionTemplate,
    SemanticField,
    SemanticFieldVariant,
    SemanticFieldVersion,
    TemplateRegionComponent,
    TemplateStatus,
    TemplateVersion,
)
from village_insight.templates.four_layer import backfill_four_layer_foundation


def test_four_layer_backfill_is_evidence_bound_and_idempotent() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        field = SemanticField(code="person.name", published_version=1)
        field.versions.append(
            SemanticFieldVersion(
                version=1,
                name="姓名",
                aliases=["人员姓名"],
                layer="base",
                data_type="text",
                status=TemplateStatus.PUBLISHED,
            )
        )
        template = DocumentTemplate(code="population.people", published_version=1)
        version = TemplateVersion(
            version=1,
            name="人口台账",
            status=TemplateStatus.PUBLISHED,
            layout_fingerprint=hashlib.sha256(b"workbook").hexdigest(),
            definition={
                "contract_version": "document-template/v1",
                "domain": "population",
                "region_kind": "table",
                "record_type": "person",
                "record_grain": "one_row_per_person",
                "field_bindings": [
                    {
                        "source_column_id": "column:1",
                        "header_path": ["家庭成员", "姓名"],
                        "semantic_field_code": "person.name",
                        "semantic_field_version": 1,
                        "role": "member",
                    }
                ],
            },
            source_metadata={
                "layout_projection_snapshot": {
                    "decisions": [
                        {
                            "data_start_offset_from_header_end": 1,
                            "data_end_gap_from_region_end": 0,
                            "excluded_row_offsets": [],
                        }
                    ]
                }
            },
        )
        template.versions.append(version)
        database.add_all([field, template])
        database.flush()
        database.add(
            TemplateRegionComponent(
                template_version_id=version.id,
                component_key="people",
                region_fingerprint=hashlib.sha256(b"region").hexdigest(),
                signature={
                    "kind": "table",
                    "headers": [["家庭成员", "姓名"]],
                },
                source_decision_index=0,
                field_binding_indexes=[0],
            )
        )
        database.flush()

        first = backfill_four_layer_foundation(database)
        second = backfill_four_layer_foundation(database)

        assert first == {
            "field_variants_created": 3,
            "region_templates_created": 1,
            "region_templates_existing": 0,
        }
        assert second == {
            "field_variants_created": 0,
            "region_templates_created": 0,
            "region_templates_existing": 1,
        }
        variants = database.query(SemanticFieldVariant).all()
        assert {variant.kind for variant in variants} == {
            "alias",
            "role_context",
        }
        region = database.query(RegionTemplate).one()
        assert region.published_version == 1
        assert region.versions[0].field_bindings[0]["semantic_field_code"] == (
            "person.name"
        )
        assert region.versions[0].source_metadata["legacy_component_id"]
    Base.metadata.drop_all(engine)
