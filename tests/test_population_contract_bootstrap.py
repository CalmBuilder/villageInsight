from __future__ import annotations

import uuid
from datetime import UTC, datetime

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
from village_insight.population_contract_bootstrap import (
    DIMENSION_FIELDS,
    IDENTITY_FIELD,
    bootstrap_population_contract,
)
from village_insight.population_contract_regression import (
    run_population_contract_regression,
)


def _published_field(
    database: Session,
    *,
    code: str,
    data_type: str,
) -> None:
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


def test_population_contract_bootstrap_publishes_only_validated_source() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    user_id = uuid.uuid4()
    item_id = uuid.uuid4()
    template_id = uuid.uuid4()
    source_sha256 = "a" * 64
    data_types = {
        IDENTITY_FIELD: "text",
        "person.name": "text",
        "person.sex": "text",
        "person.age": "integer",
        "population.group_name": "text",
        "household.number": "text",
        "household.relationship_to_head": "text",
        "household.type": "text",
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
                created_by_user_id=user_id,
                batch_id=uuid.uuid4(),
                original_name="人口明细.xlsx",
                source_path="/evidence/population.xlsx",
                source_sha256=source_sha256,
                size_bytes=100,
                status="imported",
            )
        )
        for code, data_type in data_types.items():
            _published_field(database, code=code, data_type=data_type)
        database.flush()
        for source_row, identity in ((2, "P-1"), (3, "P-2")):
            record = DatasetRecord(
                tenant_id=tenant_id,
                administrative_unit_id=unit_id,
                ingestion_batch_id=uuid.uuid4(),
                approved_plan_id=uuid.uuid4(),
                item_id=item_id,
                template_id=template_id,
                template_version=2,
                record_type="population_person",
                sheet_id="sheet-1",
                region_id="region-1",
                source_row=source_row,
                quality_status="passed",
                created_at=datetime.now(UTC),
            )
            database.add(record)
            database.flush()
            for code in (IDENTITY_FIELD, *DIMENSION_FIELDS):
                data_type = data_types[code]
                value_arguments = (
                    {"integer_value": 30}
                    if data_type == "integer"
                    else {
                        "text_value": (
                            identity if code == IDENTITY_FIELD else "值"
                        )
                    }
                )
                database.add(
                    RecordIndexValue(
                        record_id=record.id,
                        semantic_field_code=code,
                        semantic_field_version=1,
                        role="",
                        data_type=data_type,
                        **value_arguments,
                    )
                )
        database.commit()

        report = bootstrap_population_contract(
            database,
            source_sha256=source_sha256,
            expected_record_count=2,
            expected_first_row=2,
            expected_last_row=3,
            publish=True,
        )
        repeated = bootstrap_population_contract(
            database,
            source_sha256=source_sha256,
            expected_record_count=2,
            expected_first_row=2,
            expected_last_row=3,
            publish=True,
        )
        regression = run_population_contract_regression(
            database,
            {
                "case_id": "population-test",
                "benchmark_membership": "supplemental_only",
                "dataset_snapshot": {
                    "source_sha256": source_sha256,
                    "source_file": "人口明细.xlsx",
                },
                "expected_fact_set_code": "population.registered_person",
                "expected_fact_set_version": 1,
                "expected_metric": "population.registered_person.total",
                "expected_tool_grade": "official_metric",
                "expected_result": 2,
                "expected_record_count": 2,
                "expected_source_file_count": 1,
                "expected_data_village_count": 1,
            },
        )

    assert report.published is True
    assert report.record_count == 2
    assert report.distinct_identity_count == 2
    assert repeated.published is True
    assert regression.passed is True
    assert regression.metric_value == 2
