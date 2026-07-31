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
from village_insight.questions import (
    MetricQuery,
    MetricQueryScope,
    execute_metric_query,
)
from village_insight.regression_reflection import RegressionReflection
from village_insight.safe_query import (
    SafeQueryPlan,
    execute_safe_query,
)


class PopulationContractRegressionError(AssertionError):
    pass


class PopulationContractRegressionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: str = "population-contract-regression/v1"
    case_id: str
    benchmark_membership: str
    fact_set_code: str
    fact_set_version: int
    metric_code: str
    metric_value: int
    metric_grade: str
    safe_query_value: int
    record_count: int
    source_file_count: int
    data_village_count: int
    passed: bool
    reflection: RegressionReflection


def _expect_equal(name: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise PopulationContractRegressionError(
            f"{name} mismatch: expected {expected!r}, got {actual!r}"
        )


def run_population_contract_regression(
    database: Session,
    case: dict[str, Any],
) -> PopulationContractRegressionReport:
    snapshot = case["dataset_snapshot"]
    source = database.scalar(
        select(IngestionItem).where(
            IngestionItem.source_sha256 == snapshot["source_sha256"]
        )
    )
    if source is None:
        raise PopulationContractRegressionError(
            "gold source is unavailable"
        )
    _expect_equal(
        "source file",
        source.original_name,
        snapshot["source_file"],
    )
    frozen = freeze_question_scope(
        database,
        tenant_id=source.tenant_id,
        administrative_unit_ids=(source.administrative_unit_id,),
        selected_source_item_id=source.id,
        record_created_before=datetime.now(UTC),
    )
    catalog = build_question_catalog(database, frozen)
    fact_set_code = str(case["expected_fact_set_code"])
    fact_set_version = int(case["expected_fact_set_version"])
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
        raise PopulationContractRegressionError(
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
            metric_code=str(case["expected_metric"]),
            metric_version=1,
        ),
        scope,
    )
    safe_answer = execute_safe_query(
        database,
        SafeQueryPlan(
            operation="count",
            fact_set_code=fact_set_code,
            fact_set_version=fact_set_version,
            record_type=fact_entry.record_type,
        ),
        catalog_snapshot=catalog.model_dump(mode="json"),
        scope_snapshot_fingerprint=frozen.source_item_fingerprint,
        scope=scope,
    )
    expected_result = int(case["expected_result"])
    _expect_equal("official metric", metric.value, expected_result)
    _expect_equal(
        "metric grade",
        metric.result_grade,
        case["expected_tool_grade"],
    )
    _expect_equal("safe query", safe_answer.value, expected_result)
    _expect_equal(
        "record count",
        safe_answer.record_count,
        int(case["expected_record_count"]),
    )
    _expect_equal(
        "source file count",
        safe_answer.source_file_count,
        int(case["expected_source_file_count"]),
    )
    _expect_equal(
        "data village count",
        safe_answer.data_village_count,
        int(case["expected_data_village_count"]),
    )
    if not isinstance(metric.value, int) or not isinstance(
        safe_answer.value,
        int,
    ):
        raise PopulationContractRegressionError(
            "population count results must be integers"
        )
    return PopulationContractRegressionReport(
        case_id=str(case["case_id"]),
        benchmark_membership=str(case["benchmark_membership"]),
        fact_set_code=fact_set_code,
        fact_set_version=fact_set_version,
        metric_code=metric.metric_code,
        metric_value=metric.value,
        metric_grade=metric.result_grade,
        safe_query_value=safe_answer.value,
        record_count=safe_answer.record_count,
        source_file_count=safe_answer.source_file_count,
        data_village_count=safe_answer.data_village_count,
        passed=True,
        reflection=RegressionReflection(
            plan_alignment=(
                "正式指标与 SafeQuery 均从冻结单文件范围确定性执行。"
            ),
            hermes_path_exercised=False,
            scope_enforced=True,
            deterministic_result_verified=True,
            observed_deviation=(
                "本回归只验证工具与数据契约，没有经过 Hermes 理解和调度。"
            ),
            next_action="另行执行 Hermes 端到端回归，不能据此计算模型准确率。",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the deterministic population contract gold case."
    )
    parser.add_argument("case", type=Path)
    arguments = parser.parse_args()
    case = json.loads(arguments.case.read_text(encoding="utf-8"))
    with get_session_factory()() as database:
        report = run_population_contract_regression(database, case)
    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
