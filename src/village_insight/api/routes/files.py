from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from village_insight.api.dependencies import CurrentPrincipal, Database
from village_insight.db.models import (
    AdministrativeUnit,
    DatasetRecord,
    DocumentSheetCatalog,
    HermesRecognitionRecord,
    IngestionBatch,
    IngestionItem,
    MembershipRole,
    ProposalStatus,
    TemplateMatch,
    TemplateProposal,
    Tenant,
    User,
)
from village_insight.db.schema import FileLedgerItemRead, FileLedgerPage

router = APIRouter(prefix="/files", tags=["files"])


PROCESSING_STATUSES = {
    "pending",
    "profiling",
    "matching",
    "recognizing",
    "ready",
    "materializing",
}


@router.get("", response_model=FileLedgerPage)
def list_files(
    database: Database,
    principal: CurrentPrincipal,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default="", max_length=200),
    status_filter: str = Query(default="all", alias="status"),
    tenant_id: Annotated[uuid.UUID | None, Query()] = None,
    administrative_unit_id: Annotated[uuid.UUID | None, Query()] = None,
) -> FileLedgerPage:
    if not (
        principal.has("imports.read.village")
        or principal.has("imports.read.tenant")
        or principal.has("governance.review")
    ):
        raise HTTPException(status_code=403, detail="没有文件访问权限")
    filters = [IngestionItem.build_result_deletion_status != "deleted"]
    if principal.membership.role == MembershipRole.PLATFORM_ADMIN:
        pass
    elif principal.membership.role == MembershipRole.TENANT_ADMIN:
        filters.append(IngestionItem.tenant_id == principal.tenant.id)
    elif principal.has("imports.read.village"):
        filters.append(IngestionItem.tenant_id == principal.tenant.id)
        filters.append(
            IngestionItem.administrative_unit_id.in_(principal.allowed_unit_ids)
        )
    else:
        filters.append(IngestionItem.tenant_id == principal.tenant.id)
    if tenant_id is not None:
        if (
            principal.membership.role != MembershipRole.PLATFORM_ADMIN
            and tenant_id != principal.tenant.id
        ):
            raise HTTPException(status_code=404, detail="tenant not found")
        filters.append(IngestionItem.tenant_id == tenant_id)
    if administrative_unit_id is not None:
        if (
            principal.membership.role != MembershipRole.PLATFORM_ADMIN
            and administrative_unit_id not in principal.allowed_unit_ids
        ):
            raise HTTPException(status_code=404, detail="administrative unit not found")
        filters.append(IngestionItem.administrative_unit_id == administrative_unit_id)
    term = search.strip()
    if term:
        pattern = f"%{term}%"
        filters.append(
            or_(
                IngestionItem.original_name.ilike(pattern),
                IngestionBatch.name.ilike(pattern),
                AdministrativeUnit.name.ilike(pattern),
                Tenant.name.ilike(pattern),
            )
        )

    sheet_counts = (
        select(
            DocumentSheetCatalog.item_id.label("item_id"),
            func.count().label("sheet_count"),
        )
        .group_by(DocumentSheetCatalog.item_id)
        .subquery()
    )
    hermes_counts = (
        select(
            HermesRecognitionRecord.item_id.label("item_id"),
            func.count().label("hermes_count"),
        )
        .where(HermesRecognitionRecord.call_performed.is_(True))
        .group_by(HermesRecognitionRecord.item_id)
        .subquery()
    )
    record_counts = (
        select(
            DatasetRecord.item_id.label("item_id"),
            func.count().label("record_count"),
            func.count()
            .filter(DatasetRecord.mapping_status == "partial")
            .label("partial_record_count"),
        )
        .group_by(DatasetRecord.item_id)
        .subquery()
    )
    governance_counts = (
        select(
            TemplateProposal.source_item_id.label("item_id"),
            func.count(TemplateProposal.id).label("governance_count"),
        )
        .where(
            TemplateProposal.status == ProposalStatus.PENDING,
            TemplateProposal.build_result_retired_at.is_(None),
        )
        .group_by(TemplateProposal.source_item_id)
        .subquery()
    )

    status_predicates = {
        "imported": IngestionItem.formal_import_status.in_({"imported", "partial"}),
        "partial": IngestionItem.formal_import_status == "partial",
        "processing": or_(
            IngestionItem.status.in_(PROCESSING_STATUSES),
            IngestionItem.build_result_deletion_status.in_(
                {"deletion_pending", "deleting"}
            ),
        ),
        "hermes": or_(
            IngestionItem.status == "recognizing",
            func.coalesce(hermes_counts.c.hermes_count, 0) > 0,
        ),
        "review": or_(
            IngestionItem.status == "needs_review",
            func.coalesce(governance_counts.c.governance_count, 0) > 0,
        ),
        "failed": IngestionItem.status == "failed",
    }
    page_filters = [*filters]
    if status_filter != "all":
        predicate = status_predicates.get(status_filter)
        if predicate is None:
            raise HTTPException(status_code=422, detail="文件状态筛选无效")
        page_filters.append(predicate)

    base_joins = (
        select(IngestionItem.id)
        .join(IngestionBatch, IngestionBatch.id == IngestionItem.batch_id)
        .join(Tenant, Tenant.id == IngestionBatch.tenant_id)
        .join(
            AdministrativeUnit,
            AdministrativeUnit.id == IngestionBatch.administrative_unit_id,
        )
        .join(User, User.id == IngestionBatch.created_by_user_id)
        .outerjoin(hermes_counts, hermes_counts.c.item_id == IngestionItem.id)
        .outerjoin(governance_counts, governance_counts.c.item_id == IngestionItem.id)
    )
    total = database.scalar(
        base_joins.with_only_columns(func.count(IngestionItem.id))
        .where(*page_filters)
        .order_by(None)
    ) or 0
    count_row = database.execute(
        base_joins.with_only_columns(
            func.count(IngestionItem.id),
            *[
                func.count(IngestionItem.id).filter(predicate)
                for predicate in status_predicates.values()
            ],
        )
        .where(*filters)
        .order_by(None)
    ).one()
    rows = database.execute(
        select(
            IngestionItem,
            IngestionBatch,
            Tenant,
            AdministrativeUnit,
            User,
            TemplateMatch,
            func.coalesce(hermes_counts.c.hermes_count, 0),
            func.coalesce(record_counts.c.record_count, 0),
            func.coalesce(record_counts.c.partial_record_count, 0),
            func.coalesce(governance_counts.c.governance_count, 0),
            func.coalesce(sheet_counts.c.sheet_count, 0),
        )
        .join(IngestionBatch, IngestionBatch.id == IngestionItem.batch_id)
        .join(Tenant, Tenant.id == IngestionBatch.tenant_id)
        .join(
            AdministrativeUnit,
            AdministrativeUnit.id == IngestionBatch.administrative_unit_id,
        )
        .join(User, User.id == IngestionBatch.created_by_user_id)
        .outerjoin(TemplateMatch, TemplateMatch.item_id == IngestionItem.id)
        .outerjoin(hermes_counts, hermes_counts.c.item_id == IngestionItem.id)
        .outerjoin(governance_counts, governance_counts.c.item_id == IngestionItem.id)
        .outerjoin(record_counts, record_counts.c.item_id == IngestionItem.id)
        .outerjoin(sheet_counts, sheet_counts.c.item_id == IngestionItem.id)
        .where(*page_filters)
        .order_by(IngestionItem.created_at.desc(), IngestionItem.id)
        .offset(offset)
        .limit(limit)
    ).all()

    result: list[FileLedgerItemRead] = []
    for (
        item,
        batch,
        tenant,
        unit,
        creator,
        match,
        hermes_count,
        record_count,
        partial_record_count,
        governance_count,
        sheet_count,
    ) in rows:
        result.append(
            FileLedgerItemRead(
                id=item.id,
                tenant_id=batch.tenant_id,
                tenant_name=tenant.name,
                administrative_unit_id=batch.administrative_unit_id,
                administrative_unit_name=unit.name,
                created_by_user_id=batch.created_by_user_id,
                created_by_display_name=creator.display_name,
                batch_id=item.batch_id,
                batch_name=batch.name,
                batch_source_kind=batch.source_kind,
                original_name=item.original_name,
                relative_path=item.relative_path,
                size_bytes=item.size_bytes,
                status=item.status,
                evidence_status=item.evidence_status,
                formal_import_status=item.formal_import_status,
                parser_name=item.parser_name,
                error_code=item.error_code,
                error_message=item.error_message,
                build_result_deletion_status=item.build_result_deletion_status,
                build_result_deleted_at=item.build_result_deleted_at,
                build_result_deleted_by_user_id=(
                    item.build_result_deleted_by_user_id
                ),
                created_at=item.created_at,
                updated_at=item.updated_at,
                match_type=match.match_type if match else None,
                score_basis_points=match.score_basis_points if match else None,
                requires_hermes=match.requires_hermes if match else None,
                total_regions=match.total_regions if match else None,
                matched_regions=match.matched_regions if match else None,
                coverage_basis_points=(
                    match.coverage_basis_points if match else None
                ),
                hermes_call_count=hermes_count,
                record_count=record_count,
                partial_record_count=partial_record_count,
                governance_pending=governance_count > 0,
                sheet_count=sheet_count,
            )
        )
    return FileLedgerPage(
        items=result,
        total=total,
        limit=limit,
        offset=offset,
        counts=dict(
            zip(
                [
                    "all",
                    "imported",
                    "partial",
                    "processing",
                    "hermes",
                    "review",
                    "failed",
                ],
                count_row,
                strict=True,
            )
        ),
    )
