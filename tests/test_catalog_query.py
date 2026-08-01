from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.catalog_query import (
    CatalogQueryError,
    CatalogQueryPlan,
    execute_catalog_query,
)
from village_insight.db.base import Base
from village_insight.db.models import DatasetRecord, RecordIndexValue
from village_insight.questions import MetricQueryScope


def _catalog(
    fact_set_code: str,
    plan_id: uuid.UUID,
) -> dict:
    return {
        "fact_sets": [
            {
                "code": fact_set_code,
                "record_type": "person",
                "execution_provenance": {
                    "kind": "approved_plan",
                    "id": str(plan_id),
                },
            }
        ],
        "fields": [
            {
                "code": "person.name",
                "data_type": "text",
                "fact_set_codes": [fact_set_code],
            },
            {
                "code": "person.sex",
                "data_type": "text",
                "fact_set_codes": [fact_set_code],
            },
            {
                "code": "benefit.amount",
                "data_type": "decimal",
                "fact_set_codes": [fact_set_code],
            },
        ],
    }


def test_catalog_group_count_completes_requested_zero_group() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    item_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    fact_set_code = "factset.test"
    with Session(engine) as database:
        for source_row in (1, 2):
            record = DatasetRecord(
                tenant_id=tenant_id,
                administrative_unit_id=unit_id,
                ingestion_batch_id=uuid.uuid4(),
                approved_plan_id=plan_id,
                item_id=item_id,
                record_type="person",
                sheet_id="sheet-1",
                region_id="region-1",
                source_row=source_row,
                quality_status="passed",
            )
            database.add(record)
            database.flush()
            database.add(
                RecordIndexValue(
                    record_id=record.id,
                    semantic_field_code="person.sex",
                    semantic_field_version=1,
                    role="",
                    data_type="text",
                    text_value="男",
                )
            )
        database.commit()
        answer = execute_catalog_query(
            database,
            CatalogQueryPlan(
                operation="group_count",
                fact_set_code=fact_set_code,
                group_by="person.sex",
                requested_group_values=["男", "女"],
            ),
            catalog_snapshot=_catalog(fact_set_code, plan_id),
            scope=MetricQueryScope(
                tenant_id=tenant_id,
                administrative_unit_ids=frozenset({unit_id}),
                source_item_ids=frozenset({item_id}),
                source_scope_enforced=True,
            ),
        )

    assert answer.acceptance_status == "accepted"
    assert answer.rows == [
        {"person.sex": "男", "value": 2},
        {"person.sex": "女", "value": 0},
    ]
    assert answer.record_count == 2
    assert answer.grouped_record_count == 2
    assert answer.ungrouped_record_count == 0


def test_catalog_group_count_rejects_incomplete_coverage() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    item_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    record = DatasetRecord(
        tenant_id=tenant_id,
        administrative_unit_id=unit_id,
        ingestion_batch_id=uuid.uuid4(),
        approved_plan_id=plan_id,
        item_id=item_id,
        record_type="person",
        sheet_id="sheet-1",
        region_id="region-1",
        source_row=1,
        quality_status="passed",
    )
    with Session(engine) as database:
        database.add(record)
        database.commit()
        with pytest.raises(
            CatalogQueryError,
            match="group result failed coverage acceptance",
        ):
            execute_catalog_query(
                database,
                CatalogQueryPlan(
                    operation="group_count",
                    fact_set_code="factset.test",
                    group_by="person.sex",
                    requested_group_values=["男", "女"],
                ),
                catalog_snapshot=_catalog("factset.test", plan_id),
                scope=MetricQueryScope(
                    tenant_id=tenant_id,
                    administrative_unit_ids=frozenset({unit_id}),
                    source_item_ids=frozenset({item_id}),
                    source_scope_enforced=True,
                ),
            )


def test_catalog_aggregate_sums_numeric_field() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    item_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    with Session(engine) as database:
        for source_row, amount in ((1, 120), (2, 80)):
            record = DatasetRecord(
                tenant_id=tenant_id,
                administrative_unit_id=unit_id,
                ingestion_batch_id=uuid.uuid4(),
                approved_plan_id=plan_id,
                item_id=item_id,
                record_type="person",
                sheet_id="sheet-1",
                region_id="region-1",
                source_row=source_row,
                quality_status="passed",
            )
            database.add(record)
            database.flush()
            database.add(
                RecordIndexValue(
                    record_id=record.id,
                    semantic_field_code="benefit.amount",
                    semantic_field_version=1,
                    role="",
                    data_type="decimal",
                    decimal_value=amount,
                )
            )
        database.commit()

        answer = execute_catalog_query(
            database,
            CatalogQueryPlan(
                operation="aggregate",
                fact_set_code="factset.test",
                measure_field_code="benefit.amount",
                aggregation="sum",
            ),
            catalog_snapshot=_catalog("factset.test", plan_id),
            scope=MetricQueryScope(
                tenant_id=tenant_id,
                administrative_unit_ids=frozenset({unit_id}),
                source_item_ids=frozenset({item_id}),
                source_scope_enforced=True,
            ),
        )

    assert answer.acceptance_status == "accepted"
    assert answer.value == "200.0000000000"
    assert answer.record_count == 2


def test_catalog_rank_returns_highest_record_with_selected_fields() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    item_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    with Session(engine) as database:
        for source_row, name, amount in (
            (1, "甲", 120),
            (2, "乙", 280),
        ):
            record = DatasetRecord(
                tenant_id=tenant_id,
                administrative_unit_id=unit_id,
                ingestion_batch_id=uuid.uuid4(),
                approved_plan_id=plan_id,
                item_id=item_id,
                record_type="person",
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
                        semantic_field_code="person.name",
                        semantic_field_version=1,
                        role="",
                        data_type="text",
                        text_value=name,
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

        answer = execute_catalog_query(
            database,
            CatalogQueryPlan(
                operation="rank",
                fact_set_code="factset.test",
                select=["person.name", "benefit.amount"],
                order_by_field_code="benefit.amount",
                order_direction="desc",
                limit=1,
            ),
            catalog_snapshot=_catalog("factset.test", plan_id),
            scope=MetricQueryScope(
                tenant_id=tenant_id,
                administrative_unit_ids=frozenset({unit_id}),
                source_item_ids=frozenset({item_id}),
                source_scope_enforced=True,
            ),
        )

    assert answer.rows[0]["person.name"] == "乙"
    assert answer.rows[0]["benefit.amount"] == "280.0000000000"
    assert answer.record_count == 2
