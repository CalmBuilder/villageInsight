from __future__ import annotations

import argparse
import json
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    DatasetRecord,
    IngestionItem,
    QueryFactSetDefinition,
    RecordIndexValue,
    SemanticField,
    SemanticFieldVersion,
    SemanticManifestDefinition,
)
from village_insight.db.session import get_session_factory
from village_insight.question_benchmark import (
    QuestionBenchmarkCase,
    load_question_benchmark,
)

BenchmarkCategory = Literal[
    "governed_executable",
    "hermes_bounded_query_candidate",
    "hermes_semantic_assessment_required",
    "data_not_ingested",
    "should_clarify",
    "should_refuse",
    "sensitive_permission_blocked",
]
_BENCHMARK_CATEGORIES: tuple[BenchmarkCategory, ...] = (
    "governed_executable",
    "hermes_bounded_query_candidate",
    "hermes_semantic_assessment_required",
    "data_not_ingested",
    "should_clarify",
    "should_refuse",
    "sensitive_permission_blocked",
)

_FILE_NOISE = re.compile(r"[\s._\-—–·（）()【】\\/\[\]]+")
_SENSITIVE_TERMS = (
    "身份证",
    "身份证号",
    "公民身份号码",
    "手机号",
    "电话号码",
    "银行卡",
    "银行账号",
    "一卡通号",
    "社保卡号",
    "残疾证号",
    "对公账户",
    "户编号",
    "手机",
)
_AMBIGUOUS_TERMS = ("这个人", "该人员", "上述人员", "此人", "该户")
_UNSUPPORTED_RELATION_TERMS = (
    "亲属关系",
    "家庭成员关系",
    "户主与",
    "同一人",
    "重复人员",
    "跨表",
    "同比",
    "环比",
    "增长率",
    "变化趋势",
    "历年",
)
_REFUSAL_TERMS = ("预测", "推测", "猜测", "为什么会", "应该如何")
_GENERIC_QUERY_TERMS = (
    "多少",
    "总数",
    "数量",
    "几人",
    "几户",
    "名单",
    "哪些",
    "最高",
    "最低",
    "分别",
    "各村",
)
_NUMERIC_AGGREGATE_TERMS = (
    "总金额",
    "合计金额",
    "金额共",
    "总收入",
    "合计收入",
    "平均",
    "均值",
)


class BenchmarkCapabilityInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: uuid.UUID
    record_created_before: datetime
    source_keys: frozenset[str]
    field_terms: dict[str, tuple[str, ...]]
    field_data_types: dict[str, str]
    actual_field_codes: frozenset[str] = frozenset()
    source_field_codes: dict[str, tuple[str, ...]] = Field(
        default_factory=dict
    )
    source_fact_set_keys: dict[str, tuple[str, ...]] = Field(
        default_factory=dict
    )
    published_queryable_field_codes: frozenset[str] = frozenset()
    source_queryable_field_codes: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    approved_record_count: int
    approved_source_count: int


class BenchmarkClassification(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    category: BenchmarkCategory
    reason_code: str
    reference_match: Literal["matched", "unmatched", "not_specified"]
    matched_field_codes: tuple[str, ...]


class BenchmarkClassificationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: str = "question-benchmark-classification/v2"
    benchmark_sha256: str
    tenant_id: uuid.UUID
    record_created_before: datetime
    classification_status: Literal["provisional"] = "provisional"
    unique_question_count: int
    approved_record_count: int
    approved_source_count: int
    category_counts: dict[str, int]
    cases: tuple[BenchmarkClassification, ...]


def _file_key(value: str) -> str:
    name = Path(value.strip()).name.casefold()
    for suffix in (".xlsx", ".xls", ".csv", ".pdf", ".docx", ".doc"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return _FILE_NOISE.sub("", name)


def _matches_fact_provenance(
    row: Any,
    fact_set: QueryFactSetDefinition,
) -> bool:
    if row.record_type != fact_set.record_type:
        return False
    rule = fact_set.provenance_rule
    expected_id = str(rule.get("id") or "")
    if rule.get("kind") == "region_template":
        return str(
            row.region_template_id
        ) == expected_id and row.region_template_version == rule.get("version")
    if rule.get("kind") == "document_template":
        return str(row.template_id) == expected_id and row.template_version == rule.get("version")
    if rule.get("kind") == "approved_plan":
        return str(row.approved_plan_id) == expected_id
    return False


def load_capability_inventory(
    database: Session,
    *,
    tenant_id: uuid.UUID,
) -> BenchmarkCapabilityInventory:
    record_created_before = datetime.now(UTC)
    scoped_record = (
        DatasetRecord.tenant_id == tenant_id,
        DatasetRecord.quality_status == "passed",
        DatasetRecord.created_at <= record_created_before,
    )
    source_names = list(
        database.scalars(
            select(IngestionItem.original_name)
            .join(DatasetRecord, DatasetRecord.item_id == IngestionItem.id)
            .where(*scoped_record)
            .distinct()
        )
    )
    actual_field_versions = (
        select(
            RecordIndexValue.semantic_field_code.label("field_code"),
            RecordIndexValue.semantic_field_version.label("field_version"),
        )
        .join(
            DatasetRecord,
            DatasetRecord.id == RecordIndexValue.record_id,
        )
        .where(*scoped_record)
        .distinct()
        .subquery()
    )
    field_rows = database.execute(
        select(
            SemanticField.code,
            SemanticFieldVersion.name,
            SemanticFieldVersion.aliases,
            SemanticFieldVersion.data_type,
        )
        .join(
            SemanticFieldVersion,
            SemanticFieldVersion.field_id == SemanticField.id,
        )
        .join(
            actual_field_versions,
            (actual_field_versions.c.field_code == SemanticField.code)
            & (actual_field_versions.c.field_version == SemanticFieldVersion.version),
        )
        .where(SemanticField.published_version == SemanticFieldVersion.version)
    ).all()
    field_terms: dict[str, set[str]] = {}
    field_data_types: dict[str, str] = {}
    for code, name, aliases, data_type in field_rows:
        terms = field_terms.setdefault(code, set())
        field_data_types[code] = data_type
        for term in [name, *(aliases or [])]:
            normalized = str(term).strip()
            if len(normalized) >= 2:
                terms.add(normalized)
    counts = database.execute(
        select(
            func.count(distinct(DatasetRecord.id)),
            func.count(distinct(DatasetRecord.item_id)),
        ).where(*scoped_record)
    ).one()
    published_contract_rows = list(
        database.execute(
            select(QueryFactSetDefinition, SemanticManifestDefinition)
            .join(
                SemanticManifestDefinition,
                (SemanticManifestDefinition.fact_set_code == QueryFactSetDefinition.code)
                & (SemanticManifestDefinition.fact_set_version == QueryFactSetDefinition.version)
                & (
                    SemanticManifestDefinition.catalog_fingerprint
                    == QueryFactSetDefinition.catalog_fingerprint
                ),
            )
            .where(
                QueryFactSetDefinition.status == "published",
                SemanticManifestDefinition.status == "published",
            )
        )
    )
    published_queryable_field_codes: set[str] = set()
    for fact_set, manifest in published_contract_rows:
        published_queryable_field_codes.update(fact_set.identity_field_codes)
        published_queryable_field_codes.update(fact_set.dimension_field_codes)
        published_queryable_field_codes.update(
            str(measure.get("field_code"))
            for measure in fact_set.measure_definitions
            if measure.get("field_code")
        )
        published_queryable_field_codes.update(
            str(dimension.get("field_code"))
            for dimension in manifest.dimensions
            if dimension.get("field_code")
        )
        published_queryable_field_codes.update(
            str(measure.get("field_code"))
            for measure in manifest.measures
            if measure.get("field_code")
        )
    source_queryable_fields: dict[str, set[str]] = {}
    source_fields: dict[str, set[str]] = {}
    source_fact_sets: dict[str, set[str]] = {}
    source_fact_rows = database.execute(
        select(
            IngestionItem.original_name,
            DatasetRecord.record_type,
            DatasetRecord.region_template_id,
            DatasetRecord.region_template_version,
            DatasetRecord.template_id,
            DatasetRecord.template_version,
            DatasetRecord.approved_plan_id,
            RecordIndexValue.semantic_field_code,
        )
        .join(DatasetRecord, DatasetRecord.item_id == IngestionItem.id)
        .join(
            RecordIndexValue,
            RecordIndexValue.record_id == DatasetRecord.id,
        )
        .where(*scoped_record)
        .distinct()
    )
    published_fact_sets = [fact_set for fact_set, _ in published_contract_rows]
    for row in source_fact_rows:
        source_key = _file_key(row.original_name)
        if not source_key:
            continue
        source_fields.setdefault(source_key, set()).add(
            row.semantic_field_code
        )
        if row.region_template_id is not None:
            provenance = (
                f"region_template:{row.region_template_id}:"
                f"{row.region_template_version}"
            )
        elif row.template_id is not None:
            provenance = (
                f"document_template:{row.template_id}:"
                f"{row.template_version}"
            )
        else:
            provenance = f"approved_plan:{row.approved_plan_id}"
        source_fact_sets.setdefault(source_key, set()).add(
            f"{provenance}|record_type:{row.record_type}"
        )
        matching_fact_sets = [
            fact_set for fact_set in published_fact_sets if _matches_fact_provenance(row, fact_set)
        ]
        if not matching_fact_sets:
            continue
        for fact_set in matching_fact_sets:
            queryable_codes = {
                *fact_set.identity_field_codes,
                *fact_set.dimension_field_codes,
                *(
                    str(measure.get("field_code"))
                    for measure in fact_set.measure_definitions
                    if measure.get("field_code")
                ),
            }
            if row.semantic_field_code in queryable_codes:
                source_queryable_fields.setdefault(source_key, set()).add(row.semantic_field_code)
    return BenchmarkCapabilityInventory(
        tenant_id=tenant_id,
        record_created_before=record_created_before,
        source_keys=frozenset(key for name in source_names if (key := _file_key(name))),
        field_terms={code: tuple(sorted(terms)) for code, terms in sorted(field_terms.items())},
        field_data_types=field_data_types,
        actual_field_codes=frozenset(field_data_types),
        source_field_codes={
            source_key: tuple(sorted(codes))
            for source_key, codes in sorted(source_fields.items())
        },
        source_fact_set_keys={
            source_key: tuple(sorted(keys))
            for source_key, keys in sorted(source_fact_sets.items())
        },
        published_queryable_field_codes=frozenset(published_queryable_field_codes),
        source_queryable_field_codes={
            source_key: tuple(sorted(codes))
            for source_key, codes in sorted(source_queryable_fields.items())
        },
        approved_record_count=int(counts[0]),
        approved_source_count=int(counts[1]),
    )


def classify_benchmark_case(
    case: QuestionBenchmarkCase,
    inventory: BenchmarkCapabilityInventory,
    *,
    enforce_sensitive_policy: bool = False,
) -> BenchmarkClassification:
    question = case.normalized_question
    reference_keys = {
        key for occurrence in case.occurrences if (key := _file_key(occurrence.reference_file))
    }
    reference_match: Literal["matched", "unmatched", "not_specified"]
    matching_source_keys = {
        source
        for reference in reference_keys
        for source in inventory.source_keys
        if min(len(reference), len(source)) >= 4 and (reference in source or source in reference)
    }
    if not reference_keys:
        reference_match = "not_specified"
    elif matching_source_keys:
        reference_match = "matched"
    else:
        reference_match = "unmatched"
    matched_field_codes = tuple(
        code
        for code, terms in inventory.field_terms.items()
        if any(term in question for term in terms)
    )
    queryable_field_codes = (
        {
            code
            for source_key in matching_source_keys
            for code in inventory.source_queryable_field_codes.get(
                source_key,
                (),
            )
        }
        if reference_keys
        else set(inventory.published_queryable_field_codes)
    )
    actual_field_codes = (
        {
            code
            for source_key in matching_source_keys
            for code in inventory.source_field_codes.get(source_key, ())
        }
        if reference_keys
        else set(inventory.actual_field_codes)
    )
    matching_fact_set_keys = {
        key
        for source_key in matching_source_keys
        for key in inventory.source_fact_set_keys.get(source_key, ())
    }
    matched_actual_fields = set(matched_field_codes) & actual_field_codes

    category: BenchmarkCategory
    reason_code: str
    if enforce_sensitive_policy and any(term in question for term in _SENSITIVE_TERMS):
        category = "sensitive_permission_blocked"
        reason_code = "contains_direct_sensitive_identifier"
    elif any(term in question for term in _AMBIGUOUS_TERMS):
        category = "should_clarify"
        reason_code = "standalone_question_has_unresolved_reference"
    elif any(term in question for term in _REFUSAL_TERMS):
        category = "should_refuse"
        reason_code = "requests_prediction_or_causal_judgement"
    elif reference_match == "unmatched":
        category = "data_not_ingested"
        reason_code = "referenced_file_not_found_in_approved_records"
    elif any(term in question for term in _UNSUPPORTED_RELATION_TERMS):
        category = "hermes_semantic_assessment_required"
        reason_code = "requires_unpublished_relation_or_time_contract"
    elif (
        any(term in question for term in _NUMERIC_AGGREGATE_TERMS)
        and matched_field_codes
        and all(
            inventory.field_data_types.get(code) not in {"integer", "decimal"}
            for code in matched_field_codes
        )
    ):
        category = "hermes_semantic_assessment_required"
        reason_code = "numeric_measure_is_not_typed"
    elif (
        matched_field_codes
        and any(code in queryable_field_codes for code in matched_field_codes)
        and any(term in question for term in _GENERIC_QUERY_TERMS)
    ):
        category = "governed_executable"
        reason_code = "published_contract_and_queryable_field_present"
    elif (
        matched_actual_fields
        and any(term in question for term in _GENERIC_QUERY_TERMS)
        and len(matching_fact_set_keys) == 1
    ):
        category = "hermes_bounded_query_candidate"
        reason_code = "single_fact_set_and_approved_field_present"
    elif matched_actual_fields and len(matching_fact_set_keys) != 1:
        category = "should_clarify"
        reason_code = "bounded_query_fact_set_is_not_unique"
    elif matched_field_codes:
        category = "hermes_semantic_assessment_required"
        reason_code = "matching_field_is_not_present_in_referenced_data"
    else:
        category = "hermes_semantic_assessment_required"
        reason_code = "no_queryable_approved_field_match"
    return BenchmarkClassification(
        case_id=case.case_id,
        category=category,
        reason_code=reason_code,
        reference_match=reference_match,
        matched_field_codes=matched_field_codes,
    )


def build_classification_report(
    benchmark_path: Path,
    inventory: BenchmarkCapabilityInventory,
    *,
    enforce_sensitive_policy: bool = False,
) -> BenchmarkClassificationReport:
    corpus = load_question_benchmark(benchmark_path)
    cases = tuple(
        classify_benchmark_case(
            case,
            inventory,
            enforce_sensitive_policy=enforce_sensitive_policy,
        )
        for case in corpus.cases
    )
    category_counts = Counter(case.category for case in cases)
    return BenchmarkClassificationReport(
        benchmark_sha256=corpus.workbook_sha256,
        tenant_id=inventory.tenant_id,
        record_created_before=inventory.record_created_before,
        unique_question_count=corpus.unique_question_count,
        approved_record_count=inventory.approved_record_count,
        approved_source_count=inventory.approved_source_count,
        category_counts={category: category_counts[category] for category in _BENCHMARK_CATEGORIES},
        cases=cases,
    )


def _resolve_tenant_id(database: Session, value: str | None) -> uuid.UUID:
    if value:
        return uuid.UUID(value)
    tenant_ids = tuple(
        database.scalars(
            select(DatasetRecord.tenant_id)
            .where(DatasetRecord.quality_status == "passed")
            .distinct()
        )
    )
    if len(tenant_ids) != 1:
        raise ValueError("--tenant-id is required unless exactly one tenant has data")
    return tenant_ids[0]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify the real question corpus without printing question text."
    )
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--tenant-id")
    parser.add_argument(
        "--include-case-details",
        action="store_true",
        help="Include case IDs and reason codes; question text is never emitted.",
    )
    parser.add_argument(
        "--enforce-sensitive-policy",
        action="store_true",
        help="Classify direct identifiers as blocked; disabled by default.",
    )
    arguments = parser.parse_args()
    session_factory = get_session_factory()
    with session_factory() as database:
        tenant_id = _resolve_tenant_id(database, arguments.tenant_id)
        inventory = load_capability_inventory(database, tenant_id=tenant_id)
        report = build_classification_report(
            arguments.workbook,
            inventory,
            enforce_sensitive_policy=arguments.enforce_sensitive_policy,
        )
    payload = report.model_dump(mode="json")
    if not arguments.include_case_details:
        payload.pop("cases", None)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
