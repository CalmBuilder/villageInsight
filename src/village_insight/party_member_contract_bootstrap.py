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

FACT_SET_CODE = "party.member_roster"
MANIFEST_CODE = "party.member_roster"
METRIC_CODE = "party.member_roster.total"
CONTRACT_VERSION = 1
IDENTITY_FIELD = "bootstrap.shared.fc1271ecb27b13148a23"
DIMENSION_FIELDS = ("person.sex", "person.age")


class PartyMemberContractBootstrapError(ValueError):
    pass


class PartyMemberContractBootstrapReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: str = "party-member-contract-bootstrap/v1"
    source_item_id: uuid.UUID
    source_sha256: str
    administrative_unit_id: uuid.UUID
    approved_plan_id: uuid.UUID
    record_count: int
    distinct_source_row_count: int
    first_source_row: int
    last_source_row: int
    distinct_identity_count: int
    fact_set_status: str
    manifest_status: str
    metric_status: str
    published: bool


def _validate_source(
    database: Session,
    *,
    source_sha256: str,
    expected_record_count: int,
    expected_first_row: int,
    expected_last_row: int,
) -> tuple[IngestionItem, uuid.UUID, dict[str, Any]]:
    sources = list(
        database.scalars(
            select(IngestionItem).where(
                IngestionItem.source_sha256 == source_sha256
            )
        )
    )
    if len(sources) != 1:
        raise PartyMemberContractBootstrapError(
            "source SHA must resolve to exactly one ingestion item"
        )
    source = sources[0]
    rows = database.execute(
        select(
            func.count(distinct(DatasetRecord.id)),
            func.count(distinct(DatasetRecord.source_row)),
            func.min(DatasetRecord.source_row),
            func.max(DatasetRecord.source_row),
            DatasetRecord.approved_plan_id,
        )
        .where(
            DatasetRecord.item_id == source.id,
            DatasetRecord.tenant_id == source.tenant_id,
            DatasetRecord.administrative_unit_id
            == source.administrative_unit_id,
            DatasetRecord.record_type == "person",
            DatasetRecord.quality_status == "passed",
        )
        .group_by(DatasetRecord.approved_plan_id)
    ).all()
    if len(rows) != 1:
        raise PartyMemberContractBootstrapError(
            "party roster must have one immutable approved-plan provenance"
        )
    (
        record_count,
        source_row_count,
        first_row,
        last_row,
        approved_plan_id,
    ) = rows[0]
    actual_coordinates = (
        int(record_count),
        int(source_row_count),
        int(first_row),
        int(last_row),
    )
    expected_coordinates = (
        expected_record_count,
        expected_record_count,
        expected_first_row,
        expected_last_row,
    )
    if actual_coordinates != expected_coordinates:
        raise PartyMemberContractBootstrapError(
            "party roster record coordinates do not match"
        )
    record_ids = (
        select(DatasetRecord.id)
        .where(
            DatasetRecord.item_id == source.id,
            DatasetRecord.record_type == "person",
            DatasetRecord.quality_status == "passed",
            DatasetRecord.approved_plan_id == approved_plan_id,
        )
        .subquery()
    )
    identity = database.execute(
        select(
            func.count(RecordIndexValue.id),
            func.count(distinct(RecordIndexValue.text_value)),
        ).where(
            RecordIndexValue.record_id.in_(select(record_ids.c.id)),
            RecordIndexValue.semantic_field_code == IDENTITY_FIELD,
            RecordIndexValue.role == "",
            RecordIndexValue.text_value.is_not(None),
        )
    ).one()
    if (
        int(identity[0]) != expected_record_count
        or int(identity[1]) != expected_record_count
    ):
        raise PartyMemberContractBootstrapError(
            "party roster identity is missing or duplicated"
        )
    frozen = freeze_question_scope(
        database,
        tenant_id=source.tenant_id,
        administrative_unit_ids=(source.administrative_unit_id,),
        selected_source_item_id=source.id,
        record_created_before=datetime.now(UTC),
    )
    catalog = build_question_catalog(database, frozen)
    field_types = {field.code: field.data_type for field in catalog.fields}
    expected_types = {
        IDENTITY_FIELD: "text",
        "person.sex": "text",
        "person.age": "integer",
    }
    if any(
        field_types.get(code) != data_type
        for code, data_type in expected_types.items()
    ):
        raise PartyMemberContractBootstrapError(
            "party roster fields or types do not match the contract"
        )
    return (
        source,
        approved_plan_id,
        {
            "record_count": int(record_count),
            "source_row_count": int(source_row_count),
            "first_row": int(first_row),
            "last_row": int(last_row),
            "distinct_identity_count": int(identity[1]),
            "catalog_fingerprint": catalog.catalog_fingerprint,
        },
    )


def bootstrap_party_member_contract(
    database: Session,
    *,
    source_sha256: str,
    expected_record_count: int,
    expected_first_row: int,
    expected_last_row: int,
    publish: bool = False,
) -> PartyMemberContractBootstrapReport:
    source, approved_plan_id, validated = _validate_source(
        database,
        source_sha256=source_sha256,
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
    catalog_fingerprint = (
        fact_set.catalog_fingerprint
        if fact_set is not None
        else validated["catalog_fingerprint"]
    )
    fact_payload = {
        "code": FACT_SET_CODE,
        "version": CONTRACT_VERSION,
        "name": "党员名册人员",
        "description": "一条正式记录代表名册中的一名党员。",
        "aliases": ["党员", "党员人员", "党员名册"],
        "domain": "party",
        "record_type": "person",
        "record_grain": "one_party_member",
        "provenance_rule": {
            "kind": "approved_plan",
            "id": str(approved_plan_id),
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
        raise PartyMemberContractBootstrapError(
            "existing party fact-set version differs"
        )

    manifest_payload = {
        "code": MANIFEST_CODE,
        "version": CONTRACT_VERSION,
        "name": "党员名册人员语义清单",
        "description": "限定党员身份、年龄、性别和去重计数。",
        "fact_set_code": FACT_SET_CODE,
        "fact_set_version": CONTRACT_VERSION,
        "root_entity": "party_member",
        "entities": [
            {
                "code": "party_member",
                "identity_fields": [IDENTITY_FIELD],
                "grain": "one_party_member",
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
                "allow_grouped_distinct_count": True,
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
        raise PartyMemberContractBootstrapError(
            "existing party manifest version differs"
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
            name="党员总数",
            description="按名册身份字段去重统计党员人数。",
            fact_set_code=FACT_SET_CODE,
            fact_set_version=CONTRACT_VERSION,
            semantic_manifest_code=MANIFEST_CODE,
            semantic_manifest_version=CONTRACT_VERSION,
            record_type="person",
            record_grain="one_party_member",
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
            evidence_policy={"lineage": "source_cell"},
            aliases=["党员人数", "党员总数"],
            enabled=True,
        )
        database.add(metric)
        database.flush()
    if publish:
        try:
            if fact_set.status == "draft":
                publish_fact_set(database, fact_set)
            if manifest.status == "draft":
                publish_semantic_manifest(database, manifest)
            if metric.status == "draft":
                publish_metric(database, metric)
        except QueryGovernanceError as exc:
            raise PartyMemberContractBootstrapError(str(exc)) from exc
    database.commit()
    return PartyMemberContractBootstrapReport(
        source_item_id=source.id,
        source_sha256=source.source_sha256,
        administrative_unit_id=source.administrative_unit_id,
        approved_plan_id=approved_plan_id,
        record_count=validated["record_count"],
        distinct_source_row_count=validated["source_row_count"],
        first_source_row=validated["first_row"],
        last_source_row=validated["last_row"],
        distinct_identity_count=validated["distinct_identity_count"],
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
        description="Validate and bootstrap the narrow party roster contract."
    )
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--expected-record-count", type=int, required=True)
    parser.add_argument("--expected-first-row", type=int, required=True)
    parser.add_argument("--expected-last-row", type=int, required=True)
    parser.add_argument("--publish", action="store_true")
    arguments = parser.parse_args()
    with get_session_factory()() as database:
        report = bootstrap_party_member_contract(
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
