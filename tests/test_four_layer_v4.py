import hashlib

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    RegionTemplate,
    RegionTemplateReviewEvent,
    RegionTemplateVersion,
    SheetComposition,
    SheetCompositionReviewEvent,
    SheetCompositionVersion,
    TemplateStatus,
    WorkbookRoute,
    WorkbookRouteReviewEvent,
    WorkbookRouteVersion,
)
from village_insight.templates.four_layer_publish import (
    _deprecate_superseded_validated_corpus,
    stage_published_package,
)
from village_insight.templates.four_layer_seeds import validate_package
from village_insight.templates.four_layer_v4 import _approved_region_projections
from village_insight.templates.sources import VALIDATED_CORPUS_SOURCE


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> list[dict[str, object]]:
        return self._rows


class _Database:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def execute(self, _statement: object) -> _Rows:
        return _Rows(self._rows)


def test_approved_region_projections_keep_materialized_raw_only_region() -> None:
    region_id = "source:sheet:0:region:1"
    projections = _approved_region_projections(
        _Database(
            [
                {
                    "source_sha256": "a" * 64,
                    "layout_plan": {
                        "decisions": [
                            {
                                "region_candidate_id": region_id,
                                "materialize": True,
                                "data_start_row": 2,
                                "data_end_row": 4,
                            }
                        ]
                    },
                    "field_mappings": [],
                }
            ]
        )
    )

    assert projections == [
        {
            "source_sha256": "a" * 64,
            "region_id": region_id,
            "decision": {
                "region_candidate_id": region_id,
                "materialize": True,
                "data_start_row": 2,
                "data_end_row": 4,
            },
            "mappings": [],
        }
    ]


def test_approved_region_projections_still_exclude_nonmaterialized_region() -> None:
    projections = _approved_region_projections(
        _Database(
            [
                {
                    "source_sha256": "b" * 64,
                    "layout_plan": {
                        "decisions": [
                            {
                                "region_candidate_id": "source:sheet:0:region:1",
                                "materialize": False,
                            }
                        ]
                    },
                    "field_mappings": [],
                }
            ]
        )
    )

    assert projections == []


def test_new_corpus_package_deprecates_old_package_components_only() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    fingerprint = hashlib.sha256(b"old").hexdigest()
    with Session(engine) as database:
        region = RegionTemplate(code="old.region", published_version=1)
        region.versions.append(
            RegionTemplateVersion(
                version=1,
                name="旧区域",
                status=TemplateStatus.PUBLISHED,
                domain="test",
                record_type="row",
                record_grain="one_row_per_record",
                region_kind="table",
                region_fingerprint=fingerprint,
                header_signature=[],
                layout_rules={},
                field_bindings=[],
                source=VALIDATED_CORPUS_SOURCE,
            )
        )
        sheet = SheetComposition(code="old.sheet", published_version=1)
        sheet.versions.append(
            SheetCompositionVersion(
                version=1,
                name="旧组合",
                status=TemplateStatus.PUBLISHED,
                composition_fingerprint=fingerprint,
                source=VALIDATED_CORPUS_SOURCE,
            )
        )
        route = WorkbookRoute(code="old.route", published_version=1)
        route.versions.append(
            WorkbookRouteVersion(
                version=1,
                name="旧路线",
                status=TemplateStatus.PUBLISHED,
                route_fingerprint=fingerprint,
                source=VALIDATED_CORPUS_SOURCE,
            )
        )
        manual = WorkbookRoute(code="manual.route", published_version=1)
        manual.versions.append(
            WorkbookRouteVersion(
                version=1,
                name="人工路线",
                status=TemplateStatus.PUBLISHED,
                route_fingerprint=hashlib.sha256(b"manual").hexdigest(),
                source="manual",
            )
        )
        database.add_all([region, sheet, route, manual])
        database.flush()

        counts = _deprecate_superseded_validated_corpus(
            database,
            region_codes=set(),
            sheet_codes=set(),
            route_codes=set(),
            generation="test-generation",
        )

        assert counts == {
            "regions_deprecated": 1,
            "sheets_deprecated": 1,
            "routes_deprecated": 1,
        }
        assert region.published_version is None
        assert sheet.published_version is None
        assert route.published_version is None
        assert manual.published_version == 1
        assert database.query(RegionTemplateReviewEvent).count() == 1
        assert database.query(SheetCompositionReviewEvent).count() == 1
        assert database.query(WorkbookRouteReviewEvent).count() == 1
    Base.metadata.drop_all(engine)


def test_new_generation_versions_reused_region_evidence_then_is_idempotent() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    fingerprint = hashlib.sha256(b"stable-region").hexdigest()
    with Session(engine) as database:
        region = RegionTemplate(code="stable.region", published_version=1)
        region.versions.append(
            RegionTemplateVersion(
                version=1,
                name="稳定区域",
                status=TemplateStatus.PUBLISHED,
                domain="test",
                record_type="row",
                record_grain="one_row_per_record",
                region_kind="table",
                region_fingerprint=fingerprint,
                header_signature=[],
                layout_rules={"data_start_offset_from_region_start": 1},
                field_bindings=[],
                source=VALIDATED_CORPUS_SOURCE,
                source_metadata={"generation_sha256": "old-generation"},
            )
        )
        database.add(region)
        database.flush()
        package = {
            "generation_sha256": "new-generation",
            "semantic_fields": [],
            "region_templates": [
                {
                    "code": "stable.region",
                    "name": "稳定区域",
                    "domain": "test",
                    "record_type": "row",
                    "record_grain": "one_row_per_record",
                    "region_kind": "table",
                    "region_fingerprint": fingerprint,
                    "header_signature": [],
                    "layout_rules": {"data_start_offset_from_region_start": 1},
                    "field_bindings": [],
                    "evidence": [{"source_sha256": "a" * 64, "region_id": "r1"}],
                }
            ],
            "sheet_compositions": [],
            "workbook_routes": [],
        }

        first = stage_published_package(database, package=package)
        second = stage_published_package(database, package=package)

        assert first["regions_created"] == 1
        assert first["regions_reused"] == 0
        assert second["regions_created"] == 0
        assert second["regions_reused"] == 1
        assert len(region.versions) == 2
        assert region.versions[-1].source_metadata["generation_sha256"] == "new-generation"

    Base.metadata.drop_all(engine)


def test_package_validation_blocks_invalid_region_kind() -> None:
    package = {
        "contract_version": "four-layer-template-seed/v4",
        "generator_version": "test",
        "generation_sha256": "test",
        "semantic_fields": [],
        "region_templates": [
            {
                "code": "invalid.region",
                "region_kind": "unknown",
                "field_bindings": [
                    {
                        "semantic_field_code": "missing.field",
                        "source_selector": {
                            "kind": "physical_column",
                            "column_offset": -1,
                        },
                    }
                ],
            }
        ],
        "sheet_compositions": [],
        "workbook_routes": [],
        "holdout_validation": {"field_reuse_basis_points": 10_000},
        "summary": {"status_counts": {}, "unresolved_region_count": 0},
    }

    validation = validate_package(package)

    assert validation["invalid_region_kind_codes"] == ["invalid.region"]
    assert validation["invalid_source_selector_codes"] == ["invalid.region"]
    assert "invalid_region_kinds" in validation["publication_blockers"]
    assert "invalid_source_selectors" in validation["publication_blockers"]
    assert validation["safe_to_publish"] is False
