from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Select, distinct, func, select
from sqlalchemy.orm import Session, aliased

from village_insight.db.models import DatasetRecord, RecordIndexValue
from village_insight.materialization import _normalized_value
from village_insight.questions import MetricQueryScope
from village_insight.safe_query import SafeQueryFilter


class CatalogQueryError(ValueError):
    pass


class CatalogQueryPlan(BaseModel):
    contract_version: Literal["catalog-query/v1"] = "catalog-query/v1"
    operation: Literal[
        "lookup",
        "list",
        "count",
        "group_count",
        "aggregate",
        "rank",
    ]
    fact_set_code: str
    select: list[str] = Field(default_factory=list)
    filters: list[SafeQueryFilter] = Field(default_factory=list)
    group_by: str | None = None
    measure_field_code: str | None = None
    aggregation: Literal["sum", "avg", "min", "max"] | None = None
    order_by_field_code: str | None = None
    order_direction: Literal["asc", "desc"] | None = None
    requested_group_values: list[str | int | float | bool] = Field(
        default_factory=list
    )
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def validate_shape(self) -> CatalogQueryPlan:
        if self.operation in {"lookup", "list"} and not self.select:
            raise ValueError("lookup and list require selected fields")
        if self.operation == "group_count" and not self.group_by:
            raise ValueError("group_count requires one grouping field")
        if self.operation == "aggregate" and (
            not self.measure_field_code or not self.aggregation
        ):
            raise ValueError(
                "aggregate requires measure_field_code and aggregation"
            )
        if self.operation == "rank" and (
            not self.select
            or not self.order_by_field_code
            or not self.order_direction
        ):
            raise ValueError(
                "rank requires selected fields, order_by_field_code, "
                "and order_direction"
            )
        if (
            self.operation != "group_count"
            and self.requested_group_values
        ):
            raise ValueError(
                "requested_group_values is only valid for group_count"
            )
        return self


class CatalogQueryAnswer(BaseModel):
    contract_version: Literal["catalog-query-answer/v1"] = (
        "catalog-query-answer/v1"
    )
    result_grade: Literal["bounded_plan"] = "bounded_plan"
    acceptance_status: Literal["accepted"]
    result_type: Literal["record", "table", "metric"]
    rows: list[dict[str, Any]] = Field(default_factory=list)
    value: int | float | str | None = None
    record_count: int
    source_file_count: int
    data_village_count: int
    grouped_record_count: int | None = None
    ungrouped_record_count: int | None = None
    unexpected_group_values: list[Any] = Field(default_factory=list)


def _serialize(value: object) -> object:
    if isinstance(value, (date, datetime, Decimal, uuid.UUID)):
        return str(value)
    return value


def _answer_scalar(value: object) -> int | float | str | None:
    rendered = _serialize(value)
    if rendered is None or isinstance(rendered, (int, float, str)):
        return rendered
    return str(rendered)


def _catalog_fact(
    catalog: dict[str, Any],
    code: str,
) -> dict[str, Any]:
    matches = [
        item
        for item in catalog.get("fact_sets", [])
        if isinstance(item, dict) and item.get("code") == code
    ]
    if len(matches) != 1:
        raise CatalogQueryError(
            "catalog query requires exactly one frozen fact set"
        )
    return matches[0]


def _field_types(
    catalog: dict[str, Any],
    fact_set_code: str,
) -> dict[str, str]:
    return {
        str(field["code"]): str(field["data_type"])
        for field in catalog.get("fields", [])
        if isinstance(field, dict)
        and fact_set_code in field.get("fact_set_codes", [])
    }


def _fact_predicates(
    fact: dict[str, Any],
    scope: MetricQueryScope,
) -> list[Any]:
    predicates: list[Any] = [
        DatasetRecord.tenant_id == scope.tenant_id,
        DatasetRecord.administrative_unit_id.in_(
            scope.administrative_unit_ids
        ),
        DatasetRecord.quality_status == "passed",
        DatasetRecord.record_type == fact["record_type"],
    ]
    if scope.source_scope_enforced or scope.source_item_ids:
        predicates.append(DatasetRecord.item_id.in_(scope.source_item_ids))
    if scope.record_created_before is not None:
        predicates.append(
            DatasetRecord.created_at <= scope.record_created_before
        )
    provenance = fact.get("execution_provenance")
    if not isinstance(provenance, dict):
        raise CatalogQueryError("fact set has no executable provenance")
    identifier = uuid.UUID(str(provenance.get("id")))
    if provenance.get("kind") == "region_template":
        predicates.extend(
            [
                DatasetRecord.region_template_id == identifier,
                DatasetRecord.region_template_version
                == provenance.get("version"),
            ]
        )
    elif provenance.get("kind") == "document_template":
        predicates.extend(
            [
                DatasetRecord.template_id == identifier,
                DatasetRecord.template_version
                == provenance.get("version"),
            ]
        )
    elif provenance.get("kind") == "approved_plan":
        predicates.append(DatasetRecord.approved_plan_id == identifier)
    else:
        raise CatalogQueryError("fact-set provenance is invalid")
    return predicates


def _typed_column(row: Any, data_type: str) -> Any:
    return getattr(row, f"{data_type}_value")


def _apply_filters(
    statement: Select[Any],
    filters: list[SafeQueryFilter],
    field_types: dict[str, str],
) -> Select[Any]:
    for index, condition in enumerate(filters):
        if condition.field_code not in field_types:
            raise CatalogQueryError(
                f"filter field is outside the fact set: {condition.field_code}"
            )
        value_row = aliased(
            RecordIndexValue,
            name=f"catalog_filter_{index}",
        )
        data_type = field_types[condition.field_code]
        column = _typed_column(value_row, data_type)
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
        values = [
            _normalized_value(data_type, value)[1]
            for value in raw_values
        ]
        if condition.operator == "eq":
            statement = statement.where(column == values[0])
        elif condition.operator == "in":
            statement = statement.where(column.in_(values))
        elif condition.operator == "gt":
            statement = statement.where(column > values[0])
        elif condition.operator == "gte":
            statement = statement.where(column >= values[0])
        elif condition.operator == "lt":
            statement = statement.where(column < values[0])
        elif condition.operator == "lte":
            statement = statement.where(column <= values[0])
        elif condition.operator == "contains":
            if data_type != "text":
                raise CatalogQueryError(
                    "contains is only valid for text fields"
                )
            statement = statement.where(column.contains(str(values[0])))
    return statement


def _base_records(
    plan: CatalogQueryPlan,
    predicates: list[Any],
    field_types: dict[str, str],
) -> Select[Any]:
    return _apply_filters(
        select(
            DatasetRecord.id.label("record_id"),
            DatasetRecord.item_id,
            DatasetRecord.administrative_unit_id,
        ).where(*predicates),
        plan.filters,
        field_types,
    )


def _evidence(
    database: Session,
    records: Select[Any],
) -> tuple[int, int, int]:
    scoped = records.distinct().subquery()
    row = database.execute(
        select(
            func.count(distinct(scoped.c.record_id)),
            func.count(distinct(scoped.c.item_id)),
            func.count(distinct(scoped.c.administrative_unit_id)),
        )
    ).one()
    return int(row[0]), int(row[1]), int(row[2])


def execute_catalog_query(
    database: Session,
    plan: CatalogQueryPlan,
    *,
    catalog_snapshot: dict[str, Any],
    scope: MetricQueryScope,
) -> CatalogQueryAnswer:
    fact = _catalog_fact(catalog_snapshot, plan.fact_set_code)
    field_types = _field_types(
        catalog_snapshot,
        plan.fact_set_code,
    )
    referenced = {
        *plan.select,
        *(item.field_code for item in plan.filters),
        *([plan.group_by] if plan.group_by else []),
        *(
            [plan.measure_field_code]
            if plan.measure_field_code
            else []
        ),
        *(
            [plan.order_by_field_code]
            if plan.order_by_field_code
            else []
        ),
    }
    missing = referenced - field_types.keys()
    if missing:
        raise CatalogQueryError(
            "catalog query fields are outside the selected fact set: "
            + ", ".join(sorted(missing))
        )
    predicates = _fact_predicates(fact, scope)
    base_records = _base_records(plan, predicates, field_types)
    record_count, source_count, village_count = _evidence(
        database,
        base_records,
    )

    if plan.operation == "count":
        return CatalogQueryAnswer(
            acceptance_status="accepted",
            result_type="metric",
            value=record_count,
            record_count=record_count,
            source_file_count=source_count,
            data_village_count=village_count,
        )

    if plan.operation in {"lookup", "list"}:
        selected = database.execute(
            base_records.distinct().limit(
                1 if plan.operation == "lookup" else plan.limit
            )
        ).all()
        record_ids = [row.record_id for row in selected]
        values = database.scalars(
            select(RecordIndexValue).where(
                RecordIndexValue.record_id.in_(record_ids),
                RecordIndexValue.semantic_field_code.in_(plan.select),
                RecordIndexValue.role == "",
            )
        )
        rows_by_id: dict[uuid.UUID, dict[str, Any]] = {
            row.record_id: {
                "record_id": str(row.record_id),
                "item_id": str(row.item_id),
            }
            for row in selected
        }
        for value in values:
            rows_by_id[value.record_id][value.semantic_field_code] = (
                _serialize(
                    _typed_column(
                        value,
                        field_types[value.semantic_field_code],
                    )
                )
            )
        return CatalogQueryAnswer(
            acceptance_status="accepted",
            result_type=(
                "record" if plan.operation == "lookup" else "table"
            ),
            rows=[rows_by_id[record_id] for record_id in record_ids],
            record_count=record_count,
            source_file_count=source_count,
            data_village_count=village_count,
        )

    if plan.operation == "rank":
        order_code = plan.order_by_field_code
        direction = plan.order_direction
        if order_code is None or direction is None:
            raise CatalogQueryError("rank order is unavailable")
        order_row = aliased(RecordIndexValue, name="catalog_rank_order")
        order_column = _typed_column(
            order_row,
            field_types[order_code],
        )
        scoped = base_records.distinct().subquery()
        order_expression = (
            order_column.desc()
            if direction == "desc"
            else order_column.asc()
        )
        selected = database.execute(
            select(
                scoped.c.record_id,
                scoped.c.item_id,
                order_column.label("order_value"),
            )
            .join(
                order_row,
                order_row.record_id == scoped.c.record_id,
            )
            .where(
                order_row.semantic_field_code == order_code,
                order_row.role == "",
                order_column.is_not(None),
            )
            .order_by(order_expression, scoped.c.record_id)
            .limit(plan.limit)
        ).all()
        record_ids = [row.record_id for row in selected]
        selected_codes = {*plan.select, order_code}
        values = database.scalars(
            select(RecordIndexValue).where(
                RecordIndexValue.record_id.in_(record_ids),
                RecordIndexValue.semantic_field_code.in_(selected_codes),
                RecordIndexValue.role == "",
            )
        )
        rank_rows_by_id: dict[uuid.UUID, dict[str, Any]] = {
            row.record_id: {
                "record_id": str(row.record_id),
                "item_id": str(row.item_id),
                order_code: _serialize(row.order_value),
            }
            for row in selected
        }
        for value in values:
            rank_rows_by_id[value.record_id][value.semantic_field_code] = (
                _serialize(
                    _typed_column(
                        value,
                        field_types[value.semantic_field_code],
                    )
                )
            )
        return CatalogQueryAnswer(
            acceptance_status="accepted",
            result_type="table",
            rows=[
                rank_rows_by_id[record_id] for record_id in record_ids
            ],
            record_count=record_count,
            source_file_count=source_count,
            data_village_count=village_count,
        )

    if plan.operation == "aggregate":
        measure_code = plan.measure_field_code
        aggregation = plan.aggregation
        if measure_code is None or aggregation is None:
            raise CatalogQueryError("aggregate measure is unavailable")
        measure_row = aliased(RecordIndexValue, name="catalog_measure")
        measure_column = _typed_column(
            measure_row,
            field_types[measure_code],
        )
        scoped = base_records.distinct().subquery()
        aggregate_expression = {
            "sum": func.sum(measure_column),
            "avg": func.avg(measure_column),
            "min": func.min(measure_column),
            "max": func.max(measure_column),
        }[aggregation]
        if plan.group_by is None:
            aggregate_value = database.scalar(
                select(aggregate_expression)
                .select_from(scoped)
                .join(
                    measure_row,
                    measure_row.record_id == scoped.c.record_id,
                )
                .where(
                    measure_row.semantic_field_code == measure_code,
                    measure_row.role == "",
                    measure_column.is_not(None),
                )
            )
            return CatalogQueryAnswer(
                acceptance_status="accepted",
                result_type="metric",
                value=_answer_scalar(aggregate_value),
                record_count=record_count,
                source_file_count=source_count,
                data_village_count=village_count,
            )
        aggregate_group_code = plan.group_by
        group_row = aliased(RecordIndexValue, name="catalog_aggregate_group")
        group_column = _typed_column(
            group_row,
            field_types[aggregate_group_code],
        )
        aggregate_rows = database.execute(
            select(
                group_column.label("group_value"),
                aggregate_expression.label("value"),
            )
            .select_from(scoped)
            .join(
                measure_row,
                measure_row.record_id == scoped.c.record_id,
            )
            .join(
                group_row,
                group_row.record_id == scoped.c.record_id,
            )
            .where(
                measure_row.semantic_field_code == measure_code,
                measure_row.role == "",
                measure_column.is_not(None),
                group_row.semantic_field_code == aggregate_group_code,
                group_row.role == "",
                group_column.is_not(None),
            )
            .group_by(group_column)
            .order_by(group_column)
        )
        return CatalogQueryAnswer(
            acceptance_status="accepted",
            result_type="table",
            rows=[
                {
                    aggregate_group_code: _serialize(row.group_value),
                    "value": _serialize(row.value),
                }
                for row in aggregate_rows
            ],
            record_count=record_count,
            source_file_count=source_count,
            data_village_count=village_count,
        )

    group_code = plan.group_by
    if group_code is None:
        raise CatalogQueryError("group_count requires a grouping field")
    group_row = aliased(RecordIndexValue, name="catalog_group")
    group_column = _typed_column(group_row, field_types[group_code])
    statement = (
        _apply_filters(
            select(
                group_column.label("group_value"),
                func.count(distinct(DatasetRecord.id)).label("value"),
            )
            .select_from(DatasetRecord)
            .join(group_row, group_row.record_id == DatasetRecord.id)
            .where(
                *predicates,
                group_row.semantic_field_code == group_code,
                group_row.role == "",
                group_column.is_not(None),
            )
            .group_by(group_column),
            plan.filters,
            field_types,
        )
    )
    actual = {
        _serialize(row.group_value): int(row.value)
        for row in database.execute(statement)
    }
    grouped_count = sum(actual.values())
    requested = [
        _normalized_value(field_types[group_code], value)[1]
        for value in plan.requested_group_values
    ]
    unexpected = (
        sorted(
            (
                value
                for value in actual
                if value not in requested
            ),
            key=str,
        )
        if requested
        else []
    )
    ungrouped_count = record_count - grouped_count
    if ungrouped_count != 0 or unexpected:
        raise CatalogQueryError(
            "group result failed coverage acceptance: "
            f"ungrouped={ungrouped_count}, unexpected={unexpected}"
        )
    ordered_values = requested or sorted(actual, key=str)
    rows = [
        {group_code: _serialize(value), "value": actual.get(value, 0)}
        for value in ordered_values
    ]
    return CatalogQueryAnswer(
        acceptance_status="accepted",
        result_type="table",
        rows=rows,
        record_count=record_count,
        source_file_count=source_count,
        data_village_count=village_count,
        grouped_record_count=grouped_count,
        ungrouped_record_count=ungrouped_count,
        unexpected_group_values=unexpected,
    )
