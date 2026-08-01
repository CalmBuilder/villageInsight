from __future__ import annotations

from pathlib import Path

from village_insight.question_benchmark import (
    QuestionBenchmarkCase,
    QuestionBenchmarkOccurrence,
)
from village_insight.question_tool_mapping import (
    build_question_tool_mapping,
    route_question,
)


def _case(question: str, reference_file: str) -> QuestionBenchmarkCase:
    occurrence = QuestionBenchmarkOccurrence(
        sheet_name="测试",
        source_row=2,
        village_name="测试村",
        question=question,
        normalized_question=question,
        reference_file=reference_file,
        duplicate_group="duplicate-test",
        historical={},
    )
    return QuestionBenchmarkCase(
        case_id="question-test",
        normalized_question=question,
        occurrences=(occurrence,),
    )


def test_route_question_combines_reusable_tools() -> None:
    route = route_question(
        _case(
            "户编号为100的家庭有几口人，都有谁",
            "人口.xlsx",
        )
    )

    assert route.intents == (
        "household_relation",
        "count_or_group",
        "record_lookup",
    )
    assert route.primary_tools == (
        "query_household",
        "aggregate_records",
        "lookup_records",
    )
    assert route.fallback_tools[-1] == "execute_code"


def test_route_question_marks_document_ingestion_boundary() -> None:
    route = route_question(_case("村史是什么", "村史.docx"))

    assert route.primary_tools == ("search_document_facts",)
    assert route.coverage_status == "requires_document_ingestion"


def test_real_benchmark_has_one_route_per_unique_question() -> None:
    report = build_question_tool_mapping(
        Path("docs/datafiles/济南院-查村情测试清单.xlsx")
    )

    assert report.unique_question_count == 237
    assert report.route_count == 237
    assert report.structured_question_count == 163
    assert report.document_question_count == 74
