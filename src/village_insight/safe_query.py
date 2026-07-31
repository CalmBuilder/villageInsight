from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Select, asc, desc, distinct, func, select
from sqlalchemy.orm import Session, aliased

from village_insight.db.models import (
    DatasetRecord,
    QueryFactSetDefinition,
    RecordIndexValue,
    SemanticManifestDefinition,
)
from village_insight.materialization import _normalized_value
from village_insight.questions import MetricQueryScope

SafeOperation = Literal["lookup", "list", "count", "aggregate", "group_by"]
SafeOperator = Literal["eq", "in", "gt", "gte", "lt", "lte", "contains"]


class SafeQueryError(ValueError):
    pass


class SafeQueryFilter(BaseModel):
    field_code: str
    operator: SafeOperator = "eq"
    value: str | int | float | bool | list[str | int | float | bool]

    @model_validator(mode="after")
    def validate_value_shape(self) -> SafeQueryFilter:
        if self.operator == "in" and not isinstance(self.value, list):
            raise ValueError("in operator requires a list value")
        if self.operator != "in" and isinstance(self.value, list):
            raise ValueError("only in operator accepts a list value")
        return self


class SafeQueryOrder(BaseModel):
    field_code: str
    direction: Literal["asc", "desc"] = "asc"


class SafeQueryPlan(BaseModel):
    contract_version: Literal["safe-query/v1"] = "safe-query/v1"
    operation: SafeOperation
    fact_set_code: str
    fact_set_version: int = Field(ge=1)
    record_type: str
    select: list[str] = Field(default_factory=list)
    filters: list[SafeQueryFilter] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[SafeQueryOrder] = Field(default_factory=list)
    measure_field_code: str | None = None
    aggregation: Literal["count", "sum", "avg", "min", "max"] | None = None
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def validate_operation_shape(self) -> SafeQueryPlan:
        if self.operation in {"aggregate", "group_by"}:
            if not self.measure_field_code or not self.aggregation:
                raise ValueError(
                    "aggregate and group_by require a measure and aggregation"
                )
        if self.operation == "group_by" and not self.group_by:
            raise ValueError("group_by operation requires grouping fields")
        if self.operation in {"lookup", "list"} and not self.select:
            raise ValueError("lookup and list require selected fields")
        return self


class SemanticQueryPlan(BaseModel):
    plan_version: Literal["semantic-query-plan/v1"] = "semantic-query-plan/v1"
    manifest_code: str
    manifest_version: int
    fact_set_code: str
    fact_set_version: int
    root_entity: str
    operation: SafeOperation
    selected_fields: list[str]
    measure_field: str | None
    requested_dimensions: list[str]
    filters: list[SafeQueryFilter]
    aggregation: str | None
    deduplication_policy: dict[str, Any]
    scope_snapshot_fingerprint: str
    catalog_fingerprint: str
    semantic_plan_fingerprint: str = ""


class SafeQueryAnswer(BaseModel):
    contract_version: Literal["safe-query-answer/v1"] = "safe-query-answer/v1"
    result_grade: Literal["contract_query"] = "contract_query"
    result_type: Literal["record", "table", "metric"]
    semantic_plan: SemanticQueryPlan
    rows: list[dict[str, Any]] = Field(default_factory=list)
    value: int | Decimal | None = None
    aggregation: str | None = None
    record_count: int
    source_file_count: int
    data_village_count: int


@dataclass(frozen=True)
class _ResolvedContract:
    fact_set: QueryFactSetDefinition
    manifest: SemanticManifestDefinition
    field_types: dict[str, str]
    semantic_plan: SemanticQueryPlan


def _canonical_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _catalog_fact_set(
    catalog_snapshot: dict[str, Any],
    *,
    code: str,
    version: int,
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in catalog_snapshot.get("fact_sets", [])
            if isinstance(item, dict)
            and item.get("code") == code
            and item.get("version") == version
        ),
        None,
    )


def compile_safe_query(
    database: Session,
    plan: SafeQueryPlan,
    *,
    catalog_snapshot: dict[str, Any],
    scope_snapshot_fingerprint: str,
    enforce_sensitive_field_policies: bool = False,
) -> _ResolvedContract:
    catalog_fact_set = _catalog_fact_set(
        catalog_snapshot,
        code=plan.fact_set_code,
        version=plan.fact_set_version,
    )
    if (
        catalog_fact_set is None
        or catalog_fact_set.get("governance_status") != "published"
    ):
        raise SafeQueryError(
            "safe query requires a published fact set in the frozen catalog"
        )
    if catalog_fact_set.get("record_type") != plan.record_type:
        raise SafeQueryError("record type does not match the selected fact set")
    fact_set = database.scalar(
        select(QueryFactSetDefinition).where(
            QueryFactSetDefinition.code == plan.fact_set_code,
            QueryFactSetDefinition.version == plan.fact_set_version,
            QueryFactSetDefinition.status == "published",
        )
    )
    if fact_set is None:
        raise SafeQueryError("published fact set definition is unavailable")
    manifest = database.scalar(
        select(SemanticManifestDefinition).where(
            SemanticManifestDefinition.fact_set_code == fact_set.code,
            SemanticManifestDefinition.fact_set_version == fact_set.version,
            SemanticManifestDefinition.status == "published",
            SemanticManifestDefinition.catalog_fingerprint
            == fact_set.catalog_fingerprint,
        )
    )
    if manifest is None:
        raise SafeQueryError("published semantic manifest is unavailable")

    catalog_fields = {
        str(item["code"]): str(item["data_type"])
        for item in catalog_snapshot.get("fields", [])
        if isinstance(item, dict)
        and item.get("code")
        and plan.fact_set_code in item.get("fact_set_codes", [])
    }
    referenced_fields = {
        *plan.select,
        *(condition.field_code for condition in plan.filters),
        *plan.group_by,
        *(order.field_code for order in plan.order_by),
        *([plan.measure_field_code] if plan.measure_field_code else []),
        *fact_set.identity_field_codes,
    }
    unavailable = referenced_fields - catalog_fields.keys()
    if unavailable:
        raise SafeQueryError(
            "safe query references fields outside the fact set: "
            + ", ".join(sorted(unavailable))
        )
    dimensions = {
        str(item.get("field_code"))
        for item in manifest.dimensions
        if item.get("field_code")
    }
    measures = {
        str(item.get("field_code")): item
        for item in manifest.measures
        if item.get("field_code")
    }
    identities = set(fact_set.identity_field_codes)
    allowed_filter_fields = dimensions | identities
    requested_detail_fields = {
        *plan.select,
        *(order.field_code for order in plan.order_by),
    }
    if not requested_detail_fields.issubset(allowed_filter_fields):
        raise SafeQueryError(
            "selected or ordered fields are not published dimensions"
        )
    if enforce_sensitive_field_policies:
        blocked_sensitive_fields = {
            str(policy.get("field_code"))
            for policy in fact_set.sensitive_field_policies
            if policy.get("field_code") and not policy.get("queryable", False)
        }
        requested_sensitive_fields = referenced_fields & blocked_sensitive_fields
        if requested_sensitive_fields:
            raise SafeQueryError(
                "safe query requests blocked sensitive fields: "
                + ", ".join(sorted(requested_sensitive_fields))
            )
    invalid_filters = {
        condition.field_code
        for condition in plan.filters
        if condition.field_code not in allowed_filter_fields
    }
    if invalid_filters:
        raise SafeQueryError(
            "filters are not published dimensions: "
            + ", ".join(sorted(invalid_filters))
        )
    if not set(plan.group_by).issubset(dimensions):
        raise SafeQueryError("grouping fields are not published dimensions")
    if plan.measure_field_code:
        measure = measures.get(plan.measure_field_code)
        if measure is None:
            raise SafeQueryError("measure is not published in the manifest")
        allowed_aggregations = set(measure.get("allowed_aggregations") or [])
        if plan.aggregation not in allowed_aggregations:
            raise SafeQueryError("aggregation is not allowed for this measure")
        grouped_distinct_count = (
            plan.aggregation == "count"
            and measure.get("allow_grouped_distinct_count") is True
            and plan.measure_field_code in identities
            and len(identities) == 1
        )
        if (
            measure.get("additivity") == "non_additive"
            and plan.group_by
            and not grouped_distinct_count
        ):
            raise SafeQueryError(
                "non-additive measure cannot be grouped without a formal policy"
            )

    semantic_plan = SemanticQueryPlan(
        manifest_code=manifest.code,
        manifest_version=manifest.version,
        fact_set_code=fact_set.code,
        fact_set_version=fact_set.version,
        root_entity=manifest.root_entity,
        operation=plan.operation,
        selected_fields=plan.select,
        measure_field=plan.measure_field_code,
        requested_dimensions=plan.group_by,
        filters=plan.filters,
        aggregation=plan.aggregation,
        deduplication_policy=manifest.deduplication_policy,
        scope_snapshot_fingerprint=scope_snapshot_fingerprint,
        catalog_fingerprint=str(
            catalog_snapshot.get("catalog_fingerprint") or ""
        ),
    )
    semantic_plan.semantic_plan_fingerprint = _canonical_fingerprint(
        semantic_plan.model_dump(
            mode="json",
            exclude={"semantic_plan_fingerprint"},
        )
    )
    return _ResolvedContract(
        fact_set=fact_set,
        manifest=manifest,
        field_types=catalog_fields,
        semantic_plan=semantic_plan,
    )


def _value_column(value: Any, data_type: str) -> Any:
    return getattr(value, f"{data_type}_value")


def _fact_predicates(
    fact_set: QueryFactSetDefinition,
    scope: MetricQueryScope,
) -> list[Any]:
    predicates: list[Any] = [
        DatasetRecord.tenant_id == scope.tenant_id,
        DatasetRecord.administrative_unit_id.in_(
            scope.administrative_unit_ids
        ),
        DatasetRecord.quality_status == "passed",
        DatasetRecord.record_type == fact_set.record_type,
    ]
    if scope.source_scope_enforced or scope.source_item_ids:
        predicates.append(DatasetRecord.item_id.in_(scope.source_item_ids))
    if scope.record_created_before is not None:
        predicates.append(
            DatasetRecord.created_at <= scope.record_created_before
        )
    rule = fact_set.provenance_rule
    rule_id = uuid.UUID(str(rule["id"]))
    if rule["kind"] == "region_template":
        predicates.extend(
            [
                DatasetRecord.region_template_id == rule_id,
                DatasetRecord.region_template_version == rule["version"],
            ]
        )
    elif rule["kind"] == "document_template":
        predicates.extend(
            [
                DatasetRecord.template_id == rule_id,
                DatasetRecord.template_version == rule["version"],
            ]
        )
    elif rule["kind"] == "approved_plan":
        predicates.append(DatasetRecord.approved_plan_id == rule_id)
    else:
        raise SafeQueryError("unsupported published provenance rule")
    return predicates


def _apply_filters(
    statement: Select[Any],
    plan: SafeQueryPlan,
    field_types: dict[str, str],
) -> Select[Any]:
    for index, condition in enumerate(plan.filters):
        value_row = aliased(RecordIndexValue, name=f"safe_filter_{index}")
        data_type = field_types[condition.field_code]
        column = _value_column(value_row, data_type)
        statement = statement.join(
            value_row,
            value_row.record_id == DatasetRecord.id,
        ).where(
            value_row.semantic_field_code == condition.field_code,
            value_row.role == "",
        )
        raw_values = (
            condition.value
            if isinstance(condition.value, list)
            else [condition.value]
        )
        normalized_values = [
            _normalized_value(data_type, value)[1] for value in raw_values
        ]
        if condition.operator == "eq":
            statement = statement.where(column == normalized_values[0])
        elif condition.operator == "in":
            statement = statement.where(column.in_(normalized_values))
        elif condition.operator == "gt":
            statement = statement.where(column > normalized_values[0])
        elif condition.operator == "gte":
            statement = statement.where(column >= normalized_values[0])
        elif condition.operator == "lt":
            statement = statement.where(column < normalized_values[0])
        elif condition.operator == "lte":
            statement = statement.where(column <= normalized_values[0])
        elif condition.operator == "contains":
            if data_type != "text":
                raise SafeQueryError("contains is only valid for text fields")
            statement = statement.where(
                column.contains(str(normalized_values[0]))
            )
    return statement


def _evidence_counts(
    database: Session,
    filtered_records: Select[Any],
) -> tuple[int, int, int]:
    subquery = filtered_records.distinct().subquery()
    row = database.execute(
        select(
            func.count(distinct(subquery.c.record_id)),
            func.count(distinct(subquery.c.item_id)),
            func.count(distinct(subquery.c.administrative_unit_id)),
        )
    ).one()
    return int(row[0]), int(row[1]), int(row[2])


def _serialize_value(value: object) -> object:
    if isinstance(value, (date, datetime, Decimal, uuid.UUID)):
        return str(value)
    return value


def execute_safe_query(
    database: Session,
    plan: SafeQueryPlan,
    *,
    catalog_snapshot: dict[str, Any],
    scope_snapshot_fingerprint: str,
    scope: MetricQueryScope,
    enforce_sensitive_field_policies: bool = False,
) -> SafeQueryAnswer:
    contract = compile_safe_query(
        database,
        plan,
        catalog_snapshot=catalog_snapshot,
        scope_snapshot_fingerprint=scope_snapshot_fingerprint,
        enforce_sensitive_field_policies=enforce_sensitive_field_policies,
    )
    predicates = _fact_predicates(contract.fact_set, scope)
    filtered_records = _apply_filters(
        select(
            DatasetRecord.id.label("record_id"),
            DatasetRecord.item_id,
            DatasetRecord.administrative_unit_id,
        ).where(*predicates),
        plan,
        contract.field_types,
    )
    record_count, source_count, village_count = _evidence_counts(
        database,
        filtered_records,
    )

    if plan.operation == "count":
        identity_columns: list[Any] = []
        identity_statement = select().select_from(DatasetRecord)
        for index, field_code in enumerate(
            contract.fact_set.identity_field_codes
        ):
            identity_row = aliased(
                RecordIndexValue,
                name=f"safe_identity_{index}",
            )
            identity_column = _value_column(
                identity_row,
                contract.field_types[field_code],
            )
            identity_columns.append(identity_column)
            identity_statement = identity_statement.join(
                identity_row,
                identity_row.record_id == DatasetRecord.id,
            ).where(
                identity_row.semantic_field_code == field_code,
                identity_row.role == "",
                identity_column.is_not(None),
            )
        identity_statement = _apply_filters(
            identity_statement.add_columns(*identity_columns).where(
                *predicates
            ),
            plan,
            contract.field_types,
        )
        identity_subquery = identity_statement.distinct().subquery()
        identity_count = int(
            database.scalar(
                select(func.count()).select_from(identity_subquery)
            )
            or 0
        )
        return SafeQueryAnswer(
            result_type="metric",
            semantic_plan=contract.semantic_plan,
            value=identity_count,
            aggregation="count",
            record_count=record_count,
            source_file_count=source_count,
            data_village_count=village_count,
        )

    if plan.operation in {"lookup", "list"}:
        ordered_records = filtered_records
        for index, order in enumerate(plan.order_by):
            order_row = aliased(
                RecordIndexValue,
                name=f"safe_order_{index}",
            )
            order_column = _value_column(
                order_row,
                contract.field_types[order.field_code],
            )
            ordered_records = ordered_records.join(
                order_row,
                order_row.record_id == DatasetRecord.id,
            ).where(
                order_row.semantic_field_code == order.field_code,
                order_row.role == "",
            ).order_by(
                asc(order_column)
                if order.direction == "asc"
                else desc(order_column)
            )
        selected_records = database.execute(
            ordered_records.distinct().limit(
                1 if plan.operation == "lookup" else plan.limit
            )
        ).all()
        record_ids = [row.record_id for row in selected_records]
        values = database.execute(
            select(RecordIndexValue).where(
                RecordIndexValue.record_id.in_(record_ids),
                RecordIndexValue.semantic_field_code.in_(plan.select),
                RecordIndexValue.role == "",
            )
        ).scalars()
        rows_by_id: dict[uuid.UUID, dict[str, Any]] = {
            row.record_id: {
                "record_id": str(row.record_id),
                "item_id": str(row.item_id),
            }
            for row in selected_records
        }
        for value in values:
            rows_by_id[value.record_id][value.semantic_field_code] = (
                _serialize_value(
                    _value_column(
                        value,
                        contract.field_types[value.semantic_field_code],
                    )
                )
            )
        rows = [rows_by_id[record_id] for record_id in record_ids]
        return SafeQueryAnswer(
            result_type="record" if plan.operation == "lookup" else "table",
            semantic_plan=contract.semantic_plan,
            rows=rows,
            record_count=record_count,
            source_file_count=source_count,
            data_village_count=village_count,
        )

    measure_code = plan.measure_field_code
    aggregation = plan.aggregation
    if measure_code is None or aggregation is None:
        raise SafeQueryError("measure and aggregation are required")
    measure_row = aliased(RecordIndexValue, name="safe_measure")
    measure_column = _value_column(
        measure_row,
        contract.field_types[measure_code],
    )
    aggregate_expression = (
        func.count(distinct(measure_column))
        if aggregation == "count"
        else getattr(func, aggregation)(measure_column)
    )
    if plan.operation == "aggregate":
        statement = _apply_filters(
            select(aggregate_expression)
            .select_from(DatasetRecord)
            .join(measure_row, measure_row.record_id == DatasetRecord.id)
            .where(
                *predicates,
                measure_row.semantic_field_code == measure_code,
                measure_row.role == "",
            ),
            plan,
            contract.field_types,
        )
        value = database.scalar(statement)
        return SafeQueryAnswer(
            result_type="metric",
            semantic_plan=contract.semantic_plan,
            value=value,
            aggregation=aggregation,
            record_count=record_count,
            source_file_count=source_count,
            data_village_count=village_count,
        )

    group_rows: list[Any] = []
    group_columns: list[Any] = []
    statement = select()
    for index, field_code in enumerate(plan.group_by):
        group_row = aliased(RecordIndexValue, name=f"safe_group_{index}")
        group_column = _value_column(
            group_row,
            contract.field_types[field_code],
        )
        group_rows.append(group_row)
        group_columns.append(group_column)
        statement = statement.add_columns(
            group_column.label(f"group_{index}")
        )
    statement = statement.add_columns(
        aggregate_expression.label("value")
    ).select_from(DatasetRecord)
    for group_row, field_code in zip(group_rows, plan.group_by, strict=True):
        statement = statement.join(
            group_row,
            group_row.record_id == DatasetRecord.id,
        ).where(
            group_row.semantic_field_code == field_code,
            group_row.role == "",
        )
    statement = (
        statement.join(measure_row, measure_row.record_id == DatasetRecord.id)
        .where(
            *predicates,
            measure_row.semantic_field_code == measure_code,
            measure_row.role == "",
        )
        .group_by(*group_columns)
        .limit(plan.limit)
    )
    statement = _apply_filters(
        statement,
        plan,
        contract.field_types,
    )
    rows = [
        {
            **{
                field_code: _serialize_value(row[index])
                for index, field_code in enumerate(plan.group_by)
            },
            "value": _serialize_value(row[-1]),
        }
        for row in database.execute(statement).all()
    ]
    return SafeQueryAnswer(
        result_type="table",
        semantic_plan=contract.semantic_plan,
        rows=rows,
        aggregation=aggregation,
        record_count=record_count,
        source_file_count=source_count,
        data_village_count=village_count,
    )
