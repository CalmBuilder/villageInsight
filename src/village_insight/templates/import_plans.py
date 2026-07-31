from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    ApprovedImportPlan,
    DocumentProfile,
    FieldMatch,
    FormalImportStatus,
    ImportExecution,
    IngestionItem,
    ItemStatus,
    ProposalStatus,
    RegionTemplateMatch,
    RegionTemplateVersion,
    SemanticField,
    SheetCompositionMatch,
    TemplateMatch,
    TemplateProposal,
    TemplateRegionComponent,
    TemplateStatus,
    TemplateVersion,
    WorkbookRouteMatch,
)
from village_insight.parsing.contracts import WorkbookProfile
from village_insight.parsing.profile_storage import load_workbook_profile
from village_insight.templates.contracts import TemplateDefinition
from village_insight.templates.field_semantics import (
    analyze_header_path,
    header_paths_equivalent,
)
from village_insight.templates.matching import profile_regions


class ImportPlanError(ValueError):
    pass


def project_region_data_rows(
    *,
    region_start: int,
    region_end: int,
    header_end: int,
    projection: dict[str, Any],
) -> tuple[int, int]:
    if "data_start_offset_from_header_end" in projection:
        data_start = header_end + int(projection["data_start_offset_from_header_end"])
    elif "data_start_offset_from_region_start" in projection:
        data_start = region_start + int(projection["data_start_offset_from_region_start"])
    else:
        raise ImportPlanError("Region template has no reusable data start projection")
    if "data_end_gap_from_region_end" not in projection:
        raise ImportPlanError("Region template has no reusable data end projection")
    data_end = region_end - int(projection["data_end_gap_from_region_end"])
    if data_start < region_start or data_end > region_end or data_start > data_end:
        raise ImportPlanError("Region template projected an invalid data row range")
    return data_start, data_end


def resolve_reused_region_column(
    *,
    binding: dict[str, Any],
    binding_index: int,
    bindings: list[dict[str, Any]],
    current_columns: list[Any],
    projected_columns: list[Any],
) -> Any | None:
    """Resolve a published field binding to exactly one current physical column."""
    source_column_id = str(binding.get("source_column_id", ""))
    exact_source_columns = [
        column for column in current_columns if column.source_column_id == source_column_id
    ]
    if len(exact_source_columns) == 1:
        return exact_source_columns[0]

    path = _normalized_path([str(part) for part in binding.get("header_path", [])])
    projected_source = next(
        (
            entry
            for entry in projected_columns
            if isinstance(entry, dict)
            and str(entry.get("source_column_id", "")) == source_column_id
        ),
        None,
    )
    if projected_source is not None:
        offset = int(projected_source["offset"])
        if 0 <= offset < len(current_columns):
            projected_column = current_columns[offset]
            if header_paths_equivalent(
                path,
                _normalized_path(projected_column.header_path),
            ):
                return projected_column
        return None

    header_columns = [
        column
        for column in current_columns
        if header_paths_equivalent(
            path,
            _normalized_path(column.header_path),
        )
    ]
    if len(header_columns) == 1:
        return header_columns[0]

    if ":column:" in source_column_id:
        try:
            physical_column_number = int(source_column_id.rsplit(":column:", 1)[1])
        except ValueError:
            physical_column_number = 0
        physical_columns = [
            column
            for column in current_columns
            if column.column == physical_column_number
            and header_paths_equivalent(
                path,
                _normalized_path(column.header_path),
            )
        ]
        if len(physical_columns) == 1:
            return physical_columns[0]

    if len(bindings) == len(current_columns) and binding_index < len(current_columns):
        ordinal_column = current_columns[binding_index]
        if header_paths_equivalent(
            path,
            _normalized_path(ordinal_column.header_path),
        ):
            return ordinal_column
    return None


def _normalized_path(path: list[str]) -> tuple[str, ...]:
    return tuple(" ".join(part.split()) for part in path if part.strip())


def _header_signature(candidate: Any) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(" ".join(part.split()) for part in column.header_path) for column in candidate.columns
    )


def _freeze_region_decisions(
    decisions: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            **decision,
            "field_mappings": [
                mapping
                for mapping in mappings
                if str(mapping.get("sheet_id", "")) == str(decision.get("sheet_id", ""))
                and str(mapping.get("region_id", ""))
                == str(decision.get("region_candidate_id", ""))
            ],
        }
        for decision in decisions
    ]


def _canonicalize_layout_plan(layout_plan: dict[str, Any]) -> dict[str, Any]:
    """Keep exclusion evidence compact and scoped to the materialized data range."""
    decisions = layout_plan.get("decisions")
    if not isinstance(decisions, list):
        return layout_plan
    canonical_decisions: list[Any] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            canonical_decisions.append(decision)
            continue
        canonical = dict(decision)
        if "data_start_row" in canonical and "data_end_row" in canonical:
            data_start = int(canonical["data_start_row"])
            data_end = int(canonical["data_end_row"])
            canonical["excluded_rows"] = sorted(
                {
                    int(row)
                    for row in canonical.get("excluded_rows", [])
                    if data_start <= int(row) <= data_end
                }
            )
        canonical_decisions.append(canonical)
    return {**layout_plan, "decisions": canonical_decisions}


def _merge_governance_replacement_mappings(
    provisional_mappings: list[dict[str, Any]],
    confirmed_mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve prior resolved mappings and let confirmed decisions override them."""
    confirmed_source_columns = {
        str(mapping["source_column_id"]) for mapping in confirmed_mappings
    }
    inherited = [
        mapping
        for mapping in provisional_mappings
        if str(mapping["source_column_id"]) not in confirmed_source_columns
    ]
    return [*inherited, *confirmed_mappings]


def build_layout_projection_snapshot(
    profile: WorkbookProfile,
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    for decision in decisions:
        region_id = str(decision["region_candidate_id"])
        header_id = str(decision["header_candidate_id"])
        matches = [
            (sheet, region, header)
            for sheet in profile.sheets
            for region in sheet.region_candidates
            for header in sheet.header_candidates
            if region.id == region_id and header.id == header_id and header.region_id == region.id
        ]
        if len(matches) != 1:
            raise ImportPlanError(
                "approved layout decision does not resolve to one source header region"
            )
        sheet, region, header = matches[0]
        data_start = int(decision["data_start_row"])
        data_end = int(decision["data_end_row"])
        data_start_column = int(decision.get("data_start_column", region.bounds.min_column))
        data_end_column = int(decision.get("data_end_column", region.bounds.max_column))
        header_end = max(header.header_rows)
        if (
            data_start < region.bounds.min_row
            or data_end > region.bounds.max_row
            or data_end < data_start
            or data_start_column < region.bounds.min_column
            or data_end_column > region.bounds.max_column
            or data_end_column < data_start_column
        ):
            raise ImportPlanError("approved layout data range is outside its source region")
        snapshots.append(
            {
                "sheet_index": sheet.index,
                "header_signature": [list(path) for path in _header_signature(header)],
                "source_columns": [
                    {
                        "offset": offset,
                        "source_column_id": column.source_column_id,
                        "header_path": list(_normalized_path(column.header_path)),
                    }
                    for offset, column in enumerate(header.columns)
                ],
                "data_start_offset_from_header_end": data_start - header_end,
                "data_end_gap_from_region_end": region.bounds.max_row - data_end,
                "data_start_column_offset_from_region_start": (
                    data_start_column - region.bounds.min_column
                ),
                "data_end_column_gap_from_region_end": (region.bounds.max_column - data_end_column),
                "excluded_row_offsets": [
                    int(row) - data_start for row in decision.get("excluded_rows", [])
                ],
                "classification": decision.get("classification", region.kind),
                "layout_mode": decision.get("layout_mode"),
                "materialize": bool(decision.get("materialize", True)),
            }
        )
    return {
        "contract_version": "layout-projection-snapshot/v1",
        "decisions": snapshots,
    }


def ensure_layout_projection_snapshot(
    database: Session,
    version: TemplateVersion,
) -> None:
    source_metadata = version.source_metadata or {}
    decisions = source_metadata.get("approved_layout_plan")
    if not isinstance(decisions, list) or not decisions:
        return
    source_item_id = source_metadata.get("source_item_id")
    if not source_item_id:
        if source_metadata.get("layout_projection_snapshot"):
            return
        raise ImportPlanError("approved layout has no source evidence for projection snapshot")
    source_record = database.get(DocumentProfile, uuid.UUID(str(source_item_id)))
    if source_record is None:
        if source_metadata.get("layout_projection_snapshot"):
            return
        raise ImportPlanError("template source profile is unavailable for publication")
    source_profile = load_workbook_profile(source_record)
    version.source_metadata = {
        **source_metadata,
        "layout_projection_snapshot": build_layout_projection_snapshot(
            source_profile,
            decisions,
        ),
    }


def project_layout_plan(
    database: Session,
    *,
    version: TemplateVersion,
    current_profile: WorkbookProfile,
) -> dict[str, Any]:
    source_metadata = version.source_metadata or {}
    decisions = source_metadata.get("approved_layout_plan")
    if not isinstance(decisions, list) or not decisions:
        raise ImportPlanError("published template has no reusable approved layout plan")
    snapshot = source_metadata.get("layout_projection_snapshot")
    if not isinstance(snapshot, dict):
        source_item_id = source_metadata.get("source_item_id")
        source_record = (
            database.get(DocumentProfile, uuid.UUID(str(source_item_id)))
            if source_item_id
            else None
        )
        source_profile = (
            load_workbook_profile(source_record) if source_record is not None else current_profile
        )
        snapshot = build_layout_projection_snapshot(source_profile, decisions)
    snapshot_decisions = snapshot.get("decisions")
    if not isinstance(snapshot_decisions, list) or not snapshot_decisions:
        raise ImportPlanError("published template projection snapshot is invalid")
    projected: list[dict[str, Any]] = []
    for snapshot_decision in snapshot_decisions:
        sheet_index = int(snapshot_decision["sheet_index"])
        if sheet_index < 0 or sheet_index >= len(current_profile.sheets):
            raise ImportPlanError("template layout sheet index is unavailable")
        current_sheet = current_profile.sheets[sheet_index]
        signature = tuple(
            tuple(str(part) for part in path) for path in snapshot_decision["header_signature"]
        )
        matching_headers = [
            header
            for header in current_sheet.header_candidates
            if _header_signature(header) == signature
        ]
        if len(matching_headers) != 1:
            raise ImportPlanError(
                "approved template header does not resolve uniquely in current evidence"
            )
        current_header = matching_headers[0]
        current_region = next(
            (
                region
                for region in current_sheet.region_candidates
                if region.id == current_header.region_id
            ),
            None,
        )
        if current_region is None:
            raise ImportPlanError("current region for approved template is unavailable")
        current_header_end = max(current_header.header_rows)
        data_start = current_header_end + int(
            snapshot_decision["data_start_offset_from_header_end"]
        )
        data_end = current_region.bounds.max_row - int(
            snapshot_decision["data_end_gap_from_region_end"]
        )
        data_start_column = current_region.bounds.min_column + int(
            snapshot_decision.get(
                "data_start_column_offset_from_region_start",
                0,
            )
        )
        data_end_column = current_region.bounds.max_column - int(
            snapshot_decision.get("data_end_column_gap_from_region_end", 0)
        )
        excluded_rows = [
            data_start + int(offset) for offset in snapshot_decision.get("excluded_row_offsets", [])
        ]
        projected.append(
            {
                "region_candidate_id": current_region.id,
                "header_candidate_id": current_header.id,
                "data_start_row": data_start,
                "data_end_row": data_end,
                "data_start_column": data_start_column,
                "data_end_column": data_end_column,
                "excluded_rows": excluded_rows,
                "classification": snapshot_decision.get(
                    "classification",
                    current_region.kind,
                ),
                "layout_mode": snapshot_decision.get("layout_mode"),
                "materialize": bool(snapshot_decision.get("materialize", True)),
                "evidence_ids": [
                    evidence_id
                    for column in current_header.columns
                    for evidence_id in column.evidence_cell_ids
                ],
                "merge_decisions": [],
                "projected_from_template_version": version.version,
            }
        )
    return {
        "contract_version": "approved-layout-plan/v1",
        "decisions": projected,
    }


def approve_plan(
    database: Session,
    *,
    item: IngestionItem,
    template_id: uuid.UUID | None,
    template_version: int | None,
    layout_plan: dict[str, Any],
    field_mappings: list[dict[str, Any]],
    actor: str,
    comment: str,
    actor_type: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    supersedes_plan_id: uuid.UUID | None = None,
    plan_source: str = "template",
    proposal_id: uuid.UUID | None = None,
    primary_region_template_id: uuid.UUID | None = None,
    primary_region_template_version: int | None = None,
) -> ApprovedImportPlan:
    latest = database.scalar(
        select(ApprovedImportPlan)
        .where(ApprovedImportPlan.item_id == item.id)
        .order_by(ApprovedImportPlan.revision.desc())
        .limit(1)
    )
    profile = database.get(DocumentProfile, item.id)
    match = database.get(TemplateMatch, item.id)
    governance_replaces_provisional = (
        latest is not None
        and latest.plan_source == "hermes_provisional"
        and plan_source == "hermes"
        and latest.proposal_id == proposal_id
    )
    if plan_source not in {"template", "hermes", "hermes_provisional"}:
        raise ImportPlanError(f"unsupported plan source: {plan_source}")
    allowed_status = {
        "template": TemplateStatus.PUBLISHED,
        "hermes": TemplateStatus.USER_CONFIRMED,
        "hermes_provisional": TemplateStatus.ADMIN_REVIEW,
    }[plan_source]
    version = (
        database.scalar(
            select(TemplateVersion).where(
                TemplateVersion.template_id == template_id,
                TemplateVersion.version == template_version,
                TemplateVersion.status == allowed_status,
            )
        )
        if template_id is not None and template_version is not None
        else None
    )
    region_version = (
        database.scalar(
            select(RegionTemplateVersion).where(
                RegionTemplateVersion.region_template_id == primary_region_template_id,
                RegionTemplateVersion.version == primary_region_template_version,
                RegionTemplateVersion.status == TemplateStatus.PUBLISHED,
            )
        )
        if primary_region_template_id is not None and primary_region_template_version is not None
        else None
    )
    if profile is None or match is None:
        raise ImportPlanError("profile and template match are required")
    if version is None and region_version is None:
        raise ImportPlanError(f"{plan_source} template version is not available for import")
    if plan_source in {"hermes", "hermes_provisional"}:
        proposal = database.get(TemplateProposal, proposal_id)
        if (
            proposal is None
            or proposal.source_item_id != item.id
            or version is None
            or str(version.source_metadata.get("proposal_id")) != str(proposal.id)
        ):
            raise ImportPlanError("Hermes import requires the proposal bound to this file")
        expected_status = (
            ProposalStatus.ACCEPTED if plan_source == "hermes" else ProposalStatus.PENDING
        )
        if proposal.status != expected_status:
            raise ImportPlanError(f"{plan_source} requires a {expected_status} proposal")
    elif proposal_id is not None:
        raise ImportPlanError("template import cannot reference a Hermes proposal")
    profile_model = load_workbook_profile(profile)
    definition = (
        TemplateDefinition.model_validate(version.definition) if version is not None else None
    )
    if not field_mappings and plan_source != "hermes_provisional":
        if definition is not None:
            columns_by_header: dict[tuple[str, ...], set[str]] = {}
            current_source_column_ids: set[str] = set()
            for sheet in profile_model.sheets:
                for candidate in sheet.header_candidates:
                    for column in candidate.columns:
                        current_source_column_ids.add(column.source_column_id)
                        key = tuple(" ".join(part.split()) for part in column.header_path)
                        if key:
                            columns_by_header.setdefault(key, set()).add(column.source_column_id)
            derived: list[dict[str, Any]] = []
            for binding in definition.field_bindings:
                key = tuple(" ".join(part.split()) for part in binding.header_path)
                candidates = (
                    {binding.source_column_id}
                    if binding.source_column_id in current_source_column_ids
                    else columns_by_header.get(key, set())
                )
                if len(candidates) != 1:
                    raise ImportPlanError(
                        "field binding must resolve to exactly one current column: "
                        + (" / ".join(key) or binding.source_column_id)
                    )
                derived.append(
                    {
                        "source_column_id": next(iter(candidates)),
                        "header_path": binding.header_path,
                        "semantic_field_code": binding.semantic_field_code,
                        "semantic_field_version": binding.semantic_field_version,
                        "role": binding.role,
                        "normalizer": binding.normalizer,
                        "required": binding.required,
                    }
                )
            if governance_replaces_provisional and latest is not None:
                field_mappings = _merge_governance_replacement_mappings(
                    latest.field_mappings,
                    derived,
                )
            else:
                field_mappings = derived
    if not layout_plan:
        if governance_replaces_provisional and latest is not None:
            layout_plan = latest.layout_plan
        else:
            if version is None:
                raise ImportPlanError(
                    "independent Region plans require explicit layout decisions"
                )
            if version.source_metadata.get("approved_layout_plan"):
                layout_plan = project_layout_plan(
                    database,
                    version=version,
                    current_profile=profile_model,
                )
            else:
                layout_plan = {
                    "contract_version": "approved-layout-plan/v1",
                    "sheet_ids": [sheet.id for sheet in profile_model.sheets],
                    "region_candidate_ids": [
                        region.id
                        for sheet in profile_model.sheets
                        for region in sheet.region_candidates
                    ],
                }
    layout_plan = _canonicalize_layout_plan(layout_plan)
    if latest is not None:
        if (
            latest.template_id == template_id
            and latest.template_version == template_version
            and latest.primary_region_template_id == primary_region_template_id
            and latest.primary_region_template_version == primary_region_template_version
            and latest.plan_source == plan_source
            and latest.proposal_id == proposal_id
            and latest.layout_plan == layout_plan
            and latest.field_mappings == field_mappings
        ):
            return latest
        if supersedes_plan_id != latest.id:
            raise ImportPlanError(
                "a corrected plan must explicitly supersede the latest immutable plan"
            )
        completed = database.scalar(
            select(ImportExecution).where(
                ImportExecution.approved_plan_id == latest.id,
                ImportExecution.status == "completed",
            )
        )
        if completed is not None and not governance_replaces_provisional:
            raise ImportPlanError("a completed import plan cannot be superseded for the same item")
    elif supersedes_plan_id is not None:
        raise ImportPlanError("there is no previous plan to supersede")
    resolved_actor_type = actor_type or (
        "hermes"
        if actor == "system:hermes"
        else "system"
        if actor.startswith("system:")
        else "user"
    )
    plan = ApprovedImportPlan(
        item_id=item.id,
        revision=(latest.revision + 1 if latest is not None else 1),
        supersedes_plan_id=latest.id if latest is not None else None,
        source_sha256=item.source_sha256,
        profile_contract_version=profile.contract_version,
        layout_fingerprint=match.layout_fingerprint,
        plan_source=plan_source,
        proposal_id=proposal_id,
        template_id=template_id,
        template_version=template_version,
        primary_region_template_id=primary_region_template_id,
        primary_region_template_version=primary_region_template_version,
        layout_plan=layout_plan,
        field_mappings=field_mappings,
        approved_by=actor,
        approved_by_type=resolved_actor_type,
        approved_by_user_id=actor_user_id,
        approval_comment=comment,
    )
    database.add(plan)
    item.status = ItemStatus.MATERIALIZING
    item.formal_import_status = FormalImportStatus.MATERIALIZING
    database.flush()
    return plan


def build_reused_region_fragments(
    database: Session,
    *,
    item: IngestionItem,
    require_complete: bool,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    tuple[uuid.UUID, int] | None,
    tuple[uuid.UUID, int] | None,
]:
    profile_record = database.get(DocumentProfile, item.id)
    summary = database.get(TemplateMatch, item.id)
    if profile_record is None or summary is None:
        raise ImportPlanError("profile and Region match summary are required")
    if require_complete and summary.requires_hermes:
        raise ImportPlanError("unmatched Regions require Hermes before import")
    profile = load_workbook_profile(profile_record)
    current_regions = {
        (region.sheet.id, region.region.id, region.header.id): region
        for region in profile_regions(profile)
    }
    matches = list(
        database.scalars(
            select(RegionTemplateMatch)
            .where(RegionTemplateMatch.item_id == item.id)
            .order_by(
                RegionTemplateMatch.sheet_id,
                RegionTemplateMatch.region_id,
                RegionTemplateMatch.header_id,
            )
        )
    )
    if not matches or len(matches) != summary.total_regions:
        raise ImportPlanError("Region match coverage is incomplete")
    sheet_region_policy = {
        (sheet_match.sheet_id, str(assignment["source_id"])): bool(
            assignment.get("materialize", True)
        )
        for sheet_match in database.scalars(
            select(SheetCompositionMatch).where(SheetCompositionMatch.item_id == item.id)
        )
        for assignment in sheet_match.differences.get(
            "slot_assignments",
            [],
        )
        if isinstance(assignment, dict) and assignment.get("source_id")
    }
    route_match = database.get(WorkbookRouteMatch, item.id)
    workbook_sheet_policy = (
        {
            str(assignment["source_id"]): bool(assignment.get("materialize", True))
            for assignment in route_match.differences.get(
                "slot_assignments",
                [],
            )
            if isinstance(assignment, dict) and assignment.get("source_id")
        }
        if route_match is not None
        else {}
    )

    decisions: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    primary: tuple[uuid.UUID, int] | None = None
    primary_region: tuple[uuid.UUID, int] | None = None
    for match in matches:
        if match.requires_hermes:
            if require_complete:
                raise ImportPlanError("every Region must resolve to an exact template component")
            continue
        if match.region_template_id is None or match.region_template_version is None:
            raise ImportPlanError("every Region must resolve to an independent Region template")
        source_region = current_regions.get((match.sheet_id, match.region_id, match.header_id))
        if source_region is None:
            raise ImportPlanError("matched Region no longer resolves in current evidence")
        if primary is None and match.template_id is not None and match.template_version is not None:
            primary = (match.template_id, match.template_version)
        if primary_region is None:
            primary_region = (
                match.region_template_id,
                match.region_template_version,
            )
        region_version = database.scalar(
            select(RegionTemplateVersion).where(
                RegionTemplateVersion.region_template_id == match.region_template_id,
                RegionTemplateVersion.version == match.region_template_version,
                RegionTemplateVersion.status == TemplateStatus.PUBLISHED,
            )
        )
        if region_version is None:
            raise ImportPlanError("matched independent Region template version is unavailable")
        component = (
            database.get(TemplateRegionComponent, match.template_region_component_id)
            if match.template_region_component_id is not None
            else None
        )
        projection = region_version.layout_rules
        if not isinstance(projection, dict) or not projection:
            raise ImportPlanError("Region template has no reusable layout projection")
        header_end = max(source_region.header.header_rows)
        data_start, data_end = project_region_data_rows(
            region_start=source_region.region.bounds.min_row,
            region_end=source_region.region.bounds.max_row,
            header_end=header_end,
            projection=projection,
        )
        data_start_column = source_region.region.bounds.min_column + int(
            projection.get("data_start_column_offset_from_region_start", 0)
        )
        data_end_column = source_region.region.bounds.max_column - int(
            projection.get("data_end_column_gap_from_region_end", 0)
        )
        projected_layout_mode = str(projection.get("layout_mode") or "")
        if projected_layout_mode == "explicit_header_table":
            projected_layout_mode = "table"
        decisions.append(
            {
                "sheet_id": source_region.sheet.id,
                "region_candidate_id": source_region.region.id,
                "header_candidate_id": source_region.header.id,
                "data_start_row": data_start,
                "data_end_row": data_end,
                "data_start_column": data_start_column,
                "data_end_column": data_end_column,
                "excluded_rows": [
                    data_start + int(offset)
                    for offset in projection.get("excluded_row_offsets", [])
                ],
                "classification": projection.get(
                    "classification",
                    source_region.region.kind,
                ),
                **(
                    {"layout_mode": projected_layout_mode}
                    if projected_layout_mode in {"table", "matrix", "form", "headerless_table"}
                    else {}
                ),
                "materialize": (
                    bool(projection.get("materialize", True))
                    and sheet_region_policy.get(
                        (
                            source_region.sheet.id,
                            source_region.region.id,
                        ),
                        True,
                    )
                    and workbook_sheet_policy.get(
                        source_region.sheet.id,
                        True,
                    )
                ),
                "evidence_ids": [
                    evidence_id
                    for column in source_region.header.columns
                    for evidence_id in column.evidence_cell_ids
                ],
                "merge_decisions": [],
                "template_id": (str(match.template_id) if match.template_id is not None else None),
                "template_version": match.template_version,
                "region_template_id": str(match.region_template_id),
                "region_template_version": match.region_template_version,
                "template_region_component_id": (
                    str(component.id) if component is not None else None
                ),
                "component_key": (
                    component.component_key
                    if component is not None
                    else f"region-template:{match.region_template_id}"
                ),
            }
        )
        bindings = region_version.field_bindings
        projected_columns = projection.get("source_columns", [])
        for binding_index, binding in enumerate(bindings):
            source_selector = binding.get("source_selector")
            if isinstance(source_selector, dict):
                selector_kind = str(source_selector.get("kind", ""))
                if selector_kind not in {"cell", "physical_column"}:
                    raise ImportPlanError(f"unsupported Region source selector: {selector_kind}")
                projected_selector = dict(source_selector)
                if selector_kind == "cell":
                    projected_selector["row"] = source_region.region.bounds.min_row + int(
                        source_selector["row_offset"]
                    )
                    projected_selector["column"] = source_region.region.bounds.min_column + int(
                        source_selector["column_offset"]
                    )
                    if "label_row_offset" in source_selector:
                        projected_selector["label_row"] = source_region.region.bounds.min_row + int(
                            source_selector["label_row_offset"]
                        )
                    if "label_column_offset" in source_selector:
                        projected_selector["label_column"] = (
                            source_region.region.bounds.min_column
                            + int(source_selector["label_column_offset"])
                        )
                else:
                    projected_selector["column"] = source_region.region.bounds.min_column + int(
                        source_selector["column_offset"]
                    )
                mappings.append(
                    {
                        "sheet_id": source_region.sheet.id,
                        "region_id": source_region.region.id,
                        "source_column_id": binding["source_column_id"],
                        "header_path": binding["header_path"],
                        "semantic_field_code": binding["semantic_field_code"],
                        "semantic_field_version": binding["semantic_field_version"],
                        "source_selector": projected_selector,
                        "role": binding.get("role"),
                        "normalizer": binding.get("normalizer"),
                        "required": bool(binding.get("required")),
                        "region_template_id": str(match.region_template_id),
                        "region_template_version": match.region_template_version,
                        "template_region_component_id": (
                            str(component.id) if component is not None else None
                        ),
                    }
                )
                continue
            path = _normalized_path([str(part) for part in binding.get("header_path", [])])
            column = resolve_reused_region_column(
                binding=binding,
                binding_index=binding_index,
                bindings=bindings,
                current_columns=source_region.header.columns,
                projected_columns=projected_columns,
            )
            if column is None:
                if not require_complete:
                    continue
                raise ImportPlanError(
                    "Region field path must resolve to one current source column: "
                    + " / ".join(path)
                )
            mappings.append(
                {
                    "sheet_id": source_region.sheet.id,
                    "region_id": source_region.region.id,
                    "source_column_id": column.source_column_id,
                    "header_path": list(column.header_path),
                    "semantic_field_code": binding["semantic_field_code"],
                    "semantic_field_version": binding["semantic_field_version"],
                    "role": binding.get("role"),
                    "normalizer": binding.get("normalizer"),
                    "required": bool(binding.get("required")),
                    "template_id": (
                        str(match.template_id) if match.template_id is not None else None
                    ),
                    "template_version": match.template_version,
                    "region_template_id": str(match.region_template_id),
                    "region_template_version": match.region_template_version,
                    "template_region_component_id": (
                        str(component.id) if component is not None else None
                    ),
                }
            )
    if require_complete and primary_region is None:
        raise ImportPlanError("Region import plan has no primary template")
    return decisions, mappings, primary, primary_region


def build_reused_field_match_mappings(
    database: Session,
    *,
    item: IngestionItem,
    reused_decisions: list[dict[str, Any]],
    reused_mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add resolved field-level matches owned by the supplied Region decisions."""
    mapped_source_columns = {
        str(mapping["source_column_id"]) for mapping in reused_mappings
    }
    reused_region_ids = {
        str(decision["region_candidate_id"]) for decision in reused_decisions
    }
    headerless_region_ids = {
        str(decision["region_candidate_id"])
        for decision in reused_decisions
        if decision.get("layout_mode") == "headerless_table"
    }
    additions: list[dict[str, Any]] = []
    for field_match in database.scalars(
        select(FieldMatch).where(
            FieldMatch.item_id == item.id,
            FieldMatch.region_id.in_(reused_region_ids),
            FieldMatch.requires_hermes.is_(False),
            FieldMatch.semantic_field_code.is_not(None),
            FieldMatch.semantic_field_version.is_not(None),
        )
    ):
        if field_match.source_column_id in mapped_source_columns:
            continue
        mapping: dict[str, Any] = {
            "sheet_id": field_match.sheet_id,
            "region_id": field_match.region_id,
            "source_column_id": field_match.source_column_id,
            "header_path": field_match.header_path,
            "semantic_field_code": field_match.semantic_field_code,
            "semantic_field_version": field_match.semantic_field_version,
            "role": field_match.context.get("role"),
            "normalizer": None,
            "required": False,
            "field_match_id": str(field_match.id),
            "role_source": (
                "field_match_context" if field_match.context.get("role") else None
            ),
        }
        if field_match.region_id in headerless_region_ids:
            try:
                physical_column = int(
                    field_match.source_column_id.rsplit(":column:", 1)[1]
                )
            except (IndexError, ValueError) as exc:
                raise ImportPlanError(
                    "headerless field match has no physical column identity"
                ) from exc
            mapping["source_selector"] = {
                "kind": "physical_column",
                "column": physical_column,
            }
        additions.append(mapping)
        mapped_source_columns.add(field_match.source_column_id)
    return additions


def approve_matched_region_plan(
    database: Session,
    *,
    item: IngestionItem,
    actor: str = "system:auto-template",
    comment: str = "所有业务表区域精确命中已发布模板组件",
) -> ApprovedImportPlan:
    summary = database.get(TemplateMatch, item.id)
    if summary is None:
        raise ImportPlanError("Region match summary is required")
    try:
        decisions, mappings, primary, primary_region = build_reused_region_fragments(
            database,
            item=item,
            require_complete=True,
        )
    except ImportPlanError:
        matches = list(
            database.scalars(
                select(RegionTemplateMatch).where(RegionTemplateMatch.item_id == item.id)
            )
        )
        if (
            len(matches) == 1
            and summary.differences.get("workbook_fast_route")
            and not matches[0].requires_hermes
            and matches[0].template_id is not None
            and matches[0].template_version is not None
        ):
            return approve_plan(
                database,
                item=item,
                template_id=matches[0].template_id,
                template_version=matches[0].template_version,
                layout_plan={},
                field_mappings=[],
                actor=actor,
                comment=comment,
            )
        raise
    if primary_region is None:
        raise ImportPlanError("Region import plan has no primary template")
    mappings.extend(
        build_reused_field_match_mappings(
            database,
            item=item,
            reused_decisions=decisions,
            reused_mappings=mappings,
        )
    )
    normalized_mappings = _disambiguate_duplicate_field_roles(mappings)
    frozen_decisions = _freeze_region_decisions(decisions, normalized_mappings)
    latest = database.scalar(
        select(ApprovedImportPlan)
        .where(ApprovedImportPlan.item_id == item.id)
        .order_by(ApprovedImportPlan.revision.desc())
        .limit(1)
    )
    return approve_plan(
        database,
        item=item,
        template_id=primary[0] if primary is not None else None,
        template_version=primary[1] if primary is not None else None,
        primary_region_template_id=primary_region[0],
        primary_region_template_version=primary_region[1],
        layout_plan={
            "contract_version": "approved-region-import-plan/v2",
            "decisions": frozen_decisions,
        },
        field_mappings=normalized_mappings,
        actor=actor,
        comment=comment,
        supersedes_plan_id=latest.id if latest is not None else None,
    )


def approve_hybrid_region_plan(
    database: Session,
    *,
    item: IngestionItem,
    provisional_template_id: uuid.UUID,
    provisional_template_version: int,
    proposal_id: uuid.UUID,
    hermes_layout_decisions: list[dict[str, Any]],
    hermes_field_decisions: list[dict[str, Any]] | None = None,
) -> ApprovedImportPlan:
    reused_decisions, reused_mappings, _, _ = build_reused_region_fragments(
        database,
        item=item,
        require_complete=False,
    )
    reused_region_ids = {str(decision["region_candidate_id"]) for decision in reused_decisions}
    supplied_hermes_region_ids = {
        str(decision.get("region_candidate_id", "")) for decision in hermes_layout_decisions
    }
    if not supplied_hermes_region_ids or "" in supplied_hermes_region_ids:
        raise ImportPlanError("Hermes plan contains no valid unmatched Region")
    new_region_layout_decisions = [
        decision
        for decision in hermes_layout_decisions
        if str(decision.get("region_candidate_id", "")) not in reused_region_ids
    ]
    region_sheet_ids = {
        match.region_id: match.sheet_id
        for match in database.scalars(
            select(RegionTemplateMatch).where(RegionTemplateMatch.item_id == item.id)
        )
    }
    annotated_hermes = [
        {
            **decision,
            "sheet_id": region_sheet_ids.get(
                str(decision["region_candidate_id"]),
                decision.get("sheet_id"),
            ),
            "template_id": str(provisional_template_id),
            "template_version": provisional_template_version,
            "template_region_component_id": None,
            "component_key": "hermes-provisional",
        }
        for decision in new_region_layout_decisions
    ]
    resolved_field_mappings = build_reused_field_match_mappings(
        database,
        item=item,
        reused_decisions=[*reused_decisions, *annotated_hermes],
        reused_mappings=reused_mappings,
    )
    headerless_region_ids = {
        str(decision["region_candidate_id"])
        for decision in [*reused_decisions, *annotated_hermes]
        if decision.get("layout_mode") == "headerless_table"
    }
    mapped_source_columns = {
        str(mapping["source_column_id"])
        for mapping in [*reused_mappings, *resolved_field_mappings]
    }
    profile_record = database.get(DocumentProfile, item.id)
    if profile_record is None:
        raise ImportPlanError("Hermes field mappings require the current profile")
    profile = load_workbook_profile(profile_record)
    materialized_hermes_regions = {
        str(decision["region_candidate_id"])
        for decision in new_region_layout_decisions
        if bool(decision.get("materialize", True))
    }
    approved_headers: dict[str, tuple[str, Any]] = {}
    for decision in new_region_layout_decisions:
        region_id = str(decision.get("region_candidate_id") or "")
        if region_id not in materialized_hermes_regions:
            continue
        header_id = str(decision.get("header_candidate_id") or "")
        matches = [
            (sheet.id, candidate)
            for sheet in profile.sheets
            for candidate in sheet.header_candidates
            if candidate.id == header_id and candidate.region_id == region_id
        ]
        if len(matches) != 1:
            raise ImportPlanError(
                "Hermes materialized Region does not resolve to one approved header"
            )
        approved_headers[region_id] = matches[0]
    if set(approved_headers) != materialized_hermes_regions:
        raise ImportPlanError("every materialized Hermes Region must own one approved header")
    for match in database.scalars(
        select(RegionTemplateMatch).where(
            RegionTemplateMatch.item_id == item.id,
            RegionTemplateMatch.region_id.in_(reused_region_ids),
        )
    ):
        candidates = [
            (sheet.id, candidate)
            for sheet in profile.sheets
            for candidate in sheet.header_candidates
            if candidate.id == match.header_id and candidate.region_id == match.region_id
        ]
        if len(candidates) != 1:
            raise ImportPlanError(
                "reused Region does not resolve to one approved header"
            )
        approved_headers[match.region_id] = candidates[0]
    approved_field_regions = materialized_hermes_regions | reused_region_ids
    source_columns = {
        column.source_column_id: (
            sheet_id,
            region_id,
            column.header_path,
        )
        for region_id, (sheet_id, candidate) in approved_headers.items()
        for column in candidate.columns
    }
    published_fields = {
        field.code: field.published_version
        for field in database.scalars(
            select(SemanticField).where(SemanticField.published_version.is_not(None))
        )
    }
    hermes_field_mappings: list[dict[str, Any]] = []
    for decision in hermes_field_decisions or []:
        action = str(decision.get("action") or "")
        field_code = decision.get("semantic_field_code") or decision.get(
            "proposed_field_code"
        )
        source_column_id = str(decision.get("source_column_id") or "")
        source = source_columns.get(source_column_id)
        field_version = published_fields.get(str(field_code))
        if (
            action
            not in {
                "REUSE_FIELD",
                "ADD_ALIAS",
                "ROLE_VARIANT",
                "PROPOSE_NEW_FIELD",
            }
            or source is None
            or field_code is None
            or field_version is None
            or source[1] not in approved_field_regions
            or source_column_id in mapped_source_columns
        ):
            continue
        mapping = {
            "sheet_id": source[0],
            "region_id": source[1],
            "source_column_id": source_column_id,
            "header_path": source[2],
            "semantic_field_code": str(field_code),
            "semantic_field_version": field_version,
            "role": decision.get("role"),
            "normalizer": None,
            "required": False,
            "mapping_source": "hermes",
        }
        if source[1] in headerless_region_ids:
            try:
                physical_column = int(source_column_id.rsplit(":column:", 1)[1])
            except (IndexError, ValueError) as exc:
                raise ImportPlanError(
                    "headerless Hermes field has no physical column identity"
                ) from exc
            mapping["source_selector"] = {
                "kind": "physical_column",
                "column": physical_column,
            }
        hermes_field_mappings.append(mapping)
    all_mappings = _disambiguate_duplicate_field_roles(
        [
            *reused_mappings,
            *resolved_field_mappings,
            *hermes_field_mappings,
        ]
    )
    frozen_decisions = _freeze_region_decisions(
        [*reused_decisions, *annotated_hermes],
        all_mappings,
    )
    return approve_plan(
        database,
        item=item,
        template_id=provisional_template_id,
        template_version=provisional_template_version,
        layout_plan={
            "contract_version": "approved-region-import-plan/v2",
            "decisions": frozen_decisions,
        },
        field_mappings=all_mappings,
        actor="system:hermes",
        comment=("精确命中的 Region 复用已发布模板；仅未命中的 Region 使用 Hermes 临时计划"),
        plan_source="hermes_provisional",
        proposal_id=proposal_id,
    )


def _disambiguate_duplicate_field_roles(
    mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep one canonical value and give repeated semantic columns stable roles."""
    normalized = [dict(mapping) for mapping in mappings]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for mapping in normalized:
        key = (
            str(mapping.get("region_id") or ""),
            str(mapping.get("semantic_field_code") or ""),
        )
        groups.setdefault(key, []).append(mapping)
    for group in groups.values():
        if len(group) < 2:
            continue
        used_roles: set[str] = set()
        for mapping in group:
            existing_role = str(mapping.get("role") or "")
            if not existing_role:
                continue
            if existing_role not in used_roles:
                used_roles.add(existing_role)
                continue
            header_path = [
                str(part).strip() for part in mapping.get("header_path", []) if str(part).strip()
            ]
            leaf = header_path[-1] if header_path else ""
            base_role = (
                "registry_comparison"
                if any(marker in leaf for marker in ("比对", "核对", "校验"))
                else "duplicate"
            )
            role = base_role
            suffix = 2
            while role in used_roles:
                role = f"{base_role}_{suffix}"
                suffix += 1
            mapping["role"] = role
            mapping["role_source"] = "backend_conflict_disambiguation"
            mapping["requires_review"] = True
            used_roles.add(role)
        without_role = [mapping for mapping in group if not mapping.get("role")]
        canonical = next(
            (mapping for mapping in without_role if mapping.get("mapping_source") != "hermes"),
            without_role[0] if without_role else None,
        )
        for mapping in without_role:
            if mapping is canonical:
                continue
            header_path = [
                str(part).strip() for part in mapping.get("header_path", []) if str(part).strip()
            ]
            base_role = analyze_header_path(header_path).role or "duplicate"
            role = base_role
            suffix = 2
            while role in used_roles:
                role = f"{base_role}_{suffix}"
                suffix += 1
            mapping["role"] = role
            mapping["role_source"] = "backend_disambiguation"
            mapping["requires_review"] = True
            used_roles.add(role)
    return normalized
