from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    AdministrativeUnit,
    DatasetRecord,
    MetricDefinition,
    QueryFactSetDefinition,
    RecordIndexValue,
    SemanticField,
    SemanticFieldVersion,
    SemanticManifestDefinition,
)
from village_insight.question_scope import FrozenQuestionScope


class QuestionCatalogField(BaseModel):
    code: str
    version: int
    name: str
    description: str
    data_type: str
    unit: str | None
    aliases: list[str] = Field(default_factory=list)
    fact_set_codes: list[str] = Field(default_factory=list)


class QuestionFactSet(BaseModel):
    code: str
    version: int = 1
    name: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    governance_status: str = "derived"
    record_type: str
    grain: str = "dataset_record"
    identity_field_codes: list[str] = Field(default_factory=list)
    dimension_field_codes: list[str] = Field(default_factory=list)
    semantic_manifest_code: str | None = None
    semantic_manifest_version: int | None = None
    record_count: int
    source_file_count: int
    administrative_unit_count: int
    field_codes: list[str] = Field(default_factory=list)
    execution_provenance: dict[str, Any] = Field(default_factory=dict)


class QuestionCatalogMetric(BaseModel):
    code: str
    version: int = 1
    name: str
    description: str
    aggregation: str
    unit: str | None
    aliases: list[str] = Field(default_factory=list)
    allowed_filter_fields: list[str] = Field(default_factory=list)
    compatible_fact_set_codes: list[str] = Field(default_factory=list)
    governance_status: str = "legacy"


class QuestionCatalogSnapshot(BaseModel):
    contract_version: str = "village-query-catalog/v2"
    source_mode: str
    administrative_units: list[str]
    record_created_before: str
    source_item_fingerprint: str
    fact_sets: list[QuestionFactSet]
    fields: list[QuestionCatalogField]
    metrics: list[QuestionCatalogMetric]
    catalog_fingerprint: str = ""


def _matches_fact_set(
    row: Any,
    definition: QueryFactSetDefinition,
) -> bool:
    if definition.record_type != str(row.record_type):
        return False
    rule = definition.provenance_rule
    kind = rule.get("kind")
    expected_id = str(rule.get("id") or "")
    if kind == "region_template":
        return (
            str(row.region_template_id) == expected_id
            and row.region_template_version == rule.get("version")
        )
    if kind == "document_template":
        return (
            str(row.template_id) == expected_id
            and row.template_version == rule.get("version")
        )
    if kind == "approved_plan":
        return str(row.approved_plan_id) == expected_id
    return False


def _fact_identity(
    row: Any,
    definitions: list[QueryFactSetDefinition],
) -> tuple[str, str, QueryFactSetDefinition | None]:
    record_type = str(row.record_type)
    matches = [
        definition
        for definition in definitions
        if _matches_fact_set(row, definition)
    ]
    if len(matches) == 1:
        definition = matches[0]
        return definition.code, record_type, definition
    if row.region_template_id is not None:
        provenance = (
            f"region_template:{row.region_template_id}:"
            f"{row.region_template_version}"
        )
    elif row.template_id is not None:
        provenance = f"document_template:{row.template_id}:{row.template_version}"
    else:
        provenance = f"approved_plan:{row.approved_plan_id}"
    identity = f"{provenance}|record_type:{record_type}"
    code = f"factset.{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
    return code, record_type, None


def _execution_provenance(
    row: Any,
    definition: QueryFactSetDefinition | None,
) -> dict[str, Any]:
    if definition is not None:
        return dict(definition.provenance_rule)
    if row.region_template_id is not None:
        return {
            "kind": "region_template",
            "id": str(row.region_template_id),
            "version": row.region_template_version,
        }
    if row.template_id is not None:
        return {
            "kind": "document_template",
            "id": str(row.template_id),
            "version": row.template_version,
        }
    return {
        "kind": "approved_plan",
        "id": str(row.approved_plan_id),
    }


def _scoped_record_predicates(scope: FrozenQuestionScope) -> tuple[Any, ...]:
    return (
        DatasetRecord.tenant_id == scope.tenant_id,
        DatasetRecord.administrative_unit_id.in_(
            scope.administrative_unit_ids
        ),
        DatasetRecord.item_id.in_(scope.source_item_ids),
        DatasetRecord.quality_status == "passed",
        DatasetRecord.created_at <= scope.record_created_before,
    )


def build_question_catalog(
    database: Session,
    scope: FrozenQuestionScope,
) -> QuestionCatalogSnapshot:
    """Build a compact catalog only from fields present in the frozen fact scope."""

    provenance_columns = (
        DatasetRecord.record_type,
        DatasetRecord.region_template_id,
        DatasetRecord.region_template_version,
        DatasetRecord.template_id,
        DatasetRecord.template_version,
        DatasetRecord.approved_plan_id,
    )
    fact_rows = database.execute(
        select(
            *provenance_columns,
            func.count(distinct(DatasetRecord.id)).label("record_count"),
            func.count(distinct(DatasetRecord.item_id)).label(
                "source_file_count"
            ),
            func.count(
                distinct(DatasetRecord.administrative_unit_id)
            ).label("administrative_unit_count"),
        )
        .where(*_scoped_record_predicates(scope))
        .group_by(*provenance_columns)
        .order_by(DatasetRecord.record_type)
    ).all()
    published_fact_sets = list(
        database.scalars(
            select(QueryFactSetDefinition)
            .where(QueryFactSetDefinition.status == "published")
            .order_by(
                QueryFactSetDefinition.code,
                QueryFactSetDefinition.version.desc(),
            )
        )
    )
    published_manifests = list(
        database.scalars(
            select(SemanticManifestDefinition).where(
                SemanticManifestDefinition.status == "published"
            )
        )
    )
    scoped_field_versions = (
        select(
            *provenance_columns,
            RecordIndexValue.semantic_field_code.label("field_code"),
            RecordIndexValue.semantic_field_version.label("field_version"),
        )
        .join(
            RecordIndexValue,
            RecordIndexValue.record_id == DatasetRecord.id,
        )
        .where(*_scoped_record_predicates(scope))
        .distinct()
        .subquery()
    )
    field_rows = database.execute(
        select(
            *(
                scoped_field_versions.c[column.key]
                for column in provenance_columns
            ),
            SemanticField.code,
            SemanticFieldVersion.version,
            SemanticFieldVersion.name,
            SemanticFieldVersion.description,
            SemanticFieldVersion.data_type,
            SemanticFieldVersion.unit_dimension,
            SemanticFieldVersion.aliases,
        )
        .join(
            SemanticField,
            SemanticField.code == scoped_field_versions.c.field_code,
        )
        .join(
            SemanticFieldVersion,
            (
                SemanticFieldVersion.field_id == SemanticField.id
            )
            & (
                SemanticFieldVersion.version
                == scoped_field_versions.c.field_version
            ),
        )
        .where(
            SemanticField.published_version == SemanticFieldVersion.version,
        )
        .order_by(SemanticField.code)
    ).all()

    fact_sets_by_code: dict[str, QuestionFactSet] = {}
    for row in fact_rows:
        code, record_type, definition = _fact_identity(
            row,
            published_fact_sets,
        )
        manifest = next(
            (
                candidate
                for candidate in published_manifests
                if definition is not None
                and candidate.fact_set_code == definition.code
                and candidate.fact_set_version == definition.version
                and candidate.catalog_fingerprint
                == definition.catalog_fingerprint
            ),
            None,
        )
        fact_sets_by_code[code] = QuestionFactSet(
            code=code,
            version=definition.version if definition is not None else 1,
            name=definition.name if definition is not None else record_type,
            description=definition.description if definition is not None else "",
            aliases=list(definition.aliases) if definition is not None else [],
            governance_status=(
                "published" if definition is not None else "derived"
            ),
            record_type=record_type,
            grain=(
                definition.record_grain
                if definition is not None
                else "dataset_record"
            ),
            identity_field_codes=(
                list(definition.identity_field_codes)
                if definition is not None
                else []
            ),
            dimension_field_codes=(
                list(definition.dimension_field_codes)
                if definition is not None
                else []
            ),
            semantic_manifest_code=manifest.code if manifest else None,
            semantic_manifest_version=manifest.version if manifest else None,
            record_count=int(row.record_count),
            source_file_count=int(row.source_file_count),
            administrative_unit_count=int(row.administrative_unit_count),
            execution_provenance=_execution_provenance(row, definition),
        )

    fields_by_key: dict[tuple[str, int], QuestionCatalogField] = {}
    fact_field_codes: dict[str, set[str]] = {}
    for row in field_rows:
        fact_set_code, _, _ = _fact_identity(row, published_fact_sets)
        fact_field_codes.setdefault(fact_set_code, set()).add(row.code)
        key = (row.code, row.version)
        field_entry = fields_by_key.get(key)
        if field_entry is None:
            field_entry = QuestionCatalogField(
                code=row.code,
                version=row.version,
                name=row.name,
                description=row.description,
                data_type=row.data_type,
                unit=row.unit_dimension,
                aliases=list(row.aliases or []),
            )
            fields_by_key[key] = field_entry
        if fact_set_code not in field_entry.fact_set_codes:
            field_entry.fact_set_codes.append(fact_set_code)
    for code, fact_set in fact_sets_by_code.items():
        fact_set.field_codes = sorted(fact_field_codes.get(code, set()))

    available_field_codes = {
        field.code for field in fields_by_key.values()
    }
    metrics: list[QuestionCatalogMetric] = []
    for metric in database.scalars(
        select(MetricDefinition)
        .where(
            MetricDefinition.enabled.is_(True),
            MetricDefinition.status == "published",
        )
        .order_by(MetricDefinition.code, MetricDefinition.version.desc())
    ):
        required_codes = {
            metric.semantic_field_code,
            *metric.allowed_filter_fields,
        }
        if not required_codes.issubset(available_field_codes):
            continue
        compatible_fact_sets = [
            code
            for code, field_codes in fact_field_codes.items()
            if required_codes.issubset(field_codes)
            and (
                metric.fact_set_code is None
                or (
                    code == metric.fact_set_code
                    and fact_sets_by_code[code].version
                    == metric.fact_set_version
                )
            )
        ]
        if not compatible_fact_sets:
            continue
        metrics.append(
            QuestionCatalogMetric(
                code=metric.code,
                version=metric.version,
                name=metric.name,
                description=metric.description,
                aggregation=metric.aggregation,
                unit=metric.unit,
                aliases=list(metric.aliases or []),
                allowed_filter_fields=list(
                    metric.allowed_filter_fields or []
                ),
                compatible_fact_set_codes=sorted(compatible_fact_sets),
                governance_status=(
                    "published"
                    if metric.fact_set_code
                    and metric.semantic_manifest_code
                    else "legacy"
                ),
            )
        )

    unit_names = list(
        database.scalars(
            select(AdministrativeUnit.name)
            .where(
                AdministrativeUnit.tenant_id == scope.tenant_id,
                AdministrativeUnit.id.in_(scope.administrative_unit_ids),
            )
            .order_by(AdministrativeUnit.name)
        )
    )
    snapshot = QuestionCatalogSnapshot(
        source_mode=(
            "selected_file"
            if scope.selected_source_item_id is not None
            else "all_approved_files"
        ),
        administrative_units=unit_names,
        record_created_before=scope.record_created_before.isoformat(),
        source_item_fingerprint=scope.source_item_fingerprint,
        fact_sets=sorted(fact_sets_by_code.values(), key=lambda item: item.code),
        fields=sorted(fields_by_key.values(), key=lambda item: item.code),
        metrics=metrics,
    )
    canonical = snapshot.model_dump(mode="json", exclude={"catalog_fingerprint"})
    snapshot.catalog_fingerprint = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return snapshot
