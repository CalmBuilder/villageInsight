from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql import Select

from village_insight.api.dependencies import Database, GovernorPrincipal, require_governor
from village_insight.db.models import (
    AdministrativeUnit,
    DocumentProfile,
    DocumentTemplate,
    FieldMatch,
    IngestionBatch,
    IngestionItem,
    MembershipRole,
    ProposalStatus,
    QualityIssue,
    TemplateMatch,
    TemplateProposal,
    TemplateVersion,
    Tenant,
    User,
)
from village_insight.db.schema import (
    ReviewFieldEvidenceRead,
    ReviewQueueItemRead,
    ReviewQueuePage,
)
from village_insight.parsing.candidates import select_header_candidates
from village_insight.parsing.profile_storage import load_workbook_profile

router = APIRouter(
    prefix="/reviews",
    tags=["reviews"],
    dependencies=[Depends(require_governor)],
)


def _column_coordinate(index: int) -> str:
    value = index
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _field_evidence(
    database: Database,
    *,
    item_id: uuid.UUID,
    proposal: dict[str, Any],
) -> list[ReviewFieldEvidenceRead]:
    profile_record = database.get(DocumentProfile, item_id)
    if profile_record is None:
        return []
    profile = load_workbook_profile(profile_record)
    columns = {
        column.source_column_id: (sheet.id, sheet.name, candidate.region_id, column)
        for sheet in profile.sheets
        for candidate in select_header_candidates(sheet.header_candidates)
        for column in candidate.columns
    }
    suggestions = {
        str(decision.get("source_column_id")): decision
        for decision in proposal.get("field_decisions", [])
        if isinstance(decision, dict) and decision.get("source_column_id")
    }
    matches = list(
        database.scalars(
            select(FieldMatch)
            .where(FieldMatch.item_id == item_id)
            .order_by(FieldMatch.sheet_id, FieldMatch.region_id, FieldMatch.source_column_id)
        )
    )
    evidence: list[ReviewFieldEvidenceRead] = []
    for match in matches:
        source = columns.get(match.source_column_id)
        suggestion = suggestions.get(match.source_column_id, {})
        requires_resolution = bool(
            match.requires_hermes
            or suggestion.get("requires_review", False)
            or suggestion.get("action") in {"AMBIGUOUS", "SEMANTIC_CONFLICT"}
        )
        if not requires_resolution:
            continue
        sheet_id, sheet_name, region_id, column = (
            source
            if source is not None
            else (match.sheet_id, match.sheet_id, match.region_id, None)
        )
        header_path = (
            [str(part) for part in column.header_path]
            if column is not None
            else [str(part) for part in match.header_path]
        )
        column_index = int(column.column) if column is not None else 1
        candidates = match.differences.get("candidates", [])
        evidence.append(
            ReviewFieldEvidenceRead(
                source_column_id=match.source_column_id,
                sheet_id=sheet_id,
                sheet_name=sheet_name,
                region_id=region_id,
                column_index=column_index,
                column_coordinate=_column_coordinate(column_index),
                header_path=header_path,
                parent_path=header_path[:-1],
                leaf_header=header_path[-1] if header_path else "未命名列",
                observed_data_type=match.observed_data_type,
                match_type=match.match_type,
                score_basis_points=match.score_basis_points,
                candidates=(
                    [candidate for candidate in candidates if isinstance(candidate, dict)]
                    if isinstance(candidates, list)
                    else []
                ),
                hermes_suggestion=(
                    {str(key): value for key, value in suggestion.items()}
                    if suggestion
                    else {}
                ),
                requires_resolution=requires_resolution,
            )
        )
    return evidence


def _proposal_confidence(proposal: dict[str, Any]) -> float | None:
    values = [
        float(decision["confidence"])
        for group in ("field_decisions", "layout_decisions")
        for decision in proposal.get(group, [])
        if isinstance(decision, dict) and isinstance(decision.get("confidence"), (int, float))
    ]
    record_grain = proposal.get("record_grain")
    if isinstance(record_grain, dict) and isinstance(
        record_grain.get("confidence"),
        (int, float),
    ):
        values.append(float(record_grain["confidence"]))
    return min(values) if values else None


def _reason_codes(match: TemplateMatch, proposal: dict[str, Any]) -> list[str]:
    reasons = [
        str(reason)
        for reason in proposal.get("governance_reason_codes", [])
        if isinstance(reason, str)
    ]
    if match.match_type == "none":
        reasons.append("NO_TEMPLATE")
    if match.differences.get("missing_headers"):
        reasons.append("MISSING_HEADERS")
    if any(
        isinstance(decision, dict)
        and decision.get("action")
        in {"PROPOSE_NEW_FIELD", "SEMANTIC_CONFLICT", "AMBIGUOUS", "IGNORE_COLUMN"}
        for decision in proposal.get("field_decisions", [])
    ):
        reasons.append("SEMANTIC_REVIEW")
    if any(
        isinstance(decision, dict) and bool(decision.get("requires_review", True))
        for decision in proposal.get("field_decisions", [])
    ):
        reasons.append("MODEL_REVIEW_REQUIRED")
    return reasons or ["POLICY_REVIEW"]


def _review_read(
    database: Database,
    *,
    proposal: TemplateProposal,
    item: IngestionItem,
    batch: IngestionBatch,
    match: TemplateMatch,
    template: DocumentTemplate | None,
    template_version: TemplateVersion | None,
    tenant: Tenant,
    unit: AdministrativeUnit,
    creator: User,
    field_count: int,
    include_details: bool,
) -> ReviewQueueItemRead:
    reason_codes = _reason_codes(match, proposal.proposal)
    evidence = (
        _field_evidence(database, item_id=item.id, proposal=proposal.proposal)
        if include_details
        else []
    )
    issue_codes = (
        list(
            database.scalars(
                select(QualityIssue.code)
                .where(
                    QualityIssue.item_id == item.id,
                    QualityIssue.code.in_(
                        {"HERMES_LOW_CONFIDENCE", "HERMES_SEMANTIC_CONFLICT"}
                    ),
                )
                .distinct()
                .order_by(QualityIssue.code)
            )
        )
        if include_details
        else []
    )
    summary_proposal = {
        key: proposal.proposal[key]
        for key in ("template_suggestion", "record_grain")
        if key in proposal.proposal
    }
    return ReviewQueueItemRead(
        proposal_id=proposal.id,
        batch_id=batch.id,
        batch_name=batch.name,
        tenant_id=batch.tenant_id,
        tenant_name=tenant.name,
        administrative_unit_id=batch.administrative_unit_id,
        administrative_unit_name=unit.name,
        created_by_user_id=batch.created_by_user_id,
        created_by_display_name=creator.display_name,
        item_id=item.id,
        file_name=item.original_name,
        relative_path=item.relative_path,
        match_type=match.match_type,
        score_basis_points=match.score_basis_points,
        confidence=proposal.confidence or _proposal_confidence(proposal.proposal),
        reason_codes=reason_codes,
        proposal=proposal.proposal if include_details else summary_proposal,
        matched_template_code=(
            template.code if template is not None and match.match_type != "none" else None
        ),
        matched_template_name=(
            template_version.name
            if template_version is not None and match.match_type != "none"
            else None
        ),
        matched_domain=(
            template_version.definition.get("domain")
            if template_version is not None and match.match_type != "none"
            else None
        ),
        matched_record_type=(
            template_version.definition.get("record_type")
            if template_version is not None and match.match_type != "none"
            else None
        ),
        matched_record_grain=(
            template_version.definition.get("record_grain")
            if template_version is not None and match.match_type != "none"
            else None
        ),
        formal_import_status=item.formal_import_status,
        governance_issue_codes=issue_codes,
        review_kind=(
            "structure"
            if "HERMES_STRUCTURE_REVIEW_REQUIRED" in reason_codes
            else "field"
        ),
        field_evidence=evidence,
        field_count=len(evidence) if include_details else field_count,
        created_at=proposal.created_at,
    )


def _review_statement() -> Select[Any]:
    field_counts = (
        select(
            FieldMatch.item_id.label("item_id"),
            func.count(FieldMatch.id)
            .filter(FieldMatch.requires_hermes.is_(True))
            .label("field_count"),
        )
        .group_by(FieldMatch.item_id)
        .subquery()
    )
    return (
        select(
            TemplateProposal,
            IngestionItem,
            IngestionBatch,
            TemplateMatch,
            DocumentTemplate,
            TemplateVersion,
            Tenant,
            AdministrativeUnit,
            User,
            func.coalesce(field_counts.c.field_count, 0),
        )
        .join(IngestionItem, IngestionItem.id == TemplateProposal.source_item_id)
        .join(IngestionBatch, IngestionBatch.id == IngestionItem.batch_id)
        .join(Tenant, Tenant.id == IngestionBatch.tenant_id)
        .join(
            AdministrativeUnit,
            AdministrativeUnit.id == IngestionBatch.administrative_unit_id,
        )
        .join(User, User.id == IngestionBatch.created_by_user_id)
        .join(TemplateMatch, TemplateMatch.item_id == IngestionItem.id)
        .outerjoin(DocumentTemplate, DocumentTemplate.id == TemplateMatch.template_id)
        .outerjoin(
            TemplateVersion,
            and_(
                TemplateVersion.template_id == TemplateMatch.template_id,
                TemplateVersion.version == TemplateMatch.template_version,
            ),
        )
        .outerjoin(field_counts, field_counts.c.item_id == IngestionItem.id)
        .where(
            TemplateProposal.status == ProposalStatus.PENDING,
            TemplateProposal.build_result_retired_at.is_(None),
            IngestionItem.build_result_deletion_status == "active",
        )
    )


@router.get("", response_model=ReviewQueuePage)
def list_reviews(
    database: Database,
    principal: GovernorPrincipal,
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    administrative_unit_id: Annotated[uuid.UUID | None, Query()] = None,
    search: str = Query(default="", max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ReviewQueuePage:
    statement = _review_statement()
    if principal.membership.role != MembershipRole.PLATFORM_ADMIN:
        statement = statement.where(IngestionBatch.tenant_id == principal.tenant.id)
    if tenant_id is not None:
        if (
            principal.membership.role != MembershipRole.PLATFORM_ADMIN
            and tenant_id != principal.tenant.id
        ):
            raise HTTPException(status_code=404, detail="tenant not found")
        statement = statement.where(IngestionBatch.tenant_id == tenant_id)
    if administrative_unit_id is not None:
        if (
            principal.membership.role != MembershipRole.PLATFORM_ADMIN
            and administrative_unit_id not in principal.allowed_unit_ids
        ):
            raise HTTPException(status_code=404, detail="administrative unit not found")
        statement = statement.where(
            IngestionBatch.administrative_unit_id == administrative_unit_id
        )
    if search.strip():
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                IngestionItem.original_name.ilike(pattern),
                IngestionBatch.name.ilike(pattern),
                Tenant.name.ilike(pattern),
                AdministrativeUnit.name.ilike(pattern),
            )
        )
    total = database.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ) or 0
    rows = database.execute(
        statement.order_by(TemplateProposal.created_at, TemplateProposal.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return ReviewQueuePage(
        items=[
            _review_read(
                database,
                proposal=proposal,
                item=item,
                batch=batch,
                match=match,
                template=template,
                template_version=template_version,
                tenant=tenant,
                unit=unit,
                creator=creator,
                field_count=field_count,
                include_details=False,
            )
            for (
                proposal,
                item,
                batch,
                match,
                template,
                template_version,
                tenant,
                unit,
                creator,
                field_count,
            ) in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{proposal_id}", response_model=ReviewQueueItemRead)
def get_review(
    proposal_id: uuid.UUID,
    database: Database,
    principal: GovernorPrincipal,
) -> ReviewQueueItemRead:
    statement = _review_statement().where(TemplateProposal.id == proposal_id)
    if principal.membership.role != MembershipRole.PLATFORM_ADMIN:
        statement = statement.where(IngestionBatch.tenant_id == principal.tenant.id)
    row = database.execute(statement).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="governance review not found")
    (
        proposal,
        item,
        batch,
        match,
        template,
        template_version,
        tenant,
        unit,
        creator,
        field_count,
    ) = row
    return _review_read(
        database,
        proposal=proposal,
        item=item,
        batch=batch,
        match=match,
        template=template,
        template_version=template_version,
        tenant=tenant,
        unit=unit,
        creator=creator,
        field_count=field_count,
        include_details=True,
    )
