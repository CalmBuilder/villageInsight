from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from village_insight.api.dependencies import CurrentPrincipal, Database
from village_insight.config import get_settings
from village_insight.db.models import (
    AdministrativeUnit,
    AdministrativeUnitType,
    DatasetRecord,
    IngestionItem,
    MembershipRole,
    QuestionConversation,
    QuestionFactResult,
    QuestionRun,
    UserStatus,
    utcnow,
)
from village_insight.db.session import get_session_factory
from village_insight.hermes.configuration import resolve_configuration
from village_insight.hermes.runtime import (
    EmbeddedHermesRuntime,
    HermesCallPolicy,
    HermesUnavailableError,
    stop_chat_run,
)
from village_insight.question_catalog import build_question_catalog
from village_insight.question_scope import freeze_question_scope
from village_insight.question_source_versions import source_supersession_map

router = APIRouter(prefix="/questions", tags=["questions"])


class QuestionRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    retry_of_run_id: uuid.UUID | None = None


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    scope_unit_id: uuid.UUID | None = None
    source_item_id: uuid.UUID | None = None


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    scope_name: str
    scope_unit_id: uuid.UUID
    scope_mode: str
    source_item_id: uuid.UUID | None
    source_name: str | None
    run_count: int
    created_at: Any
    updated_at: Any


class ConversationPage(BaseModel):
    items: list[ConversationSummary]
    page: int
    page_size: int
    total: int
    total_pages: int


class ConversationBulkDelete(BaseModel):
    conversation_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=240)


class ConversationDeleteResult(BaseModel):
    deleted: int


class QuestionRunView(BaseModel):
    id: uuid.UUID
    retry_of_run_id: uuid.UUID | None
    question: str
    answer_text: str
    answer: dict[str, Any]
    status: str
    route: str
    source_item_id: uuid.UUID | None
    tool_trace: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    error_code: str | None
    started_at: Any
    created_at: Any
    completed_at: Any | None


class ConversationDetail(BaseModel):
    conversation: ConversationSummary
    runs: list[QuestionRunView]
    run_total: int
    has_more_before: bool


class RunStarted(BaseModel):
    run_id: uuid.UUID
    conversation_id: uuid.UUID


class StopResult(BaseModel):
    run_id: uuid.UUID
    stopped: bool


class QuestionSourceRead(BaseModel):
    id: uuid.UUID
    file_name: str
    administrative_unit_id: uuid.UUID
    administrative_unit_name: str
    record_count: int
    updated_at: Any
    is_default: bool
    superseded_by_item_id: uuid.UUID | None


class QuestionSourcePage(BaseModel):
    items: list[QuestionSourceRead]
    page: int
    page_size: int
    total: int
    default_total: int
    total_pages: int


def _require_question_access(principal: CurrentPrincipal) -> None:
    if not (
        principal.has("questions.ask.tenant")
        or principal.has("questions.ask.village")
    ):
        raise HTTPException(status_code=403, detail="没有问答权限")
    if principal.scope_unit is None or not principal.allowed_unit_ids:
        raise HTTPException(status_code=403, detail="问答范围无效")


@dataclass(frozen=True)
class ResolvedQuestionScope:
    unit: AdministrativeUnit
    include_descendants: bool
    unit_ids: tuple[uuid.UUID, ...]
    unit_names: tuple[str, ...]
    name: str
    mode: str


@dataclass(frozen=True)
class ResolvedQuestionSource:
    item_id: uuid.UUID
    file_name: str
    administrative_unit_id: uuid.UUID
    administrative_unit_name: str
    record_count: int


def _resolve_question_scope(
    database: Database,
    principal: CurrentPrincipal,
    requested_scope_unit_id: uuid.UUID | None,
) -> ResolvedQuestionScope:
    _require_question_access(principal)
    if principal.scope_unit is None:
        raise HTTPException(status_code=403, detail="问答范围无效")
    if principal.membership.role == MembershipRole.VILLAGE_OPERATOR:
        if (
            requested_scope_unit_id is not None
            and requested_scope_unit_id != principal.scope_unit.id
        ):
            raise HTTPException(status_code=403, detail="不能查询其他村")
        return ResolvedQuestionScope(
            unit=principal.scope_unit,
            include_descendants=False,
            unit_ids=(principal.scope_unit.id,),
            unit_names=(principal.scope_unit.name,),
            name=principal.scope_unit.name,
            mode="village",
        )
    if principal.membership.role != MembershipRole.TENANT_ADMIN:
        raise HTTPException(status_code=403, detail="问答范围无效")

    if requested_scope_unit_id in {None, principal.scope_unit.id}:
        villages = list(
            database.scalars(
                select(AdministrativeUnit)
                .where(
                    AdministrativeUnit.id.in_(principal.allowed_unit_ids),
                    AdministrativeUnit.tenant_id == principal.tenant.id,
                    AdministrativeUnit.unit_type == AdministrativeUnitType.VILLAGE,
                    AdministrativeUnit.status == UserStatus.ACTIVE,
                )
                .order_by(AdministrativeUnit.name, AdministrativeUnit.id)
            )
        )
        if not villages:
            raise HTTPException(status_code=403, detail="当前租户没有可查询的村")
        return ResolvedQuestionScope(
            unit=principal.scope_unit,
            include_descendants=True,
            unit_ids=tuple(village.id for village in villages),
            unit_names=tuple(village.name for village in villages),
            name=f"全部村（{len(villages)}个）",
            mode="all_villages",
        )

    unit = database.get(AdministrativeUnit, requested_scope_unit_id)
    if (
        unit is None
        or unit.id not in principal.allowed_unit_ids
        or unit.tenant_id != principal.tenant.id
        or unit.unit_type != AdministrativeUnitType.VILLAGE
        or unit.status != UserStatus.ACTIVE
    ):
        raise HTTPException(status_code=403, detail="所选村不在当前授权范围")
    return ResolvedQuestionScope(
        unit=unit,
        include_descendants=False,
        unit_ids=(unit.id,),
        unit_names=(unit.name,),
        name=unit.name,
        mode="village",
    )


def _conversation_scope(
    database: Database,
    principal: CurrentPrincipal,
    conversation: QuestionConversation,
) -> ResolvedQuestionScope:
    scope = _resolve_question_scope(
        database,
        principal,
        conversation.scope_unit_id,
    )
    if scope.include_descendants != conversation.include_descendants:
        raise HTTPException(status_code=404, detail="问数会话范围已失效")
    return scope


def _resolve_question_source(
    database: Database,
    principal: CurrentPrincipal,
    scope: ResolvedQuestionScope,
    source_item_id: uuid.UUID | None,
) -> ResolvedQuestionSource | None:
    if source_item_id is None:
        return None
    row = database.execute(
        select(
            IngestionItem.id,
            IngestionItem.original_name,
            IngestionItem.administrative_unit_id,
            AdministrativeUnit.name,
            func.count(DatasetRecord.id),
        )
        .join(
            AdministrativeUnit,
            AdministrativeUnit.id == IngestionItem.administrative_unit_id,
        )
        .join(
            DatasetRecord,
            DatasetRecord.item_id == IngestionItem.id,
        )
        .where(
            IngestionItem.id == source_item_id,
            IngestionItem.tenant_id == principal.tenant.id,
            IngestionItem.administrative_unit_id.in_(scope.unit_ids),
            DatasetRecord.tenant_id == principal.tenant.id,
            DatasetRecord.administrative_unit_id.in_(scope.unit_ids),
            DatasetRecord.quality_status == "passed",
        )
        .group_by(
            IngestionItem.id,
            IngestionItem.original_name,
            IngestionItem.administrative_unit_id,
            AdministrativeUnit.name,
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="问数文件不存在或尚无已审核记录")
    return ResolvedQuestionSource(
        item_id=row[0],
        file_name=row[1],
        administrative_unit_id=row[2],
        administrative_unit_name=row[3],
        record_count=int(row[4]),
    )


def _conversation_context(
    database: Database,
    principal: CurrentPrincipal,
    conversation: QuestionConversation,
) -> tuple[ResolvedQuestionScope, ResolvedQuestionSource | None]:
    scope = _conversation_scope(database, principal, conversation)
    source = _resolve_question_source(
        database,
        principal,
        scope,
        conversation.source_item_id,
    )
    return scope, source


def _owned_conversation(
    database: Database,
    principal: CurrentPrincipal,
    conversation_id: uuid.UUID,
) -> QuestionConversation:
    conversation = database.scalar(
        select(QuestionConversation).where(
            QuestionConversation.id == conversation_id,
            QuestionConversation.tenant_id == principal.tenant.id,
            QuestionConversation.requested_by_user_id == principal.user.id,
            QuestionConversation.status != "deleted",
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="问数会话不存在")
    _conversation_context(database, principal, conversation)
    return conversation


def _conversation_summary(
    conversation: QuestionConversation,
    *,
    scope: ResolvedQuestionScope,
    source: ResolvedQuestionSource | None,
    run_count: int,
) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        status=conversation.status,
        scope_name=scope.name,
        scope_unit_id=scope.unit.id,
        scope_mode=scope.mode,
        source_item_id=source.item_id if source else None,
        source_name=source.file_name if source else None,
        run_count=run_count,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _run_view(run: QuestionRun) -> QuestionRunView:
    return QuestionRunView(
        id=run.id,
        retry_of_run_id=run.retry_of_run_id,
        question=run.question,
        answer_text=run.answer_text,
        answer=run.answer,
        status=run.status,
        route=run.route,
        source_item_id=run.source_item_id,
        tool_trace=run.tool_trace,
        evidence=run.evidence,
        error_code=run.error_code,
        started_at=run.started_at,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


def _sse(event: str, data: dict[str, Any], sequence: int) -> str:
    payload = {"sequence": sequence, **data}
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
    )


def _friendly_tool_name(tool_name: str) -> str:
    return {
        "describe_query_schema": "核对可查询字段",
        "query_metric": "计算已发布指标",
        "execute_safe_query": "执行已验证查询",
        "execute_bounded_query": "执行受控结构化查询",
        "lookup_records": "查询人员或业务记录",
        "aggregate_records": "统计或分组汇总",
        "summarize_values": "汇总金额或数值",
        "rank_records": "查询最高或最低记录",
        "query_household": "核对家庭成员关系",
        "describe_source_fields": "查看文件和 Sheet 字段",
        "lookup_source_records": "核对原始表格记录",
        "query_postgres": "查询已审核数据",
        "execute_code": "自主查询 PostgreSQL",
    }.get(tool_name, "查询数据")


def _tool_output_summary(output: object) -> dict[str, Any]:
    if not isinstance(output, dict):
        return {}
    evidence = output.get("evidence_summary")
    status = output.get("status")
    return {
        "status": status,
        "result_type": output.get("result_type"),
        "result_grade": output.get("result_grade"),
        "row_count": output.get("row_count"),
        "record_count": (
            evidence.get("record_count")
            if isinstance(evidence, dict)
            else None
        ),
        "source_file_count": (
            evidence.get("source_file_count")
            if isinstance(evidence, dict)
            else None
        ),
        "data_village_count": (
            evidence.get("data_village_count")
            if isinstance(evidence, dict)
            else None
        ),
        "error_code": output.get("error_code"),
        "message": "查询未完成，正在调整查询方式" if status == "error" else None,
    }


def _is_fact_tool_result(result: object) -> bool:
    return bool(
        isinstance(result, dict)
        and result.get("status") == "success"
        and result.get("tool")
        in {
            "query_metric",
            "execute_safe_query",
            "execute_bounded_query",
            "lookup_records",
            "aggregate_records",
            "summarize_values",
            "rank_records",
            "query_household",
            "describe_source_fields",
            "lookup_source_records",
            "query_postgres",
        }
    )


def _is_backend_accepted_fact_result(result: object) -> bool:
    return bool(
        _is_fact_tool_result(result)
        and isinstance(result, dict)
        and result.get("acceptance_status") not in {
            "candidate_only",
            "empty",
        }
    )


def _execution_fingerprint(
    result: dict[str, Any],
    scope_snapshot: dict[str, Any],
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "result": result,
                "scope_snapshot": scope_snapshot,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _store_fact_results(
    database: Database,
    *,
    run: QuestionRun,
    tool_results: list[dict[str, Any]],
) -> list[QuestionFactResult]:
    factual = [
        result
        for result in tool_results
        if _is_fact_tool_result(result)
    ]
    stored_results: list[QuestionFactResult] = []
    for result in factual:
        semantic_plan = result.get("semantic_plan")
        metric = result.get("metric")
        evidence = result.get("evidence_summary")
        stored = QuestionFactResult(
            question_run_id=run.id,
            tool_name=str(result.get("tool") or ""),
            result_grade=str(result.get("result_grade") or "bounded_sql"),
            contract_version=str(result.get("contract_version") or ""),
            fact_set_code=(
                semantic_plan.get("fact_set_code")
                if isinstance(semantic_plan, dict)
                else result.get("fact_set_code")
            ),
            fact_set_version=(
                semantic_plan.get("fact_set_version")
                if isinstance(semantic_plan, dict)
                else None
            ),
            semantic_manifest_code=(
                semantic_plan.get("manifest_code")
                if isinstance(semantic_plan, dict)
                else None
            ),
            semantic_manifest_version=(
                semantic_plan.get("manifest_version")
                if isinstance(semantic_plan, dict)
                else None
            ),
            metric_code=(
                metric.get("metric_code")
                if isinstance(metric, dict)
                else None
            ),
            metric_version=(
                metric.get("metric_version")
                if isinstance(metric, dict)
                else None
            ),
            safe_query_plan=(
                result.get("safe_query_plan")
                if isinstance(result.get("safe_query_plan"), dict)
                else {}
            ),
            semantic_query_plan=(
                semantic_plan if isinstance(semantic_plan, dict) else {}
            ),
            semantic_plan_fingerprint=(
                semantic_plan.get("semantic_plan_fingerprint")
                if isinstance(semantic_plan, dict)
                else None
            ),
            execution_fingerprint=_execution_fingerprint(
                result,
                run.scope_snapshot,
            ),
            structured_result=result,
            record_count=(
                int(evidence.get("record_count") or 0)
                if isinstance(evidence, dict)
                else 0
            ),
            source_file_count=(
                int(evidence.get("source_file_count") or 0)
                if isinstance(evidence, dict)
                else 0
            ),
            data_village_count=(
                int(evidence["data_village_count"])
                if isinstance(evidence, dict)
                and evidence.get("data_village_count") is not None
                else None
            ),
            dataset_snapshot=run.scope_snapshot,
            eligible_source_item_fingerprint=str(
                run.scope_snapshot.get("source_item_fingerprint") or ""
            ),
            accepted=_is_backend_accepted_fact_result(result),
        )
        database.add(stored)
        stored_results.append(stored)
    database.flush()
    return stored_results


def _history_for(
    database: Database,
    conversation_id: uuid.UUID,
    *,
    exclude_run_ids: tuple[uuid.UUID, ...],
) -> list[dict[str, Any]]:
    rows = list(
        database.scalars(
            select(QuestionRun)
            .where(
                QuestionRun.conversation_id == conversation_id,
                QuestionRun.id.not_in(exclude_run_ids),
                QuestionRun.status.in_(("succeeded", "needs_clarification")),
            )
            .order_by(QuestionRun.created_at.desc())
            .limit(12)
        )
    )
    history: list[dict[str, Any]] = []
    for row in reversed(rows):
        history.append({"role": "user", "content": row.question})
        if row.answer_text:
            history.append({"role": "assistant", "content": row.answer_text})
    return history


def _resolve_retry_run(
    database: Database,
    principal: CurrentPrincipal,
    conversation: QuestionConversation,
    retry_of_run_id: uuid.UUID | None,
) -> QuestionRun | None:
    if retry_of_run_id is None:
        return None
    original = database.scalar(
        select(QuestionRun).where(
            QuestionRun.id == retry_of_run_id,
            QuestionRun.conversation_id == conversation.id,
            QuestionRun.tenant_id == principal.tenant.id,
            QuestionRun.requested_by_user_id == principal.user.id,
        )
    )
    if original is None:
        raise HTTPException(status_code=404, detail="原查询记录不存在")
    if original.status == "running":
        raise HTTPException(status_code=409, detail="原查询仍在进行，不能重新查询")
    latest_run_id = database.scalar(
        select(QuestionRun.id)
        .where(QuestionRun.conversation_id == conversation.id)
        .order_by(QuestionRun.created_at.desc(), QuestionRun.id.desc())
        .limit(1)
    )
    if latest_run_id != original.id:
        raise HTTPException(status_code=409, detail="只能重新查询当前会话最后一轮")
    return original


def _question_system_prompt(
    scope: ResolvedQuestionScope,
    source: ResolvedQuestionSource | None,
) -> str:
    scope_catalog = "、".join(scope.unit_names)
    source_instruction = (
        f"本会话还固定限定到文件“{source.file_name}”。工具已强制注入文件范围，"
        "不要把其他文件的数据并入答案。"
        if source is not None
        else "本会话查询当前行政区划范围内的全部已审核文件。"
    )
    return f"""
你是 VillageInsight 的村情问数助手。当前用户可查询范围为：{scope.name}。
当前范围内的村：{scope_catalog}。
{source_instruction}
正式事实只归属上述村，不归属租户或乡镇容器。查询全部范围时不要按“{scope.name}”
过滤 administrative_unit，应直接使用工具已注入的完整村范围。

你必须使用 village_query 工具查询 PostgreSQL 中已审核的数据后再回答事实问题：
1. 不确定字段代码或指标时，先调用 describe_query_schema。已知所需语义字段时必须把
   所有字段代码放入 field_codes 以筛出同时包含这些字段的事实集；未知代码时用 search
   搜索业务名称。catalog_match 提示截断时必须缩小条件后重查，不能从截断目录推断
   “没有数据”。
2. 已存在正式指标时必须调用 query_metric，数字逐字采用工具结果，不得心算或改写。
3. 查询目录存在已发布事实集和语义清单时，详情、名单、计数、聚合和分组必须优先调用
   execute_safe_query，提交结构化意图，不得提供 SQL。`governance_status=derived` 或没有
   `semantic_manifest_code` 的事实集不能调用 execute_safe_query。
4. 户号、户主、家庭成员和家庭关系优先调用 query_household；人员或业务记录的属性
   查询调用 lookup_records；条件计数和单字段分组调用 aggregate_records。三者都只
   提交结构化意图，由后端校验字段、范围和数据来源。金额、面积、收入、数量的
   求和、平均、最大和最小值调用 summarize_values。查询最高、最低、最年长、最年轻
   或 Top N 记录调用 rank_records。询问一个文件或 Sheet 有哪些表头、需要填写哪些
   字段时调用 describe_source_fields。
5. 上述专用工具无法表达时，可先调用 execute_bounded_query；仍无法表达时调用
   query_postgres，由你动态生成受控只读 SQL；
   后端会校验 SQL、注入租户/村/文件范围并限制返回行数。必须提交目录中的
   fact_set_code。
   fact_set_code 只是工具参数，不是 question_* 表中的列，不得写入 SQL 条件。
   如果按姓名、编号等稳定字段在某一个事实集查不到，或者所需字段仍是临时编码，
   必须调用 lookup_source_records 跨全部已审核事实集定位记录，并按用户问题提供
   source_header_terms 读取必要的原始表头和值。不得因为单一事实集返回空就声称
   当前范围没有该人员或记录。
6. 查询目录已返回当前可用 record_type。空结果不代表其他相关事实集也没有数据；
   应继续检查同时包含问题所需字段的相关事实集，直到取得非空证据或全部核对完毕。
   没有正式指标的单一统计应一次完成，并返回
   record_count、source_file_count 和 data_village_count 三个标准证据列。
   只有后端校验后返回 acceptance_status=accepted 的结果才能进入最终回答。
7. 工具无结果、数据冲突或口径不明确时，明确说明；只有无法通过工具确定的信息才询问用户。
   任何属性都必须有同名语义字段或源字段的工具证据；不得根据姓名、称谓、亲属关系或
   常识推断性别、年龄、身份、收入等字段。用户询问性别时只采用 person.sex 的结果，
   `household.relationship_to_head` 只能说明与户主关系，不能作为性别证据。
8. 不展示 SQL、表名、字段代码、工具名、内部 ID 或模型推理过程。
9. 回答使用简洁中文，先给结论，再说明统计口径和已核对的记录/来源数量。
   只输出纯文本，不使用 Markdown 加粗或代码标记。
   查询授权范围不等于实际数据覆盖范围；必须根据 data_village_count 说明有数据的村数，
   不得声称没有正式记录的村已被数据覆盖。
10. 已有业务工具无法完成查询时，使用 Hermes 原生 execute_code 自主编写 Python 和
    PostgreSQL 只读查询。只读连接从环境变量 VILLAGE_INSIGHT_QUERY_DATABASE_URL 获取；
    同时读取 VILLAGE_INSIGHT_QUERY_TENANT_ID、VILLAGE_INSIGHT_QUERY_UNIT_IDS、
    VILLAGE_INSIGHT_QUERY_SOURCE_ITEM_IDS 和 VILLAGE_INSIGHT_QUERY_RECORD_CREATED_BEFORE，
    在 dataset_records 查询中完整应用租户、村、文件、quality_status='passed' 和数据
    水位条件。可连接 dataset_records、record_index_values、record_value_lineage、
    ingestion_items、administrative_units 和语义字段目录。该数据库账号只有 SELECT
    权限；不得尝试写入、修改会话为可写、访问系统终端、文件系统或外部网络。
    该连接是 psycopg 可直接识别的标准 postgresql:// URL。不得猜测数据库列名；
    不确定时先查询 information_schema.columns。关键连接为：
    dataset_records.id = record_index_values.record_id，
    dataset_records.item_id = ingestion_items.id；
    原 Excel 行内容保存在 dataset_records.raw_data JSONB。
""".strip()


@router.get("/sources", response_model=QuestionSourcePage)
def list_question_sources(
    database: Database,
    principal: CurrentPrincipal,
    scope_unit_id: uuid.UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=15, ge=1, le=50),
    search: str = Query(default="", max_length=120),
) -> QuestionSourcePage:
    scope = _resolve_question_scope(database, principal, scope_unit_id)
    scope_filters = [
        IngestionItem.tenant_id == principal.tenant.id,
        IngestionItem.administrative_unit_id.in_(scope.unit_ids),
        DatasetRecord.tenant_id == principal.tenant.id,
        DatasetRecord.administrative_unit_id.in_(scope.unit_ids),
        DatasetRecord.quality_status == "passed",
    ]
    filters = [*scope_filters]
    normalized_search = search.strip()
    if normalized_search:
        filters.append(
            IngestionItem.original_name.ilike(f"%{normalized_search}%")
        )
    grouped = (
        select(IngestionItem.id)
        .join(DatasetRecord, DatasetRecord.item_id == IngestionItem.id)
        .where(*filters)
        .group_by(IngestionItem.id)
        .subquery()
    )
    eligible_source_item_ids = tuple(
        database.scalars(select(grouped.c.id).order_by(grouped.c.id))
    )
    total = len(eligible_source_item_ids)
    all_eligible_source_item_ids = tuple(
        database.scalars(
            select(IngestionItem.id)
            .join(DatasetRecord, DatasetRecord.item_id == IngestionItem.id)
            .where(*scope_filters)
            .group_by(IngestionItem.id)
            .order_by(IngestionItem.id)
        )
    )
    supersessions = source_supersession_map(
        database,
        tenant_id=principal.tenant.id,
        administrative_unit_ids=scope.unit_ids,
        eligible_source_item_ids=all_eligible_source_item_ids,
        declared_before=utcnow(),
    )
    rows = database.execute(
        select(
            IngestionItem.id,
            IngestionItem.original_name,
            IngestionItem.administrative_unit_id,
            AdministrativeUnit.name,
            func.count(DatasetRecord.id),
            func.max(DatasetRecord.created_at),
        )
        .join(
            AdministrativeUnit,
            AdministrativeUnit.id == IngestionItem.administrative_unit_id,
        )
        .join(DatasetRecord, DatasetRecord.item_id == IngestionItem.id)
        .where(*filters)
        .group_by(
            IngestionItem.id,
            IngestionItem.original_name,
            IngestionItem.administrative_unit_id,
            AdministrativeUnit.name,
        )
        .order_by(
            func.max(DatasetRecord.created_at).desc(),
            IngestionItem.id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return QuestionSourcePage(
        items=[
            QuestionSourceRead(
                id=row[0],
                file_name=row[1],
                administrative_unit_id=row[2],
                administrative_unit_name=row[3],
                record_count=int(row[4]),
                updated_at=row[5],
                is_default=row[0] not in supersessions,
                superseded_by_item_id=supersessions.get(row[0]),
            )
            for row in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
        default_total=total
        - sum(item_id in supersessions for item_id in eligible_source_item_ids),
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get("/conversations", response_model=ConversationPage)
def list_conversations(
    database: Database,
    principal: CurrentPrincipal,
    scope_unit_id: uuid.UUID | None = None,
    source_item_id: uuid.UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    search: str = Query(default="", max_length=120),
) -> ConversationPage:
    scope = _resolve_question_scope(database, principal, scope_unit_id)
    source = _resolve_question_source(
        database,
        principal,
        scope,
        source_item_id,
    )
    filters = [
        QuestionConversation.tenant_id == principal.tenant.id,
        QuestionConversation.requested_by_user_id == principal.user.id,
        QuestionConversation.scope_unit_id == scope.unit.id,
        QuestionConversation.include_descendants == scope.include_descendants,
        QuestionConversation.status != "deleted",
        (
            QuestionConversation.source_item_id == source.item_id
            if source is not None
            else QuestionConversation.source_item_id.is_(None)
        ),
    ]
    normalized_search = search.strip()
    if normalized_search:
        filters.append(QuestionConversation.title.ilike(f"%{normalized_search}%"))

    total = int(
        database.scalar(
            select(func.count(QuestionConversation.id)).where(*filters)
        )
        or 0
    )
    run_counts = (
        select(
            QuestionRun.conversation_id.label("conversation_id"),
            func.count(QuestionRun.id).label("run_count"),
        )
        .group_by(QuestionRun.conversation_id)
        .subquery()
    )
    rows = database.execute(
        select(
            QuestionConversation,
            func.coalesce(run_counts.c.run_count, 0),
        )
        .outerjoin(
            run_counts,
            run_counts.c.conversation_id == QuestionConversation.id,
        )
        .where(*filters)
        .order_by(
            QuestionConversation.updated_at.desc(),
            QuestionConversation.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ConversationPage(
        items=[
            _conversation_summary(
                conversation,
                scope=scope,
                source=source,
                run_count=int(run_count),
            )
            for conversation, run_count in rows
        ],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.post(
    "/conversations",
    response_model=ConversationSummary,
    status_code=201,
)
def create_conversation(
    payload: ConversationCreate,
    database: Database,
    principal: CurrentPrincipal,
) -> ConversationSummary:
    scope = _resolve_question_scope(
        database,
        principal,
        payload.scope_unit_id,
    )
    source = _resolve_question_source(
        database,
        principal,
        scope,
        payload.source_item_id,
    )
    conversation = QuestionConversation(
        tenant_id=principal.tenant.id,
        requested_by_user_id=principal.user.id,
        scope_unit_id=scope.unit.id,
        source_item_id=source.item_id if source else None,
        include_descendants=scope.include_descendants,
        title=(payload.title or "新的问数").strip() or "新的问数",
    )
    database.add(conversation)
    database.commit()
    database.refresh(conversation)
    return _conversation_summary(
        conversation,
        scope=scope,
        source=source,
        run_count=0,
    )


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationSummary,
)
def rename_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationRename,
    database: Database,
    principal: CurrentPrincipal,
) -> ConversationSummary:
    conversation = _owned_conversation(
        database,
        principal,
        conversation_id,
    )
    scope, source = _conversation_context(database, principal, conversation)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="会话标题不能为空")
    conversation.title = title
    conversation.updated_at = utcnow()
    run_count = int(
        database.scalar(
            select(func.count(QuestionRun.id)).where(
                QuestionRun.conversation_id == conversation.id
            )
        )
        or 0
    )
    database.commit()
    database.refresh(conversation)
    return _conversation_summary(
        conversation,
        scope=scope,
        source=source,
        run_count=run_count,
    )


def _soft_delete_conversations(
    database: Database,
    principal: CurrentPrincipal,
    conversation_ids: list[uuid.UUID],
) -> int:
    unique_ids = list(dict.fromkeys(conversation_ids))
    conversations = list(
        database.scalars(
            select(QuestionConversation).where(
                QuestionConversation.id.in_(unique_ids),
                QuestionConversation.tenant_id == principal.tenant.id,
                QuestionConversation.requested_by_user_id == principal.user.id,
                QuestionConversation.status != "deleted",
            )
        )
    )
    if len(conversations) != len(unique_ids):
        raise HTTPException(status_code=404, detail="部分问数会话不存在")
    for conversation in conversations:
        _conversation_context(database, principal, conversation)

    running_id = database.scalar(
        select(QuestionRun.id).where(
            QuestionRun.conversation_id.in_(unique_ids),
            QuestionRun.status == "running",
        )
    )
    if running_id is not None:
        raise HTTPException(status_code=409, detail="运行中的会话不能删除，请先停止查询")

    deleted_at = utcnow()
    for conversation in conversations:
        conversation.status = "deleted"
        conversation.updated_at = deleted_at
    database.commit()
    return len(conversations)


@router.delete(
    "/conversations/{conversation_id}",
    response_model=ConversationDeleteResult,
)
def delete_conversation(
    conversation_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> ConversationDeleteResult:
    deleted = _soft_delete_conversations(
        database,
        principal,
        [conversation_id],
    )
    return ConversationDeleteResult(deleted=deleted)


@router.post(
    "/conversations/bulk-delete",
    response_model=ConversationDeleteResult,
)
def bulk_delete_conversations(
    payload: ConversationBulkDelete,
    database: Database,
    principal: CurrentPrincipal,
) -> ConversationDeleteResult:
    deleted = _soft_delete_conversations(
        database,
        principal,
        payload.conversation_ids,
    )
    return ConversationDeleteResult(deleted=deleted)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
def get_conversation(
    conversation_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
    run_offset: int = Query(default=0, ge=0),
    run_limit: int = Query(default=20, ge=1, le=50),
) -> ConversationDetail:
    conversation = _owned_conversation(
        database,
        principal,
        conversation_id,
    )
    scope, source = _conversation_context(database, principal, conversation)
    run_total = int(
        database.scalar(
            select(func.count(QuestionRun.id)).where(
                QuestionRun.conversation_id == conversation.id
            )
        )
        or 0
    )
    descending_runs = list(
        database.scalars(
            select(QuestionRun)
            .where(QuestionRun.conversation_id == conversation.id)
            .order_by(QuestionRun.created_at.desc(), QuestionRun.id.desc())
            .offset(run_offset)
            .limit(run_limit)
        )
    )
    runs = list(reversed(descending_runs))
    return ConversationDetail(
        conversation=_conversation_summary(
            conversation,
            scope=scope,
            source=source,
            run_count=run_total,
        ),
        runs=[_run_view(run) for run in runs],
        run_total=run_total,
        has_more_before=run_offset + len(runs) < run_total,
    )


@router.post(
    "/conversations/{conversation_id}/runs",
    response_class=StreamingResponse,
)
async def create_run(
    conversation_id: uuid.UUID,
    payload: QuestionRequest,
    database: Database,
    principal: CurrentPrincipal,
) -> StreamingResponse:
    conversation = _owned_conversation(
        database,
        principal,
        conversation_id,
    )
    scope, source = _conversation_context(database, principal, conversation)
    running = database.scalar(
        select(QuestionRun.id).where(
            QuestionRun.conversation_id == conversation.id,
            QuestionRun.status == "running",
        )
    )
    if running is not None:
        raise HTTPException(status_code=409, detail="当前会话仍在查询，请稍候或先停止")
    retry_of_run = _resolve_retry_run(
        database,
        principal,
        conversation,
        payload.retry_of_run_id,
    )
    question = (
        retry_of_run.question
        if retry_of_run is not None
        else payload.question.strip()
    )
    scope_snapshot = freeze_question_scope(
        database,
        tenant_id=principal.tenant.id,
        administrative_unit_ids=scope.unit_ids,
        record_created_before=utcnow(),
        selected_source_item_id=source.item_id if source else None,
    )
    catalog_snapshot = build_question_catalog(database, scope_snapshot)
    run = QuestionRun(
        conversation_id=conversation.id,
        retry_of_run_id=retry_of_run.id if retry_of_run is not None else None,
        tenant_id=principal.tenant.id,
        requested_by_user_id=principal.user.id,
        scope_unit_id=scope.unit.id,
        source_item_id=source.item_id if source else None,
        include_descendants=scope.include_descendants,
        question=question,
        scope_snapshot=scope_snapshot.model_dump(mode="json"),
        catalog_snapshot=catalog_snapshot.model_dump(mode="json"),
        validated_query_plan={},
        answer={},
        status="running",
        route="hermes_studio",
        tool_trace=[],
        answer_text="",
        evidence=[],
    )
    existing_run = database.scalar(
        select(QuestionRun.id).where(
            QuestionRun.conversation_id == conversation.id
        )
    )
    database.add(run)
    if existing_run is None:
        conversation.title = question[:48]
    conversation.updated_at = utcnow()
    database.commit()
    history = _history_for(
        database,
        conversation.id,
        exclude_run_ids=(
            (run.id, retry_of_run.id)
            if retry_of_run is not None
            else (run.id,)
        ),
    )
    settings = get_settings()
    resolved = resolve_configuration(database, settings)
    runtime = EmbeddedHermesRuntime(settings, resolved.connection)
    run_id = run.id
    tenant_id = principal.tenant.id
    allowed_unit_ids = scope.unit_ids
    allowed_source_item_ids = scope_snapshot.source_item_ids

    async def event_stream() -> AsyncIterator[str]:
        sequence = 1
        tool_trace: list[dict[str, Any]] = []
        query_plans: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        answer_text = ""
        reasoning_text = ""
        clarification_requested = False
        yield _sse(
            "run.started",
            {
                "run_id": run_id,
                "conversation_id": conversation_id,
                "message": "正在理解问题",
                "started_at": run.started_at,
            },
            sequence,
        )
        sequence += 1
        try:
            async for event in runtime.stream_chat(
                system_prompt=_question_system_prompt(scope, source),
                user_message=question,
                conversation_history=history,
                database_url=settings.database_url,
                tenant_id=tenant_id,
                administrative_unit_ids=allowed_unit_ids,
                run_id=run_id,
                source_item_ids=allowed_source_item_ids,
                source_scope_enforced=True,
                record_created_before=scope_snapshot.record_created_before,
                catalog_snapshot=catalog_snapshot.model_dump(mode="json"),
                policy=HermesCallPolicy(
                    thinking_enabled=False,
                    max_tokens=settings.hermes_max_tokens,
                    json_mode=False,
                    enabled_toolsets=(
                        "village_query",
                        "clarify",
                        "code_execution",
                    ),
                    repair_attempts=0,
                    timeout_seconds=settings.hermes_timeout_seconds,
                    max_iterations=90,
                ),
            ):
                public_data = {
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                    **event.data,
                }
                if event.event == "tool.started":
                    tool_name = str(event.data.get("tool_name") or "")
                    arguments = event.data.get("arguments")
                    plan: dict[str, Any] = {"tool_name": tool_name}
                    if isinstance(arguments, dict):
                        if arguments.get("metric_code"):
                            plan["metric_code"] = arguments["metric_code"]
                        if arguments.get("fact_set_code"):
                            plan["fact_set_code"] = arguments[
                                "fact_set_code"
                            ]
                        if arguments.get("operation"):
                            plan["operation"] = arguments["operation"]
                        if arguments.get("group_by"):
                            plan["group_by"] = arguments["group_by"]
                        if arguments.get("sql"):
                            plan["sql"] = arguments["sql"]
                    query_plans.append(plan)
                    tool_trace.append(
                        {
                            "tool_call_id": event.data.get("tool_call_id"),
                            "tool_name": tool_name,
                            "label": _friendly_tool_name(tool_name),
                            "status": "running",
                        }
                    )
                    public_data = {
                        "run_id": run_id,
                        "conversation_id": conversation_id,
                        "tool_call_id": event.data.get("tool_call_id"),
                        "label": _friendly_tool_name(tool_name),
                    }
                elif event.event in {"tool.completed", "tool.failed"}:
                    tool_name = str(event.data.get("tool_name") or "")
                    output = event.data.get("output")
                    summary = _tool_output_summary(output)
                    for trace in reversed(tool_trace):
                        if trace.get("tool_call_id") == event.data.get(
                            "tool_call_id"
                        ):
                            trace["status"] = (
                                "error"
                                if summary.get("status") == "error"
                                else "completed"
                            )
                            trace.update(summary)
                            trace["duration_ms"] = event.data.get("duration_ms")
                            break
                    public_data = {
                        "run_id": run_id,
                        "conversation_id": conversation_id,
                        "tool_call_id": event.data.get("tool_call_id"),
                        "label": _friendly_tool_name(tool_name),
                        "duration_ms": event.data.get("duration_ms"),
                        **summary,
                    }
                elif event.event == "clarify.requested":
                    clarification_requested = True
                elif event.event in {"reasoning.delta", "thinking.delta"}:
                    # Hermes Studio keeps model reasoning separate from assistant
                    # content. Preserve that boundary here, but do not expose or
                    # persist raw reasoning in the product UI.
                    reasoning_text += str(
                        event.data.get("text") or event.data.get("delta") or ""
                    )
                    public_data = {
                        "run_id": run_id,
                        "conversation_id": conversation_id,
                        "active": bool(reasoning_text),
                    }
                    yield _sse("reasoning.status", public_data, sequence)
                    sequence += 1
                    continue
                elif event.event == "answer.completed":
                    answer_text = str(event.data.get("content") or "")
                    raw_results = event.data.get("tool_results")
                    tool_results = (
                        [
                            result
                            for result in raw_results
                            if isinstance(result, dict)
                        ]
                        if isinstance(raw_results, list)
                        else []
                    )
                    public_data = {
                        "run_id": run_id,
                        "conversation_id": conversation_id,
                        "content": answer_text,
                        "answer": {},
                    }
                yield _sse(event.event, public_data, sequence)
                sequence += 1

            status = (
                "needs_clarification"
                if clarification_requested
                else ("succeeded" if answer_text.strip() else "failed")
            )
            completed_at = utcnow()
            with get_session_factory()() as update_database:
                stored_run = update_database.get(QuestionRun, run_id)
                stored_conversation = update_database.get(
                    QuestionConversation,
                    conversation_id,
                )
                if stored_run is not None and stored_run.status == "stopped":
                    return
                if stored_run is not None:
                    stored_run.validated_query_plan = {
                        "contract_version": "hermes-studio-tool-run/v1",
                        "plans": query_plans,
                    }
                    stored_run.answer = {}
                    stored_run.answer_text = answer_text
                    stored_run.tool_trace = tool_trace
                    evidence: list[dict[str, Any]] = []
                    for result in tool_results:
                        evidence_summary = result.get("evidence_summary")
                        if isinstance(evidence_summary, dict):
                            evidence.append(evidence_summary)
                    stored_run.evidence = evidence
                    stored_run.status = status
                    _store_fact_results(
                        update_database,
                        run=stored_run,
                        tool_results=tool_results,
                    )
                    stored_run.error_code = (
                        "empty_agent_response"
                        if status == "failed"
                        else None
                    )
                    stored_run.completed_at = completed_at
                if stored_conversation is not None:
                    stored_conversation.updated_at = utcnow()
                update_database.commit()
            yield _sse(
                "run.completed",
                {
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                    "status": status,
                    "completed_at": completed_at,
                    "duration_ms": max(
                        0,
                        round(
                            (completed_at - run.started_at).total_seconds()
                            * 1_000
                        ),
                    ),
                },
                sequence,
            )
        except HermesUnavailableError as exc:
            with get_session_factory()() as update_database:
                stored_run = update_database.get(QuestionRun, run_id)
                if stored_run is not None and stored_run.status == "running":
                    stored_run.status = "failed"
                    stored_run.error_code = type(exc).__name__
                    stored_run.tool_trace = tool_trace
                    stored_run.completed_at = utcnow()
                    update_database.commit()
            yield _sse(
                "run.failed",
                {
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                    "message": "Hermes 暂时无法完成查询，请稍后重试",
                    "error_code": type(exc).__name__,
                },
                sequence,
            )
        except Exception as exc:
            stopped = False
            with get_session_factory()() as update_database:
                stored_run = update_database.get(QuestionRun, run_id)
                if stored_run is not None:
                    stopped = stored_run.status == "stopped"
                    if stored_run.status == "running":
                        stored_run.status = "failed"
                        stored_run.error_code = type(exc).__name__
                        stored_run.tool_trace = tool_trace
                        stored_run.completed_at = utcnow()
                        update_database.commit()
            yield _sse(
                "run.stopped" if stopped else "run.failed",
                {
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                    "message": (
                        "本次查询已停止"
                        if stopped
                        else "查询过程出现异常，请稍后重试"
                    ),
                    "error_code": "" if stopped else type(exc).__name__,
                },
                sequence,
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/conversations/{conversation_id}/stop",
    response_model=StopResult,
)
def stop_run(
    conversation_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> StopResult:
    conversation = _owned_conversation(
        database,
        principal,
        conversation_id,
    )
    run = database.scalar(
        select(QuestionRun)
        .where(
            QuestionRun.conversation_id == conversation.id,
            QuestionRun.status == "running",
        )
        .order_by(QuestionRun.created_at.desc())
    )
    if run is None:
        raise HTTPException(status_code=409, detail="当前会话没有正在进行的查询")
    stopped = stop_chat_run(str(run.id))
    run.status = "stopped"
    run.error_code = "stopped_by_user"
    run.completed_at = utcnow()
    database.commit()
    return StopResult(run_id=run.id, stopped=stopped)


@router.get("/runs/{run_id}/evidence")
def get_run_evidence(
    run_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> dict[str, Any]:
    run = database.scalar(
        select(QuestionRun).where(
            QuestionRun.id == run_id,
            QuestionRun.tenant_id == principal.tenant.id,
            QuestionRun.requested_by_user_id == principal.user.id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="查询记录不存在")
    if run.conversation_id is None:
        raise HTTPException(status_code=404, detail="查询记录没有有效会话")
    _owned_conversation(database, principal, run.conversation_id)
    fact_results = list(
        database.scalars(
            select(QuestionFactResult)
            .where(QuestionFactResult.question_run_id == run.id)
            .order_by(QuestionFactResult.created_at)
        )
    )
    return {
        "run_id": run.id,
        "scope_snapshot": run.scope_snapshot,
        "catalog_snapshot": run.catalog_snapshot,
        "query_plan": run.validated_query_plan,
        "evidence": run.evidence,
        "tool_trace": run.tool_trace,
        "fact_results": [
            {
                "id": result.id,
                "tool_name": result.tool_name,
                "result_grade": result.result_grade,
                "contract_version": result.contract_version,
                "fact_set_code": result.fact_set_code,
                "fact_set_version": result.fact_set_version,
                "semantic_manifest_code": result.semantic_manifest_code,
                "semantic_manifest_version": result.semantic_manifest_version,
                "metric_code": result.metric_code,
                "metric_version": result.metric_version,
                "semantic_plan_fingerprint": (
                    result.semantic_plan_fingerprint
                ),
                "execution_fingerprint": result.execution_fingerprint,
                "record_count": result.record_count,
                "source_file_count": result.source_file_count,
                "data_village_count": result.data_village_count,
                "accepted": result.accepted,
            }
            for result in fact_results
        ],
    }


@router.post("", response_model=RunStarted)
def ask_question(
    payload: QuestionRequest,
    database: Database,
    principal: CurrentPrincipal,
) -> RunStarted:
    """Compatibility entry: create a durable conversation and queued run shell."""

    scope = _resolve_question_scope(database, principal, None)
    conversation = QuestionConversation(
        tenant_id=principal.tenant.id,
        requested_by_user_id=principal.user.id,
        scope_unit_id=scope.unit.id,
        include_descendants=scope.include_descendants,
        title=payload.question.strip()[:48],
    )
    database.add(conversation)
    database.flush()
    scope_snapshot = freeze_question_scope(
        database,
        tenant_id=principal.tenant.id,
        administrative_unit_ids=scope.unit_ids,
        record_created_before=utcnow(),
    )
    catalog_snapshot = build_question_catalog(database, scope_snapshot)
    run = QuestionRun(
        conversation_id=conversation.id,
        tenant_id=principal.tenant.id,
        requested_by_user_id=principal.user.id,
        scope_unit_id=scope.unit.id,
        include_descendants=scope.include_descendants,
        question=payload.question.strip(),
        scope_snapshot=scope_snapshot.model_dump(mode="json"),
        catalog_snapshot=catalog_snapshot.model_dump(mode="json"),
        validated_query_plan={},
        answer={},
        status="pending",
        route="hermes_studio",
    )
    database.add(run)
    database.commit()
    return RunStarted(run_id=run.id, conversation_id=conversation.id)
