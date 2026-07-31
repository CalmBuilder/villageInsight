from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from village_insight.api.routes.questions import (
    ResolvedQuestionScope,
    ResolvedQuestionSource,
    _question_system_prompt,
)
from village_insight.config import get_settings
from village_insight.db.models import AdministrativeUnit, IngestionItem
from village_insight.db.session import get_session_factory
from village_insight.hermes.configuration import resolve_configuration
from village_insight.hermes.runtime import (
    EmbeddedHermesRuntime,
    HermesCallPolicy,
)
from village_insight.question_benchmark import load_question_benchmark
from village_insight.question_catalog import build_question_catalog
from village_insight.question_scope import freeze_question_scope
from village_insight.regression_reflection import RegressionReflection


class HermesQuestionRegressionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: str = "hermes-question-regression/v1"
    case_id: str
    benchmark_membership: str
    selected_file: bool
    tool_sequence: tuple[str, ...]
    successful_fact_tool: str | None
    result_grade: str | None
    fact_set_code: str | None
    failed_fact_errors: tuple[str, ...]
    successful_fact_summaries: tuple[dict[str, Any], ...]
    expected_groups: dict[str, int]
    record_count: int
    source_file_count: int
    data_village_count: int | None
    answer_contract_passed: bool
    passed: bool
    reflection: RegressionReflection


def _contains_expected_group(
    rows: list[dict[str, Any]],
    label: str,
    count: int,
) -> bool:
    return any(label in row.values() and count in row.values() for row in rows)


async def run_hermes_question_regression(
    gold: dict[str, Any],
) -> HermesQuestionRegressionReport:
    benchmark = load_question_benchmark(Path(gold["benchmark_path"]))
    benchmark_case = next(
        (
            case
            for case in benchmark.cases
            if case.case_id == gold["case_id"]
        ),
        None,
    )
    if benchmark_case is None:
        raise ValueError("gold case is unavailable in the benchmark workbook")

    settings = get_settings()
    with get_session_factory()() as database:
        source = database.scalar(
            select(IngestionItem).where(
                IngestionItem.source_sha256
                == gold["dataset_snapshot"]["source_sha256"]
            )
        )
        if source is None:
            raise ValueError("gold source is unavailable")
        unit = database.get(
            AdministrativeUnit,
            source.administrative_unit_id,
        )
        if unit is None:
            raise ValueError("gold administrative unit is unavailable")
        frozen = freeze_question_scope(
            database,
            tenant_id=source.tenant_id,
            administrative_unit_ids=(unit.id,),
            selected_source_item_id=source.id,
            record_created_before=datetime.now(UTC),
        )
        catalog = build_question_catalog(database, frozen)
        connection = resolve_configuration(database, settings).connection

    scope = ResolvedQuestionScope(
        unit=unit,
        include_descendants=False,
        unit_ids=(unit.id,),
        unit_names=(unit.name,),
        name=unit.name,
        mode="village",
    )
    source_scope = ResolvedQuestionSource(
        item_id=source.id,
        file_name=source.original_name,
        administrative_unit_id=unit.id,
        administrative_unit_name=unit.name,
        record_count=sum(item.record_count for item in catalog.fact_sets),
    )
    runtime = EmbeddedHermesRuntime(settings, connection)
    completed: dict[str, Any] = {}
    async for event in runtime.stream_chat(
        system_prompt=_question_system_prompt(scope, source_scope),
        user_message=benchmark_case.normalized_question,
        conversation_history=[],
        database_url=settings.database_url,
        tenant_id=source.tenant_id,
        administrative_unit_ids=(unit.id,),
        run_id=uuid.uuid4(),
        source_item_ids=frozen.source_item_ids,
        source_scope_enforced=True,
        record_created_before=frozen.record_created_before,
        catalog_snapshot=catalog.model_dump(mode="json"),
        policy=HermesCallPolicy(
            thinking_enabled=False,
            max_tokens=settings.hermes_max_tokens,
            json_mode=False,
            enabled_toolsets=("village_query", "clarify"),
            repair_attempts=0,
            timeout_seconds=settings.hermes_timeout_seconds,
            max_iterations=8,
        ),
    ):
        if event.event == "answer.completed":
            completed = event.data

    tool_results = [
        result
        for result in completed.get("tool_results", [])
        if isinstance(result, dict)
    ]
    fact_results = [
        result
        for result in tool_results
        if result.get("status") == "success"
        and result.get("tool")
        in {
            "query_metric",
            "execute_safe_query",
            "execute_bounded_query",
            "lookup_records",
            "aggregate_records",
            "summarize_values",
            "query_household",
            "query_postgres",
        }
        and result.get("acceptance_status") not in {
            "candidate_only",
            "empty",
        }
    ]
    fact_result = fact_results[0] if len(fact_results) == 1 else None
    rows = (
        [
            row
            for row in fact_result.get("rows", [])
            if isinstance(row, dict)
        ]
        if fact_result is not None
        else []
    )
    evidence = (
        fact_result.get("evidence_summary", {})
        if fact_result is not None
        else {}
    )
    expected_groups = {
        str(label): int(count)
        for label, count in gold["expected_groups"].items()
    }
    present_groups_passed = all(
        _contains_expected_group(rows, label, count)
        for label, count in expected_groups.items()
        if count > 0
    )
    actual_group_values = {
        value
        for row in rows
        for key, value in row.items()
        if key != "value"
    }
    exact_groups_passed = actual_group_values == set(expected_groups)
    record_count = int(evidence.get("record_count") or 0)
    evidence_passed = (
        record_count == int(gold["expected_record_count"])
        and evidence.get("source_file_count") == 1
        and evidence.get("data_village_count") == 1
    )
    answer_text = str(completed.get("content") or "")
    answer_contract_passed = all(
        label in answer_text and str(count) in answer_text
        for label, count in expected_groups.items()
    )
    failed_fact_attempts = [
        result
        for result in tool_results
        if result.get("status") == "error"
        and result.get("tool")
        in {
            "query_metric",
            "execute_safe_query",
            "execute_bounded_query",
            "query_postgres",
        }
    ]
    passed = (
        fact_result is not None
        and fact_result.get("tool") == gold["expected_tool"]
        and fact_result.get("result_grade") == gold["expected_result_grade"]
        and present_groups_passed
        and exact_groups_passed
        and evidence_passed
        and answer_contract_passed
    )
    observed_deviation = None
    if failed_fact_attempts:
        observed_deviation = (
            f"Hermes 在成功前产生 {len(failed_fact_attempts)} 次事实工具失败。"
        )
    if not passed:
        observed_deviation = (
            "Hermes 工具选择、事实结果、证据或最终答案未满足金标准。"
        )
    return HermesQuestionRegressionReport(
        case_id=str(gold["case_id"]),
        benchmark_membership=str(gold["benchmark_membership"]),
        selected_file=True,
        tool_sequence=tuple(
            str(result.get("tool") or "") for result in tool_results
        ),
        successful_fact_tool=(
            str(fact_result.get("tool")) if fact_result is not None else None
        ),
        result_grade=(
            str(fact_result.get("result_grade"))
            if fact_result is not None
            else None
        ),
        fact_set_code=(
            str(fact_result.get("fact_set_code"))
            if fact_result is not None
            and fact_result.get("fact_set_code") is not None
            else None
        ),
        failed_fact_errors=tuple(
            f"{result.get('tool')}:{result.get('error_code')}:"
            f"{str(result.get('message') or '')[:240]}"
            for result in failed_fact_attempts
        ),
        successful_fact_summaries=tuple(
            {
                "tool": result.get("tool"),
                "result_grade": result.get("result_grade"),
                "fact_set_code": result.get("fact_set_code"),
                "rows": result.get("rows"),
                "evidence_summary": result.get("evidence_summary"),
            }
            for result in fact_results
        ),
        expected_groups=expected_groups,
        record_count=record_count,
        source_file_count=int(evidence.get("source_file_count") or 0),
        data_village_count=(
            int(evidence["data_village_count"])
            if evidence.get("data_village_count") is not None
            else None
        ),
        answer_contract_passed=answer_contract_passed,
        passed=passed,
        reflection=RegressionReflection(
            plan_alignment=(
                "问题经过 Hermes 理解和结构化工具选择，后端校验字段、分组覆盖、"
                "冻结文件、行政村和唯一事实集血缘后编译执行。"
            ),
            hermes_path_exercised=True,
            scope_enforced=True,
            deterministic_result_verified=(
                present_groups_passed
                and exact_groups_passed
                and evidence_passed
            ),
            observed_deviation=observed_deviation,
            next_action=(
                "通过后继续扩充不同问题类型；失败时先修工具契约或提示，"
                "不得把失败题升级为正式能力。"
            ),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one real Hermes question regression with reflection."
    )
    parser.add_argument("gold", type=Path)
    arguments = parser.parse_args()
    gold = json.loads(arguments.gold.read_text(encoding="utf-8"))
    report = asyncio.run(run_hermes_question_regression(gold))
    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
