from __future__ import annotations

import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    AdministrativeUnit,
    DatasetRecord,
    IngestionItem,
    RecordIndexValue,
    SemanticField,
    SemanticFieldVersion,
    Tenant,
)
from village_insight.party_member_contract_bootstrap import (
    IDENTITY_FIELD,
    bootstrap_party_member_contract,
)
from village_insight.party_member_contract_regression import (
    run_party_member_contract_regression,
)


def test_party_member_contract_bootstrap_validates_and_publishes() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    item_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    source_sha256 = "b" * 64
    field_types = {
        IDENTITY_FIELD: "text",
        "person.sex": "text",
        "person.age": "integer",
    }
    with Session(engine) as database:
        database.add(Tenant(id=tenant_id, name="测试租户"))
        database.add(
            AdministrativeUnit(
                id=unit_id,
                tenant_id=tenant_id,
                unit_type="village",
                name="青禾村",
            )
        )
        database.add(
            IngestionItem(
                id=item_id,
                tenant_id=tenant_id,
                administrative_unit_id=unit_id,
                created_by_user_id=uuid.uuid4(),
                batch_id=uuid.uuid4(),
                original_name="党员名册.xlsx",
                source_path="/evidence/party.xlsx",
                source_sha256=source_sha256,
                size_bytes=100,
                status="imported",
            )
        )
        for code, data_type in field_types.items():
            field = SemanticField(code=code, published_version=1)
            field.versions.append(
                SemanticFieldVersion(
                    version=1,
                    name=code,
                    layer="domain",
                    data_type=data_type,
                    status="published",
                )
            )
            database.add(field)
        database.flush()
        for source_row, identity, sex, age in (
            (3, "M-1", "女", 55),
            (4, "M-2", "男", 72),
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
                        semantic_field_code=IDENTITY_FIELD,
                        semantic_field_version=1,
                        role="",
                        data_type="text",
                        text_value=identity,
                    ),
                    RecordIndexValue(
                        record_id=record.id,
                        semantic_field_code="person.sex",
                        semantic_field_version=1,
                        role="",
                        data_type="text",
                        text_value=sex,
                    ),
                    RecordIndexValue(
                        record_id=record.id,
                        semantic_field_code="person.age",
                        semantic_field_version=1,
                        role="",
                        data_type="integer",
                        integer_value=age,
                    ),
                ]
            )
        database.commit()

        report = bootstrap_party_member_contract(
            database,
            source_sha256=source_sha256,
            expected_record_count=2,
            expected_first_row=3,
            expected_last_row=4,
            publish=True,
        )
        repeated = bootstrap_party_member_contract(
            database,
            source_sha256=source_sha256,
            expected_record_count=2,
            expected_first_row=3,
            expected_last_row=4,
            publish=True,
        )
        regression = run_party_member_contract_regression(
            database,
            {
                "benchmark_membership": "mother_corpus",
                "expected_mother_case_count": 2,
                "dataset_snapshot": {
                    "source_sha256": source_sha256,
                    "source_file": "党员名册.xlsx",
                },
                "expected_fact_set_code": "party.member_roster",
                "expected_fact_set_version": 1,
                "expected_metric": "party.member_roster.total",
                "expected_total": 2,
                "cases": [
                    {
                        "case_id": "age",
                        "benchmark_case_ids": ["mother-age"],
                        "safe_query_plan": {
                            "operation": "count",
                            "fact_set_code": "party.member_roster",
                            "fact_set_version": 1,
                            "record_type": "person",
                            "filters": [
                                {
                                    "field_code": "person.age",
                                    "operator": "gte",
                                    "value": 50,
                                },
                                {
                                    "field_code": "person.age",
                                    "operator": "lte",
                                    "value": 70,
                                },
                            ],
                        },
                        "expected_result": 1,
                        "expected_record_count": 1,
                    },
                    {
                        "case_id": "sex",
                        "benchmark_case_ids": ["mother-sex"],
                        "safe_query_plan": {
                            "operation": "group_by",
                            "fact_set_code": "party.member_roster",
                            "fact_set_version": 1,
                            "record_type": "person",
                            "group_by": ["person.sex"],
                            "measure_field_code": IDENTITY_FIELD,
                            "aggregation": "count",
                        },
                        "expected_rows": [
                            {"person.sex": "女", "value": 1},
                            {"person.sex": "男", "value": 1},
                        ],
                        "expected_record_count": 2,
                    },
                ],
            },
        )

    assert report.published is True
    assert report.distinct_identity_count == 2
    assert repeated.published is True
    assert regression.passed is True
    assert regression.mother_case_count == 2
