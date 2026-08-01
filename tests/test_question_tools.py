from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    AdministrativeUnit,
    DatasetRecord,
    IngestionItem,
    MetricDefinition,
    QueryFactSetDefinition,
    RecordIndexValue,
    SemanticField,
    SemanticFieldVersion,
    SemanticManifestDefinition,
    Tenant,
)
from village_insight.hermes.question_tools import (
    QuestionToolContext,
    QuestionToolError,
    _handle_describe_query_schema,
    _handle_describe_source_fields,
    _handle_execute_bounded_query,
    _handle_lookup_source_records,
    _handle_query_household,
    _handle_query_metric,
    _handle_query_postgres,
    _validate_query_sql,
    activate_question_tools,
)
from village_insight.query_governance import (
    contract_fingerprint,
    publish_fact_set,
    publish_semantic_manifest,
)
from village_insight.question_catalog import build_question_catalog
from village_insight.question_scope import freeze_question_scope


def test_postgres_tool_rejects_non_question_tables_and_writes() -> None:
    with pytest.raises(QuestionToolError, match=r"question_\* virtual table"):
        _validate_query_sql("SELECT username FROM users")
    with pytest.raises(QuestionToolError, match="only SELECT"):
        _validate_query_sql("DELETE FROM question_records")
    with pytest.raises(QuestionToolError, match="function is not allowed"):
        _validate_query_sql("SELECT pg_sleep(10) FROM question_records")
    _validate_query_sql(
        "SELECT administrative_unit, COUNT(DISTINCT record_id) AS total "
        "FROM question_records GROUP BY administrative_unit "
        "ORDER BY total DESC LIMIT 5"
    )



def _seed_query_database(
    database_url: str,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    second_unit_id = uuid.uuid4()
    shared_plan_id = uuid.uuid4()
    shared_template_id = uuid.uuid4()
    with Session(engine) as database:
        database.add(Tenant(id=tenant_id, name="测试租户"))
        database.add_all(
            [
            AdministrativeUnit(
                id=unit_id,
                tenant_id=tenant_id,
                unit_type="village",
                name="青禾村",
            ),
            AdministrativeUnit(
                id=second_unit_id,
                tenant_id=tenant_id,
                unit_type="village",
                name="稻香村",
            ),
            ]
        )
        field = SemanticField(code="person.count", published_version=1)
        database.add(field)
        database.flush()
        database.add(
            SemanticFieldVersion(
                field_id=field.id,
                version=1,
                name="人数",
                description="记录中的人数",
                layer="domain",
                data_type="integer",
                status="published",
            )
        )
        database.add(
            MetricDefinition(
                code="population.total",
                name="总人数",
                semantic_field_code="person.count",
                semantic_field_version=1,
                aggregation="sum",
                unit="人",
                allowed_filter_fields=[],
            )
        )
        record = DatasetRecord(
            tenant_id=tenant_id,
            administrative_unit_id=unit_id,
            ingestion_batch_id=uuid.uuid4(),
            approved_plan_id=uuid.uuid4(),
            item_id=uuid.uuid4(),
            template_id=shared_template_id,
            template_version=1,
            record_type="population",
            sheet_id="sheet-1",
            region_id="region-1",
            source_row=3,
            quality_status="passed",
        )
        database.add(record)
        database.flush()
        database.add(
            RecordIndexValue(
                record_id=record.id,
                semantic_field_code="person.count",
                semantic_field_version=1,
                role="",
                data_type="integer",
                integer_value=7,
            )
        )
        second_record = DatasetRecord(
            tenant_id=tenant_id,
            administrative_unit_id=second_unit_id,
            ingestion_batch_id=uuid.uuid4(),
            approved_plan_id=shared_plan_id,
            item_id=uuid.uuid4(),
            template_id=shared_template_id,
            template_version=1,
            record_type="population",
            sheet_id="sheet-1",
            region_id="region-1",
            source_row=3,
            quality_status="passed",
        )
        database.add(second_record)
        database.flush()
        database.add(
            RecordIndexValue(
                record_id=second_record.id,
                semantic_field_code="person.count",
                semantic_field_version=1,
                role="",
                data_type="integer",
                integer_value=5,
            )
        )
        database.commit()
    engine.dispose()
    return tenant_id, unit_id, second_unit_id


def _frozen_catalog(
    database_url: str,
    tenant_id: uuid.UUID,
    unit_ids: tuple[uuid.UUID, ...],
    *,
    selected_source_item_id: uuid.UUID | None = None,
):
    engine = create_engine(database_url)
    with Session(engine) as database:
        frozen = freeze_question_scope(
            database,
            tenant_id=tenant_id,
            administrative_unit_ids=unit_ids,
            selected_source_item_id=selected_source_item_id,
            record_created_before=datetime.now(UTC),
        )
        catalog = build_question_catalog(database, frozen)
    engine.dispose()
    assert len(catalog.fact_sets) == 1
    return frozen, catalog, catalog.fact_sets[0].code


def test_household_tool_searches_every_compatible_fact_set(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'household-tool.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    item_id = uuid.uuid4()
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
                original_name="户籍人口.xlsx",
                source_path="/evidence/户籍人口.xlsx",
                source_sha256="1" * 64,
                size_bytes=100,
                status="imported",
            )
        )
        for row_number, name, relationship, sex in (
            (3, "何吉明", "户主", "男性"),
            (4, "谢成群", "配偶", "女性"),
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
                source_row=row_number,
                quality_status="passed",
                raw_data={
                    "columns": {
                        "name": {
                            "header_path": ["姓名"],
                            "source_cell": {
                                "display_value": name,
                                "coordinate": f"F{row_number}",
                            },
                        },
                        "birth": {
                            "header_path": ["出生日期"],
                            "source_cell": {
                                "display_value": "1930年10月20日",
                                "coordinate": f"S{row_number}",
                            },
                        },
                        "address_area": {
                            "header_path": ["住址行政区划"],
                            "source_cell": {
                                "display_value": "重庆市巴南区",
                                "coordinate": f"I{row_number}",
                            },
                        },
                        "address_door": {
                            "header_path": ["住址门牌号"],
                            "source_cell": {
                                "display_value": "青禾组180号",
                                "coordinate": f"M{row_number}",
                            },
                        },
                        "relationship": {
                            "header_path": ["与户主关系"],
                            "source_cell": {
                                "display_value": relationship,
                                "coordinate": f"D{row_number}",
                            },
                        },
                    }
                },
            )
            database.add(record)
            database.flush()
            for code, value in (
                ("household.number", "000650087"),
                ("household.relationship_to_head", relationship),
                ("person.name", name),
                ("person.sex", sex),
                ("bootstrap.address", "青禾组180号"),
            ):
                database.add(
                    RecordIndexValue(
                        record_id=record.id,
                        semantic_field_code=code,
                        semantic_field_version=1,
                        role="",
                        data_type="text",
                        text_value=value,
                    )
                )
        database.commit()
    catalog = {
        "fact_sets": [
            {
                "code": "population.household",
                "record_type": "person",
                "field_codes": [
                    "household.number",
                    "household.relationship_to_head",
                    "person.name",
                    "person.sex",
                ],
                "execution_provenance": {
                    "kind": "approved_plan",
                    "id": str(plan_id),
                },
            }
        ],
        "fields": [
            {
                "code": code,
                "data_type": "text",
                "fact_set_codes": ["population.household"],
            }
            for code in (
                "household.number",
                "household.relationship_to_head",
                "person.name",
                "person.sex",
            )
        ],
    }
    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(unit_id,),
            source_item_ids=(item_id,),
            source_scope_enforced=True,
            catalog_snapshot=catalog,
            run_id=uuid.uuid4(),
        )
    )

    answer = json.loads(
        _handle_query_household(
            {
                "lookup_kind": "household_number",
                "lookup_value": "000650087",
                "result_kind": "household_head",
            }
        )
    )

    assert answer["acceptance_status"] == "accepted"
    assert answer["record_count"] == 1
    assert answer["rows"][0]["person.name"] == "何吉明"
    assert answer["rows"][0]["person.sex"] == "男性"

    source_answer = json.loads(
        _handle_lookup_source_records(
            {
                "filters": [
                    {
                        "field_code": "person.name",
                        "operator": "eq",
                        "value": "何吉明",
                    }
                ],
                "source_header_terms": ["出生"],
            }
        )
    )

    assert source_answer["record_count"] == 1
    assert source_answer["rows"][0]["source_fields"] == [
        {
            "header_path": ["出生日期"],
            "value": "1930年10月20日",
            "coordinate": "S3",
        }
    ]

    address_answer = json.loads(
        _handle_lookup_source_records(
            {
                "source_filters": [
                    {
                        "header_terms": ["住址"],
                        "operator": "contains",
                        "value": "重庆市巴南区青禾组180",
                    }
                ],
                "source_header_terms": ["姓名", "与户主关系"],
            }
        )
    )

    assert address_answer["record_count"] == 2
    assert {
        tuple(
            cell["value"]
            for cell in row["source_fields"]
        )
        for row in address_answer["rows"]
    } == {("何吉明", "户主"), ("谢成群", "配偶")}

    source_fields = json.loads(_handle_describe_source_fields({}))

    assert source_fields["acceptance_status"] == "accepted"
    assert source_fields["source_file_count"] == 1
    assert source_fields["rows"][0]["file_name"] == "户籍人口.xlsx"
    assert source_fields["rows"][0]["source_headers"] == [
        "与户主关系",
        "住址行政区划",
        "住址门牌号",
        "出生日期",
        "姓名",
    ]


def test_describe_query_schema_filters_fact_sets_by_required_fields() -> None:
    activate_question_tools(
        QuestionToolContext(
            database_url="sqlite+pysqlite://",
            tenant_id=uuid.uuid4(),
            administrative_unit_ids=(uuid.uuid4(),),
            source_item_ids=(),
            catalog_snapshot={
                "contract_version": "village-query-catalog/v2",
                "fact_sets": [
                    {
                        "code": "population.household",
                        "name": "户籍人口",
                        "record_type": "person",
                        "field_codes": [
                            "household.number",
                            "person.name",
                        ],
                    },
                    {
                        "code": "agriculture.crop",
                        "name": "农作物",
                        "record_type": "crop",
                        "field_codes": ["crop.name"],
                    },
                ],
                "fields": [
                    {"code": "household.number", "name": "户号"},
                    {"code": "person.name", "name": "姓名"},
                    {"code": "crop.name", "name": "作物名称"},
                ],
            },
            run_id=uuid.uuid4(),
        )
    )

    catalog = json.loads(
        _handle_describe_query_schema(
            {"field_codes": ["household.number", "person.name"]}
        )
    )

    assert [fact_set["code"] for fact_set in catalog["fact_sets"]] == [
        "population.household"
    ]
    assert catalog["catalog_match"]["matched_fact_sets"] == 1
    assert {
        field["code"] for field in catalog["fields"]
    } == {"household.number", "person.name"}


def test_question_tools_execute_scoped_metric_and_virtual_table_query(
    tmp_path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'question-tools.db'}"
    tenant_id, unit_id, _ = _seed_query_database(database_url)
    frozen, query_catalog, fact_set_code = _frozen_catalog(
        database_url,
        tenant_id,
        (unit_id,),
    )
    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(unit_id,),
            source_item_ids=frozen.source_item_ids,
            source_scope_enforced=True,
            record_created_before=frozen.record_created_before,
            catalog_snapshot=query_catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )

    metric = json.loads(
        _handle_query_metric({"metric_code": "population.total"})
    )
    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(unit_id,),
            source_item_ids=frozen.source_item_ids,
            source_scope_enforced=True,
            record_created_before=frozen.record_created_before,
            catalog_snapshot=query_catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )
    table = json.loads(
        _handle_query_postgres(
            {
                "fact_set_code": fact_set_code,
                "sql": (
                    "SELECT record_id, item_id, integer_value "
                    "FROM question_values "
                    "WHERE field_code = :field_code"
                ),
                "params": {"field_code": "person.count"},
            }
        )
    )
    catalog = json.loads(_handle_describe_query_schema({}))
    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(unit_id,),
            source_item_ids=frozen.source_item_ids,
            source_scope_enforced=True,
            record_created_before=frozen.record_created_before,
            catalog_snapshot=query_catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )
    aggregate = json.loads(
        _handle_query_postgres(
            {
                "fact_set_code": fact_set_code,
                "sql": (
                    "SELECT COUNT(DISTINCT record_id) AS record_count, "
                    "COUNT(DISTINCT item_id) AS source_file_count, "
                    "COUNT(DISTINCT administrative_unit) AS data_village_count "
                    "FROM question_records"
                ),
            }
        )
    )
    grouped = json.loads(
        _handle_query_postgres(
            {
                "fact_set_code": fact_set_code,
                "sql": (
                    "SELECT administrative_unit, "
                    "COUNT(DISTINCT record_id) AS member_count "
                    "FROM question_records "
                    "GROUP BY administrative_unit "
                    "ORDER BY member_count DESC LIMIT 5"
                ),
            }
        )
    )
    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(unit_id,),
            source_item_ids=frozen.source_item_ids,
            source_scope_enforced=True,
            record_created_before=frozen.record_created_before,
            catalog_snapshot=query_catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )
    bounded = json.loads(
        _handle_execute_bounded_query(
            {
                "contract_version": "catalog-query/v1",
                "operation": "count",
                "fact_set_code": fact_set_code,
            }
        )
    )

    assert metric["status"] == "success"
    assert metric["metric"]["value"] == 7
    assert catalog["available_record_types"] == ["population"]
    assert catalog["scope"]["fact_storage_level"] == "village"
    assert table["status"] == "success"
    assert table["row_count"] == 1
    assert table["rows"][0]["integer_value"] == 7
    assert aggregate["evidence_summary"] == {
        "record_count": 1,
        "source_file_count": 1,
        "data_village_count": 1,
    }
    assert grouped["acceptance_status"] == "accepted"
    assert grouped["rows"][0]["member_count"] == 1
    assert bounded["acceptance_status"] == "accepted"
    assert bounded["value"] == 1


def test_question_scope_closes_village_producer_to_township_consumer(
    tmp_path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'question-scope.db'}"
    tenant_id, village_a, village_b = _seed_query_database(database_url)
    frozen, query_catalog, fact_set_code = _frozen_catalog(
        database_url,
        tenant_id,
        (village_a, village_b),
    )

    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a,),
            run_id=uuid.uuid4(),
        )
    )
    village_metric = json.loads(
        _handle_query_metric({"metric_code": "population.total"})
    )
    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a, village_b),
            source_item_ids=frozen.source_item_ids,
            source_scope_enforced=True,
            record_created_before=frozen.record_created_before,
            catalog_snapshot=query_catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )
    township_metric = json.loads(
        _handle_query_metric({"metric_code": "population.total"})
    )
    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a, village_b),
            source_item_ids=frozen.source_item_ids,
            source_scope_enforced=True,
            record_created_before=frozen.record_created_before,
            catalog_snapshot=query_catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )
    township_rows = json.loads(
        _handle_query_postgres(
            {
                "fact_set_code": fact_set_code,
                "sql": (
                    "SELECT administrative_unit, integer_value "
                    "FROM question_values "
                    "WHERE field_code = :field_code "
                    "AND administrative_unit IN :villages "
                    "ORDER BY administrative_unit"
                ),
                "params": {
                    "field_code": "person.count",
                    "villages": ["青禾村", "稻香村"],
                },
            }
        )
    )

    assert village_metric["metric"]["value"] == 7
    assert village_metric["metric"]["record_count"] == 1
    assert township_metric["metric"]["value"] == 12
    assert township_metric["metric"]["record_count"] == 2
    assert {
        row["administrative_unit"] for row in township_rows["rows"]
    } == {"青禾村", "稻香村"}


def test_query_postgres_backend_enforces_selected_fact_provenance(
    tmp_path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'question-fact-scope.db'}"
    tenant_id, village_a, _ = _seed_query_database(database_url)
    engine = create_engine(database_url)
    with Session(engine) as database:
        other = DatasetRecord(
            tenant_id=tenant_id,
            administrative_unit_id=village_a,
            ingestion_batch_id=uuid.uuid4(),
            approved_plan_id=uuid.uuid4(),
            item_id=uuid.uuid4(),
            template_id=uuid.uuid4(),
            template_version=1,
            record_type="population",
            sheet_id="sheet-other",
            region_id="region-other",
            source_row=4,
            quality_status="passed",
        )
        database.add(other)
        database.flush()
        other_template_id = other.template_id
        database.add(
            RecordIndexValue(
                record_id=other.id,
                semantic_field_code="person.count",
                semantic_field_version=1,
                role="",
                data_type="integer",
                integer_value=99,
            )
        )
        database.commit()
        frozen = freeze_question_scope(
            database,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a,),
            record_created_before=datetime.now(UTC),
        )
        catalog = build_question_catalog(database, frozen)
    engine.dispose()
    selected = next(
        fact_set
        for fact_set in catalog.fact_sets
        if fact_set.record_count == 1
        and fact_set.execution_provenance.get("id")
        != str(other_template_id)
    )
    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a,),
            source_item_ids=frozen.source_item_ids,
            source_scope_enforced=True,
            record_created_before=frozen.record_created_before,
            catalog_snapshot=catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )
    result = json.loads(
        _handle_query_postgres(
            {
                "fact_set_code": selected.code,
                "sql": (
                    "SELECT record_id, item_id, integer_value "
                    "FROM question_values WHERE field_code = :field_code"
                ),
                "params": {"field_code": "person.count"},
            }
        )
    )
    invalid = json.loads(
        _handle_query_postgres(
            {
                "fact_set_code": "factset.not-in-catalog",
                "sql": "SELECT record_id FROM question_records",
            }
        )
    )

    assert result["status"] == "success"
    assert [row["integer_value"] for row in result["rows"]] == [7]
    assert invalid["status"] == "error"
    assert "exactly one fact_set_code" in invalid["message"]


def test_question_tools_enforce_selected_file_scope(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'question-file-scope.db'}"
    tenant_id, village_a, village_b = _seed_query_database(database_url)
    engine = create_engine(database_url)
    with Session(engine) as database:
        selected_item_id = database.scalar(
            select(DatasetRecord.item_id).where(
                DatasetRecord.administrative_unit_id == village_a
            )
        )
    engine.dispose()
    assert selected_item_id is not None
    frozen, query_catalog, fact_set_code = _frozen_catalog(
        database_url,
        tenant_id,
        (village_a, village_b),
        selected_source_item_id=selected_item_id,
    )

    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a, village_b),
            source_item_ids=(selected_item_id,),
            source_scope_enforced=True,
            record_created_before=frozen.record_created_before,
            catalog_snapshot=query_catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )
    metric = json.loads(
        _handle_query_metric({"metric_code": "population.total"})
    )
    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a, village_b),
            source_item_ids=(selected_item_id,),
            source_scope_enforced=True,
            record_created_before=frozen.record_created_before,
            catalog_snapshot=query_catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )
    aggregate = json.loads(
        _handle_query_postgres(
            {
                "fact_set_code": fact_set_code,
                "sql": (
                    "SELECT COUNT(DISTINCT record_id) AS record_count, "
                    "COUNT(DISTINCT item_id) AS source_file_count, "
                    "COUNT(DISTINCT administrative_unit) AS data_village_count "
                    "FROM question_records"
                ),
            }
        )
    )

    assert metric["metric"]["value"] == 7
    assert metric["metric"]["source_file_count"] == 1
    assert aggregate["rows"] == [
        {
            "record_count": 1,
            "source_file_count": 1,
            "data_village_count": 1,
        }
    ]


def test_question_tools_enforce_an_explicitly_empty_source_scope(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'question-empty-scope.db'}"
    tenant_id, village_a, _ = _seed_query_database(database_url)
    frozen, query_catalog, fact_set_code = _frozen_catalog(
        database_url,
        tenant_id,
        (village_a,),
    )
    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a,),
            source_scope_enforced=True,
            record_created_before=frozen.record_created_before,
            catalog_snapshot=query_catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )

    metric = json.loads(
        _handle_query_metric({"metric_code": "population.total"})
    )
    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a,),
            source_scope_enforced=True,
            record_created_before=frozen.record_created_before,
            catalog_snapshot=query_catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )
    table = json.loads(
        _handle_query_postgres(
            {
                "fact_set_code": fact_set_code,
                "sql": "SELECT record_id FROM question_records",
            }
        )
    )

    assert metric["metric"]["value"] == 0
    assert metric["metric"]["record_count"] == 0
    assert table["rows"] == []


def test_query_catalog_contains_only_fields_present_in_frozen_records(
    tmp_path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'question-catalog.db'}"
    tenant_id, village_a, _ = _seed_query_database(database_url)
    engine = create_engine(database_url)
    with Session(engine) as database:
        unused_field = SemanticField(code="unused.value", published_version=1)
        database.add(unused_field)
        database.flush()
        database.add(
            SemanticFieldVersion(
                field_id=unused_field.id,
                version=1,
                name="未入库字段",
                description="全局发布但当前事实范围没有值",
                layer="domain",
                data_type="integer",
                status="published",
            )
        )
        database.add(
            MetricDefinition(
                code="unused.total",
                name="未入库指标",
                semantic_field_code="unused.value",
                semantic_field_version=1,
                aggregation="sum",
                unit="个",
                allowed_filter_fields=[],
            )
        )
        database.commit()
        frozen_scope = freeze_question_scope(
            database,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a,),
            record_created_before=datetime.now(UTC),
        )
        catalog = build_question_catalog(database, frozen_scope)
    engine.dispose()

    assert [field.code for field in catalog.fields] == ["person.count"]
    assert [metric.code for metric in catalog.metrics] == ["population.total"]
    assert len(catalog.fact_sets) == 1
    assert catalog.fact_sets[0].field_codes == ["person.count"]
    assert len(catalog.catalog_fingerprint) == 64

    activate_question_tools(
        QuestionToolContext(
            database_url=database_url,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a,),
            source_item_ids=frozen_scope.source_item_ids,
            source_scope_enforced=True,
            record_created_before=frozen_scope.record_created_before,
            catalog_snapshot=catalog.model_dump(mode="json"),
            run_id=uuid.uuid4(),
        )
    )
    tool_catalog = json.loads(_handle_describe_query_schema({}))
    assert tool_catalog["contract_version"] == "village-query-catalog/v2"
    assert tool_catalog["fields"][0]["code"] == "person.count"
    assert tool_catalog["scope"]["source_mode"] == "all_approved_files"


def test_query_catalog_promotes_only_published_matching_fact_contract(
    tmp_path,
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'formal-catalog.db'}"
    tenant_id, village_a, _ = _seed_query_database(database_url)
    engine = create_engine(database_url)
    with Session(engine) as database:
        frozen_scope = freeze_question_scope(
            database,
            tenant_id=tenant_id,
            administrative_unit_ids=(village_a,),
            record_created_before=datetime.now(UTC),
        )
        discovery_catalog = build_question_catalog(database, frozen_scope)
        record = database.scalar(
            select(DatasetRecord).where(
                DatasetRecord.administrative_unit_id == village_a
            )
        )
        assert record is not None
        fact_payload = {
            "code": "population.registry",
            "version": 1,
            "record_type": "population",
            "record_grain": "one_population_row",
            "provenance_rule": {
                "kind": "document_template",
                "id": str(record.template_id),
                "version": record.template_version,
            },
            "identity_field_codes": ["person.count"],
            "catalog_fingerprint": discovery_catalog.catalog_fingerprint,
        }
        fact_set = QueryFactSetDefinition(
            **fact_payload,
            name="人口台账",
            description="正式测试事实集",
            aliases=[],
            status="draft",
            domain="population",
            dimension_field_codes=[],
            measure_definitions=[
                {"field_code": "person.count", "additivity": "additive"}
            ],
            time_dimensions=[],
            status_dimensions=[],
            sensitive_field_policies=[],
            conflict_policy={"mode": "reject"},
            definition_fingerprint=contract_fingerprint(fact_payload),
        )
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
            "root_entity": "population_row",
            "entities": [{"code": "population_row"}],
            "dimensions": [],
            "measures": [],
            "relationships": [],
            "allowed_join_paths": [],
            "max_join_depth": 0,
            "deduplication_policy": {"mode": "record"},
            "default_time_policy": {},
            "evidence_policy": {"lineage": "source_cell"},
            "catalog_fingerprint": discovery_catalog.catalog_fingerprint,
        }
        manifest = SemanticManifestDefinition(
            **manifest_payload,
            manifest_fingerprint=contract_fingerprint(manifest_payload),
        )
        database.add(manifest)
        database.flush()
        publish_semantic_manifest(database, manifest)
        database.commit()

        governed_catalog = build_question_catalog(database, frozen_scope)
    engine.dispose()

    official = next(
        fact_set
        for fact_set in governed_catalog.fact_sets
        if fact_set.code == "population.registry"
    )
    assert official.governance_status == "published"
    assert official.semantic_manifest_code == "population.registry"
    assert official.identity_field_codes == ["person.count"]
