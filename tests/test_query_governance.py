from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    MetricDefinition,
    QueryFactSetDefinition,
    SemanticField,
    SemanticFieldVersion,
    SemanticManifestDefinition,
)
from village_insight.query_governance import (
    QueryGovernanceError,
    contract_fingerprint,
    publish_fact_set,
    publish_metric,
    publish_semantic_manifest,
)


def _published_field(database: Session, code: str) -> None:
    field = SemanticField(code=code, published_version=1)
    field.versions.append(
        SemanticFieldVersion(
            version=1,
            name="人员编号",
            layer="domain",
            data_type="text",
            status="published",
        )
    )
    database.add(field)


def _fact_set(catalog_fingerprint: str) -> QueryFactSetDefinition:
    payload = {
        "code": "population.registry",
        "version": 1,
        "record_type": "population",
        "record_grain": "one_person",
        "provenance_rule": {
            "kind": "document_template",
            "id": str(uuid.uuid4()),
            "version": 1,
        },
        "identity_field_codes": ["person.id"],
        "catalog_fingerprint": catalog_fingerprint,
    }
    return QueryFactSetDefinition(
        **payload,
        name="人口台账",
        description="",
        aliases=[],
        status="draft",
        domain="population",
        dimension_field_codes=[],
        measure_definitions=[
            {
                "field_code": "person.id",
                "additivity": "additive",
            }
        ],
        time_dimensions=[],
        status_dimensions=[],
        sensitive_field_policies=[],
        conflict_policy={"mode": "reject"},
        definition_fingerprint=contract_fingerprint(payload),
    )


def test_fact_set_and_manifest_require_published_immutable_contracts() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    catalog_fingerprint = "a" * 64
    with Session(engine) as database:
        _published_field(database, "person.id")
        fact_set = _fact_set(catalog_fingerprint)
        database.add(fact_set)
        database.flush()

        publish_fact_set(database, fact_set)
        assert fact_set.status == "published"
        assert fact_set.published_at is not None
        with pytest.raises(QueryGovernanceError, match="only a draft"):
            publish_fact_set(database, fact_set)

        manifest_payload = {
            "code": "population.registry",
            "version": 1,
            "name": "人口台账语义清单",
            "description": "",
            "status": "draft",
            "fact_set_code": fact_set.code,
            "fact_set_version": fact_set.version,
            "root_entity": "person",
            "entities": [
                {"code": "person", "identity_fields": ["person.id"]}
            ],
            "dimensions": [],
            "measures": [
                {
                    "field_code": "person.id",
                    "allowed_aggregations": ["count"],
                    "additivity": "additive",
                }
            ],
            "relationships": [],
            "allowed_join_paths": [],
            "max_join_depth": 0,
            "deduplication_policy": {"mode": "identity"},
            "default_time_policy": {},
            "evidence_policy": {"lineage": "source_cell"},
            "catalog_fingerprint": catalog_fingerprint,
        }
        manifest = SemanticManifestDefinition(
            **manifest_payload,
            manifest_fingerprint=contract_fingerprint(manifest_payload),
        )
        database.add(manifest)
        database.flush()

        publish_semantic_manifest(database, manifest)
        assert manifest.status == "published"
        assert manifest.published_at is not None
        metric = MetricDefinition(
            code="population.total",
            version=1,
            status="draft",
            name="总人数",
            fact_set_code=fact_set.code,
            fact_set_version=fact_set.version,
            semantic_manifest_code=manifest.code,
            semantic_manifest_version=manifest.version,
            record_type=fact_set.record_type,
            record_grain=fact_set.record_grain,
            semantic_field_code="person.id",
            semantic_field_version=1,
            aggregation="count",
            additivity="additive",
            identity_field_codes=["person.id"],
            allowed_filter_fields=[],
            allowed_group_fields=[],
            forbidden_aggregation_dimensions=[],
            deduplication_policy={"mode": "identity"},
            status_filters=[],
            time_policy={},
            null_policy="exclude",
            conflict_policy="reject",
            evidence_policy={"lineage": "source_cell"},
            aliases=[],
        )
        database.add(metric)
        database.flush()
        publish_metric(database, metric)
        assert metric.status == "published"
        assert metric.published_at is not None


def test_fact_set_rejects_unpublished_identity_field() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        fact_set = _fact_set("b" * 64)
        database.add(fact_set)
        database.flush()

        with pytest.raises(QueryGovernanceError, match="unpublished fields"):
            publish_fact_set(database, fact_set)


def test_metric_rejects_additivity_mismatch() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        _published_field(database, "person.id")
        fact_set = _fact_set("c" * 64)
        fact_set.measure_definitions = [
            {
                "field_code": "person.id",
                "additivity": "non_additive",
            }
        ]
        database.add(fact_set)
        database.flush()
        publish_fact_set(database, fact_set)
        manifest_payload = {
            "code": "population.registry",
            "version": 1,
            "name": "人口台账语义清单",
            "description": "",
            "status": "draft",
            "fact_set_code": fact_set.code,
            "fact_set_version": fact_set.version,
            "root_entity": "person",
            "entities": [
                {"code": "person", "identity_fields": ["person.id"]}
            ],
            "dimensions": [],
            "measures": [
                {
                    "field_code": "person.id",
                    "allowed_aggregations": ["count"],
                    "additivity": "non_additive",
                }
            ],
            "relationships": [],
            "allowed_join_paths": [],
            "max_join_depth": 0,
            "deduplication_policy": {"mode": "identity"},
            "default_time_policy": {},
            "evidence_policy": {"lineage": "source_cell"},
            "catalog_fingerprint": fact_set.catalog_fingerprint,
        }
        manifest = SemanticManifestDefinition(
            **manifest_payload,
            manifest_fingerprint=contract_fingerprint(manifest_payload),
        )
        database.add(manifest)
        database.flush()
        publish_semantic_manifest(database, manifest)
        metric = MetricDefinition(
            code="population.total",
            version=1,
            status="draft",
            name="总人数",
            fact_set_code=fact_set.code,
            fact_set_version=fact_set.version,
            semantic_manifest_code=manifest.code,
            semantic_manifest_version=manifest.version,
            record_type=fact_set.record_type,
            record_grain=fact_set.record_grain,
            semantic_field_code="person.id",
            semantic_field_version=1,
            aggregation="count",
            additivity="additive",
            identity_field_codes=["person.id"],
            allowed_filter_fields=[],
            allowed_group_fields=[],
            forbidden_aggregation_dimensions=[],
            deduplication_policy={"mode": "identity"},
            status_filters=[],
            time_policy={},
            null_policy="exclude",
            conflict_policy="reject",
            evidence_policy={"lineage": "source_cell"},
            aliases=[],
        )
        database.add(metric)
        database.flush()

        with pytest.raises(QueryGovernanceError, match="additivity"):
            publish_metric(database, metric)
