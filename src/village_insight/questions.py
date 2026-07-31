from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from village_insight.db.models import (
    DatasetRecord,
    MetricDefinition,
    QueryFactSetDefinition,
    RecordIndexValue,
    SemanticField,
    SemanticFieldVersion,
)
from village_insight.materialization import _normalized_value


class MetricFilter(BaseModel):
    field_code: str
    operator: Literal["eq"] = "eq"
    value: str


class MetricQuery(BaseModel):
    contract_version: Literal["metric-query/v1"] = "metric-query/v1"
    metric_code: str
    metric_version: int | None = Field(default=None, ge=1)
    filters: list[MetricFilter] = Field(default_factory=list)


class MetricQueryScope(BaseModel):
    tenant_id: uuid.UUID
    administrative_unit_ids: frozenset[uuid.UUID]
    source_item_ids: frozenset[uuid.UUID] = Field(default_factory=frozenset)
    source_scope_enforced: bool = False
    record_created_before: datetime | None = None


class MetricAnswer(BaseModel):
    metric_code: str
    metric_version: int
    metric_name: str
    result_grade: Literal["official_metric", "bounded_sql"]
    value: int | Decimal | None
    unit: str | None
    aggregation: str
    filters: list[MetricFilter]
    record_count: int
    source_file_count: int
    query_plan: str


class MetricQueryError(ValueError):
    pass


def execute_metric_query(
    database: Session,
    query: MetricQuery,
    scope: MetricQueryScope,
) -> MetricAnswer:
    if not scope.administrative_unit_ids:
        raise MetricQueryError("query scope has no administrative units")
    metric_statement = (
        select(MetricDefinition)
        .where(
            MetricDefinition.code == query.metric_code,
            MetricDefinition.enabled.is_(True),
            MetricDefinition.status == "published",
        )
        .order_by(MetricDefinition.version.desc())
        .limit(1)
    )
    if query.metric_version is not None:
        metric_statement = metric_statement.where(
            MetricDefinition.version == query.metric_version
        )
    metric = database.scalar(metric_statement)
    if metric is None:
        raise MetricQueryError(f"unsupported metric: {query.metric_code}")
    fact_set = None
    if metric.fact_set_code is not None:
        if (
            metric.fact_set_version is None
            or metric.semantic_manifest_code is None
            or metric.semantic_manifest_version is None
        ):
            raise MetricQueryError("official metric contract is incomplete")
        fact_set = database.scalar(
            select(QueryFactSetDefinition).where(
                QueryFactSetDefinition.code == metric.fact_set_code,
                QueryFactSetDefinition.version == metric.fact_set_version,
                QueryFactSetDefinition.status == "published",
            )
        )
        if fact_set is None:
            raise MetricQueryError("official metric fact set is unavailable")
    target_field = database.scalar(
        select(SemanticFieldVersion)
        .join(SemanticField)
        .where(
            SemanticField.code == metric.semantic_field_code,
            SemanticFieldVersion.version == metric.semantic_field_version,
        )
    )
    if target_field is None:
        raise MetricQueryError("metric field version is unavailable")

    statement = (
        select(DatasetRecord.id, DatasetRecord.item_id, RecordIndexValue)
        .join(
            RecordIndexValue,
            RecordIndexValue.record_id == DatasetRecord.id,
        )
        .where(
            RecordIndexValue.semantic_field_code == metric.semantic_field_code,
            RecordIndexValue.semantic_field_version == metric.semantic_field_version,
            RecordIndexValue.role == "",
            DatasetRecord.quality_status == "passed",
            DatasetRecord.tenant_id == scope.tenant_id,
            DatasetRecord.administrative_unit_id.in_(scope.administrative_unit_ids),
        )
    )
    if scope.source_scope_enforced or scope.source_item_ids:
        statement = statement.where(
            DatasetRecord.item_id.in_(scope.source_item_ids)
        )
    if scope.record_created_before is not None:
        statement = statement.where(
            DatasetRecord.created_at <= scope.record_created_before
        )
    if fact_set is not None:
        statement = statement.where(
            DatasetRecord.record_type == fact_set.record_type
        )
        provenance = fact_set.provenance_rule
        provenance_id = uuid.UUID(str(provenance["id"]))
        if provenance["kind"] == "region_template":
            statement = statement.where(
                DatasetRecord.region_template_id == provenance_id,
                DatasetRecord.region_template_version
                == provenance["version"],
            )
        elif provenance["kind"] == "document_template":
            statement = statement.where(
                DatasetRecord.template_id == provenance_id,
                DatasetRecord.template_version == provenance["version"],
            )
        elif provenance["kind"] == "approved_plan":
            statement = statement.where(
                DatasetRecord.approved_plan_id == provenance_id
            )
        else:
            raise MetricQueryError("unsupported metric provenance rule")
    for index, condition in enumerate(query.filters):
        if condition.field_code not in metric.allowed_filter_fields:
            raise MetricQueryError(
                f"filter field is not allowed for this metric: {condition.field_code}"
            )
        filter_field = database.scalar(
            select(SemanticFieldVersion)
            .join(SemanticField)
            .where(
                SemanticField.code == condition.field_code,
                SemanticField.published_version == SemanticFieldVersion.version,
            )
        )
        if filter_field is None:
            raise MetricQueryError(f"filter field is unavailable: {condition.field_code}")
        _, normalized = _normalized_value(filter_field.data_type, condition.value)
        filter_value = aliased(RecordIndexValue, name=f"filter_value_{index}")
        statement = statement.join(
            filter_value,
            filter_value.record_id == DatasetRecord.id,
        ).where(
            filter_value.semantic_field_code == condition.field_code,
            getattr(filter_value, f"{filter_field.data_type}_value") == normalized,
        )
    for index, status_condition in enumerate(metric.status_filters):
        field_code = str(status_condition.get("field_code") or "")
        if (
            not field_code
            or status_condition.get("operator", "eq") != "eq"
        ):
            raise MetricQueryError("unsupported published status filter")
        filter_field = database.scalar(
            select(SemanticFieldVersion)
            .join(SemanticField)
            .where(
                SemanticField.code == field_code,
                SemanticField.published_version
                == SemanticFieldVersion.version,
            )
        )
        if filter_field is None:
            raise MetricQueryError("published status filter is unavailable")
        _, normalized = _normalized_value(
            filter_field.data_type,
            status_condition.get("value"),
        )
        filter_value = aliased(
            RecordIndexValue,
            name=f"status_filter_value_{index}",
        )
        statement = statement.join(
            filter_value,
            filter_value.record_id == DatasetRecord.id,
        ).where(
            filter_value.semantic_field_code == field_code,
            filter_value.role == "",
            getattr(filter_value, f"{filter_field.data_type}_value")
            == normalized,
        )

    rows = database.execute(statement).all()
    record_ids = {row[0] for row in rows}
    item_ids = {row[1] for row in rows}
    values = [getattr(row[2], f"{target_field.data_type}_value") for row in rows]
    result: int | Decimal | None
    if metric.aggregation == "count":
        if metric.identity_field_codes:
            identity_rows = database.execute(
                select(
                    RecordIndexValue.record_id,
                    RecordIndexValue.semantic_field_code,
                    RecordIndexValue.text_value,
                    RecordIndexValue.integer_value,
                    RecordIndexValue.decimal_value,
                    RecordIndexValue.boolean_value,
                    RecordIndexValue.date_value,
                    RecordIndexValue.datetime_value,
                ).where(
                    RecordIndexValue.record_id.in_(record_ids),
                    RecordIndexValue.semantic_field_code.in_(
                        metric.identity_field_codes
                    ),
                    RecordIndexValue.role == "",
                )
            ).all()
            identities_by_record: dict[
                uuid.UUID,
                dict[str, object],
            ] = {}
            for identity_row in identity_rows:
                values_by_type = identity_row[2:]
                value = next(
                    (
                        candidate
                        for candidate in values_by_type
                        if candidate is not None
                    ),
                    None,
                )
                identities_by_record.setdefault(
                    identity_row.record_id,
                    {},
                )[identity_row.semantic_field_code] = value
            identity_tuples = {
                tuple(values[field] for field in metric.identity_field_codes)
                for values in identities_by_record.values()
                if all(
                    field in values
                    for field in metric.identity_field_codes
                )
            }
            result = len(identity_tuples)
        else:
            result = len(record_ids)
    elif metric.aggregation == "sum":
        result = sum((value for value in values if value is not None), start=0)
    elif metric.aggregation == "avg":
        present = [value for value in values if value is not None]
        result = sum(present, start=0) / len(present) if present else None
    elif metric.aggregation == "min":
        present = [value for value in values if value is not None]
        result = min(present) if present else None
    elif metric.aggregation == "max":
        present = [value for value in values if value is not None]
        result = max(present) if present else None
    else:
        raise MetricQueryError(f"unsupported aggregation: {metric.aggregation}")
    return MetricAnswer(
        metric_code=metric.code,
        metric_version=metric.version,
        metric_name=metric.name,
        result_grade=(
            "official_metric"
            if metric.fact_set_code and metric.semantic_manifest_code
            else "bounded_sql"
        ),
        value=result,
        unit=metric.unit,
        aggregation=metric.aggregation,
        filters=query.filters,
        record_count=len(record_ids),
        source_file_count=len(item_ids),
        query_plan=(
            f"{metric.aggregation}({metric.semantic_field_code}@{metric.semantic_field_version})"
        ),
    )
