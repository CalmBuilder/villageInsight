from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from village_insight.db.models import (
    DocumentTemplate,
    FieldMatch,
    MatchType,
    RegionTemplate,
    RegionTemplateMatch,
    RegionTemplateVersion,
    SemanticField,
    SemanticFieldVersion,
    SemanticIgnoreRule,
    SheetComposition,
    SheetCompositionMatch,
    SheetCompositionVersion,
    TemplateMatch,
    TemplateRegionComponent,
    TemplateStatus,
    TemplateVersion,
    WorkbookRoute,
    WorkbookRouteMatch,
    WorkbookRouteVersion,
)
from village_insight.parsing.candidates import select_header_candidates
from village_insight.parsing.contracts import (
    HeaderCandidate,
    RegionCandidate,
    SheetProfile,
    WorkbookProfile,
)
from village_insight.templates.field_semantics import (
    analyze_header_path,
    equivalent_semantic_labels,
    header_paths_equivalent,
    looks_like_observed_value_header,
    normalize_role_code,
    semantic_header_path,
)

MATCHER_VERSION = "four-layer-matcher/v5"
FIELD_MATCHER_VERSION = "contextual-field-matcher/v5"
FIELD_DIRECT_REUSE_THRESHOLD = 8_500
FIELD_DIRECT_REUSE_MARGIN = 1_500
SHEET_MATCHER_VERSION = "sheet-composition-matcher/v1"
WORKBOOK_MATCHER_VERSION = "workbook-route-matcher/v1"


def _normalized_path(path: list[str]) -> list[str]:
    return [" ".join(part.split()) for part in path if part.strip()]


def _normalized_headers(profile: WorkbookProfile) -> list[list[str]]:
    headers: list[list[str]] = []
    for sheet in profile.sheets:
        for candidate in select_header_candidates(sheet.header_candidates):
            for column in candidate.columns:
                path = _normalized_path(column.header_path)
                if path:
                    headers.append(path)
    return sorted(headers)


def layout_signature(profile: WorkbookProfile) -> dict[str, Any]:
    sheets: list[dict[str, Any]] = []
    for sheet in profile.sheets:
        selected_headers = select_header_candidates(sheet.header_candidates)
        header_by_region = {candidate.region_id: candidate for candidate in selected_headers}
        regions = [
            {
                "kind": region.kind,
                "columns": region.bounds.max_column - region.bounds.min_column + 1,
                "header_depth": len(header_by_region[region.id].header_rows),
            }
            for region in sheet.region_candidates
            if region.id in header_by_region
        ]
        sheets.append(
            {
                "hidden": sheet.hidden,
                "regions": sorted(
                    regions,
                    key=lambda region: (
                        str(region["kind"]),
                        int(region["columns"]),
                        int(region["header_depth"]),
                    ),
                ),
            }
        )
    return {
        "format": profile.detection.format,
        "sheets": sheets,
        "headers": _normalized_headers(profile),
    }


def _fingerprint(signature: Any) -> str:
    canonical = json.dumps(
        signature,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def layout_fingerprint(profile: WorkbookProfile) -> str:
    return _fingerprint(layout_signature(profile))


def _header_signature(header: HeaderCandidate) -> list[list[str]]:
    return [path for column in header.columns if (path := semantic_header_path(column.header_path))]


def region_signature(
    region: RegionCandidate,
    header: HeaderCandidate,
) -> dict[str, Any]:
    paths = _header_signature(header)
    return {
        "kind": region.kind,
        "columns": len(paths),
        "header_depth": max((len(path) for path in paths), default=0),
        "headers": paths,
    }


def region_fingerprint(
    region: RegionCandidate,
    header: HeaderCandidate,
) -> str:
    return _region_fingerprint(region_signature(region, header))


def _region_fingerprint(signature: dict[str, Any]) -> str:
    return _fingerprint({key: value for key, value in signature.items() if key != "kind"})


@dataclass(frozen=True)
class ProfileRegion:
    sheet: SheetProfile
    region: RegionCandidate
    header: HeaderCandidate
    signature: dict[str, Any]
    fingerprint: str


@dataclass
class FieldCatalogEntry:
    code: str
    version: int
    data_type: str
    unit: str | None
    aliases: set[str]
    full_paths: set[str]
    contexts: set[tuple[str, str]]
    roles: set[str]


@dataclass(frozen=True)
class IgnoreCatalogEntry:
    rule_key: str
    header_path: str
    domain: str
    record_type: str
    observed_data_type: str | None


def _normalized_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _published_ignore_catalog(database: Session) -> list[IgnoreCatalogEntry]:
    return [
        IgnoreCatalogEntry(
            rule_key=rule.rule_key,
            header_path=_normalized_label(" / ".join(rule.header_path)),
            domain=rule.domain,
            record_type=rule.record_type,
            observed_data_type=rule.observed_data_type,
        )
        for rule in database.scalars(
            select(SemanticIgnoreRule).where(SemanticIgnoreRule.status == TemplateStatus.PUBLISHED)
        )
    ]


def _published_field_catalog(
    database: Session,
    versions: list[TemplateVersion],
    region_versions: list[RegionTemplateVersion] | None = None,
) -> dict[str, FieldCatalogEntry]:
    entries: dict[str, FieldCatalogEntry] = {}
    rows = database.execute(
        select(SemanticField, SemanticFieldVersion).where(
            SemanticField.id == SemanticFieldVersion.field_id,
            SemanticField.published_version == SemanticFieldVersion.version,
            SemanticFieldVersion.status == TemplateStatus.PUBLISHED,
        )
    )
    for field, version in rows:
        entry = FieldCatalogEntry(
            code=field.code,
            version=version.version,
            data_type=version.data_type,
            unit=version.unit_dimension,
            aliases={
                normalized
                for label in [version.name, *version.aliases]
                if label
                for normalized in equivalent_semantic_labels(label)
            },
            full_paths=set(),
            contexts=set(),
            roles=set(),
        )
        for variant in version.variants:
            if variant.alias:
                entry.aliases.update(equivalent_semantic_labels(variant.alias))
            if variant.header_path:
                entry.full_paths.add(_normalized_label(" / ".join(variant.header_path)))
                entry.aliases.update(equivalent_semantic_labels(variant.header_path[-1]))
            if variant.domain and variant.record_type:
                entry.contexts.add((variant.domain, variant.record_type))
            if variant.role:
                normalized_role = normalize_role_code(variant.role)
                if normalized_role:
                    entry.roles.add(normalized_role)
        entries[field.code] = entry
    for version in versions:
        definition = version.definition
        context = (
            str(definition.get("domain") or ""),
            str(definition.get("record_type") or ""),
        )
        for binding in definition.get("field_bindings", []):
            code = str(binding.get("semantic_field_code") or "")
            bound_entry = entries.get(code)
            if bound_entry is None:
                continue
            path = _normalized_path([str(part) for part in binding.get("header_path", [])])
            if path:
                bound_entry.full_paths.add(_normalized_label(" / ".join(path)))
                bound_entry.aliases.update(equivalent_semantic_labels(path[-1]))
            if all(context):
                bound_entry.contexts.add(context)
            role = str(binding.get("role") or "")
            if role:
                normalized_role = normalize_role_code(role)
                if normalized_role:
                    bound_entry.roles.add(normalized_role)
    for version in region_versions or []:
        context = (version.domain, version.record_type)
        for binding in version.field_bindings:
            code = str(binding.get("semantic_field_code") or "")
            bound_entry = entries.get(code)
            if bound_entry is None:
                continue
            path = semantic_header_path([str(part) for part in binding.get("header_path", [])])
            if path:
                bound_entry.full_paths.add(_normalized_label(" / ".join(path)))
                bound_entry.aliases.update(equivalent_semantic_labels(path[-1]))
            if all(context):
                bound_entry.contexts.add(context)
            normalized_region_role = normalize_role_code(str(binding.get("role") or ""))
            if normalized_region_role:
                bound_entry.roles.add(normalized_region_role)
    return entries


def _observed_data_type(
    profile_region: ProfileRegion,
    *,
    column: int,
) -> str | None:
    header_end = max(profile_region.header.header_rows)
    values = [
        cell.display_value
        for cell in profile_region.sheet.cells
        if cell.column == column
        and header_end < cell.row <= profile_region.region.bounds.max_row
        and cell.display_value not in (None, "")
    ][:20]
    if not values:
        return None
    kinds: set[str] = set()
    for value in values:
        if isinstance(value, bool):
            kinds.add("boolean")
        elif isinstance(value, datetime):
            kinds.add("datetime")
        elif isinstance(value, date):
            kinds.add("date")
        elif isinstance(value, int):
            kinds.add("integer")
        elif isinstance(value, float):
            kinds.add("decimal")
        else:
            kinds.add("text")
    if kinds <= {"integer", "decimal"}:
        return "decimal" if "decimal" in kinds else "integer"
    return next(iter(kinds)) if len(kinds) == 1 else "text"


def _types_compatible(observed: str | None, expected: str) -> bool:
    if observed is None:
        return False
    return observed == expected or {observed, expected} <= {"integer", "decimal"}


def _legacy_component_binding(
    version: TemplateVersion | None,
    component: TemplateRegionComponent | None,
    header_path: list[str],
) -> dict[str, Any] | None:
    if version is None or component is None:
        return None
    bindings = version.definition.get("field_bindings", [])
    normalized = _normalized_path(header_path)
    candidates = [
        bindings[index]
        for index in component.field_binding_indexes
        if 0 <= index < len(bindings)
        and header_paths_equivalent(
            _normalized_path([str(part) for part in bindings[index].get("header_path", [])]),
            normalized,
        )
    ]
    return candidates[0] if len(candidates) == 1 else None


def _exact_workbook_template_binding(
    version: TemplateVersion | None,
    *,
    source_region: ProfileRegion,
    column: Any,
) -> dict[str, Any] | None:
    """Reuse legacy bindings only after the whole workbook matched exactly."""

    if version is None:
        return None
    bindings: list[dict[str, Any]] = [
        dict(binding)
        for binding in version.definition.get("field_bindings", [])
        if isinstance(binding, dict)
    ]
    source_id_candidates = [
        binding
        for binding in bindings
        if binding.get("source_column_id") == column.source_column_id
    ]
    if len(source_id_candidates) == 1:
        return source_id_candidates[0]
    normalized = _normalized_path([str(part) for part in column.header_path])
    header_candidates = [
        binding
        for binding in bindings
        if header_paths_equivalent(
            _normalized_path([str(part) for part in binding.get("header_path", [])]),
            normalized,
        )
    ]
    if len(header_candidates) == 1:
        return header_candidates[0]
    source_columns = source_region.header.columns
    if len(bindings) != len(source_columns):
        return None
    column_index = next(
        (
            index
            for index, candidate in enumerate(source_columns)
            if candidate.source_column_id == column.source_column_id
        ),
        None,
    )
    return bindings[column_index] if column_index is not None else None


def _region_template_binding(
    version: RegionTemplateVersion | None,
    header_path: list[str],
    *,
    source_region: ProfileRegion,
    column: Any,
    allow_ordinal: bool,
    verified_source_region: bool = False,
) -> dict[str, Any] | None:
    if version is None:
        return None
    source_id_candidates = [
        binding
        for binding in version.field_bindings
        if binding.get("source_column_id") == column.source_column_id
    ]
    if len(source_id_candidates) == 1:
        return source_id_candidates[0]
    normalized = _normalized_path(header_path)
    header_candidates = [
        binding
        for binding in version.field_bindings
        if header_paths_equivalent(
            _normalized_path([str(part) for part in binding.get("header_path", [])]),
            normalized,
        )
    ]
    if len(header_candidates) == 1:
        return header_candidates[0]
    column_offset = column.column - source_region.region.bounds.min_column
    selector_candidates = [
        binding
        for binding in version.field_bindings
        if (binding.get("source_selector") or {}).get("kind") == "physical_column"
        and binding["source_selector"].get("column_offset") == column_offset
        and (
            verified_source_region
            or binding["source_selector"].get("header_path_sha256") is None
            or binding["source_selector"].get("header_path_sha256") == _fingerprint(normalized)
        )
    ]
    if len(selector_candidates) == 1:
        return selector_candidates[0]
    source_columns = source_region.header.columns
    if allow_ordinal and len(version.field_bindings) == len(source_columns):
        column_index = next(
            (
                index
                for index, candidate in enumerate(source_columns)
                if candidate.source_column_id == column.source_column_id
            ),
            None,
        )
        if column_index is not None:
            return version.field_bindings[column_index]
    return None


def _value_data_type(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "decimal"
    return "text"


def _form_field_match_values(
    *,
    source_region: ProfileRegion,
    region_version: RegionTemplateVersion,
    binding: dict[str, Any],
) -> dict[str, Any]:
    selector = binding["source_selector"]
    row = source_region.region.bounds.min_row + int(selector["row_offset"])
    column = source_region.region.bounds.min_column + int(selector["column_offset"])
    cell = next(
        (
            candidate
            for candidate in source_region.sheet.cells
            if candidate.row == row and candidate.column == column
        ),
        None,
    )
    header_path = [str(part) for part in binding.get("header_path", [])]
    semantics = analyze_header_path(header_path)
    return {
        "source_column_id": str(
            binding.get("source_column_id")
            or f"form:r{selector['row_offset']}:c{selector['column_offset']}"
        ),
        "header_path": header_path,
        "observed_data_type": _value_data_type(cell.display_value if cell is not None else None),
        "semantic_field_code": str(binding["semantic_field_code"]),
        "semantic_field_version": int(binding["semantic_field_version"]),
        "match_type": MatchType.EXACT,
        "score_basis_points": 10_000,
        "context": {
            "domain": region_version.domain,
            "record_type": region_version.record_type,
            "header_parent": header_path[:-1],
            "base_label": semantics.base_label,
            "concept_key": semantics.concept_key,
            "role": binding.get("role") or semantics.role,
            "role_evidence": semantics.role_evidence,
        },
        "differences": {
            "candidates": [],
            "matched_by": "region_template_cell_selector",
            "source_selector": selector,
        },
        "requires_hermes": False,
    }


def _field_match_values(
    *,
    source_region: ProfileRegion,
    column: Any,
    best_region_version: RegionTemplateVersion | None,
    best_version: TemplateVersion | None,
    best_component: TemplateRegionComponent | None,
    catalog: dict[str, FieldCatalogEntry],
    ignore_catalog: list[IgnoreCatalogEntry],
    allow_ordinal_binding: bool = False,
) -> dict[str, Any]:
    observed_type = _observed_data_type(source_region, column=column.column)
    raw_header_path = [str(part) for part in column.header_path]
    semantic_path = semantic_header_path(raw_header_path)
    header_semantics = analyze_header_path(semantic_path or raw_header_path)
    verified_source_region = bool(
        best_region_version is not None
        and any(
            isinstance(evidence, dict) and evidence.get("region_id") == source_region.region.id
            for evidence in (best_region_version.source_metadata or {}).get("evidence", [])
        )
    )
    direct = _region_template_binding(
        best_region_version,
        semantic_path,
        source_region=source_region,
        column=column,
        allow_ordinal=allow_ordinal_binding,
        verified_source_region=verified_source_region,
    )
    if direct is None:
        direct = _legacy_component_binding(
            best_version,
            best_component,
            semantic_path,
        )
    if direct is None and allow_ordinal_binding:
        direct = _exact_workbook_template_binding(
            best_version,
            source_region=source_region,
            column=column,
        )
    context = {
        "domain": (
            best_region_version.domain
            if best_region_version is not None
            else str(best_version.definition.get("domain") or "")
            if best_version is not None
            else None
        ),
        "record_type": (
            best_region_version.record_type
            if best_region_version is not None
            else str(best_version.definition.get("record_type") or "")
            if best_version is not None
            else None
        ),
        "header_parent": semantic_path[:-1],
        "base_label": header_semantics.base_label,
        "concept_key": header_semantics.concept_key,
        "role": (
            str(direct.get("role"))
            if direct is not None and direct.get("role")
            else header_semantics.role
        ),
        "role_evidence": header_semantics.role_evidence,
    }
    auxiliary_reason = _deterministic_auxiliary_column_reason(column=column)
    if auxiliary_reason is not None:
        return {
            "observed_data_type": observed_type,
            "semantic_field_code": None,
            "semantic_field_version": None,
            "match_type": MatchType.EXACT,
            "score_basis_points": 10_000,
            "context": context,
            "differences": {
                "candidates": [],
                "matched_by": "deterministic_auxiliary_column",
                "ignored": True,
                "ignore_reason": auxiliary_reason,
            },
            "requires_hermes": False,
        }
    region_ignored_paths = {
        _normalized_label(" / ".join(semantic_header_path(path)))
        for path in (
            (best_region_version.source_metadata or {}).get("ignored_header_paths", [])
            if best_region_version is not None
            else []
        )
        if isinstance(path, list) and semantic_header_path(path)
    }
    if full_label := _normalized_label(" / ".join(semantic_path)):
        if full_label in region_ignored_paths:
            return {
                "observed_data_type": observed_type,
                "semantic_field_code": None,
                "semantic_field_version": None,
                "match_type": MatchType.EXACT,
                "score_basis_points": 10_000,
                "context": context,
                "differences": {
                    "candidates": [],
                    "matched_by": "approved_region_ignore",
                    "ignored": True,
                    "ignore_reason": "not_in_approved_region_plan",
                },
                "requires_hermes": False,
            }
    ignored_columns = (
        (best_region_version.source_metadata or {}).get("ignored_columns", [])
        if best_region_version is not None
        else []
    )
    column_offset = column.column - source_region.region.bounds.min_column
    header_path_sha256 = _fingerprint(semantic_path)
    if any(
        isinstance(rule, dict)
        and rule.get("column_offset") == column_offset
        and (verified_source_region or rule.get("header_path_sha256") == header_path_sha256)
        for rule in ignored_columns
    ):
        return {
            "observed_data_type": observed_type,
            "semantic_field_code": None,
            "semantic_field_version": None,
            "match_type": MatchType.EXACT,
            "score_basis_points": 10_000,
            "context": context,
            "differences": {
                "candidates": [],
                "matched_by": "approved_region_ignore",
                "ignored": True,
                "ignore_reason": "not_in_approved_region_plan",
            },
            "requires_hermes": False,
        }
    if direct is not None:
        return {
            "observed_data_type": observed_type,
            "semantic_field_code": str(direct["semantic_field_code"]),
            "semantic_field_version": int(direct["semantic_field_version"]),
            "match_type": MatchType.EXACT,
            "score_basis_points": 10_000,
            "context": context,
            "differences": {
                "candidates": [],
                "matched_by": (
                    "region_template"
                    if best_region_version is not None
                    else "legacy_region_component"
                ),
            },
            "requires_hermes": False,
        }
    if (
        verified_source_region
        and best_region_version is not None
        and best_region_version.source == "validated_corpus"
    ):
        return {
            "observed_data_type": observed_type,
            "semantic_field_code": None,
            "semantic_field_version": None,
            "match_type": MatchType.EXACT,
            "score_basis_points": 10_000,
            "context": context,
            "differences": {
                "candidates": [],
                "matched_by": "verified_approved_region_projection",
                "ignored": True,
                "ignore_reason": "not_in_approved_region_plan",
            },
            "requires_hermes": False,
        }

    full_label = _normalized_label(" / ".join(semantic_path))
    leaf_label = _normalized_label(semantic_path[-1]) if semantic_path else ""
    base_label = header_semantics.normalized_base_label
    actual_context = (
        str(context["domain"] or ""),
        str(context["record_type"] or ""),
    )
    ignored = next(
        (
            entry
            for entry in ignore_catalog
            if entry.header_path == full_label
            and (entry.domain, entry.record_type) == actual_context
            and (
                entry.observed_data_type is None
                or observed_type is None
                or entry.observed_data_type == observed_type
            )
        ),
        None,
    )
    if ignored is not None:
        return {
            "observed_data_type": observed_type,
            "semantic_field_code": None,
            "semantic_field_version": None,
            "match_type": MatchType.EXACT,
            "score_basis_points": 10_000,
            "context": context,
            "differences": {
                "candidates": [],
                "matched_by": "contextual_ignore_rule",
                "ignore_rule_key": ignored.rule_key,
                "ignored": True,
            },
            "requires_hermes": False,
        }
    scored: list[tuple[int, FieldCatalogEntry, list[str]]] = []
    for entry in catalog.values():
        score = 0
        reasons: list[str] = []
        if full_label and full_label in entry.full_paths:
            score += 7000
            reasons.append("full_header_path")
        elif leaf_label and leaf_label in entry.aliases:
            score += 4500
            reasons.append("published_alias")
        elif base_label and base_label in entry.aliases:
            score += 4500
            reasons.append("normalized_base_alias")
        elif leaf_label and any(
            len(alias) >= 2 and (alias in leaf_label or leaf_label in alias)
            for alias in entry.aliases
        ):
            score += 3500
            reasons.append("semantic_label_overlap")
        if _types_compatible(observed_type, entry.data_type):
            score += 1000
            reasons.append("data_type")
        if all(actual_context) and actual_context in entry.contexts:
            score += 2000
            reasons.append("region_context")
        normalized_role = _normalized_label(header_semantics.role or "")
        if normalized_role and normalized_role in entry.roles:
            score += 1000
            reasons.append("role")
        elif normalized_role and not entry.roles and reasons:
            score += 500
            reasons.append("role_variant_candidate")
        elif normalized_role and entry.roles:
            score = max(score - 1000, 0)
            reasons.append("role_conflict")
        if score:
            scored.append((min(score, 10_000), entry, reasons))
    scored.sort(key=lambda item: (-item[0], item[1].code))
    top_score = scored[0][0] if scored else 0
    second_score = scored[1][0] if len(scored) > 1 else 0
    score_margin = top_score - second_score
    leaders = [item for item in scored if item[0] == top_score]
    leader_has_semantic_identity = bool(leaders) and (
        full_label in leaders[0][1].full_paths
        or leaf_label in leaders[0][1].aliases
        or base_label in leaders[0][1].aliases
    )
    deterministic_path_match = (
        len(leaders) == 1
        and "full_header_path" in leaders[0][2]
        and (observed_type is None or _types_compatible(observed_type, leaders[0][1].data_type))
    )
    exact = deterministic_path_match or (
        len(leaders) == 1
        and leader_has_semantic_identity
        and top_score >= FIELD_DIRECT_REUSE_THRESHOLD
        and score_margin >= FIELD_DIRECT_REUSE_MARGIN
    )
    selected = leaders[0][1] if exact else None
    return {
        "observed_data_type": observed_type,
        "semantic_field_code": selected.code if selected else None,
        "semantic_field_version": selected.version if selected else None,
        "match_type": (
            MatchType.EXACT if exact else MatchType.PARTIAL if scored else MatchType.NONE
        ),
        "score_basis_points": top_score,
        "context": context,
        "differences": {
            "candidates": [
                {
                    "semantic_field_code": entry.code,
                    "semantic_field_version": entry.version,
                    "score_basis_points": score,
                    "reasons": reasons,
                    "compatible_roles": sorted(entry.roles),
                }
                for score, entry, reasons in scored[:5]
            ],
            "ambiguous": len(leaders) > 1,
            "score_margin_basis_points": score_margin,
            "direct_reuse_threshold": FIELD_DIRECT_REUSE_THRESHOLD,
            "direct_reuse_margin": FIELD_DIRECT_REUSE_MARGIN,
            "deterministic_path_match": deterministic_path_match,
        },
        "requires_hermes": not exact,
    }


def _deterministic_auxiliary_column_reason(*, column: Any) -> str | None:
    """Exclude every unnamed column from formal semantic mappings.

    Raw cells remain immutable evidence. No content-based inference is allowed
    to turn a headerless physical column into a published business field.
    """
    header_path = [str(part) for part in column.header_path]
    if not _normalized_path(header_path):
        return "unnamed_column"
    if looks_like_observed_value_header(header_path):
        return "observed_value_header"
    return None


def profile_regions(profile: WorkbookProfile) -> list[ProfileRegion]:
    regions: list[ProfileRegion] = []
    for sheet in profile.sheets:
        region_by_id = {region.id: region for region in sheet.region_candidates}
        for header in select_header_candidates(sheet.header_candidates):
            region = region_by_id.get(header.region_id)
            if region is None or _formula_only_region(sheet, region):
                continue
            signature = region_signature(region, header)
            regions.append(
                ProfileRegion(
                    sheet=sheet,
                    region=region,
                    header=header,
                    signature=signature,
                    fingerprint=_region_fingerprint(signature),
                )
            )
    return regions


def profile_region_candidates(profile: WorkbookProfile) -> list[ProfileRegion]:
    """Return every physical Region/header evidence pair retained by parsing."""
    regions: list[ProfileRegion] = []
    for sheet in profile.sheets:
        region_by_id = {region.id: region for region in sheet.region_candidates}
        for header in sheet.header_candidates:
            region = region_by_id.get(header.region_id)
            if region is None or _formula_only_region(sheet, region):
                continue
            signature = region_signature(region, header)
            regions.append(
                ProfileRegion(
                    sheet=sheet,
                    region=region,
                    header=header,
                    signature=signature,
                    fingerprint=_region_fingerprint(signature),
                )
            )
    return regions


def _profile_regions_for_matching(
    profile: WorkbookProfile,
    *,
    published_regions: list[RegionTemplateVersion],
    components: list[tuple[TemplateVersion, TemplateRegionComponent]],
) -> list[ProfileRegion]:
    """Choose the header candidate best supported by the published catalog.

    Parsing keeps every candidate as evidence. The matcher must not freeze the
    parser's generic top-ranked header before it can compare that candidate to
    approved Region signatures.
    """
    defaults = {region.region.id: region.header.id for region in profile_regions(profile)}
    selected: list[ProfileRegion] = []
    for sheet in profile.sheets:
        headers_by_region: dict[str, list[HeaderCandidate]] = defaultdict(list)
        for header in sheet.header_candidates:
            headers_by_region[header.region_id].append(header)
        for region in sheet.region_candidates:
            if _formula_only_region(sheet, region):
                continue
            candidates: list[ProfileRegion] = []
            for header in headers_by_region.get(region.id, []):
                signature = region_signature(region, header)
                candidates.append(
                    ProfileRegion(
                        sheet=sheet,
                        region=region,
                        header=header,
                        signature=signature,
                        fingerprint=_region_fingerprint(signature),
                    )
                )
            if not candidates:
                continue
            default_header_id = defaults.get(region.id)

            def rank(
                candidate: ProfileRegion,
                default_header_id: str | None = default_header_id,
            ) -> tuple[int, int, int, float, int, int]:
                verified = any(
                    _matches_verified_source_region(
                        version,
                        profile=profile,
                        source_region=candidate,
                    )
                    for version in published_regions
                )
                exact = any(
                    version.region_fingerprint == candidate.fingerprint
                    for version in published_regions
                ) or any(
                    component.region_fingerprint == candidate.fingerprint
                    for _, component in components
                )
                score = max(
                    (
                        _best_region_version_score(candidate.signature, version)[0]
                        for version in published_regions
                    ),
                    default=0,
                )
                return (
                    int(verified),
                    int(exact),
                    score,
                    candidate.header.confidence,
                    int(candidate.header.id == default_header_id),
                    -len(candidate.header.header_rows),
                )

            selected.append(max(candidates, key=rank))
    return selected


def _known_source_ignored_regions(
    database: Session,
    profile: WorkbookProfile,
) -> set[str]:
    ignored: set[str] = set()
    for version in database.scalars(
        select(WorkbookRouteVersion)
        .join(WorkbookRoute)
        .where(
            WorkbookRouteVersion.status == TemplateStatus.PUBLISHED,
            WorkbookRoute.published_version == WorkbookRouteVersion.version,
        )
    ):
        metadata = version.source_metadata or {}
        members = metadata.get("members", [])
        if not any(
            isinstance(member, dict) and member.get("source_sha256") == profile.source_sha256
            for member in members
        ):
            continue
        ignored.update(
            str(row["region_candidate_id"])
            for row in metadata.get("ignored_regions", [])
            if isinstance(row, dict)
            and row.get("source_sha256") == profile.source_sha256
            and row.get("region_candidate_id")
        )
    return ignored


def _matches_verified_source_region(
    version: RegionTemplateVersion,
    *,
    profile: WorkbookProfile,
    source_region: ProfileRegion,
) -> bool:
    evidence = (version.source_metadata or {}).get("evidence", [])
    return any(
        isinstance(row, dict)
        and row.get("source_sha256") == profile.source_sha256
        and row.get("region_id") == source_region.region.id
        for row in evidence
    )


def _matches_form_anchors(
    version: RegionTemplateVersion,
    *,
    source_region: ProfileRegion,
) -> bool:
    if version.layout_rules.get("layout_mode") != "form":
        return False
    cells = {
        (cell.row, cell.column): cell
        for cell in source_region.sheet.cells
        if source_region.region.bounds.min_row <= cell.row <= source_region.region.bounds.max_row
        and source_region.region.bounds.min_column
        <= cell.column
        <= source_region.region.bounds.max_column
    }
    anchors = []
    for binding in version.field_bindings:
        selector = binding.get("source_selector")
        if (
            not isinstance(selector, dict)
            or selector.get("kind") != "cell"
            or "label_row_offset" not in selector
            or "label_column_offset" not in selector
        ):
            continue
        row = source_region.region.bounds.min_row + int(selector["label_row_offset"])
        column = source_region.region.bounds.min_column + int(selector["label_column_offset"])
        cell = cells.get((row, column))
        expected = _normalized_label(str(binding.get("header_path", [""])[-1]))
        actual = _normalized_label(str(cell.display_value)) if cell is not None else ""
        anchors.append(bool(expected and actual == expected))
    return len(anchors) >= 2 and all(anchors)


def _formula_only_region(
    sheet: SheetProfile,
    region: RegionCandidate,
) -> bool:
    cells = [
        cell
        for cell in sheet.cells
        if region.bounds.min_row <= cell.row <= region.bounds.max_row
        and region.bounds.min_column <= cell.column <= region.bounds.max_column
        and cell.display_value not in (None, "")
    ]
    return bool(cells) and all(cell.formula is not None for cell in cells)


def _snapshot_signature(decision: dict[str, Any]) -> dict[str, Any]:
    paths = [
        _normalized_path([str(part) for part in path])
        for path in decision.get("header_signature", [])
        if isinstance(path, list)
    ]
    return {
        "kind": str(decision.get("classification") or "unknown"),
        "columns": len(paths),
        "header_depth": max((len(path) for path in paths), default=0),
        "headers": paths,
    }


def ensure_region_components(
    database: Session,
    version: TemplateVersion,
) -> list[TemplateRegionComponent]:
    existing = list(
        database.scalars(
            select(TemplateRegionComponent)
            .where(TemplateRegionComponent.template_version_id == version.id)
            .order_by(TemplateRegionComponent.source_decision_index)
        )
    )
    snapshot = (version.source_metadata or {}).get("layout_projection_snapshot")
    decisions = snapshot.get("decisions") if isinstance(snapshot, dict) else None
    if not isinstance(decisions, list) or not decisions:
        return existing

    bindings = version.definition.get("field_bindings", [])
    by_index = {component.source_decision_index: component for component in existing}
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            continue
        signature = _snapshot_signature(decision)
        fingerprint = _region_fingerprint(signature)
        header_paths = {
            tuple(path) for path in signature["headers"] if isinstance(path, list) and path
        }
        source_columns = decision.get("source_columns", [])
        source_column_ids = {
            str(column.get("source_column_id"))
            for column in source_columns
            if isinstance(column, dict) and column.get("source_column_id")
        }
        binding_indexes = [
            binding_index
            for binding_index, binding in enumerate(bindings)
            if (
                str(binding.get("source_column_id")) in source_column_ids
                if source_column_ids
                else tuple(_normalized_path([str(part) for part in binding.get("header_path", [])]))
                in header_paths
            )
        ]
        component = by_index.get(index)
        values = {
            "component_key": f"region-{index + 1}-{fingerprint[:16]}",
            "region_fingerprint": fingerprint,
            "signature": signature,
            "field_binding_indexes": binding_indexes,
        }
        if component is None:
            component = TemplateRegionComponent(
                template_version_id=version.id,
                source_decision_index=index,
                **values,
            )
            database.add(component)
            by_index[index] = component
        else:
            for key, value in values.items():
                setattr(component, key, value)
    database.flush()
    return [by_index[index] for index in sorted(by_index)]


def _header_set(signature: dict[str, Any]) -> set[str]:
    paths = [
        _normalized_path([str(part) for part in path])
        for path in signature.get("headers", [])
        if isinstance(path, list) and path
    ]
    common_prefix: list[str] = []
    if paths and all(len(path) > 1 for path in paths):
        for parts in zip(*paths, strict=False):
            if len(set(parts)) != 1:
                break
            common_prefix.append(parts[0])
    title = " ".join(common_prefix)
    if common_prefix and any(
        marker in title for marker in ("表", "名册", "台账", "登记", "汇总", "清册")
    ):
        paths = [path[len(common_prefix) :] for path in paths]
    return {" / ".join(path) for path in paths if path}


def _component_score(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    actual_headers = _header_set(actual)
    expected_headers = _header_set(expected)
    union = actual_headers | expected_headers
    header_score = (
        round(10_000 * len(actual_headers & expected_headers) / len(union)) if union else 0
    )
    structural_mismatches = [key for key in ("columns",) if actual.get(key) != expected.get(key)]
    if actual_headers != expected_headers and actual.get("header_depth") != expected.get(
        "header_depth"
    ):
        structural_mismatches.append("header_depth")
    if (
        actual.get("kind") not in {None, "unknown"}
        and expected.get("kind") not in {None, "unknown"}
        and actual.get("kind") != expected.get("kind")
    ):
        structural_mismatches.append("kind")
    score = max(0, header_score - 750 * len(structural_mismatches))
    return score, {
        "missing_headers": sorted(expected_headers - actual_headers),
        "new_headers": sorted(actual_headers - expected_headers),
        "structural_mismatches": structural_mismatches,
    }


def _region_version_signature(
    version: RegionTemplateVersion,
) -> dict[str, Any]:
    headers = [_normalized_path([str(part) for part in path]) for path in version.header_signature]
    return {
        "kind": version.region_kind,
        "columns": len(headers),
        "header_depth": max((len(path) for path in headers), default=0),
        "headers": headers,
    }


def _region_version_signatures(
    version: RegionTemplateVersion,
) -> list[dict[str, Any]]:
    variants = [version.header_signature]
    metadata_variants = (version.source_metadata or {}).get("header_variants", [])
    if isinstance(metadata_variants, list):
        variants.extend(variant for variant in metadata_variants if isinstance(variant, list))
    signatures: list[dict[str, Any]] = []
    seen: set[str] = set()
    for variant in variants:
        headers = [
            _normalized_path([str(part) for part in path])
            for path in variant
            if isinstance(path, list)
        ]
        signature = {
            "kind": version.region_kind,
            "columns": len(headers),
            "header_depth": max((len(path) for path in headers), default=0),
            "headers": headers,
        }
        fingerprint = _fingerprint(signature)
        if fingerprint not in seen:
            seen.add(fingerprint)
            signatures.append(signature)
    return signatures


def _best_region_version_score(
    actual: dict[str, Any],
    version: RegionTemplateVersion,
) -> tuple[int, dict[str, Any]]:
    return max(
        (_component_score(actual, expected) for expected in _region_version_signatures(version)),
        key=lambda result: result[0],
        default=(
            0,
            {
                "missing_headers": [],
                "new_headers": sorted(_header_set(actual)),
                "structural_mismatches": [],
            },
        ),
    )


def _legacy_region_references(
    database: Session,
    version: RegionTemplateVersion | None,
) -> tuple[TemplateVersion | None, TemplateRegionComponent | None]:
    if version is None:
        return None, None
    metadata = version.source_metadata or {}
    template_id = metadata.get("legacy_template_id")
    template_version = metadata.get("legacy_template_version")
    component_id = metadata.get("legacy_component_id")
    if not template_id or not template_version or not component_id:
        return None, None
    try:
        legacy_template_id = uuid.UUID(str(template_id))
        legacy_component_id = uuid.UUID(str(component_id))
        component = database.get(
            TemplateRegionComponent,
            legacy_component_id,
        )
    except (TypeError, ValueError):
        return None, None
    legacy_version = database.scalar(
        select(TemplateVersion).where(
            TemplateVersion.template_id == legacy_template_id,
            TemplateVersion.version == int(template_version),
            TemplateVersion.status == TemplateStatus.PUBLISHED,
        )
    )
    if (
        legacy_version is None
        or component is None
        or component.template_version_id != legacy_version.id
    ):
        return None, None
    return legacy_version, component


def _slot_coverage(
    expected: list[tuple[Any, int, bool, str, bool]],
    actual: list[tuple[Any, int, str]],
) -> tuple[
    int,
    list[str],
    list[tuple[Any, int, str]],
    list[dict[str, Any]],
]:
    remaining = list(actual)
    matched = 0
    missing: list[str] = []
    assignments: list[dict[str, Any]] = []
    for template_id, version, required, slot_key, materialize in expected:
        index = next(
            (
                position
                for position, actual_value in enumerate(remaining)
                if actual_value[:2] == (template_id, version)
            ),
            None,
        )
        if index is None:
            if required:
                missing.append(slot_key)
            continue
        matched += 1
        _, _, source_id = remaining.pop(index)
        assignments.append(
            {
                "slot_key": slot_key,
                "source_id": source_id,
                "materialize": materialize,
            }
        )
    return matched, missing, remaining, assignments


def _match_sheet_compositions(
    database: Session,
    *,
    item_id: Any,
    profile: WorkbookProfile,
    region_matches: list[RegionTemplateMatch],
) -> list[SheetCompositionMatch]:
    database.execute(delete(SheetCompositionMatch).where(SheetCompositionMatch.item_id == item_id))
    published = list(
        database.scalars(
            select(SheetCompositionVersion)
            .join(SheetComposition)
            .where(
                SheetCompositionVersion.status == TemplateStatus.PUBLISHED,
                SheetComposition.published_version == SheetCompositionVersion.version,
            )
            .options(selectinload(SheetCompositionVersion.region_slots))
        )
    )
    by_sheet: dict[str, list[RegionTemplateMatch]] = {}
    for match in region_matches:
        by_sheet.setdefault(match.sheet_id, []).append(match)
    records: list[SheetCompositionMatch] = []
    for sheet in profile.sheets:
        actual = [
            (
                match.region_template_id,
                match.region_template_version,
                match.region_id,
            )
            for match in by_sheet.get(sheet.id, [])
            if match.match_type == MatchType.EXACT
            and match.region_template_id is not None
            and match.region_template_version is not None
        ]
        best_version: SheetCompositionVersion | None = None
        best_score = 0
        best_values: tuple[
            int,
            list[str],
            list[tuple[Any, int, str]],
            list[dict[str, Any]],
        ] = (0, [], actual, [])
        for candidate in published:
            expected = [
                (
                    slot.region_template_id,
                    slot.region_template_version,
                    slot.required,
                    slot.slot_key,
                    slot.materialize,
                )
                for slot in candidate.region_slots
            ]
            matched, missing, unknown, assignments = _slot_coverage(
                expected,
                actual,
            )
            coverage = round(10_000 * matched / len(expected)) if expected else 0
            score = max(0, coverage - 500 * len(unknown))
            if score > best_score:
                best_version = candidate
                best_score = score
                best_values = (matched, missing, unknown, assignments)
        matched, missing, unknown, assignments = best_values
        total = len(best_version.region_slots) if best_version else 0
        exact = best_version is not None and not missing and not unknown and matched == total
        match_type = (
            MatchType.EXACT if exact else MatchType.PARTIAL if best_score > 0 else MatchType.NONE
        )
        record = SheetCompositionMatch(
            item_id=item_id,
            sheet_id=sheet.id,
            sheet_composition_id=(
                best_version.sheet_composition_id if best_version is not None else None
            ),
            sheet_composition_version=(best_version.version if best_version is not None else None),
            match_type=match_type,
            score_basis_points=best_score,
            total_slots=total,
            matched_slots=matched,
            coverage_basis_points=(round(10_000 * matched / total) if total else 0),
            differences={
                "missing_required_slots": missing,
                "unmatched_region_templates": [
                    {
                        "region_template_id": str(template_id),
                        "region_template_version": version,
                    }
                    for template_id, version, _ in unknown
                ],
                "slot_assignments": assignments,
            },
            matcher_version=SHEET_MATCHER_VERSION,
        )
        database.add(record)
        records.append(record)
    return records


def _match_workbook_routes(
    database: Session,
    *,
    item_id: Any,
    sheet_matches: list[SheetCompositionMatch],
) -> WorkbookRouteMatch:
    database.execute(delete(WorkbookRouteMatch).where(WorkbookRouteMatch.item_id == item_id))
    published = list(
        database.scalars(
            select(WorkbookRouteVersion)
            .join(WorkbookRoute)
            .where(
                WorkbookRouteVersion.status == TemplateStatus.PUBLISHED,
                WorkbookRoute.published_version == WorkbookRouteVersion.version,
            )
            .options(selectinload(WorkbookRouteVersion.sheet_slots))
        )
    )
    actual = [
        (
            match.sheet_composition_id,
            match.sheet_composition_version,
            match.sheet_id,
        )
        for match in sheet_matches
        if match.match_type == MatchType.EXACT
        and match.sheet_composition_id is not None
        and match.sheet_composition_version is not None
    ]
    best_version: WorkbookRouteVersion | None = None
    best_score = 0
    best_values: tuple[
        int,
        list[str],
        list[tuple[Any, int, str]],
        list[dict[str, Any]],
    ] = (0, [], actual, [])
    for candidate in published:
        expected = [
            (
                slot.sheet_composition_id,
                slot.sheet_composition_version,
                slot.required,
                slot.slot_key,
                slot.materialize,
            )
            for slot in candidate.sheet_slots
        ]
        matched, missing, unknown, assignments = _slot_coverage(
            expected,
            actual,
        )
        coverage = round(10_000 * matched / len(expected)) if expected else 0
        score = max(0, coverage - 500 * len(unknown))
        if score > best_score:
            best_version = candidate
            best_score = score
            best_values = (matched, missing, unknown, assignments)
    matched, missing, unknown, assignments = best_values
    total = len(best_version.sheet_slots) if best_version else 0
    exact = best_version is not None and not missing and not unknown and matched == total
    match_type = (
        MatchType.EXACT if exact else MatchType.PARTIAL if best_score > 0 else MatchType.NONE
    )
    record = WorkbookRouteMatch(
        item_id=item_id,
        workbook_route_id=(best_version.workbook_route_id if best_version is not None else None),
        workbook_route_version=(best_version.version if best_version is not None else None),
        match_type=match_type,
        score_basis_points=best_score,
        total_slots=total,
        matched_slots=matched,
        coverage_basis_points=(round(10_000 * matched / total) if total else 0),
        differences={
            "missing_required_slots": missing,
            "unmatched_sheet_compositions": [
                {
                    "sheet_composition_id": str(composition_id),
                    "sheet_composition_version": version,
                }
                for composition_id, version, _ in unknown
            ],
            "slot_assignments": assignments,
        },
        matcher_version=WORKBOOK_MATCHER_VERSION,
    )
    database.add(record)
    return record


def match_profile(
    database: Session,
    *,
    item_id: Any,
    profile: WorkbookProfile,
) -> TemplateMatch:
    fingerprint = layout_fingerprint(profile)
    published = list(
        database.scalars(
            select(TemplateVersion)
            .join(DocumentTemplate)
            .where(
                TemplateVersion.status == TemplateStatus.PUBLISHED,
                DocumentTemplate.published_version == TemplateVersion.version,
            )
        )
    )
    components = [
        (version, component)
        for version in published
        for component in ensure_region_components(database, version)
    ]
    published_regions = list(
        database.scalars(
            select(RegionTemplateVersion)
            .join(RegionTemplate)
            .where(
                RegionTemplateVersion.status == TemplateStatus.PUBLISHED,
                RegionTemplate.published_version == RegionTemplateVersion.version,
            )
        )
    )
    field_catalog = _published_field_catalog(database, published, published_regions)
    ignore_catalog = _published_ignore_catalog(database)
    workbook_exact = next(
        (candidate for candidate in published if candidate.layout_fingerprint == fingerprint),
        None,
    )
    ignored_region_ids = _known_source_ignored_regions(database, profile)
    source_regions = [
        source_region
        for source_region in _profile_regions_for_matching(
            profile,
            published_regions=published_regions,
            components=components,
        )
        if source_region.region.id not in ignored_region_ids
    ]
    ignored_auxiliary_regions = [
        {
            "sheet_id": sheet.id,
            "region_id": region.id,
            "reason": "formula_only_derived_region",
        }
        for sheet in profile.sheets
        for region in sheet.region_candidates
        if _formula_only_region(sheet, region) or region.id in ignored_region_ids
    ]
    database.execute(delete(RegionTemplateMatch).where(RegionTemplateMatch.item_id == item_id))
    database.execute(delete(FieldMatch).where(FieldMatch.item_id == item_id))

    region_matches: list[RegionTemplateMatch] = []
    for source_region in source_regions:
        precomputed_field_values: dict[str, dict[str, Any]] = {}
        exact_region_candidates = [
            candidate
            for candidate in published_regions
            if _matches_verified_source_region(
                candidate,
                profile=profile,
                source_region=source_region,
            )
            or _matches_form_anchors(
                candidate,
                source_region=source_region,
            )
            or candidate.region_fingerprint == source_region.fingerprint
        ]
        source_column_ids = {column.source_column_id for column in source_region.header.columns}
        exact_region = max(
            exact_region_candidates,
            key=lambda candidate: (
                int(
                    _matches_verified_source_region(
                        candidate,
                        profile=profile,
                        source_region=source_region,
                    )
                ),
                int(candidate.source == "validated_corpus"),
                sum(
                    str(binding.get("source_column_id") or "") in source_column_ids
                    for binding in candidate.field_bindings
                ),
                str(candidate.region_template_id),
            ),
            default=None,
        )
        exact_legacy = next(
            (
                (version, component)
                for version, component in components
                if component.region_fingerprint == source_region.fingerprint
            ),
            None,
        )
        best_region_version: RegionTemplateVersion | None = None
        best_version: TemplateVersion | None = None
        best_component: TemplateRegionComponent | None = None
        best_score = 0
        differences: dict[str, Any] = {
            "missing_headers": [],
            "new_headers": sorted(_header_set(source_region.signature)),
            "structural_mismatches": [],
        }
        match_type = MatchType.NONE
        if exact_region is not None:
            best_region_version = exact_region
            best_version, best_component = _legacy_region_references(
                database,
                exact_region,
            )
            best_score = 10_000
            differences = {
                "missing_headers": [],
                "new_headers": [],
                "structural_mismatches": [],
                "matched_by": "region_template_fingerprint",
            }
            match_type = MatchType.EXACT
        elif not published_regions and exact_legacy is not None:
            best_version, best_component = exact_legacy
            best_score = 10_000
            differences = {
                "missing_headers": [],
                "new_headers": [],
                "structural_mismatches": [],
                "matched_by": "legacy_region_component",
            }
            match_type = MatchType.EXACT
        else:
            for candidate_region in published_regions:
                candidate_score, candidate_differences = _best_region_version_score(
                    source_region.signature,
                    candidate_region,
                )
                if candidate_score > best_score:
                    best_region_version = candidate_region
                    best_score = candidate_score
                    differences = candidate_differences
            if best_region_version is not None:
                best_version, best_component = _legacy_region_references(
                    database,
                    best_region_version,
                )
            if not published_regions:
                for candidate_version, candidate_component in components:
                    candidate_score, candidate_differences = _component_score(
                        source_region.signature,
                        candidate_component.signature,
                    )
                    if candidate_score > best_score:
                        best_version = candidate_version
                        best_component = candidate_component
                        best_score = candidate_score
                        differences = candidate_differences
            if (
                best_score == 10_000
                and not differences["missing_headers"]
                and not differences["new_headers"]
                and not differences["structural_mismatches"]
            ):
                differences["matched_by"] = "normalized_region_signature"
                match_type = MatchType.EXACT
            elif best_score > 0:
                match_type = MatchType.PARTIAL
        if published_regions and match_type == MatchType.PARTIAL:
            semantic_matches: list[
                tuple[
                    int,
                    str,
                    RegionTemplateVersion,
                    dict[str, dict[str, Any]],
                    dict[str, Any],
                ]
            ] = []
            for candidate_region in published_regions:
                candidate_values = {
                    column.source_column_id: _field_match_values(
                        source_region=source_region,
                        column=column,
                        best_region_version=candidate_region,
                        best_version=None,
                        best_component=None,
                        catalog=field_catalog,
                        ignore_catalog=ignore_catalog,
                        allow_ordinal_binding=False,
                    )
                    for column in source_region.header.columns
                }
                actual_fields = Counter(
                    str(values["semantic_field_code"])
                    for values in candidate_values.values()
                    if values["semantic_field_code"] is not None
                )
                expected_fields = Counter(
                    str(binding["semantic_field_code"])
                    for binding in candidate_region.field_bindings
                )
                if (
                    expected_fields
                    and actual_fields == expected_fields
                    and all(not values["requires_hermes"] for values in candidate_values.values())
                ):
                    candidate_score, candidate_differences = _best_region_version_score(
                        source_region.signature,
                        candidate_region,
                    )
                    semantic_matches.append(
                        (
                            candidate_score,
                            str(candidate_region.region_template_id),
                            candidate_region,
                            candidate_values,
                            candidate_differences,
                        )
                    )
            if semantic_matches:
                (
                    _,
                    _,
                    best_region_version,
                    precomputed_field_values,
                    semantic_differences,
                ) = max(semantic_matches, key=lambda row: (row[0], row[1]))
                best_version, best_component = _legacy_region_references(
                    database,
                    best_region_version,
                )
                differences = {
                    **semantic_differences,
                    "matched_by": "semantic_field_signature",
                }
                match_type = MatchType.EXACT
                best_score = 10_000
        if not published_regions and workbook_exact is not None and best_version is None:
            best_version = workbook_exact
            best_score = 10_000
            differences = {
                "missing_headers": [],
                "new_headers": [],
                "structural_mismatches": [],
                "matched_by": "workbook_fast_route",
            }
            match_type = MatchType.EXACT
        region_match = RegionTemplateMatch(
            item_id=item_id,
            sheet_id=source_region.sheet.id,
            region_id=source_region.region.id,
            header_id=source_region.header.id,
            region_fingerprint=source_region.fingerprint,
            match_type=match_type,
            score_basis_points=best_score,
            template_region_component_id=best_component.id if best_component else None,
            template_id=best_version.template_id if best_version else None,
            template_version=best_version.version if best_version else None,
            region_template_id=(
                best_region_version.region_template_id if best_region_version else None
            ),
            region_template_version=(best_region_version.version if best_region_version else None),
            differences=differences,
            requires_hermes=match_type != MatchType.EXACT,
            matcher_version=MATCHER_VERSION,
        )
        database.add(region_match)
        region_matches.append(region_match)
        form_region_version = (
            best_region_version
            if best_region_version is not None
            and match_type == MatchType.EXACT
            and best_region_version.layout_rules.get("layout_mode") == "form"
            else None
        )
        form_bindings = (
            [
                binding
                for binding in form_region_version.field_bindings
                if (binding.get("source_selector") or {}).get("kind") == "cell"
            ]
            if form_region_version is not None
            else []
        )
        if form_region_version is not None:
            for binding in form_bindings:
                database.add(
                    FieldMatch(
                        item_id=item_id,
                        sheet_id=source_region.sheet.id,
                        region_id=source_region.region.id,
                        header_id=source_region.header.id,
                        matcher_version=FIELD_MATCHER_VERSION,
                        **_form_field_match_values(
                            source_region=source_region,
                            region_version=form_region_version,
                            binding=binding,
                        ),
                    )
                )
        if form_bindings:
            continue
        for column in source_region.header.columns:
            field_values = precomputed_field_values.get(column.source_column_id)
            database.add(
                FieldMatch(
                    item_id=item_id,
                    sheet_id=source_region.sheet.id,
                    region_id=source_region.region.id,
                    header_id=source_region.header.id,
                    source_column_id=column.source_column_id,
                    header_path=column.header_path,
                    matcher_version=FIELD_MATCHER_VERSION,
                    **(
                        field_values
                        if field_values is not None
                        else _field_match_values(
                            source_region=source_region,
                            column=column,
                            best_region_version=best_region_version,
                            best_version=best_version,
                            best_component=best_component,
                            catalog=field_catalog,
                            ignore_catalog=ignore_catalog,
                            allow_ordinal_binding=match_type == MatchType.EXACT,
                        )
                    ),
                )
            )

    database.flush()
    unresolved_fields = list(
        database.scalars(
            select(FieldMatch)
            .where(
                FieldMatch.item_id == item_id,
                FieldMatch.requires_hermes.is_(True),
            )
            .order_by(
                FieldMatch.sheet_id,
                FieldMatch.region_id,
                FieldMatch.source_column_id,
            )
        )
    )
    total_regions = len(region_matches)
    matched_regions = sum(
        region_match.match_type == MatchType.EXACT for region_match in region_matches
    )
    coverage = round(10_000 * matched_regions / total_regions) if total_regions else 0
    unmatched = [
        {
            "sheet_id": region_match.sheet_id,
            "region_id": region_match.region_id,
            "header_id": region_match.header_id,
            "match_type": region_match.match_type,
            "score_basis_points": region_match.score_basis_points,
            "template_id": (
                str(region_match.template_id) if region_match.template_id is not None else None
            ),
            "template_version": region_match.template_version,
            "region_template_id": (
                str(region_match.region_template_id)
                if region_match.region_template_id is not None
                else None
            ),
            "region_template_version": region_match.region_template_version,
            "differences": region_match.differences,
        }
        for region_match in region_matches
        if region_match.match_type != MatchType.EXACT
    ]
    matched_versions = {
        (region_match.template_id, region_match.template_version)
        for region_match in region_matches
        if region_match.match_type == MatchType.EXACT
        and region_match.template_id is not None
        and region_match.template_version is not None
    }
    best: TemplateVersion | None = workbook_exact
    if best is None and len(matched_versions) == 1:
        template_id, template_version = next(iter(matched_versions))
        best = next(
            (
                version
                for version in published
                if version.template_id == template_id and version.version == template_version
            ),
            None,
        )
    if total_regions and matched_regions == total_regions:
        match_type = MatchType.EXACT
        score = 10_000
    elif matched_regions or any(
        region_match.score_basis_points > 0 for region_match in region_matches
    ):
        match_type = MatchType.PARTIAL
        score = coverage
    else:
        match_type = MatchType.NONE
        score = 0
    differences = {
        "contract_version": "workbook-region-match-summary/v1",
        "unmatched_regions": unmatched,
        "matched_template_versions": [
            {
                "template_id": str(template_id),
                "template_version": template_version,
            }
            for template_id, template_version in sorted(
                matched_versions,
                key=lambda item: (str(item[0]), item[1]),
            )
        ],
        "workbook_fast_route": workbook_exact is not None,
        "ignored_auxiliary_regions": ignored_auxiliary_regions,
        "unmatched_fields": [
            {
                "sheet_id": field.sheet_id,
                "region_id": field.region_id,
                "header_id": field.header_id,
                "source_column_id": field.source_column_id,
                "header_path": field.header_path,
                "match_type": field.match_type,
                "score_basis_points": field.score_basis_points,
                "differences": field.differences,
            }
            for field in unresolved_fields
        ],
    }
    sheet_matches = _match_sheet_compositions(
        database,
        item_id=item_id,
        profile=profile,
        region_matches=region_matches,
    )
    workbook_route_match = _match_workbook_routes(
        database,
        item_id=item_id,
        sheet_matches=sheet_matches,
    )
    differences["sheet_compositions"] = [
        {
            "sheet_id": sheet_match.sheet_id,
            "sheet_composition_id": (
                str(sheet_match.sheet_composition_id)
                if sheet_match.sheet_composition_id is not None
                else None
            ),
            "sheet_composition_version": sheet_match.sheet_composition_version,
            "match_type": sheet_match.match_type,
            "coverage_basis_points": sheet_match.coverage_basis_points,
        }
        for sheet_match in sheet_matches
    ]
    differences["workbook_route"] = {
        "workbook_route_id": (
            str(workbook_route_match.workbook_route_id)
            if workbook_route_match.workbook_route_id is not None
            else None
        ),
        "workbook_route_version": workbook_route_match.workbook_route_version,
        "match_type": workbook_route_match.match_type,
        "coverage_basis_points": workbook_route_match.coverage_basis_points,
    }

    record = database.get(TemplateMatch, item_id)
    values = {
        "source_sha256": profile.source_sha256,
        "profile_contract_version": profile.contract_version,
        "layout_fingerprint": fingerprint,
        "match_type": match_type,
        "score_basis_points": score,
        "template_id": best.template_id if best else None,
        "template_version": best.version if best else None,
        "differences": differences,
        "requires_hermes": bool(unmatched or unresolved_fields),
        "matcher_version": MATCHER_VERSION,
        "total_regions": total_regions,
        "matched_regions": matched_regions,
        "coverage_basis_points": coverage,
    }
    if record is None:
        record = TemplateMatch(item_id=item_id, **values)
        database.add(record)
    else:
        for key, value in values.items():
            setattr(record, key, value)
    database.flush()
    return record
