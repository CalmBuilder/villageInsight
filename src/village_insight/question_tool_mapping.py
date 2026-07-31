from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from village_insight.question_benchmark import (
    QuestionBenchmarkCase,
    load_question_benchmark,
)

QuestionIntent = Literal[
    "record_lookup",
    "count_or_group",
    "numeric_summary",
    "ranking",
    "household_relation",
    "source_schema",
    "document_fact",
]
CoverageStatus = Literal[
    "structured_tool_chain",
    "requires_document_ingestion",
]

_STRUCTURED_SUFFIXES = {".xls", ".xlsx"}
_COUNT_TERMS = re.compile(
    r"多少(?:人|户|位|名|个|条|项目|党员|村民|资金|金额|亩|米|平)?"
    r"|几(?:人|户|口|位|名|个|种|社|组|支部)"
    r"|男女(?:各|比例)|各有多少|分别有多少|占比|比例|总共有|共有多少"
)
_NUMERIC_SUMMARY_TERMS = re.compile(
    r"总共发放|一共发放|共发放|发放多少|总计金额|补贴金额总计|合计金额"
    r"|总金额|一共多少薪酬|一共方法多少薪酬|分别补贴了多少金额|总收入"
    r"|平均|均值|花费多少钱|支出好多钱|资金多少钱|收入大概多少"
)
_RANKING_TERMS = re.compile(
    r"最多|最少|最大|最小|最高|最低|最年长|最年轻|哪个(?:人|支出).*"
    r"(?:多|高|低)|哪一项支出"
)
_HOUSEHOLD_TERMS = re.compile(
    r"户主|家庭成员|家里有几口|家庭有几口|家庭几口|家庭户编号.*(?:都有谁|几口)"
    r"|户编号.*家庭|亲属关系|是什么关系|与户主|配偶|父母亲|父亲|母亲|儿媳"
    r"|女儿|儿子|孩子|监护人|直系亲属|妻子|长女|次子"
)
_SOURCE_SCHEMA_TERMS = re.compile(r"需要填写什么|要填写什么|填写哪些|表头|字段清单")


class QuestionToolRoute(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    sheet_name: str
    source_row: int
    reference_file: str
    question: str
    intents: tuple[QuestionIntent, ...]
    primary_tools: tuple[str, ...]
    fallback_tools: tuple[str, ...]
    coverage_status: CoverageStatus


class QuestionToolMappingReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: Literal["question-tool-mapping/v1"] = (
        "question-tool-mapping/v1"
    )
    benchmark_path: str
    benchmark_sha256: str
    unique_question_count: int
    route_count: int
    structured_question_count: int
    document_question_count: int
    intent_counts: dict[str, int]
    primary_tool_counts: dict[str, int]
    routes: tuple[QuestionToolRoute, ...]


def _suffix(reference_file: str) -> str:
    return Path(reference_file.strip()).suffix.lower()


def _append_unique[T](values: list[T], value: T) -> None:
    if value not in values:
        values.append(value)


def route_question(case: QuestionBenchmarkCase) -> QuestionToolRoute:
    occurrence = case.occurrences[0]
    question = occurrence.question
    if _suffix(occurrence.reference_file) not in _STRUCTURED_SUFFIXES:
        return QuestionToolRoute(
            case_id=case.case_id,
            sheet_name=occurrence.sheet_name,
            source_row=occurrence.source_row,
            reference_file=occurrence.reference_file,
            question=question,
            intents=("document_fact",),
            primary_tools=("search_document_facts",),
            fallback_tools=(),
            coverage_status="requires_document_ingestion",
        )

    intents: list[QuestionIntent] = []
    tools: list[str] = []
    if _SOURCE_SCHEMA_TERMS.search(question):
        _append_unique(intents, "source_schema")
        _append_unique(tools, "describe_source_fields")
    if _HOUSEHOLD_TERMS.search(question):
        _append_unique(intents, "household_relation")
        _append_unique(tools, "query_household")
    if (
        _RANKING_TERMS.search(question)
        and "最低生活保障" not in question
    ):
        _append_unique(intents, "ranking")
        _append_unique(tools, "rank_records")
    if _NUMERIC_SUMMARY_TERMS.search(question):
        _append_unique(intents, "numeric_summary")
        _append_unique(tools, "summarize_values")
    if _COUNT_TERMS.search(question):
        _append_unique(intents, "count_or_group")
        _append_unique(tools, "aggregate_records")
    if not intents or re.search(
        r"谁|是否|是不是|什么|哪里|多久|时间|日期|号码|编号|状态|地址|职务"
        r"|学历|民族|收入|金额|学校|支部|类型|原因|折扣|账号|联系电话",
        question,
    ):
        _append_unique(intents, "record_lookup")
        _append_unique(tools, "lookup_records")

    return QuestionToolRoute(
        case_id=case.case_id,
        sheet_name=occurrence.sheet_name,
        source_row=occurrence.source_row,
        reference_file=occurrence.reference_file,
        question=question,
        intents=tuple(intents),
        primary_tools=tuple(tools),
        fallback_tools=(
            "lookup_source_records",
            "query_postgres",
            "execute_code",
        ),
        coverage_status="structured_tool_chain",
    )


def build_question_tool_mapping(
    benchmark_path: Path,
) -> QuestionToolMappingReport:
    corpus = load_question_benchmark(benchmark_path)
    routes = tuple(route_question(case) for case in corpus.cases)
    intent_counts = Counter(
        intent for route in routes for intent in route.intents
    )
    tool_counts = Counter(
        tool for route in routes for tool in route.primary_tools
    )
    structured_count = sum(
        route.coverage_status == "structured_tool_chain"
        for route in routes
    )
    return QuestionToolMappingReport(
        benchmark_path=corpus.workbook_path,
        benchmark_sha256=corpus.workbook_sha256,
        unique_question_count=corpus.unique_question_count,
        route_count=len(routes),
        structured_question_count=structured_count,
        document_question_count=len(routes) - structured_count,
        intent_counts=dict(sorted(intent_counts.items())),
        primary_tool_counts=dict(sorted(tool_counts.items())),
        routes=routes,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Map every real benchmark question to reusable tools."
    )
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = build_question_tool_mapping(arguments.workbook)
    rendered = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    if arguments.output is None:
        print(rendered)
        return
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(arguments.output)


if __name__ == "__main__":
    main()
