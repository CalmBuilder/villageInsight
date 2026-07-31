from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from village_insight.db.models import IngestionItem
from village_insight.db.session import get_session_factory
from village_insight.question_catalog import build_question_catalog
from village_insight.question_scope import freeze_question_scope
from village_insight.questions import MetricQuery, MetricQueryScope, execute_metric_query
from village_insight.regression_reflection import RegressionReflection
from village_insight.safe_query import SafeQueryPlan, execute_safe_query


class PartyMemberContractRegressionError(AssertionError):
    pass


class PartyMemberCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    benchmark_case_ids: tuple[str, ...]
    result: int | list[dict[str, Any]]
    record_count: int
    source_file_count: int
    data_village_count: int
    passed: bool


class PartyMemberContractRegressionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: str = "party-member-contract-regression/v1"
    benchmark_membership: str
    mother_case_count: int
    passed_case_count: int
    fact_set_code: str
    fact_set_version: int
    metric_code: str
    metric_value: int
    metric_grade: str
    cases: tuple[PartyMemberCaseResult, ...]
    passed: bool
    reflection: RegressionReflection


def _expect_equal(name: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise PartyMemberContractRegressionError(
            f"{name} mismatch: expected {expected!r}, got {actual!r}"
        )


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def run_party_member_contract_regression(
    database: Session,
    gold: dict[str, Any],
) -> PartyMemberContractRegressionReport:
    snapshot = gold["dataset_snapshot"]
    source = database.scalar(
        select(IngestionItem).where(
            IngestionItem.source_sha256 == snapshot["source_sha256"]
        )
    )
    if source is None:
        raise PartyMemberContractRegressionError("gold source is unavailable")
    _expect_equal("source file", source.original_name, snapshot["source_file"])

    frozen = freeze_question_scope(
        database,
        tenant_id=source.tenant_id,
        administrative_unit_ids=(source.administrative_unit_id,),
        selected_source_item_id=source.id,
        record_created_before=datetime.now(UTC),
    )
    catalog = build_question_catalog(database, frozen)
    fact_set_code = str(gold["expected_fact_set_code"])
    fact_set_version = int(gold["expected_fact_set_version"])
    fact_entry = next(
        (
            item
            for item in catalog.fact_sets
            if item.code == fact_set_code
            and item.version == fact_set_version
        ),
        None,
    )
    if fact_entry is None or fact_entry.governance_status != "published":
        raise PartyMemberContractRegressionError(
            "gold fact set is not published in the frozen catalog"
        )
    scope = MetricQueryScope(
        tenant_id=source.tenant_id,
        administrative_unit_ids=frozenset(
            {source.administrative_unit_id}
        ),
        source_item_ids=frozenset(frozen.source_item_ids),
        source_scope_enforced=True,
        record_created_before=frozen.record_created_before,
    )
    metric = execute_metric_query(
        database,
        MetricQuery(
            metric_code=str(gold["expected_metric"]),
            metric_version=1,
        ),
        scope,
    )
    _expect_equal("official total", metric.value, gold["expected_total"])
    _expect_equal("metric grade", metric.result_grade, "official_metric")
    if not isinstance(metric.value, int):
        raise PartyMemberContractRegressionError(
            "party-member total must be an integer"
        )

    results: list[PartyMemberCaseResult] = []
    for case in gold["cases"]:
        answer = execute_safe_query(
            database,
            SafeQueryPlan.model_validate(case["safe_query_plan"]),
            catalog_snapshot=catalog.model_dump(mode="json"),
            scope_snapshot_fingerprint=frozen.source_item_fingerprint,
            scope=scope,
        )
        actual: int | list[dict[str, Any]]
        expected: int | list[dict[str, Any]]
        if answer.result_type == "table":
            actual = _normalize_rows(answer.rows)
            expected = _normalize_rows(case["expected_rows"])
        else:
            if not isinstance(answer.value, int):
                raise PartyMemberContractRegressionError(
                    f"{case['case_id']} did not return an integer"
                )
            actual = answer.value
            expected = int(case["expected_result"])
        _expect_equal(f"{case['case_id']} result", actual, expected)
        _expect_equal(
            f"{case['case_id']} record count",
            answer.record_count,
            int(case["expected_record_count"]),
        )
        _expect_equal(
            f"{case['case_id']} source count",
            answer.source_file_count,
            1,
        )
        _expect_equal(
            f"{case['case_id']} village count",
            answer.data_village_count,
            1,
        )
        results.append(
            PartyMemberCaseResult(
                case_id=str(case["case_id"]),
                benchmark_case_ids=tuple(case["benchmark_case_ids"]),
                result=actual,
                record_count=answer.record_count,
                source_file_count=answer.source_file_count,
                data_village_count=answer.data_village_count,
                passed=True,
            )
        )
    mother_case_ids = {
        benchmark_case_id
        for result in results
        for benchmark_case_id in result.benchmark_case_ids
    }
    _expect_equal(
        "mother case count",
        len(mother_case_ids),
        int(gold["expected_mother_case_count"]),
    )
    return PartyMemberContractRegressionReport(
        benchmark_membership=str(gold["benchmark_membership"]),
        mother_case_count=len(mother_case_ids),
        passed_case_count=len(mother_case_ids),
        fact_set_code=fact_set_code,
        fact_set_version=fact_set_version,
        metric_code=metric.metric_code,
        metric_value=metric.value,
        metric_grade=metric.result_grade,
        cases=tuple(results),
        passed=True,
        reflection=RegressionReflection(
            plan_alignment=(
                "两道母题均从冻结单文件范围经正式指标或 SafeQuery 执行。"
            ),
            hermes_path_exercised=False,
            scope_enforced=True,
            deterministic_result_verified=True,
            observed_deviation=(
                "本回归绕过了 Hermes，只能证明查询工具正确。"
            ),
            next_action="必须增加同题 Hermes 工具选择和最终回答端到端回归。",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run deterministic party-roster mother-corpus cases."
    )
    parser.add_argument("gold", type=Path)
    arguments = parser.parse_args()
    gold = json.loads(arguments.gold.read_text(encoding="utf-8"))
    with get_session_factory()() as database:
        report = run_party_member_contract_regression(database, gold)
    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
