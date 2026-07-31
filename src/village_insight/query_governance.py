from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    MetricDefinition,
    QueryFactSetDefinition,
    SemanticField,
    SemanticFieldVersion,
    SemanticManifestDefinition,
    utcnow,
)


class QueryGovernanceError(ValueError):
    pass


def contract_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def validate_provenance_rule(rule: dict[str, Any]) -> None:
    kind = rule.get("kind")
    allowed = {"region_template", "document_template", "approved_plan"}
    if kind not in allowed:
        raise QueryGovernanceError("unsupported provenance rule kind")
    if not rule.get("id"):
        raise QueryGovernanceError("provenance rule requires an immutable id")
    if kind != "approved_plan" and not isinstance(rule.get("version"), int):
        raise QueryGovernanceError(
            "template provenance rule requires an immutable version"
        )


def _published_field_codes(database: Session) -> set[str]:
    return set(
        database.scalars(
            select(SemanticField.code)
            .join(
                SemanticFieldVersion,
                SemanticFieldVersion.field_id == SemanticField.id,
            )
            .where(
                SemanticField.published_version
                == SemanticFieldVersion.version,
                SemanticFieldVersion.status == "published",
            )
        )
    )


def publish_fact_set(
    database: Session,
    fact_set: QueryFactSetDefinition,
) -> QueryFactSetDefinition:
    if fact_set.status != "draft":
        raise QueryGovernanceError("only a draft fact set can be published")
    validate_provenance_rule(fact_set.provenance_rule)
    published_fields = _published_field_codes(database)
    referenced_fields = {
        *fact_set.identity_field_codes,
        *fact_set.dimension_field_codes,
        *(
            str(measure.get("field_code"))
            for measure in fact_set.measure_definitions
            if measure.get("field_code")
        ),
        *(
            str(dimension.get("field_code"))
            for dimension in fact_set.time_dimensions
            if dimension.get("field_code")
        ),
        *(
            str(dimension.get("field_code"))
            for dimension in fact_set.status_dimensions
            if dimension.get("field_code")
        ),
    }
    unavailable = referenced_fields - published_fields
    if unavailable:
        raise QueryGovernanceError(
            "fact set references unpublished fields: "
            + ", ".join(sorted(unavailable))
        )
    if not fact_set.identity_field_codes:
        raise QueryGovernanceError(
            "fact set requires an explicit identity field policy"
        )
    fact_set.status = "published"
    fact_set.published_at = utcnow()
    database.flush()
    return fact_set


def publish_semantic_manifest(
    database: Session,
    manifest: SemanticManifestDefinition,
) -> SemanticManifestDefinition:
    if manifest.status != "draft":
        raise QueryGovernanceError(
            "only a draft semantic manifest can be published"
        )
    fact_set = database.scalar(
        select(QueryFactSetDefinition).where(
            QueryFactSetDefinition.code == manifest.fact_set_code,
            QueryFactSetDefinition.version == manifest.fact_set_version,
            QueryFactSetDefinition.status == "published",
        )
    )
    if fact_set is None:
        raise QueryGovernanceError(
            "semantic manifest requires a published fact set version"
        )
    if manifest.catalog_fingerprint != fact_set.catalog_fingerprint:
        raise QueryGovernanceError(
            "semantic manifest catalog fingerprint does not match fact set"
        )
    entity_names = {
        str(entity.get("code"))
        for entity in manifest.entities
        if entity.get("code")
    }
    if manifest.root_entity not in entity_names:
        raise QueryGovernanceError("root entity is not declared")
    if manifest.relationships and manifest.max_join_depth == 0:
        raise QueryGovernanceError(
            "relationships require an explicit non-zero join depth"
        )
    manifest.status = "published"
    manifest.published_at = utcnow()
    database.flush()
    return manifest


def publish_metric(
    database: Session,
    metric: MetricDefinition,
) -> MetricDefinition:
    if metric.status != "draft":
        raise QueryGovernanceError("only a draft metric can be published")
    if (
        not metric.fact_set_code
        or metric.fact_set_version is None
        or not metric.semantic_manifest_code
        or metric.semantic_manifest_version is None
    ):
        raise QueryGovernanceError(
            "official metric requires fact set and semantic manifest versions"
        )
    fact_set = database.scalar(
        select(QueryFactSetDefinition).where(
            QueryFactSetDefinition.code == metric.fact_set_code,
            QueryFactSetDefinition.version == metric.fact_set_version,
            QueryFactSetDefinition.status == "published",
        )
    )
    manifest = database.scalar(
        select(SemanticManifestDefinition).where(
            SemanticManifestDefinition.code
            == metric.semantic_manifest_code,
            SemanticManifestDefinition.version
            == metric.semantic_manifest_version,
            SemanticManifestDefinition.fact_set_code == metric.fact_set_code,
            SemanticManifestDefinition.fact_set_version
            == metric.fact_set_version,
            SemanticManifestDefinition.status == "published",
        )
    )
    if fact_set is None or manifest is None:
        raise QueryGovernanceError(
            "official metric requires matching published contracts"
        )
    if manifest.catalog_fingerprint != fact_set.catalog_fingerprint:
        raise QueryGovernanceError("metric contract catalog fingerprints differ")
    measures = {
        str(item.get("field_code")): item
        for item in manifest.measures
        if item.get("field_code")
    }
    measure = measures.get(metric.semantic_field_code)
    if measure is None:
        raise QueryGovernanceError("metric field is not a published measure")
    if metric.aggregation not in set(
        measure.get("allowed_aggregations") or []
    ):
        raise QueryGovernanceError(
            "metric aggregation is not allowed by the manifest"
        )
    fact_measures = {
        str(item.get("field_code")): item
        for item in fact_set.measure_definitions
        if item.get("field_code")
    }
    fact_measure = fact_measures.get(metric.semantic_field_code)
    if (
        fact_measure is None
        or metric.additivity != measure.get("additivity")
        or metric.additivity != fact_measure.get("additivity")
    ):
        raise QueryGovernanceError(
            "metric additivity does not match the published contracts"
        )
    dimensions = {
        str(item.get("field_code"))
        for item in manifest.dimensions
        if item.get("field_code")
    }
    filter_fields = {
        *metric.allowed_filter_fields,
        *(
            str(item.get("field_code"))
            for item in metric.status_filters
            if item.get("field_code")
        ),
    }
    if not filter_fields.issubset(dimensions):
        raise QueryGovernanceError(
            "metric filters must be published dimensions"
        )
    if metric.record_type != fact_set.record_type:
        raise QueryGovernanceError("metric record type does not match fact set")
    if metric.record_grain != fact_set.record_grain:
        raise QueryGovernanceError("metric grain does not match fact set")
    if set(metric.identity_field_codes) != set(
        fact_set.identity_field_codes
    ):
        raise QueryGovernanceError(
            "metric identity policy does not match fact set"
        )
    if metric.time_policy:
        raise QueryGovernanceError(
            "time-aware metrics are not supported by this contract version"
        )
    metric.status = "published"
    metric.published_at = utcnow()
    database.flush()
    return metric
