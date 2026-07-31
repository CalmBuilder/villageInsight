from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    DatasetRecord,
    IngestionItem,
    MetricDefinition,
    QueryFactSetDefinition,
    RecordIndexValue,
    SemanticManifestDefinition,
)
from village_insight.db.session import get_session_factory
from village_insight.query_governance import (
    QueryGovernanceError,
    contract_fingerprint,
    publish_fact_set,
    publish_metric,
    publish_semantic_manifest,
)
from village_insight.question_catalog import build_question_catalog
from village_insight.question_scope import freeze_question_scope

FACT_SET_CODE = "population.registered_person"
MANIFEST_CODE = "population.registered_person"
METRIC_CODE = "population.registered_person.total"
CONTRACT_VERSION = 1
IDENTITY_FIELD = "person.id_card_number"
DIMENSION_FIELDS = (
    "person.name",
    "person.sex",
    "person.age",
    "population.group_name",
    "household.number",
    "household.relationship_to_head",
    "household.type",
)


class PopulationContractBootstrapError(ValueError):
    pass


class PopulationContractBootstrapReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: str = "population-contract-bootstrap/v1"
    source_item_id: uuid.UUID
    source_sha256: str
    source_file_name: str
    administrative_unit_id: uuid.UUID
    template_id: uuid.UUID
    template_version: int
    record_count: int
    distinct_source_row_count: int
    first_source_row: int
    last_source_row: int
    identity_present_count: int
    distinct_identity_count: int
    fact_set_status: str
    manifest_status: str
    metric_status: str
    published: bool


def _one_source(
    database: Session,
    *,
    source_sha256: str,
) -> IngestionItem:
    sources = list(
        database.scalars(
            select(IngestionItem).where(
                IngestionItem.source_sha256 == source_sha256
            )
        )
    )
    if len(sources) != 1:
        raise PopulationContractBootstrapError(
            "source SHA must resolve to exactly one ingestion item"
        )
    return sources[0]


def _validate_population_source(
    database: Session,
    *,
    source: IngestionItem,
    expected_record_count: int,
    expected_first_row: int,
    expected_last_row: int,
) -> tuple[uuid.UUID, int, dict[str, Any]]:
    record_summary = database.execute(
        select(
            func.count(distinct(DatasetRecord.id)),
            func.count(distinct(DatasetRecord.source_row)),
            func.min(DatasetRecord.source_row),
            func.max(DatasetRecord.source_row),
        ).where(
            DatasetRecord.item_id == source.id,
            DatasetRecord.tenant_id == source.tenant_id,
            DatasetRecord.administrative_unit_id
            == source.administrative_unit_id,
            DatasetRecord.record_type == "population_person",
            DatasetRecord.quality_status == "passed",
        )
    ).one()
    (
        record_count,
        distinct_source_rows,
        first_source_row,
        last_source_row,
    ) = record_summary
    provenance_rows = database.execute(
        select(
            DatasetRecord.template_id,
            DatasetRecord.template_version,
        )
        .where(
            DatasetRecord.item_id == source.id,
            DatasetRecord.record_type == "population_person",
            DatasetRecord.quality_status == "passed",
        )
        .distinct()
    ).all()
    expected = (
        expected_record_count,
        expected_record_count,
        expected_first_row,
        expected_last_row,
        1,
        1,
    )
    actual = (
        int(record_count),
        int(distinct_source_rows),
        int(first_source_row or 0),
        int(last_source_row or 0),
        len(provenance_rows),
    )
    if actual != expected[:-1] or len(provenance_rows) != 1:
        raise PopulationContractBootstrapError(
            "population source record coordinates or provenance do not match"
        )
    template_id, template_version = provenance_rows[0]
    if template_id is None or template_version is None:
        raise PopulationContractBootstrapError(
            "population source has no immutable document-template provenance"
        )

    scoped_records = (
        select(DatasetRecord.id)
        .where(
            DatasetRecord.item_id == source.id,
            DatasetRecord.record_type == "population_person",
            DatasetRecord.quality_status == "passed",
        )
        .subquery()
    )
    identity_summary = database.execute(
        select(
            func.count(RecordIndexValue.id),
            func.count(distinct(RecordIndexValue.text_value)),
        ).where(
            RecordIndexValue.record_id.in_(select(scoped_records.c.id)),
            RecordIndexValue.semantic_field_code == IDENTITY_FIELD,
            RecordIndexValue.role == "",
            RecordIndexValue.text_value.is_not(None),
        )
    ).one()
    identity_present = int(identity_summary[0])
    distinct_identity = int(identity_summary[1])
    if (
        identity_present != expected_record_count
        or distinct_identity != expected_record_count
    ):
        raise PopulationContractBootstrapError(
            "population identity is missing or duplicated"
        )

    scope = freeze_question_scope(
        database,
        tenant_id=source.tenant_id,
        administrative_unit_ids=(source.administrative_unit_id,),
        selected_source_item_id=source.id,
        record_created_before=datetime.now(UTC),
    )
    if scope.source_item_ids != (source.id,):
        raise PopulationContractBootstrapError(
            "population source is not eligible in the frozen question scope"
        )
    catalog = build_question_catalog(database, scope)
    actual_fields = {
        field.code: field.data_type for field in catalog.fields
    }
    required_types = {
        IDENTITY_FIELD: "text",
        "person.name": "text",
        "person.sex": "text",
        "person.age": "integer",
        "population.group_name": "text",
        "household.number": "text",
        "household.relationship_to_head": "text",
        "household.type": "text",
    }
    if any(
        actual_fields.get(field_code) != data_type
        for field_code, data_type in required_types.items()
    ):
        raise PopulationContractBootstrapError(
            "population source fields or data types do not match the contract"
        )
    return (
        template_id,
        int(template_version),
        {
            "record_count": int(record_count),
            "distinct_source_rows": int(distinct_source_rows),
            "first_source_row": int(first_source_row),
            "last_source_row": int(last_source_row),
            "identity_present": identity_present,
            "distinct_identity": distinct_identity,
            "catalog_fingerprint": catalog.catalog_fingerprint,
        },
    )


def _fact_set_payload(
    *,
    template_id: uuid.UUID,
    template_version: int,
    catalog_fingerprint: str,
) -> dict[str, Any]:
    return {
        "code": FACT_SET_CODE,
        "version": CONTRACT_VERSION,
        "name": "户籍人口人员",
        "description": "一条正式记录代表人口明细表中的一个户籍人员。",
        "aliases": ["人口人员", "户籍人员", "人口明细"],
        "domain": "population",
        "record_type": "population_person",
        "record_grain": "one_registered_person",
        "provenance_rule": {
            "kind": "document_template",
            "id": str(template_id),
            "version": template_version,
        },
        "identity_field_codes": [IDENTITY_FIELD],
        "dimension_field_codes": list(DIMENSION_FIELDS),
        "measure_definitions": [
            {
                "field_code": IDENTITY_FIELD,
                "additivity": "non_additive",
            }
        ],
        "time_dimensions": [],
        "status_dimensions": [],
        "sensitive_field_policies": [],
        "conflict_policy": {"mode": "reject"},
        "catalog_fingerprint": catalog_fingerprint,
    }


def _manifest_payload(*, catalog_fingerprint: str) -> dict[str, Any]:
    return {
        "code": MANIFEST_CODE,
        "version": CONTRACT_VERSION,
        "name": "户籍人口人员语义清单",
        "description": "限定人口人员身份、维度和去重口径。",
        "fact_set_code": FACT_SET_CODE,
        "fact_set_version": CONTRACT_VERSION,
        "root_entity": "person",
        "entities": [
            {
                "code": "person",
                "identity_fields": [IDENTITY_FIELD],
                "grain": "one_registered_person",
            }
        ],
        "dimensions": [
            {"field_code": field_code} for field_code in DIMENSION_FIELDS
        ],
        "measures": [
            {
                "field_code": IDENTITY_FIELD,
                "allowed_aggregations": ["count"],
                "additivity": "non_additive",
            }
        ],
        "relationships": [],
        "allowed_join_paths": [],
        "max_join_depth": 0,
        "deduplication_policy": {
            "mode": "identity",
            "identity_field_codes": [IDENTITY_FIELD],
        },
        "default_time_policy": {},
        "evidence_policy": {
            "lineage": "source_cell",
            "require_source_coordinates": True,
        },
        "catalog_fingerprint": catalog_fingerprint,
    }


def bootstrap_population_contract(
    database: Session,
    *,
    source_sha256: str,
    expected_record_count: int,
    expected_first_row: int,
    expected_last_row: int,
    publish: bool = False,
) -> PopulationContractBootstrapReport:
    source = _one_source(database, source_sha256=source_sha256)
    template_id, template_version, validation = _validate_population_source(
        database,
        source=source,
        expected_record_count=expected_record_count,
        expected_first_row=expected_first_row,
        expected_last_row=expected_last_row,
    )
    fact_set = database.scalar(
        select(QueryFactSetDefinition).where(
            QueryFactSetDefinition.code == FACT_SET_CODE,
            QueryFactSetDefinition.version == CONTRACT_VERSION,
        )
    )
    contract_catalog_fingerprint = (
        fact_set.catalog_fingerprint
        if fact_set is not None
        else validation["catalog_fingerprint"]
    )
    fact_payload = _fact_set_payload(
        template_id=template_id,
        template_version=template_version,
        catalog_fingerprint=contract_catalog_fingerprint,
    )
    fact_fingerprint = contract_fingerprint(fact_payload)
    if fact_set is None:
        fact_set = QueryFactSetDefinition(
            **fact_payload,
            status="draft",
            definition_fingerprint=fact_fingerprint,
        )
        database.add(fact_set)
        database.flush()
    elif fact_set.definition_fingerprint != fact_fingerprint:
        raise PopulationContractBootstrapError(
            "existing population fact-set version has a different definition"
        )

    manifest_payload = _manifest_payload(
        catalog_fingerprint=contract_catalog_fingerprint
    )
    manifest_fingerprint = contract_fingerprint(manifest_payload)
    manifest = database.scalar(
        select(SemanticManifestDefinition).where(
            SemanticManifestDefinition.code == MANIFEST_CODE,
            SemanticManifestDefinition.version == CONTRACT_VERSION,
        )
    )
    if manifest is None:
        manifest = SemanticManifestDefinition(
            **manifest_payload,
            status="draft",
            manifest_fingerprint=manifest_fingerprint,
        )
        database.add(manifest)
        database.flush()
    elif manifest.manifest_fingerprint != manifest_fingerprint:
        raise PopulationContractBootstrapError(
            "existing population manifest version has a different definition"
        )

    metric = database.scalar(
        select(MetricDefinition).where(
            MetricDefinition.code == METRIC_CODE,
            MetricDefinition.version == CONTRACT_VERSION,
        )
    )
    if metric is None:
        metric = MetricDefinition(
            code=METRIC_CODE,
            version=CONTRACT_VERSION,
            status="draft",
            name="户籍总人口",
            description="按公民身份号码去重统计户籍人口人员数。",
            fact_set_code=FACT_SET_CODE,
            fact_set_version=CONTRACT_VERSION,
            semantic_manifest_code=MANIFEST_CODE,
            semantic_manifest_version=CONTRACT_VERSION,
            record_type="population_person",
            record_grain="one_registered_person",
            semantic_field_code=IDENTITY_FIELD,
            semantic_field_version=1,
            aggregation="count",
            additivity="non_additive",
            unit="人",
            allowed_filter_fields=list(DIMENSION_FIELDS),
            allowed_group_fields=list(DIMENSION_FIELDS),
            forbidden_aggregation_dimensions=[],
            identity_field_codes=[IDENTITY_FIELD],
            deduplication_policy={"mode": "identity"},
            status_filters=[],
            time_policy={},
            null_policy="reject",
            conflict_policy="reject",
            evidence_policy={
                "lineage": "source_cell",
                "require_source_coordinates": True,
            },
            aliases=["总人口", "总人数", "人口总数", "全村人口"],
            enabled=True,
        )
        database.add(metric)
        database.flush()
    elif (
        metric.fact_set_code != FACT_SET_CODE
        or metric.semantic_manifest_code != MANIFEST_CODE
        or metric.semantic_field_code != IDENTITY_FIELD
        or metric.aggregation != "count"
    ):
        raise PopulationContractBootstrapError(
            "existing population metric version has a different definition"
        )

    if publish:
        try:
            if fact_set.status == "draft":
                publish_fact_set(database, fact_set)
            if manifest.status == "draft":
                publish_semantic_manifest(database, manifest)
            if metric.status == "draft":
                publish_metric(database, metric)
        except QueryGovernanceError as exc:
            raise PopulationContractBootstrapError(str(exc)) from exc
    database.commit()
    return PopulationContractBootstrapReport(
        source_item_id=source.id,
        source_sha256=source.source_sha256,
        source_file_name=source.original_name,
        administrative_unit_id=source.administrative_unit_id,
        template_id=template_id,
        template_version=template_version,
        record_count=validation["record_count"],
        distinct_source_row_count=validation["distinct_source_rows"],
        first_source_row=validation["first_source_row"],
        last_source_row=validation["last_source_row"],
        identity_present_count=validation["identity_present"],
        distinct_identity_count=validation["distinct_identity"],
        fact_set_status=fact_set.status,
        manifest_status=manifest.status,
        metric_status=metric.status,
        published=(
            fact_set.status == "published"
            and manifest.status == "published"
            and metric.status == "published"
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and bootstrap the narrow population query contract."
    )
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--expected-record-count", type=int, required=True)
    parser.add_argument("--expected-first-row", type=int, required=True)
    parser.add_argument("--expected-last-row", type=int, required=True)
    parser.add_argument("--publish", action="store_true")
    arguments = parser.parse_args()
    with get_session_factory()() as database:
        report = bootstrap_population_contract(
            database,
            source_sha256=arguments.source_sha256,
            expected_record_count=arguments.expected_record_count,
            expected_first_row=arguments.expected_first_row,
            expected_last_row=arguments.expected_last_row,
            publish=arguments.publish,
        )
    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
