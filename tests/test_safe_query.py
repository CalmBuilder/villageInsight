from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    AdministrativeUnit,
    DatasetRecord,
    QueryFactSetDefinition,
    RecordIndexValue,
    SemanticField,
    SemanticFieldVersion,
    SemanticManifestDefinition,
    Tenant,
)
from village_insight.query_governance import (
    contract_fingerprint,
    publish_fact_set,
    publish_semantic_manifest,
)
from village_insight.question_catalog import build_question_catalog
from village_insight.question_scope import freeze_question_scope
from village_insight.questions import MetricQueryScope
from village_insight.safe_query import (
    SafeQueryError,
    SafeQueryPlan,
    execute_safe_query,
)


def _field(
    database: Session,
    *,
    code: str,
    name: str,
    data_type: str,
) -> None:
    field = SemanticField(code=code, published_version=1)
    field.versions.append(
        SemanticFieldVersion(
            version=1,
            name=name,
            layer="domain",
            data_type=data_type,
            status="published",
        )
    )
    database.add(field)


def _seed_governed_query(
    database: Session,
    *,
    sensitive_field_policies: list[dict] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, tuple[uuid.UUID, ...], dict]:
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    item_id = uuid.uuid4()
    template_id = uuid.uuid4()
    database.add(Tenant(id=tenant_id, name="测试租户"))
    database.add(
        AdministrativeUnit(
            id=unit_id,
            tenant_id=tenant_id,
            unit_type="village",
            name="青禾村",
        )
    )
    _field(database, code="person.id", name="人员编号", data_type="text")
    _field(database, code="person.gender", name="性别", data_type="text")
    _field(database, code="benefit.amount", name="补贴金额", data_type="decimal")
    database.flush()
    for source_row, person_id, gender, amount in (
        (2, "P-1", "女", Decimal("2.00")),
        (3, "P-2", "女", Decimal("3.00")),
        (4, "P-3", "男", Decimal("4.00")),
    ):
        record = DatasetRecord(
            tenant_id=tenant_id,
            administrative_unit_id=unit_id,
            ingestion_batch_id=uuid.uuid4(),
            approved_plan_id=uuid.uuid4(),
            item_id=item_id,
            template_id=template_id,
            template_version=1,
            record_type="benefit_person",
            sheet_id="sheet-1",
            region_id="region-1",
            source_row=source_row,
            quality_status="passed",
        )
        database.add(record)
        database.flush()
        database.add_all(
            [
                RecordIndexValue(
                    record_id=record.id,
                    semantic_field_code="person.id",
                    semantic_field_version=1,
                    role="",
                    data_type="text",
                    text_value=person_id,
                ),
                RecordIndexValue(
                    record_id=record.id,
                    semantic_field_code="person.gender",
                    semantic_field_version=1,
                    role="",
                    data_type="text",
                    text_value=gender,
                ),
                RecordIndexValue(
                    record_id=record.id,
                    semantic_field_code="benefit.amount",
                    semantic_field_version=1,
                    role="",
                    data_type="decimal",
                    decimal_value=amount,
                ),
            ]
        )
    database.commit()
    frozen = freeze_question_scope(
        database,
        tenant_id=tenant_id,
        administrative_unit_ids=(unit_id,),
        record_created_before=datetime.now(UTC),
    )
    discovery = build_question_catalog(database, frozen)
    fact_payload = {
        "code": "benefit.person",
        "version": 1,
        "record_type": "benefit_person",
        "record_grain": "one_person_benefit",
        "provenance_rule": {
            "kind": "document_template",
            "id": str(template_id),
            "version": 1,
        },
        "identity_field_codes": ["person.id"],
        "catalog_fingerprint": discovery.catalog_fingerprint,
    }
    fact_set = QueryFactSetDefinition(
        **fact_payload,
        name="人员补贴",
        description="",
        aliases=[],
        status="draft",
        domain="benefit",
        dimension_field_codes=["person.gender"],
        measure_definitions=[
            {
                "field_code": "benefit.amount",
                "additivity": "additive",
            },
            {
                "field_code": "person.id",
                "additivity": "non_additive",
            },
        ],
        time_dimensions=[],
        status_dimensions=[],
        sensitive_field_policies=sensitive_field_policies or [],
        conflict_policy={"mode": "reject"},
        definition_fingerprint=contract_fingerprint(fact_payload),
    )
    database.add(fact_set)
    database.flush()
    publish_fact_set(database, fact_set)
    manifest_payload = {
        "code": "benefit.person",
        "version": 1,
        "name": "人员补贴语义清单",
        "description": "",
        "status": "draft",
        "fact_set_code": fact_set.code,
        "fact_set_version": fact_set.version,
        "root_entity": "person",
        "entities": [
            {"code": "person", "identity_fields": ["person.id"]}
        ],
        "dimensions": [{"field_code": "person.gender"}],
        "measures": [
            {
                "field_code": "benefit.amount",
                "allowed_aggregations": ["sum", "avg", "min", "max"],
                "additivity": "additive",
            },
            {
                "field_code": "person.id",
                "allowed_aggregations": ["count"],
                "additivity": "non_additive",
                "allow_grouped_distinct_count": True,
            },
        ],
        "relationships": [],
        "allowed_join_paths": [],
        "max_join_depth": 0,
        "deduplication_policy": {"mode": "identity"},
        "default_time_policy": {},
        "evidence_policy": {"lineage": "source_cell"},
        "catalog_fingerprint": discovery.catalog_fingerprint,
    }
    manifest = SemanticManifestDefinition(
        **manifest_payload,
        manifest_fingerprint=contract_fingerprint(manifest_payload),
    )
    database.add(manifest)
    database.flush()
    publish_semantic_manifest(database, manifest)
    database.commit()
    governed = build_question_catalog(database, frozen)
    return (
        tenant_id,
        unit_id,
        frozen.source_item_ids,
        governed.model_dump(mode="json"),
    )


def test_safe_query_executes_count_list_aggregate_and_group() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        tenant_id, unit_id, source_ids, catalog = _seed_governed_query(database)
        scope = MetricQueryScope(
            tenant_id=tenant_id,
            administrative_unit_ids=frozenset({unit_id}),
            source_item_ids=frozenset(source_ids),
            source_scope_enforced=True,
            record_created_before=datetime.now(UTC),
        )
        common = {
            "fact_set_code": "benefit.person",
            "fact_set_version": 1,
            "record_type": "benefit_person",
        }
        count = execute_safe_query(
            database,
            SafeQueryPlan(
                operation="count",
                filters=[
                    {
                        "field_code": "person.gender",
                        "operator": "eq",
                        "value": "女",
                    }
                ],
                **common,
            ),
            catalog_snapshot=catalog,
            scope_snapshot_fingerprint="scope-1",
            scope=scope,
        )
        details = execute_safe_query(
            database,
            SafeQueryPlan(
                operation="list",
                select=["person.id", "person.gender"],
                order_by=[{"field_code": "person.id", "direction": "desc"}],
                limit=2,
                **common,
            ),
            catalog_snapshot=catalog,
            scope_snapshot_fingerprint="scope-1",
            scope=scope,
        )
        total = execute_safe_query(
            database,
            SafeQueryPlan(
                operation="aggregate",
                measure_field_code="benefit.amount",
                aggregation="sum",
                **common,
            ),
            catalog_snapshot=catalog,
            scope_snapshot_fingerprint="scope-1",
            scope=scope,
        )
        grouped = execute_safe_query(
            database,
            SafeQueryPlan(
                operation="group_by",
                group_by=["person.gender"],
                measure_field_code="benefit.amount",
                aggregation="sum",
                **common,
            ),
            catalog_snapshot=catalog,
            scope_snapshot_fingerprint="scope-1",
            scope=scope,
        )
        grouped_count = execute_safe_query(
            database,
            SafeQueryPlan(
                operation="group_by",
                group_by=["person.gender"],
                measure_field_code="person.id",
                aggregation="count",
                **common,
            ),
            catalog_snapshot=catalog,
            scope_snapshot_fingerprint="scope-1",
            scope=scope,
        )

    assert count.value == 2
    assert count.record_count == 2
    assert [row["person.id"] for row in details.rows] == ["P-3", "P-2"]
    assert total.value == Decimal("9.00")
    assert {
        (row["person.gender"], Decimal(str(row["value"])))
        for row in grouped.rows
    } == {("女", Decimal("5.00")), ("男", Decimal("4.00"))}
    assert {
        (row["person.gender"], row["value"])
        for row in grouped_count.rows
    } == {("女", 2), ("男", 1)}
    assert len(total.semantic_plan.semantic_plan_fingerprint) == 64


def test_safe_query_rejects_derived_fact_set() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        tenant_id, unit_id, source_ids, catalog = _seed_governed_query(database)
        derived = next(
            (
                item
                for item in catalog["fact_sets"]
                if item["governance_status"] == "derived"
            ),
            None,
        )
        if derived is None:
            derived = {
                "code": "factset.derived",
                "version": 1,
                "record_type": "benefit_person",
                "governance_status": "derived",
            }
            catalog["fact_sets"].append(derived)
        with pytest.raises(SafeQueryError, match="published fact set"):
            execute_safe_query(
                database,
                SafeQueryPlan(
                    operation="count",
                    fact_set_code=derived["code"],
                    fact_set_version=1,
                    record_type="benefit_person",
                ),
                catalog_snapshot=catalog,
                scope_snapshot_fingerprint="scope-1",
                scope=MetricQueryScope(
                    tenant_id=tenant_id,
                    administrative_unit_ids=frozenset({unit_id}),
                    source_item_ids=frozenset(source_ids),
                    source_scope_enforced=True,
                ),
            )


def test_sensitive_field_policy_is_opt_in_during_accuracy_baseline() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        tenant_id, unit_id, source_ids, catalog = _seed_governed_query(
            database,
            sensitive_field_policies=[
                {"field_code": "person.id", "queryable": False}
            ],
        )
        plan = SafeQueryPlan(
            operation="list",
            fact_set_code="benefit.person",
            fact_set_version=1,
            record_type="benefit_person",
            select=["person.id"],
        )
        scope = MetricQueryScope(
            tenant_id=tenant_id,
            administrative_unit_ids=frozenset({unit_id}),
            source_item_ids=frozenset(source_ids),
            source_scope_enforced=True,
        )

        answer = execute_safe_query(
            database,
            plan,
            catalog_snapshot=catalog,
            scope_snapshot_fingerprint="scope-1",
            scope=scope,
        )
        assert len(answer.rows) == 3

        with pytest.raises(SafeQueryError, match="blocked sensitive fields"):
            execute_safe_query(
                database,
                plan,
                catalog_snapshot=catalog,
                scope_snapshot_fingerprint="scope-1",
                scope=scope,
                enforce_sensitive_field_policies=True,
            )
