from __future__ import annotations

import uuid
from datetime import UTC, datetime

from village_insight.question_benchmark import (
    QuestionBenchmarkCase,
    QuestionBenchmarkOccurrence,
)
from village_insight.question_benchmark_classification import (
    BenchmarkCapabilityInventory,
    classify_benchmark_case,
)


def _case(question: str, reference_file: str = "") -> QuestionBenchmarkCase:
    return QuestionBenchmarkCase(
        case_id=f"question-{uuid.uuid4().hex}",
        normalized_question=question,
        occurrences=(
            QuestionBenchmarkOccurrence(
                sheet_name="测试村",
                source_row=2,
                village_name="测试村",
                question=question,
                normalized_question=question,
                reference_file=reference_file,
                duplicate_group="group-1",
                historical={},
            ),
        ),
    )


def _inventory() -> BenchmarkCapabilityInventory:
    return BenchmarkCapabilityInventory(
        tenant_id=uuid.uuid4(),
        record_created_before=datetime.now(UTC),
        source_keys=frozenset({"人口明细表"}),
        field_terms={
            "population.count": ("人口数量", "人数"),
            "person.name": ("姓名",),
        },
        field_data_types={
            "population.count": "integer",
            "person.name": "text",
        },
        actual_field_codes=frozenset(
            {"population.count", "person.name"}
        ),
        source_field_codes={
            "人口明细表": ("person.name", "population.count"),
        },
        source_fact_set_keys={
            "人口明细表": ("document_template:one|record_type:person",),
        },
        published_queryable_field_codes=frozenset({"population.count"}),
        source_queryable_field_codes={
            "人口明细表": ("population.count",),
        },
        approved_record_count=10,
        approved_source_count=1,
    )


def test_classification_requires_approved_data_and_queryable_field() -> None:
    executable = classify_benchmark_case(
        _case("人口数量总数是多少？", "人口明细表.xlsx"),
        _inventory(),
    )
    missing_data = classify_benchmark_case(
        _case("人口数量总数是多少？", "尚未导入表.xlsx"),
        _inventory(),
    )
    unsupported = classify_benchmark_case(
        _case("历年人口数量变化趋势是什么？", "人口明细表.xlsx"),
        _inventory(),
    )

    assert executable.category == "governed_executable"
    assert executable.matched_field_codes == ("population.count",)
    assert missing_data.category == "data_not_ingested"
    assert unsupported.category == "hermes_semantic_assessment_required"


def test_matching_data_without_contract_is_a_hermes_query_candidate() -> None:
    inventory = _inventory().model_copy(
        update={
            "published_queryable_field_codes": frozenset(),
            "source_queryable_field_codes": {},
        }
    )
    result = classify_benchmark_case(
        _case("人口数量总数是多少？", "人口明细表.xlsx"),
        inventory,
    )

    assert result.category == "hermes_bounded_query_candidate"
    assert result.reason_code == "single_fact_set_and_approved_field_present"


def test_sensitive_and_ambiguous_questions_take_precedence() -> None:
    sensitive = classify_benchmark_case(
        _case("某人的身份证号是多少？", "尚未导入表.xlsx"),
        _inventory(),
        enforce_sensitive_policy=True,
    )
    ambiguous = classify_benchmark_case(
        _case("该人员的人数是多少？", "人口明细表.xlsx"),
        _inventory(),
    )
    formal_id_label = classify_benchmark_case(
        _case("某人的公民身份号码是什么？", "尚未导入表.xlsx"),
        _inventory(),
        enforce_sensitive_policy=True,
    )

    assert sensitive.category == "sensitive_permission_blocked"
    assert sensitive.reason_code == "contains_direct_sensitive_identifier"
    assert formal_id_label.category == "sensitive_permission_blocked"
    assert formal_id_label.reason_code == "contains_direct_sensitive_identifier"
    assert ambiguous.category == "should_clarify"
