from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, create_engine, distinct, select, text
from sqlalchemy.orm import Session, aliased
from sqlglot import exp, parse
from sqlglot.errors import ParseError

from village_insight.catalog_query import (
    CatalogQueryError,
    CatalogQueryPlan,
    execute_catalog_query,
)
from village_insight.db.models import (
    AdministrativeUnit,
    DatasetRecord,
    IngestionItem,
    MetricDefinition,
    RecordIndexValue,
    SemanticField,
    SemanticFieldVersion,
)
from village_insight.questions import (
    MetricQuery,
    MetricQueryError,
    MetricQueryScope,
    execute_metric_query,
)
from village_insight.safe_query import (
    SafeQueryError,
    SafeQueryPlan,
    execute_safe_query,
)

QUESTION_TOOLSET = "village_query"
MAX_QUERY_CHARS = 8_000
MAX_QUERY_ROWS = 200
DEFAULT_QUERY_ROWS = 50
STATEMENT_TIMEOUT_MS = 60_000

_ALLOWED_VIRTUAL_TABLES = frozenset(
    {
        "question_records",
        "question_values",
        "question_lineage",
        "question_sources",
    }
)
_BLOCKED_FUNCTION_PREFIXES = (
    "pg_",
    "dblink",
    "lo_",
)
_BLOCKED_FUNCTIONS = frozenset(
    {
        "set_config",
        "current_setting",
        "nextval",
        "setval",
        "currval",
        "query_to_xml",
        "database_to_xml",
    }
)
_PARAMETER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class QuestionToolError(ValueError):
    pass


@dataclass
class QuestionToolContext:
    database_url: str
    tenant_id: uuid.UUID
    administrative_unit_ids: tuple[uuid.UUID, ...]
    run_id: uuid.UUID
    source_item_ids: tuple[uuid.UUID, ...] = ()
    source_scope_enforced: bool = False
    record_created_before: datetime | None = None
    catalog_snapshot: dict[str, Any] = field(default_factory=dict)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


_active_context: QuestionToolContext | None = None


def activate_question_tools(context: QuestionToolContext) -> None:
    """Register the run-local VillageInsight tools in the forked Hermes process."""

    global _active_context
    _active_context = context
    from tools.registry import registry  # type: ignore[import-untyped]

    for name, schema, handler, description in (
        (
            "describe_query_schema",
            DESCRIBE_QUERY_SCHEMA,
            _handle_describe_query_schema,
            "Inspect the current user's published village-data query catalog.",
        ),
        (
            "query_metric",
            QUERY_METRIC_SCHEMA,
            _handle_query_metric,
            "Execute one governed deterministic metric query.",
        ),
        (
            "execute_safe_query",
            EXECUTE_SAFE_QUERY_SCHEMA,
            _handle_execute_safe_query,
            "Execute one published structured query contract without model SQL.",
        ),
        (
            "execute_bounded_query",
            EXECUTE_BOUNDED_QUERY_SCHEMA,
            _handle_execute_bounded_query,
            "Execute one validated structured query against a frozen fact set.",
        ),
        (
            "lookup_records",
            LOOKUP_RECORDS_SCHEMA,
            _handle_lookup_records,
            "Look up records from a selected frozen fact set.",
        ),
        (
            "aggregate_records",
            AGGREGATE_RECORDS_SCHEMA,
            _handle_aggregate_records,
            "Count or group records from a selected frozen fact set.",
        ),
        (
            "summarize_values",
            SUMMARIZE_VALUES_SCHEMA,
            _handle_summarize_values,
            "Sum, average, minimize, or maximize one numeric field.",
        ),
        (
            "rank_records",
            RANK_RECORDS_SCHEMA,
            _handle_rank_records,
            "Return the highest or lowest records by one typed field.",
        ),
        (
            "query_household",
            QUERY_HOUSEHOLD_SCHEMA,
            _handle_query_household,
            "Find a household head or members across compatible fact sets.",
        ),
        (
            "describe_source_fields",
            DESCRIBE_SOURCE_FIELDS_SCHEMA,
            _handle_describe_source_fields,
            "List original spreadsheet headers by file and Sheet.",
        ),
        (
            "lookup_source_records",
            LOOKUP_SOURCE_RECORDS_SCHEMA,
            _handle_lookup_source_records,
            "Locate records across fact sets and read selected original spreadsheet fields.",
        ),
        (
            "query_postgres",
            QUERY_POSTGRES_SCHEMA,
            _handle_query_postgres,
            "Execute one bounded read-only query against scoped village-data virtual tables.",
        ),
    ):
        registry.register(
            name=name,
            toolset=QUESTION_TOOLSET,
            schema=schema,
            handler=handler,
            description=description,
            emoji="▦",
            max_result_size_chars=50_000,
        )


def current_tool_results() -> list[dict[str, Any]]:
    return list(_require_context().tool_results)


def _require_context() -> QuestionToolContext:
    if _active_context is None:
        raise QuestionToolError("question tool context is unavailable")
    return _active_context


def _json_default(value: object) -> object:
    if isinstance(value, (uuid.UUID, date, datetime, Decimal)):
        return str(value)
    return str(value)


def _json_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=_json_default, separators=(",", ":"))


def _record_result(payload: dict[str, Any]) -> str:
    rendered = _json_result(payload)
    normalized = json.loads(rendered)
    _require_context().tool_results.append(normalized)
    return rendered


def _error_result(tool: str, exc: Exception) -> str:
    code = type(exc).__name__
    if isinstance(exc, QuestionToolError):
        code = "invalid_query"
    elif isinstance(exc, MetricQueryError):
        code = "invalid_metric_query"
    elif isinstance(exc, SafeQueryError):
        code = "invalid_safe_query"
    elif isinstance(exc, CatalogQueryError):
        code = "invalid_bounded_query"
    return _record_result(
        {
            "status": "error",
            "tool": tool,
            "error_code": code,
            "message": str(exc)[:800],
        }
    )


def _handle_describe_query_schema(
    args: dict[str, Any],
    **_kwargs: Any,
) -> str:
    try:
        context = _require_context()
        if context.catalog_snapshot:
            snapshot = dict(context.catalog_snapshot)
            required_field_codes = {
                str(value).strip()
                for value in args.get("field_codes", [])
                if str(value).strip()
            }
            search = str(args.get("search") or "").strip().casefold()
            all_fact_sets = [
                {
                    key: value
                    for key, value in fact_set.items()
                    if key != "execution_provenance"
                }
                for fact_set in snapshot.get("fact_sets", [])
                if isinstance(fact_set, dict)
            ]
            matching_fact_sets = [
                fact_set
                for fact_set in all_fact_sets
                if (
                    not required_field_codes
                    or required_field_codes.issubset(
                        set(fact_set.get("field_codes", []))
                    )
                )
                and (
                    not search
                    or search
                    in " ".join(
                        [
                            str(fact_set.get("code") or ""),
                            str(fact_set.get("name") or ""),
                            str(fact_set.get("description") or ""),
                            " ".join(fact_set.get("aliases", [])),
                            " ".join(fact_set.get("field_codes", [])),
                        ]
                    ).casefold()
                )
            ]
            max_fact_sets = 40
            snapshot["fact_sets"] = matching_fact_sets[:max_fact_sets]
            returned_field_codes = {
                str(code)
                for fact_set in snapshot["fact_sets"]
                for code in fact_set.get("field_codes", [])
            }
            all_fields = [
                field
                for field in snapshot.get("fields", [])
                if isinstance(field, dict)
            ]
            matching_fields = [
                field
                for field in all_fields
                if (
                    not required_field_codes
                    or field.get("code") in returned_field_codes
                )
                and (
                    not search
                    or search
                    in " ".join(
                        [
                            str(field.get("code") or ""),
                            str(field.get("name") or ""),
                            str(field.get("description") or ""),
                            " ".join(field.get("aliases", [])),
                        ]
                    ).casefold()
                    or field.get("code") in returned_field_codes
                )
            ]
            max_fields = 160
            snapshot["fields"] = matching_fields[:max_fields]
            snapshot["catalog_match"] = {
                "requested_field_codes": sorted(required_field_codes),
                "search": search,
                "total_fact_sets": len(all_fact_sets),
                "matched_fact_sets": len(matching_fact_sets),
                "returned_fact_sets": len(snapshot["fact_sets"]),
                "fact_sets_truncated": len(matching_fact_sets) > max_fact_sets,
                "total_fields": len(all_fields),
                "returned_fields": len(snapshot["fields"]),
                "fields_truncated": len(matching_fields) > max_fields,
            }
            snapshot.update(
                {
                    "status": "success",
                    "tool": "describe_query_schema",
                    "scope": {
                        "administrative_units": snapshot.get(
                            "administrative_units", []
                        ),
                        "fact_storage_level": "village",
                        "aggregation_rule": (
                            "The authorized unit list is complete. Query all "
                            "listed villages for a whole-scope answer; never "
                            "filter by a township or tenant container name."
                        ),
                        "source_mode": snapshot.get("source_mode"),
                        "record_created_before": snapshot.get(
                            "record_created_before"
                        ),
                    },
                    "available_record_types": sorted(
                        {
                            str(fact_set.get("record_type"))
                            for fact_set in snapshot.get("fact_sets", [])
                            if isinstance(fact_set, dict)
                            and fact_set.get("record_type")
                        }
                    ),
                    "virtual_tables": _question_virtual_tables(),
                }
            )
            return _record_result(snapshot)
        engine = create_engine(context.database_url, pool_pre_ping=True)
        try:
            with Session(engine) as database:
                fields = list(
                    database.execute(
                        select(
                            SemanticField.code,
                            SemanticFieldVersion.version,
                            SemanticFieldVersion.name,
                            SemanticFieldVersion.description,
                            SemanticFieldVersion.data_type,
                            SemanticFieldVersion.unit_dimension,
                            SemanticFieldVersion.aliases,
                        )
                        .join(
                            SemanticFieldVersion,
                            SemanticFieldVersion.field_id == SemanticField.id,
                        )
                        .where(
                            SemanticField.published_version
                            == SemanticFieldVersion.version
                        )
                        .order_by(SemanticField.code)
                    ).all()
                )
                metrics = list(
                    database.scalars(
                        select(MetricDefinition)
                        .where(MetricDefinition.enabled.is_(True))
                        .order_by(MetricDefinition.code)
                    )
                )
                units = list(
                    database.scalars(
                        select(AdministrativeUnit)
                        .where(
                            AdministrativeUnit.tenant_id == context.tenant_id,
                            AdministrativeUnit.id.in_(
                                context.administrative_unit_ids
                            ),
                        )
                        .order_by(AdministrativeUnit.name)
                    )
                )
                record_type_query = select(
                    distinct(DatasetRecord.record_type)
                ).where(
                    DatasetRecord.tenant_id == context.tenant_id,
                    DatasetRecord.administrative_unit_id.in_(
                        context.administrative_unit_ids
                    ),
                    DatasetRecord.quality_status == "passed",
                )
                if context.source_item_ids:
                    record_type_query = record_type_query.where(
                        DatasetRecord.item_id.in_(context.source_item_ids)
                    )
                record_types = list(
                    database.scalars(
                        record_type_query.order_by(DatasetRecord.record_type)
                    )
                )
        finally:
            engine.dispose()
        return _record_result(
            {
                "status": "success",
                "tool": "describe_query_schema",
                "contract_version": "village-query-catalog/v1",
                "scope": {
                    "administrative_units": [unit.name for unit in units],
                    "fact_storage_level": "village",
                    "aggregation_rule": (
                        "The authorized unit list is complete. Query all listed "
                        "villages for a whole-scope answer; never filter by a "
                        "township or tenant container name."
                    ),
                    "source_mode": (
                        "selected_file"
                        if context.source_item_ids
                        else "all_approved_files"
                    ),
                },
                "available_record_types": record_types,
                "virtual_tables": _question_virtual_tables(),
                "fields": [
                    {
                        "code": row.code,
                        "version": row.version,
                        "name": row.name,
                        "description": row.description,
                        "data_type": row.data_type,
                        "unit": row.unit_dimension,
                        "aliases": row.aliases,
                    }
                    for row in fields
                ],
                "metrics": [
                    {
                        "code": metric.code,
                        "name": metric.name,
                        "description": metric.description,
                        "aggregation": metric.aggregation,
                        "unit": metric.unit,
                        "aliases": metric.aliases,
                        "allowed_filter_fields": metric.allowed_filter_fields,
                    }
                    for metric in metrics
                ],
            }
        )
    except Exception as exc:
        return _error_result("describe_query_schema", exc)


def _question_virtual_tables() -> dict[str, list[str]]:
    return {
        "question_records": [
            "record_id",
            "item_id",
            "administrative_unit",
            "record_type",
            "sheet_id",
            "region_id",
            "source_row",
            "template_version",
            "created_at",
        ],
        "question_values": [
            "record_id",
            "item_id",
            "administrative_unit",
            "record_type",
            "field_code",
            "field_version",
            "role",
            "data_type",
            "text_value",
            "integer_value",
            "decimal_value",
            "boolean_value",
            "date_value",
            "datetime_value",
        ],
        "question_lineage": [
            "record_id",
            "field_code",
            "source_sha256",
            "sheet_id",
            "source_cell_id",
            "coordinate",
            "display_value",
        ],
        "question_sources": [
            "item_id",
            "file_name",
            "relative_path",
        ],
    }


def _handle_query_metric(args: dict[str, Any], **_kwargs: Any) -> str:
    try:
        context = _require_context()
        query = MetricQuery.model_validate(
            {
                "metric_code": args.get("metric_code"),
                "metric_version": args.get("metric_version"),
                "filters": args.get("filters") or [],
            }
        )
        engine = create_engine(context.database_url, pool_pre_ping=True)
        try:
            with Session(engine) as database:
                answer = execute_metric_query(
                    database,
                    query,
                    MetricQueryScope(
                        tenant_id=context.tenant_id,
                        administrative_unit_ids=frozenset(
                            context.administrative_unit_ids
                        ),
                        source_item_ids=frozenset(context.source_item_ids),
                        source_scope_enforced=context.source_scope_enforced,
                        record_created_before=context.record_created_before,
                    ),
                )
        finally:
            engine.dispose()
        return _record_result(
            {
                "status": "success",
                "tool": "query_metric",
                "contract_version": "metric-answer/v1",
                "query_run_id": str(context.run_id),
                "result_type": "metric",
                "result_grade": answer.result_grade,
                "metric": answer.model_dump(mode="json"),
                "evidence_summary": {
                    "record_count": answer.record_count,
                    "source_file_count": answer.source_file_count,
                },
            }
        )
    except Exception as exc:
        return _error_result("query_metric", exc)


def _handle_execute_safe_query(
    args: dict[str, Any],
    **_kwargs: Any,
) -> str:
    try:
        context = _require_context()
        plan = SafeQueryPlan.model_validate(args)
        engine = create_engine(context.database_url, pool_pre_ping=True)
        try:
            with Session(engine) as database:
                answer = execute_safe_query(
                    database,
                    plan,
                    catalog_snapshot=context.catalog_snapshot,
                    scope_snapshot_fingerprint=str(
                        context.catalog_snapshot.get(
                            "source_item_fingerprint",
                            "",
                        )
                    ),
                    scope=MetricQueryScope(
                        tenant_id=context.tenant_id,
                        administrative_unit_ids=frozenset(
                            context.administrative_unit_ids
                        ),
                        source_item_ids=frozenset(context.source_item_ids),
                        source_scope_enforced=context.source_scope_enforced,
                        record_created_before=context.record_created_before,
                    ),
                )
        finally:
            engine.dispose()
        payload = answer.model_dump(mode="json")
        return _record_result(
            {
                "status": "success",
                "tool": "execute_safe_query",
                "query_run_id": str(context.run_id),
                "safe_query_plan": plan.model_dump(mode="json"),
                **payload,
                "evidence_summary": {
                    "record_count": answer.record_count,
                    "source_file_count": answer.source_file_count,
                    "data_village_count": answer.data_village_count,
                },
            }
        )
    except Exception as exc:
        return _error_result("execute_safe_query", exc)


def _execute_catalog_plan(
    args: dict[str, Any],
    *,
    tool_name: str,
) -> str:
    try:
        context = _require_context()
        plan = CatalogQueryPlan.model_validate(args)
        engine = create_engine(context.database_url, pool_pre_ping=True)
        try:
            with Session(engine) as database:
                answer = execute_catalog_query(
                    database,
                    plan,
                    catalog_snapshot=context.catalog_snapshot,
                    scope=MetricQueryScope(
                        tenant_id=context.tenant_id,
                        administrative_unit_ids=frozenset(
                            context.administrative_unit_ids
                        ),
                        source_item_ids=frozenset(
                            context.source_item_ids
                        ),
                        source_scope_enforced=context.source_scope_enforced,
                        record_created_before=context.record_created_before,
                    ),
                )
        finally:
            engine.dispose()
        payload = answer.model_dump(mode="json")
        if (
            answer.result_type in {"record", "table"}
            and answer.record_count == 0
        ):
            payload["acceptance_status"] = "empty"
        return _record_result(
            {
                "status": "success",
                "tool": tool_name,
                "query_run_id": str(context.run_id),
                "fact_set_code": plan.fact_set_code,
                "catalog_query_plan": plan.model_dump(mode="json"),
                **payload,
                "evidence_summary": {
                    "record_count": answer.record_count,
                    "source_file_count": answer.source_file_count,
                    "data_village_count": answer.data_village_count,
                    "grouped_record_count": answer.grouped_record_count,
                    "ungrouped_record_count": answer.ungrouped_record_count,
                    "unexpected_group_values": (
                        answer.unexpected_group_values
                    ),
                },
            }
        )
    except Exception as exc:
        return _error_result(tool_name, exc)


def _handle_execute_bounded_query(
    args: dict[str, Any],
    **_kwargs: Any,
) -> str:
    return _execute_catalog_plan(args, tool_name="execute_bounded_query")


def _handle_lookup_records(
    args: dict[str, Any],
    **_kwargs: Any,
) -> str:
    if args.get("operation") not in {"lookup", "list"}:
        return _error_result(
            "lookup_records",
            QuestionToolError("lookup_records only supports lookup or list"),
        )
    return _execute_catalog_plan(args, tool_name="lookup_records")


def _handle_aggregate_records(
    args: dict[str, Any],
    **_kwargs: Any,
) -> str:
    if args.get("operation") not in {"count", "group_count"}:
        return _error_result(
            "aggregate_records",
            QuestionToolError(
                "aggregate_records only supports count or group_count"
            ),
        )
    return _execute_catalog_plan(args, tool_name="aggregate_records")


def _handle_summarize_values(
    args: dict[str, Any],
    **_kwargs: Any,
) -> str:
    if args.get("operation") != "aggregate":
        return _error_result(
            "summarize_values",
            QuestionToolError(
                "summarize_values only supports aggregate"
            ),
        )
    return _execute_catalog_plan(args, tool_name="summarize_values")


def _handle_rank_records(
    args: dict[str, Any],
    **_kwargs: Any,
) -> str:
    if args.get("operation") != "rank":
        return _error_result(
            "rank_records",
            QuestionToolError("rank_records only supports rank"),
        )
    return _execute_catalog_plan(args, tool_name="rank_records")


def _household_fact_sets(
    catalog_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    required = {
        "household.number",
        "household.relationship_to_head",
        "person.name",
    }
    return [
        fact_set
        for fact_set in catalog_snapshot.get("fact_sets", [])
        if isinstance(fact_set, dict)
        and required.issubset(set(fact_set.get("field_codes", [])))
    ]


def _question_metric_scope(context: QuestionToolContext) -> MetricQueryScope:
    return MetricQueryScope(
        tenant_id=context.tenant_id,
        administrative_unit_ids=frozenset(
            context.administrative_unit_ids
        ),
        source_item_ids=frozenset(context.source_item_ids),
        source_scope_enforced=context.source_scope_enforced,
        record_created_before=context.record_created_before,
    )


def _handle_query_household(
    args: dict[str, Any],
    **_kwargs: Any,
) -> str:
    try:
        context = _require_context()
        lookup_kind = str(args.get("lookup_kind") or "")
        lookup_value = str(args.get("lookup_value") or "").strip()
        result_kind = str(args.get("result_kind") or "")
        if lookup_kind not in {"household_number", "person_name"}:
            raise QuestionToolError("household lookup kind is invalid")
        if result_kind not in {"household_head", "household_members"}:
            raise QuestionToolError("household result kind is invalid")
        if not lookup_value:
            raise QuestionToolError("household lookup value is required")
        fact_sets = _household_fact_sets(context.catalog_snapshot)
        if not fact_sets:
            raise QuestionToolError(
                "the frozen catalog has no household-compatible fact set"
            )
        scope = _question_metric_scope(context)
        rows_by_record: dict[str, dict[str, Any]] = {}
        item_ids: set[str] = set()
        villages_with_data = 0
        engine = create_engine(context.database_url, pool_pre_ping=True)
        try:
            with Session(engine) as database:
                for fact_set in fact_sets:
                    fact_set_fields = set(fact_set.get("field_codes", []))
                    household_select = [
                        "household.number",
                        "household.relationship_to_head",
                        "person.name",
                    ]
                    if "person.sex" in fact_set_fields:
                        household_select.append("person.sex")
                    household_numbers = [lookup_value]
                    if lookup_kind == "person_name":
                        person_answer = execute_catalog_query(
                            database,
                            CatalogQueryPlan(
                                operation="list",
                                fact_set_code=str(fact_set["code"]),
                                select=["household.number"],
                                filters=[
                                    {
                                        "field_code": "person.name",
                                        "operator": "eq",
                                        "value": lookup_value,
                                    }
                                ],
                                limit=200,
                            ),
                            catalog_snapshot=context.catalog_snapshot,
                            scope=scope,
                        )
                        household_numbers = sorted(
                            {
                                str(row["household.number"])
                                for row in person_answer.rows
                                if row.get("household.number")
                                not in (None, "")
                            }
                        )
                    for household_number in household_numbers:
                        filters: list[dict[str, Any]] = [
                            {
                                "field_code": "household.number",
                                "operator": "eq",
                                "value": household_number,
                            }
                        ]
                        if result_kind == "household_head":
                            filters.append(
                                {
                                    "field_code": (
                                        "household.relationship_to_head"
                                    ),
                                    "operator": "eq",
                                    "value": "户主",
                                }
                            )
                        answer = execute_catalog_query(
                            database,
                            CatalogQueryPlan(
                                operation="list",
                                fact_set_code=str(fact_set["code"]),
                                select=household_select,
                                filters=filters,
                                limit=200,
                            ),
                            catalog_snapshot=context.catalog_snapshot,
                            scope=scope,
                        )
                        villages_with_data += answer.data_village_count
                        for row in answer.rows:
                            record_id = str(row["record_id"])
                            rows_by_record[record_id] = {
                                **row,
                                "fact_set_code": fact_set["code"],
                            }
                            item_ids.add(str(row["item_id"]))
        finally:
            engine.dispose()
        rows = list(rows_by_record.values())
        data_village_count = min(
            villages_with_data,
            len(context.administrative_unit_ids),
        )
        return _record_result(
            {
                "status": "success",
                "tool": "query_household",
                "contract_version": "household-query-answer/v1",
                "result_type": (
                    "record"
                    if result_kind == "household_head"
                    else "table"
                ),
                "result_grade": "contract_query",
                "acceptance_status": "accepted" if rows else "empty",
                "rows": rows,
                "record_count": len(rows),
                "source_file_count": len(item_ids),
                "data_village_count": data_village_count,
                "evidence_summary": {
                    "record_count": len(rows),
                    "source_file_count": len(item_ids),
                    "data_village_count": data_village_count,
                },
            }
        )
    except Exception as exc:
        return _error_result("query_household", exc)


def _source_field_cells(
    raw_data: object,
    *,
    header_terms: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    if not isinstance(raw_data, dict):
        return [], []
    columns = raw_data.get("columns")
    if not isinstance(columns, dict):
        return [], []
    normalized_terms = [
        term.strip().casefold() for term in header_terms if term.strip()
    ]
    available_headers: list[str] = []
    selected_cells: list[dict[str, Any]] = []
    for column in columns.values():
        if not isinstance(column, dict):
            continue
        header_path = [
            str(part).strip()
            for part in column.get("header_path", [])
            if str(part).strip()
        ]
        header = " / ".join(header_path)
        if header and header not in available_headers:
            available_headers.append(header)
        if not normalized_terms:
            continue
        normalized_header = header.casefold()
        if not any(term in normalized_header for term in normalized_terms):
            continue
        source_cell = column.get("source_cell")
        if not isinstance(source_cell, dict):
            continue
        value = source_cell.get("display_value")
        if value in (None, ""):
            value = source_cell.get("raw_value")
        selected_cells.append(
            {
                "header_path": header_path,
                "value": value,
                "coordinate": source_cell.get("coordinate"),
            }
        )
    return available_headers, selected_cells


def _normalized_source_text(value: object) -> str:
    return re.sub(r"[\s,，。.;；:：/\\\-—_]+", "", str(value or "")).casefold()


def _handle_describe_source_fields(
    args: dict[str, Any],
    **_kwargs: Any,
) -> str:
    try:
        context = _require_context()
        requested_limit = int(args.get("limit") or 50)
        result_limit = min(max(requested_limit, 1), 100)
        predicates: list[Any] = [
            DatasetRecord.tenant_id == context.tenant_id,
            DatasetRecord.administrative_unit_id.in_(
                context.administrative_unit_ids
            ),
            DatasetRecord.quality_status == "passed",
        ]
        if context.source_scope_enforced or context.source_item_ids:
            predicates.append(
                DatasetRecord.item_id.in_(context.source_item_ids)
            )
        if context.record_created_before is not None:
            predicates.append(
                DatasetRecord.created_at <= context.record_created_before
            )
        statement = (
            select(
                DatasetRecord,
                IngestionItem.original_name,
            )
            .join(IngestionItem, IngestionItem.id == DatasetRecord.item_id)
            .where(*predicates)
            .order_by(
                IngestionItem.original_name,
                DatasetRecord.sheet_id,
                DatasetRecord.source_row,
            )
            .limit(5_000)
        )
        engine = create_engine(context.database_url, pool_pre_ping=True)
        try:
            with Session(engine) as database:
                records = list(database.execute(statement))
        finally:
            engine.dispose()
        grouped: dict[tuple[str, str, str], set[str]] = {}
        for record, file_name in records:
            key = (str(record.item_id), str(file_name), record.sheet_id)
            headers, _ = _source_field_cells(
                record.raw_data,
                header_terms=[],
            )
            grouped.setdefault(key, set()).update(headers)
        rows = [
            {
                "item_id": item_id,
                "file_name": file_name,
                "sheet_id": sheet_id,
                "source_headers": sorted(headers),
            }
            for (item_id, file_name, sheet_id), headers in grouped.items()
        ][:result_limit]
        source_count = len({row["item_id"] for row in rows})
        return _record_result(
            {
                "status": "success",
                "tool": "describe_source_fields",
                "contract_version": "source-field-catalog/v1",
                "result_type": "table",
                "result_grade": "bounded_plan",
                "acceptance_status": "accepted" if rows else "empty",
                "rows": rows,
                "record_count": len(rows),
                "source_file_count": source_count,
                "data_village_count": len(
                    context.administrative_unit_ids
                )
                if rows
                else 0,
                "evidence_summary": {
                    "record_count": len(rows),
                    "source_file_count": source_count,
                    "data_village_count": len(
                        context.administrative_unit_ids
                    )
                    if rows
                    else 0,
                },
            }
        )
    except Exception as exc:
        return _error_result("describe_source_fields", exc)


def _record_matches_source_filters(
    raw_data: object,
    source_filters: list[dict[str, str]],
) -> bool:
    if not source_filters:
        return True
    if not isinstance(raw_data, dict):
        return False
    columns = raw_data.get("columns")
    if not isinstance(columns, dict):
        return False
    for source_filter in source_filters:
        header_terms = [
            term
            for term in source_filter["header_terms"].split("\n")
            if term
        ]
        values: list[str] = []
        for column in columns.values():
            if not isinstance(column, dict):
                continue
            header = " / ".join(
                str(part).strip()
                for part in column.get("header_path", [])
                if str(part).strip()
            ).casefold()
            if not any(term in header for term in header_terms):
                continue
            source_cell = column.get("source_cell")
            if not isinstance(source_cell, dict):
                continue
            value = source_cell.get("display_value")
            if value in (None, ""):
                value = source_cell.get("raw_value")
            if value not in (None, ""):
                values.append(_normalized_source_text(value))
        expected = _normalized_source_text(source_filter["value"])
        combined = "".join(values)
        if source_filter["operator"] == "eq":
            matched = expected in values or expected == combined
        else:
            matched = any(expected in value for value in values) or (
                expected in combined
            )
        if not matched:
            return False
    return True


def _handle_lookup_source_records(
    args: dict[str, Any],
    **_kwargs: Any,
) -> str:
    try:
        context = _require_context()
        filters = args.get("filters") or []
        source_filters = args.get("source_filters") or []
        if not isinstance(filters, list) or len(filters) > 4:
            raise QuestionToolError(
                "lookup_source_records accepts at most four semantic filters"
            )
        if not isinstance(source_filters, list) or len(source_filters) > 4:
            raise QuestionToolError(
                "lookup_source_records accepts at most four source filters"
            )
        if not filters and not source_filters:
            raise QuestionToolError(
                "lookup_source_records requires a semantic or source filter"
            )
        catalog_field_codes = {
            str(field.get("code"))
            for field in context.catalog_snapshot.get("fields", [])
            if isinstance(field, dict) and field.get("code")
        }
        query = (
            select(
                DatasetRecord,
                IngestionItem.original_name,
                AdministrativeUnit.name.label("village_name"),
            )
            .join(IngestionItem, IngestionItem.id == DatasetRecord.item_id)
            .join(
                AdministrativeUnit,
                AdministrativeUnit.id == DatasetRecord.administrative_unit_id,
            )
            .where(
                DatasetRecord.tenant_id == context.tenant_id,
                DatasetRecord.administrative_unit_id.in_(
                    context.administrative_unit_ids
                ),
                DatasetRecord.quality_status == "passed",
            )
        )
        if context.source_scope_enforced or context.source_item_ids:
            query = query.where(
                DatasetRecord.item_id.in_(context.source_item_ids)
            )
        if context.record_created_before is not None:
            query = query.where(
                DatasetRecord.created_at <= context.record_created_before
            )
        normalized_filters: list[dict[str, str]] = []
        for index, raw_filter in enumerate(filters):
            if not isinstance(raw_filter, dict):
                raise QuestionToolError("semantic filter must be an object")
            field_code = str(raw_filter.get("field_code") or "").strip()
            operator = str(raw_filter.get("operator") or "eq")
            value = str(raw_filter.get("value") or "").strip()
            if field_code not in catalog_field_codes:
                raise QuestionToolError(
                    f"semantic filter field is unavailable: {field_code}"
                )
            if operator not in {"eq", "contains"}:
                raise QuestionToolError(
                    "semantic filter operator must be eq or contains"
                )
            if not value:
                raise QuestionToolError("semantic filter value is required")
            filter_value = aliased(
                RecordIndexValue,
                name=f"source_filter_{index}",
            )
            query = query.join(
                filter_value,
                filter_value.record_id == DatasetRecord.id,
            ).where(
                filter_value.semantic_field_code == field_code,
                filter_value.role == "",
            )
            if operator == "eq":
                query = query.where(filter_value.text_value == value)
            else:
                query = query.where(filter_value.text_value.contains(value))
            normalized_filters.append(
                {
                    "field_code": field_code,
                    "operator": operator,
                    "value": value,
                }
            )
        normalized_source_filters: list[dict[str, str]] = []
        for index, raw_filter in enumerate(source_filters):
            if not isinstance(raw_filter, dict):
                raise QuestionToolError("source filter must be an object")
            header_terms = [
                str(value).strip().casefold()
                for value in (raw_filter.get("header_terms") or [])
                if str(value).strip()
            ]
            operator = str(raw_filter.get("operator") or "contains")
            value = str(raw_filter.get("value") or "").strip()
            if not header_terms:
                raise QuestionToolError(
                    "source filter requires at least one header term"
                )
            if operator not in {"eq", "contains"}:
                raise QuestionToolError(
                    "source filter operator must be eq or contains"
                )
            if not value:
                raise QuestionToolError("source filter value is required")
            normalized_value = _normalized_source_text(value)
            probe = (
                normalized_value
                if operator == "eq" or len(normalized_value) <= 6
                else normalized_value[-6:]
            )
            filter_value = aliased(
                RecordIndexValue,
                name=f"raw_source_filter_{index}",
            )
            query = query.join(
                filter_value,
                filter_value.record_id == DatasetRecord.id,
            ).where(
                filter_value.role == "",
                filter_value.text_value.contains(probe),
            )
            normalized_source_filters.append(
                {
                    "header_terms": "\n".join(header_terms),
                    "operator": operator,
                    "value": value,
                }
            )
        requested_limit = int(args.get("limit") or 20)
        result_limit = min(max(requested_limit, 1), 100)
        header_terms = [
            str(value)
            for value in (args.get("source_header_terms") or [])
            if str(value).strip()
        ][:20]
        query = query.order_by(
            IngestionItem.original_name,
            DatasetRecord.sheet_id,
            DatasetRecord.source_row,
        ).limit(min(max(result_limit * 20, 100), 2_000))
        engine = create_engine(context.database_url, pool_pre_ping=True)
        try:
            with Session(engine) as database:
                matches = list(database.execute(query).all())
        finally:
            engine.dispose()
        rows: list[dict[str, Any]] = []
        source_ids: set[str] = set()
        villages: set[str] = set()
        for record, file_name, village_name in matches:
            if not _record_matches_source_filters(
                record.raw_data,
                normalized_source_filters,
            ):
                continue
            available_headers, selected_cells = _source_field_cells(
                record.raw_data,
                header_terms=header_terms,
            )
            rows.append(
                {
                    "record_id": str(record.id),
                    "item_id": str(record.item_id),
                    "file_name": file_name,
                    "village": village_name,
                    "sheet_id": record.sheet_id,
                    "source_row": record.source_row,
                    "available_source_headers": available_headers,
                    "source_fields": selected_cells,
                }
            )
            source_ids.add(str(record.item_id))
            villages.add(str(village_name))
            if len(rows) >= result_limit:
                break
        return _record_result(
            {
                "status": "success",
                "tool": "lookup_source_records",
                "contract_version": "source-record-lookup/v1",
                "result_type": "table",
                "result_grade": "bounded_plan",
                "acceptance_status": "accepted" if rows else "empty",
                "filters": normalized_filters,
                "source_filters": [
                    {
                        **source_filter,
                        "header_terms": source_filter[
                            "header_terms"
                        ].split("\n"),
                    }
                    for source_filter in normalized_source_filters
                ],
                "source_header_terms": header_terms,
                "rows": rows,
                "record_count": len(rows),
                "source_file_count": len(source_ids),
                "data_village_count": len(villages),
                "evidence_summary": {
                    "record_count": len(rows),
                    "source_file_count": len(source_ids),
                    "data_village_count": len(villages),
                },
            }
        )
    except Exception as exc:
        return _error_result("lookup_source_records", exc)


def _validate_query_sql(sql: str) -> None:
    if not sql or len(sql) > MAX_QUERY_CHARS:
        raise QuestionToolError("SQL is empty or exceeds the 8000 character limit")
    try:
        statements = parse(sql, read="postgres")
    except ParseError as exc:
        raise QuestionToolError(f"SQL cannot be parsed: {exc}") from exc
    if len(statements) != 1:
        raise QuestionToolError("exactly one SQL statement is required")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise QuestionToolError("only SELECT or WITH ... SELECT is allowed")
    blocked_nodes = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Command,
        exp.Copy,
        exp.Merge,
    )
    if any(statement.find(node_type) is not None for node_type in blocked_nodes):
        raise QuestionToolError("SQL contains a non-read-only operation")
    if re.search(r"\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b", sql, re.I):
        raise QuestionToolError("row locks are not allowed")

    cte_names = {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    table_names = {
        table.name.lower()
        for table in statement.find_all(exp.Table)
        if table.name
    }
    if not table_names.intersection(_ALLOWED_VIRTUAL_TABLES):
        raise QuestionToolError("query must read at least one question_* virtual table")
    unknown_tables = table_names - _ALLOWED_VIRTUAL_TABLES - cte_names
    if unknown_tables:
        raise QuestionToolError(
            "query references unavailable tables: "
            + ", ".join(sorted(unknown_tables))
        )
    for function in statement.find_all(exp.Func):
        name = (
            str(function.name)
            if isinstance(function, exp.Anonymous)
            else str(function.sql_name())  # type: ignore[no-untyped-call]
        ).lower()
        if name in _BLOCKED_FUNCTIONS or name.startswith(_BLOCKED_FUNCTION_PREFIXES):
            raise QuestionToolError(f"function is not allowed: {name}")
def _validated_parameters(value: object) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise QuestionToolError("params must be an object")
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if not _PARAMETER_NAME.fullmatch(key) or key.startswith("__vi_"):
            raise QuestionToolError(f"invalid query parameter name: {key[:80]}")
        if not isinstance(raw_value, (str, int, float, bool, list, type(None))):
            raise QuestionToolError(f"unsupported parameter value for: {key}")
        if isinstance(raw_value, list):
            if len(raw_value) > 200 or not all(
                isinstance(item, (str, int, float, bool, type(None)))
                for item in raw_value
            ):
                raise QuestionToolError(f"invalid list parameter: {key}")
        result[key] = raw_value
    return result


def _scoped_query(
    sql: str,
    *,
    filter_by_source: bool,
    filter_by_watermark: bool,
    provenance_kind: str | None = None,
) -> str:
    source_filter = (
        "\n      AND dr.item_id IN :__vi_source_item_ids"
        if filter_by_source
        else ""
    )
    watermark_filter = (
        "\n      AND dr.created_at <= :__vi_record_created_before"
        if filter_by_watermark
        else ""
    )
    fact_filter = "\n      AND dr.record_type = :__vi_fact_record_type"
    if provenance_kind == "region_template":
        fact_filter += (
            "\n      AND dr.region_template_id = :__vi_fact_provenance_id"
            "\n      AND dr.region_template_version = :__vi_fact_provenance_version"
        )
    elif provenance_kind == "document_template":
        fact_filter += (
            "\n      AND dr.template_id = :__vi_fact_provenance_id"
            "\n      AND dr.template_version = :__vi_fact_provenance_version"
        )
    elif provenance_kind == "approved_plan":
        fact_filter += (
            "\n      AND dr.approved_plan_id = :__vi_fact_provenance_id"
        )
    return f"""
WITH question_records AS (
    SELECT
        dr.id AS record_id,
        dr.item_id,
        au.name AS administrative_unit,
        dr.record_type,
        dr.sheet_id,
        dr.region_id,
        dr.source_row,
        dr.template_version,
        dr.created_at
    FROM dataset_records AS dr
    JOIN administrative_units AS au ON au.id = dr.administrative_unit_id
    WHERE dr.tenant_id = :__vi_tenant_id
      AND dr.administrative_unit_id IN :__vi_allowed_unit_ids
      AND dr.quality_status = 'passed'
      {fact_filter}
      {source_filter}
      {watermark_filter}
),
question_values AS (
    SELECT
        qr.record_id,
        qr.item_id,
        qr.administrative_unit,
        qr.record_type,
        riv.semantic_field_code AS field_code,
        riv.semantic_field_version AS field_version,
        riv.role,
        riv.data_type,
        riv.text_value,
        riv.integer_value,
        riv.decimal_value,
        riv.boolean_value,
        riv.date_value,
        riv.datetime_value
    FROM question_records AS qr
    JOIN record_index_values AS riv ON riv.record_id = qr.record_id
),
question_lineage AS (
    SELECT
        qr.record_id,
        riv.semantic_field_code AS field_code,
        lineage.source_sha256,
        lineage.sheet_id,
        lineage.source_cell_id,
        lineage.coordinate,
        lineage.display_value
    FROM question_records AS qr
    JOIN record_index_values AS riv ON riv.record_id = qr.record_id
    JOIN record_value_lineage AS lineage
      ON lineage.record_index_value_id = riv.id
),
question_sources AS (
    SELECT DISTINCT
        qr.item_id,
        item.original_name AS file_name,
        item.relative_path
    FROM question_records AS qr
    JOIN ingestion_items AS item ON item.id = qr.item_id
)
SELECT *
FROM (
{sql}
) AS requested_query
LIMIT :__vi_max_rows
"""


def _handle_query_postgres(args: dict[str, Any], **_kwargs: Any) -> str:
    try:
        context = _require_context()
        fact_set_code = str(args.get("fact_set_code") or "").strip()
        fact_sets = [
            fact_set
            for fact_set in context.catalog_snapshot.get("fact_sets", [])
            if isinstance(fact_set, dict)
            and fact_set.get("code") == fact_set_code
        ]
        if len(fact_sets) != 1:
            raise QuestionToolError(
                "query_postgres requires exactly one fact_set_code from "
                "the frozen catalog"
            )
        fact_set = fact_sets[0]
        provenance = fact_set.get("execution_provenance")
        if not isinstance(provenance, dict):
            raise QuestionToolError(
                "selected fact set has no executable provenance"
            )
        provenance_kind = str(provenance.get("kind") or "")
        if provenance_kind not in {
            "region_template",
            "document_template",
            "approved_plan",
        }:
            raise QuestionToolError("selected fact-set provenance is invalid")
        provenance_id = uuid.UUID(str(provenance.get("id")))
        sql = str(args.get("sql") or "").strip()
        _validate_query_sql(sql)
        parameters = _validated_parameters(args.get("params"))
        requested_limit = int(args.get("limit") or DEFAULT_QUERY_ROWS)
        result_limit = min(max(requested_limit, 1), MAX_QUERY_ROWS)
        parameters.update(
            {
                "__vi_tenant_id": context.tenant_id,
                "__vi_allowed_unit_ids": list(
                    context.administrative_unit_ids
                ),
                "__vi_max_rows": result_limit,
                "__vi_fact_record_type": str(fact_set["record_type"]),
                "__vi_fact_provenance_id": provenance_id,
            }
        )
        if provenance_kind in {"region_template", "document_template"}:
            parameters["__vi_fact_provenance_version"] = int(
                provenance["version"]
            )
        expanding_parameters: list[Any] = [
            bindparam(key, expanding=True)
            for key, value in parameters.items()
            if isinstance(value, list)
        ]
        scope_parameters: list[Any] = [
            bindparam("__vi_allowed_unit_ids", expanding=True),
        ]
        filter_by_source = (
            context.source_scope_enforced or bool(context.source_item_ids)
        )
        if filter_by_source:
            parameters["__vi_source_item_ids"] = list(
                context.source_item_ids
            )
            scope_parameters.append(
                bindparam("__vi_source_item_ids", expanding=True)
            )
        if context.record_created_before is not None:
            parameters["__vi_record_created_before"] = (
                context.record_created_before
            )
        statement = text(
            _scoped_query(
                sql,
                filter_by_source=filter_by_source,
                filter_by_watermark=context.record_created_before is not None,
                provenance_kind=provenance_kind,
            )
        ).bindparams(*scope_parameters, *expanding_parameters)
        engine = create_engine(context.database_url, pool_pre_ping=True)
        try:
            if engine.dialect.name == "sqlite":
                parameters["__vi_tenant_id"] = context.tenant_id.hex
                parameters["__vi_allowed_unit_ids"] = [
                    unit_id.hex for unit_id in context.administrative_unit_ids
                ]
                parameters["__vi_fact_provenance_id"] = provenance_id.hex
                if filter_by_source:
                    parameters["__vi_source_item_ids"] = [
                        item_id.hex for item_id in context.source_item_ids
                    ]
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    if connection.dialect.name == "postgresql":
                        connection.execute(
                            text(
                                "SET LOCAL statement_timeout = "
                                f"'{STATEMENT_TIMEOUT_MS}ms'"
                            )
                        )
                        connection.execute(
                            text("SET TRANSACTION READ ONLY")
                        )
                    rows = connection.execute(statement, parameters).mappings().all()
                finally:
                    transaction.rollback()
        finally:
            engine.dispose()
        rendered_rows = [dict(row) for row in rows]
        columns = list(rendered_rows[0]) if rendered_rows else []
        source_ids = {
            str(row["item_id"])
            for row in rendered_rows
            if row.get("item_id") not in (None, "")
        }
        record_ids = {
            str(row["record_id"])
            for row in rendered_rows
            if row.get("record_id") not in (None, "")
        }
        aggregate_record_count = None
        aggregate_source_count = None
        aggregate_village_count = None
        if len(rendered_rows) == 1:
            row = rendered_rows[0]
            if isinstance(row.get("record_count"), int):
                aggregate_record_count = row["record_count"]
            if isinstance(row.get("source_file_count"), int):
                aggregate_source_count = row["source_file_count"]
            if isinstance(row.get("data_village_count"), int):
                aggregate_village_count = row["data_village_count"]
        return _record_result(
            {
                "status": "success",
                "tool": "query_postgres",
                "contract_version": "postgres-query-result/v1",
                "query_run_id": str(context.run_id),
                "fact_set_code": fact_set_code,
                "result_type": "table",
                "result_grade": "bounded_sql",
                "acceptance_status": "accepted",
                "columns": columns,
                "rows": rendered_rows,
                "row_count": len(rendered_rows),
                "truncated": len(rendered_rows) >= result_limit,
                "evidence_summary": {
                    "record_count": (
                        aggregate_record_count
                        if aggregate_record_count is not None
                        else (
                            len(record_ids)
                            or int(fact_set.get("record_count") or 0)
                        )
                    ),
                    "source_file_count": (
                        aggregate_source_count
                        if aggregate_source_count is not None
                        else (
                            len(source_ids)
                            or int(fact_set.get("source_file_count") or 0)
                        )
                    ),
                    "data_village_count": (
                        aggregate_village_count
                        if aggregate_village_count is not None
                        else int(
                            fact_set.get("administrative_unit_count") or 0
                        )
                    ),
                },
            }
        )
    except Exception as exc:
        return _error_result("query_postgres", exc)


DESCRIBE_QUERY_SCHEMA = {
    "name": "describe_query_schema",
    "description": (
        "Search the published query catalog and return matching fact sets, "
        "fields, authorized scope, record types, and question_* virtual-table "
        "columns. Provide every semantic field needed by the question in "
        "field_codes so the catalog is not truncated. Use search only when an "
        "exact field code is unknown. Call again with narrower filters when "
        "catalog_match reports truncation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "field_codes": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 12,
                "description": (
                    "Exact semantic field codes that must all exist in each "
                    "returned fact set, for example household.number."
                ),
            },
            "search": {
                "type": "string",
                "description": (
                    "Optional name, alias, business term, or code fragment."
                ),
            },
        },
    },
}

QUERY_METRIC_SCHEMA = {
    "name": "query_metric",
    "description": (
        "Execute one published deterministic metric. Use this for official "
        "counts, totals, averages, minimums, and maximums whenever a matching "
        "metric exists. Never calculate or rewrite the returned number."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "metric_code": {
                "type": "string",
                "description": "Exact published metric code.",
            },
            "metric_version": {
                "type": "integer",
                "minimum": 1,
                "description": "Exact immutable metric version from the catalog.",
            },
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_code": {"type": "string"},
                        "operator": {
                            "type": "string",
                            "enum": ["eq"],
                        },
                        "value": {"type": "string"},
                    },
                    "required": ["field_code", "value"],
                },
            },
        },
        "required": ["metric_code"],
    },
}

EXECUTE_SAFE_QUERY_SCHEMA = {
    "name": "execute_safe_query",
    "description": (
        "Execute a deterministic structured query against one published fact "
        "set and semantic manifest. Use only when governance_status is "
        "published and semantic_manifest_code is present. Prefer this over "
        "query_postgres for such governed fact sets. Never provide SQL."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "contract_version": {
                "type": "string",
                "enum": ["safe-query/v1"],
            },
            "operation": {
                "type": "string",
                "enum": ["lookup", "list", "count", "aggregate", "group_by"],
            },
            "fact_set_code": {"type": "string"},
            "fact_set_version": {"type": "integer", "minimum": 1},
            "record_type": {"type": "string"},
            "select": {
                "type": "array",
                "items": {"type": "string"},
            },
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_code": {"type": "string"},
                        "operator": {
                            "type": "string",
                            "enum": [
                                "eq",
                                "in",
                                "gt",
                                "gte",
                                "lt",
                                "lte",
                                "contains",
                            ],
                        },
                        "value": {},
                    },
                    "required": ["field_code", "value"],
                },
            },
            "group_by": {
                "type": "array",
                "items": {"type": "string"},
            },
            "order_by": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_code": {"type": "string"},
                        "direction": {
                            "type": "string",
                            "enum": ["asc", "desc"],
                        },
                    },
                    "required": ["field_code"],
                },
            },
            "measure_field_code": {"type": "string"},
            "aggregation": {
                "type": "string",
                "enum": ["count", "sum", "avg", "min", "max"],
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": [
            "contract_version",
            "operation",
            "fact_set_code",
            "fact_set_version",
            "record_type",
        ],
    },
}

EXECUTE_BOUNDED_QUERY_SCHEMA: dict[str, Any] = {
    "name": "execute_bounded_query",
    "description": (
        "Execute a backend-compiled structured query against one frozen "
        "derived or published fact set. Use this for lookup, list, count, or "
        "grouped record count when no published Semantic Manifest is "
        "available. For group_count, set group_by and include every category "
        "explicitly requested by the user in requested_group_values, using "
        "the exact canonical stored values shown by the field semantics. Do "
        "not add synonyms or speculative categories. The "
        "backend rejects null or unexpected group coverage. Never provide SQL."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "contract_version": {
                "type": "string",
                "enum": ["catalog-query/v1"],
            },
            "operation": {
                "type": "string",
                "enum": ["lookup", "list", "count", "group_count"],
            },
            "fact_set_code": {"type": "string"},
            "select": {
                "type": "array",
                "items": {"type": "string"},
            },
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_code": {"type": "string"},
                        "operator": {
                            "type": "string",
                            "enum": [
                                "eq",
                                "in",
                                "gt",
                                "gte",
                                "lt",
                                "lte",
                                "contains",
                            ],
                        },
                        "value": {},
                    },
                    "required": ["field_code", "value"],
                },
            },
            "group_by": {"type": "string"},
            "requested_group_values": {
                "type": "array",
                "items": {},
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
            },
        },
        "required": [
            "contract_version",
            "operation",
            "fact_set_code",
        ],
    },
}

LOOKUP_RECORDS_SCHEMA = {
    "name": "lookup_records",
    "description": (
        "Look up a named person, household, organization, or matching records "
        "inside one fact set. Use this for detail questions that ask who, "
        "whether, when, where, status, or several attributes. Set operation "
        "to lookup only when exactly one record is requested. Set operation "
        "to list for all matching records, member lists, rosters, or questions "
        "containing all/every/which people. The backend compiles and validates "
        "the query; never provide SQL."
    ),
    "parameters": {
        **EXECUTE_BOUNDED_QUERY_SCHEMA["parameters"],
        "properties": {
            **EXECUTE_BOUNDED_QUERY_SCHEMA["parameters"]["properties"],
            "operation": {
                "type": "string",
                "enum": ["lookup", "list"],
            },
        },
    },
}

AGGREGATE_RECORDS_SCHEMA = {
    "name": "aggregate_records",
    "description": (
        "Count matching records or group counts by one published field inside "
        "one fact set. Use this for how many, category distribution, sex, "
        "branch, type, status, or similar grouped-count questions. The backend "
        "compiles and validates the query; never provide SQL."
    ),
    "parameters": {
        **EXECUTE_BOUNDED_QUERY_SCHEMA["parameters"],
        "properties": {
            **EXECUTE_BOUNDED_QUERY_SCHEMA["parameters"]["properties"],
            "operation": {
                "type": "string",
                "enum": ["count", "group_count"],
            },
        },
    },
}

SUMMARIZE_VALUES_SCHEMA = {
    "name": "summarize_values",
    "description": (
        "Calculate sum, average, minimum, or maximum for one numeric field, "
        "optionally grouped by one category. Use this for subsidy, income, "
        "area, quantity, or other numeric aggregate questions. The backend "
        "compiles and validates the query; never provide SQL."
    ),
    "parameters": {
        **EXECUTE_BOUNDED_QUERY_SCHEMA["parameters"],
        "properties": {
            **EXECUTE_BOUNDED_QUERY_SCHEMA["parameters"]["properties"],
            "operation": {
                "type": "string",
                "enum": ["aggregate"],
            },
            "measure_field_code": {"type": "string"},
            "aggregation": {
                "type": "string",
                "enum": ["sum", "avg", "min", "max"],
            },
        },
        "required": [
            "contract_version",
            "operation",
            "fact_set_code",
            "measure_field_code",
            "aggregation",
        ],
    },
}

RANK_RECORDS_SCHEMA = {
    "name": "rank_records",
    "description": (
        "Return the top or bottom records from one fact set by a numeric, date, "
        "or other typed field. Use this for highest, lowest, oldest, youngest, "
        "most, least, top-N, and bottom-N questions. The backend compiles and "
        "validates the query; never provide SQL."
    ),
    "parameters": {
        **EXECUTE_BOUNDED_QUERY_SCHEMA["parameters"],
        "properties": {
            **EXECUTE_BOUNDED_QUERY_SCHEMA["parameters"]["properties"],
            "operation": {
                "type": "string",
                "enum": ["rank"],
            },
            "order_by_field_code": {"type": "string"},
            "order_direction": {
                "type": "string",
                "enum": ["asc", "desc"],
            },
        },
        "required": [
            "contract_version",
            "operation",
            "fact_set_code",
            "select",
            "order_by_field_code",
            "order_direction",
        ],
    },
}

QUERY_HOUSEHOLD_SCHEMA = {
    "name": "query_household",
    "description": (
        "Resolve household membership across every compatible frozen fact set "
        "without guessing a fact-set code. Use this first for a household "
        "number, household head, household members, or a person's household. "
        "It returns the household number, relationship to head, name, and "
        "published sex field when that field exists."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "lookup_kind": {
                "type": "string",
                "enum": ["household_number", "person_name"],
            },
            "lookup_value": {"type": "string"},
            "result_kind": {
                "type": "string",
                "enum": ["household_head", "household_members"],
            },
        },
        "required": [
            "lookup_kind",
            "lookup_value",
            "result_kind",
        ],
    },
}

DESCRIBE_SOURCE_FIELDS_SCHEMA = {
    "name": "describe_source_fields",
    "description": (
        "List original Excel header paths grouped by source file and Sheet in "
        "the current frozen scope. Use this when the user asks what a table "
        "contains or which fields must be filled, or before a raw source lookup "
        "when semantic field names are unknown."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 50,
            },
        },
    },
}

LOOKUP_SOURCE_RECORDS_SCHEMA = {
    "name": "lookup_source_records",
    "description": (
        "Locate matching records across every approved fact set in the frozen "
        "tenant/village/file scope, then return selected original spreadsheet "
        "headers and cell values from those rows. Filter by stable semantic "
        "fields, original source headers, or both. Use source_filters when an "
        "address, date, status, or other field still has a temporary semantic "
        "code. Source filters concatenate values from all matching headers in "
        "one row, so a complete address split across several columns can match. "
        "source_header_terms are case-insensitive header fragments; for "
        "example a broad address term can return all address components. Include "
        "the answer columns such as name and relationship in source_header_terms. "
        "If source_header_terms is empty, only available header names are "
        "returned and source values remain hidden."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "filters": {
                "type": "array",
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "field_code": {"type": "string"},
                        "operator": {
                            "type": "string",
                            "enum": ["eq", "contains"],
                        },
                        "value": {"type": "string"},
                    },
                    "required": ["field_code", "operator", "value"],
                },
            },
            "source_filters": {
                "type": "array",
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "header_terms": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 8,
                            "items": {"type": "string"},
                        },
                        "operator": {
                            "type": "string",
                            "enum": ["eq", "contains"],
                        },
                        "value": {"type": "string"},
                    },
                    "required": ["header_terms", "operator", "value"],
                },
            },
            "source_header_terms": {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string"},
                "description": (
                    "Original spreadsheet header fragments whose values are "
                    "needed for the answer."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
            },
        },
        "anyOf": [
            {"required": ["filters"]},
            {"required": ["source_filters"]},
        ],
    },
}

QUERY_POSTGRES_SCHEMA = {
    "name": "query_postgres",
    "description": (
        "Execute one bounded read-only PostgreSQL SELECT against only "
        "question_records, question_values, question_lineage, or "
        "question_sources. Use named :parameters for user-provided values. "
        "For detail/list evidence, include record_id and item_id in the result. "
        "Grouped aggregates, joins, ordering, and top-N queries are supported; "
        "name every calculated column with an informative alias. Evidence "
        "coverage is derived by the backend from the frozen fact set, so a "
        "grouped result does not need extra evidence-only columns. "
        "Always select exactly one fact_set_code from describe_query_schema. "
        "fact_set_code is a tool argument, not a column in any question_* "
        "virtual table; the backend injects its record type and provenance, "
        "so never add a fact_set_code SQL predicate. "
        "The backend validates and scopes this generated SQL before execution; "
        "a successful result is accepted as answer evidence. "
        "Do not use this tool to replace a published official metric."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "fact_set_code": {
                "type": "string",
                "description": (
                    "Exactly one fact-set code returned by "
                    "describe_query_schema."
                ),
            },
            "sql": {
                "type": "string",
                "description": "One SELECT or WITH ... SELECT statement.",
            },
            "params": {
                "type": "object",
                "description": (
                    "Named scalar/list parameters referenced by SQL. Use "
                    "`column IN :name` for list parameters."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_QUERY_ROWS,
                "default": DEFAULT_QUERY_ROWS,
            },
        },
        "required": ["fact_set_code", "sql"],
    },
}
