from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from village_insight.api.dependencies import CurrentPrincipal, Database
from village_insight.config import get_settings
from village_insight.db.models import (
    AdministrativeUnit,
    AdministrativeUnitType,
    ApprovedImportPlan,
    DocumentProfile,
    FieldMatch,
    IngestionBatch,
    IngestionItem,
    MembershipRole,
    ProposalStatus,
    QualityIssue,
    RegionTemplateMatch,
    TemplateMatch,
    TemplateProposal,
    Tenant,
    TenantKind,
    UserStatus,
    utcnow,
)
from village_insight.db.schema import (
    ApprovedImportPlanRead,
    BatchCreate,
    BatchRead,
    DirectoryBatchCreate,
    FieldMatchRead,
    ImportPlanApprove,
    ItemRead,
    ProposalAcceptCommand,
    QualityIssueRead,
    RegionTemplateMatchRead,
    ReviewCommand,
    TemplateMatchRead,
    TemplateProposalRead,
)
from village_insight.hermes.recognition import (
    ProposalResolutionError,
    accept_recognition_proposal,
)
from village_insight.jobs.queue import enqueue_for_item
from village_insight.parsing.contracts import WorkbookProfile
from village_insight.reimport import ReimportError, reset_item_for_reimport
from village_insight.storage import (
    ImportPathError,
    copy_local_file,
    discover_files,
    resolve_import_directory,
    safe_relative_path,
    save_upload,
)
from village_insight.templates.governance import (
    GovernanceError,
    commit_field_governance,
    publish_governed_regions,
)
from village_insight.templates.import_plans import ImportPlanError, approve_plan

router = APIRouter(prefix="/batches", tags=["batches"])


def resolve_import_unit(
    database: Database,
    principal: CurrentPrincipal,
    requested_unit_id: uuid.UUID | None,
) -> AdministrativeUnit:
    if principal.membership.role == MembershipRole.VILLAGE_OPERATOR:
        if (
            not principal.has("imports.create")
            or principal.scope_unit is None
            or principal.scope_unit.unit_type != AdministrativeUnitType.VILLAGE
            or principal.include_descendants
        ):
            raise HTTPException(status_code=403, detail="当前村级身份配置无效")
        if requested_unit_id is not None and requested_unit_id != principal.scope_unit.id:
            raise HTTPException(status_code=403, detail="村级数据员只能向本村入库")
        return principal.scope_unit
    if principal.membership.role == MembershipRole.TENANT_ADMIN:
        if not principal.has("imports.create.any_village"):
            raise HTTPException(status_code=403, detail="没有跨村入库权限")
        if requested_unit_id is None:
            raise HTTPException(status_code=422, detail="总管理员入库时必须选择所属村")
        unit = database.get(AdministrativeUnit, requested_unit_id)
        target_tenant = database.get(Tenant, unit.tenant_id) if unit else None
        if (
            unit is None
            or target_tenant is None
            or unit.tenant_id != principal.tenant.id
            or target_tenant.kind != TenantKind.BUSINESS
            or target_tenant.status != UserStatus.ACTIVE
            or unit.unit_type != AdministrativeUnitType.VILLAGE
            or unit.status != UserStatus.ACTIVE
            or unit.id not in principal.allowed_unit_ids
        ):
            raise HTTPException(status_code=404, detail="目标业务租户或村不存在")
        return unit
    raise HTTPException(status_code=403, detail="当前身份不能上传入库")


def require_batch_import_access(
    principal: CurrentPrincipal,
    batch: IngestionBatch,
) -> None:
    if principal.membership.role == MembershipRole.TENANT_ADMIN:
        if (
            principal.has("imports.create.any_village")
            and batch.tenant_id == principal.tenant.id
            and batch.administrative_unit_id in principal.allowed_unit_ids
        ):
            return
    elif principal.membership.role == MembershipRole.VILLAGE_OPERATOR:
        if (
            principal.has("imports.create")
            and batch.administrative_unit_id in principal.allowed_unit_ids
        ):
            return
    raise HTTPException(status_code=403, detail="当前身份不能向该批次上传文件")


def require_batch_access(
    database: Database,
    batch_id: uuid.UUID,
    principal: CurrentPrincipal,
) -> IngestionBatch:
    batch = database.get(IngestionBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    if (
        principal.membership.role != MembershipRole.PLATFORM_ADMIN
        and batch.tenant_id != principal.tenant.id
    ):
        raise HTTPException(status_code=404, detail="batch not found")
    village_access = (
        principal.has("imports.read.village")
        and batch.administrative_unit_id in principal.allowed_unit_ids
    )
    tenant_access = (
        principal.has("imports.read.tenant")
        and batch.administrative_unit_id in principal.allowed_unit_ids
    )
    governance_access = principal.has("governance.review")
    if not village_access and not tenant_access and not governance_access:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch


def add_stored_item(
    database: Database,
    *,
    batch: IngestionBatch,
    path: Path,
    original_name: str,
    relative_path: str | None,
    sha256: str,
    size_bytes: int,
) -> IngestionItem | None:
    duplicate = database.scalar(
        select(IngestionItem).where(
            IngestionItem.tenant_id == batch.tenant_id,
            IngestionItem.administrative_unit_id == batch.administrative_unit_id,
            IngestionItem.source_sha256 == sha256,
        )
    )
    if duplicate is not None:
        path.unlink(missing_ok=True)
        return None
    item = IngestionItem(
        tenant_id=batch.tenant_id,
        administrative_unit_id=batch.administrative_unit_id,
        created_by_user_id=batch.created_by_user_id,
        batch_id=batch.id,
        original_name=original_name,
        relative_path=relative_path,
        source_path=str(path),
        source_sha256=sha256,
        size_bytes=size_bytes,
    )
    try:
        with database.begin_nested():
            database.add(item)
            database.flush()
    except IntegrityError:
        path.unlink(missing_ok=True)
        return None
    enqueue_for_item(
        database,
        item=item,
        kind="PROFILE_FILE",
        payload={},
        idempotency_key=f"profile:{item.id}",
    )
    return item


@router.get("", response_model=list[BatchRead])
def list_batches(
    database: Database,
    principal: CurrentPrincipal,
) -> list[IngestionBatch]:
    statement = select(IngestionBatch)
    if principal.membership.role == MembershipRole.PLATFORM_ADMIN:
        pass
    elif principal.membership.role == MembershipRole.TENANT_ADMIN:
        statement = statement.where(IngestionBatch.tenant_id == principal.tenant.id)
    elif principal.has("imports.read.village"):
        statement = statement.where(IngestionBatch.tenant_id == principal.tenant.id)
        statement = statement.where(
            IngestionBatch.administrative_unit_id.in_(principal.allowed_unit_ids)
        )
    elif not principal.has("governance.review"):
        raise HTTPException(status_code=403, detail="没有文件访问权限")
    return list(database.scalars(statement.order_by(IngestionBatch.created_at.desc())))


@router.post("", response_model=BatchRead, status_code=status.HTTP_201_CREATED)
def create_batch(
    payload: BatchCreate,
    database: Database,
    principal: CurrentPrincipal,
) -> IngestionBatch:
    target_unit = resolve_import_unit(
        database,
        principal,
        payload.administrative_unit_id,
    )
    batch = IngestionBatch(
        name=payload.name,
        source_kind="upload",
        tenant_id=target_unit.tenant_id,
        administrative_unit_id=target_unit.id,
        created_by_user_id=principal.user.id,
    )
    database.add(batch)
    database.commit()
    database.refresh(batch)
    return batch


@router.get("/{batch_id}", response_model=BatchRead)
def get_batch(
    batch_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> IngestionBatch:
    return require_batch_access(database, batch_id, principal)


@router.get("/{batch_id}/items", response_model=list[ItemRead])
def list_items(
    batch_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> list[IngestionItem]:
    require_batch_access(database, batch_id, principal)
    batch = database.scalar(
        select(IngestionBatch)
        .where(IngestionBatch.id == batch_id)
        .options(selectinload(IngestionBatch.items))
    )
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return sorted(batch.items, key=lambda item: item.created_at, reverse=True)


@router.get("/{batch_id}/items/{item_id}/profile", response_model=WorkbookProfile)
def get_item_profile(
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> dict[str, object]:
    require_batch_access(database, batch_id, principal)
    item = database.get(IngestionItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="batch item not found")
    profile = database.get(DocumentProfile, item_id)
    if profile is None:
        raise HTTPException(status_code=409, detail="document profile is not ready")
    return profile.profile


@router.get(
    "/{batch_id}/items/{item_id}/match",
    response_model=TemplateMatchRead,
)
def get_item_match(
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> TemplateMatch:
    require_batch_access(database, batch_id, principal)
    item = database.get(IngestionItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="batch item not found")
    match = database.get(TemplateMatch, item_id)
    if match is None:
        raise HTTPException(status_code=409, detail="template match is not ready")
    return match


@router.get(
    "/{batch_id}/items/{item_id}/region-matches",
    response_model=list[RegionTemplateMatchRead],
)
def list_item_region_matches(
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> list[RegionTemplateMatch]:
    require_batch_access(database, batch_id, principal)
    item = database.get(IngestionItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="batch item not found")
    return list(
        database.scalars(
            select(RegionTemplateMatch)
            .where(RegionTemplateMatch.item_id == item_id)
            .order_by(
                RegionTemplateMatch.sheet_id,
                RegionTemplateMatch.region_id,
                RegionTemplateMatch.header_id,
            )
        )
    )


@router.get(
    "/{batch_id}/items/{item_id}/field-matches",
    response_model=list[FieldMatchRead],
)
def list_item_field_matches(
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> list[FieldMatch]:
    require_batch_access(database, batch_id, principal)
    item = database.get(IngestionItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="batch item not found")
    return list(
        database.scalars(
            select(FieldMatch)
            .where(FieldMatch.item_id == item_id)
            .order_by(
                FieldMatch.sheet_id,
                FieldMatch.region_id,
                FieldMatch.header_id,
                FieldMatch.source_column_id,
            )
        )
    )


@router.get(
    "/{batch_id}/items/{item_id}/proposals",
    response_model=list[TemplateProposalRead],
)
def list_item_proposals(
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> list[TemplateProposal]:
    require_batch_access(database, batch_id, principal)
    item = database.get(IngestionItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="batch item not found")
    return list(
        database.scalars(
            select(TemplateProposal)
            .where(TemplateProposal.source_item_id == item_id)
            .order_by(TemplateProposal.created_at.desc())
        )
    )


@router.get(
    "/{batch_id}/items/{item_id}/quality-issues",
    response_model=list[QualityIssueRead],
)
def list_item_quality_issues(
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> list[QualityIssue]:
    require_batch_access(database, batch_id, principal)
    item = database.get(IngestionItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="batch item not found")
    return list(
        database.scalars(
            select(QualityIssue)
            .where(QualityIssue.item_id == item_id)
            .order_by(QualityIssue.created_at.desc())
        )
    )


@router.post(
    "/{batch_id}/items/{item_id}/proposals/{proposal_id}/accept",
    response_model=TemplateProposalRead,
)
def accept_item_proposal(
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    proposal_id: uuid.UUID,
    command: ProposalAcceptCommand,
    database: Database,
    principal: CurrentPrincipal,
) -> TemplateProposal:
    require_batch_access(database, batch_id, principal)
    if not principal.has("governance.review"):
        raise HTTPException(status_code=403, detail="没有治理权限")
    item = database.get(IngestionItem, item_id)
    proposal = database.get(TemplateProposal, proposal_id)
    if (
        item is None
        or item.batch_id != batch_id
        or proposal is None
        or proposal.source_item_id != item_id
    ):
        raise HTTPException(status_code=404, detail="proposal not found")
    try:
        suggested_grain = proposal.proposal.get("record_grain")
        record_grain = (
            command.record_grain
            or (
                str(suggested_grain.get("value"))
                if isinstance(suggested_grain, dict) and suggested_grain.get("value")
                else None
            )
            or "one_row_per_record"
        )
        governance = commit_field_governance(
            database,
            proposal=proposal,
            resolutions=command.field_resolutions,
            domain=command.domain,
            record_type=command.record_type,
            record_grain=record_grain,
            actor=principal.user.username,
            actor_user_id=principal.user.id,
            comment=command.comment,
        )
        template = accept_recognition_proposal(
            database,
            proposal=proposal,
            actor=principal.user.username,
            actor_user_id=principal.user.id,
            comment=command.comment,
            template_code=command.template_code,
            template_name=command.template_name,
            domain=command.domain,
            record_type=command.record_type,
            record_grain=record_grain,
            field_decisions=governance.field_decisions,
        )
        region_refs = publish_governed_regions(
            database,
            proposal=proposal,
            governance=governance,
            template_name=command.template_name,
            actor=principal.user.username,
            actor_user_id=principal.user.id,
        )
        candidate_version = max(template.versions, key=lambda version: version.version)
        latest_plan = database.scalar(
            select(ApprovedImportPlan)
            .where(ApprovedImportPlan.item_id == item.id)
            .order_by(ApprovedImportPlan.revision.desc())
            .limit(1)
        )
        plan = approve_plan(
            database,
            item=item,
            template_id=template.id,
            template_version=candidate_version.version,
            layout_plan={},
            field_mappings=[],
            actor=principal.user.username,
            comment=f"Hermes 建议已由用户确认：{command.comment}".strip(),
            actor_user_id=principal.user.id,
            plan_source="hermes",
            proposal_id=proposal.id,
            supersedes_plan_id=latest_plan.id if latest_plan is not None else None,
            primary_region_template_id=(
                uuid.UUID(str(region_refs[0]["region_template_id"]))
                if region_refs
                else None
            ),
            primary_region_template_version=(
                int(region_refs[0]["region_template_version"])
                if region_refs
                else None
            ),
        )
        governance.resolution.approved_plan_id = plan.id
        enqueue_for_item(
            database,
            item=item,
            kind="MATERIALIZE_FILE",
            payload={"plan_id": str(plan.id)},
            idempotency_key=f"materialize:{plan.id}",
            max_attempts=1,
        )
    except (GovernanceError, ProposalResolutionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ImportPlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    database.commit()
    database.refresh(proposal)
    return proposal


@router.post(
    "/{batch_id}/items/{item_id}/proposals/{proposal_id}/reject",
    response_model=TemplateProposalRead,
)
def reject_item_proposal(
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    proposal_id: uuid.UUID,
    command: ReviewCommand,
    database: Database,
    principal: CurrentPrincipal,
) -> TemplateProposal:
    require_batch_access(database, batch_id, principal)
    if not principal.has("governance.review"):
        raise HTTPException(status_code=403, detail="没有治理权限")
    item = database.get(IngestionItem, item_id)
    proposal = database.get(TemplateProposal, proposal_id)
    if (
        item is None
        or item.batch_id != batch_id
        or proposal is None
        or proposal.source_item_id != item_id
    ):
        raise HTTPException(status_code=404, detail="proposal not found")
    if proposal.status != ProposalStatus.PENDING:
        raise HTTPException(status_code=409, detail="proposal has already been resolved")
    if not command.comment.strip():
        raise HTTPException(status_code=422, detail="reject requires a comment")
    proposal.status = ProposalStatus.REJECTED
    proposal.resolution_comment = command.comment
    proposal.resolved_by_user_id = principal.user.id
    proposal.resolved_at = utcnow()
    database.commit()
    database.refresh(proposal)
    return proposal


@router.post(
    "/{batch_id}/items/{item_id}/reimport",
    response_model=ItemRead,
)
def reimport_item(
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> IngestionItem:
    batch = require_batch_access(database, batch_id, principal)
    require_batch_import_access(principal, batch)
    item = database.get(IngestionItem, item_id)
    if item is None or item.batch_id != batch.id:
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        reset_item_for_reimport(database, item.id)
        database.commit()
    except ReimportError as exc:
        database.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    database.refresh(item)
    return item


@router.post(
    "/{batch_id}/items/{item_id}/approve",
    response_model=ApprovedImportPlanRead,
    status_code=status.HTTP_201_CREATED,
)
def approve_import_plan(
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ImportPlanApprove,
    database: Database,
    principal: CurrentPrincipal,
) -> ApprovedImportPlan:
    require_batch_access(database, batch_id, principal)
    if not (
        principal.has("imports.create") or principal.has("governance.review")
    ):
        raise HTTPException(status_code=403, detail="没有入库批准权限")
    item = database.get(IngestionItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="batch item not found")
    try:
        plan = approve_plan(
            database,
            item=item,
            template_id=payload.template_id,
            template_version=payload.template_version,
            layout_plan=payload.layout_plan,
            field_mappings=payload.field_mappings,
            actor=principal.user.username,
            comment=payload.comment,
            actor_user_id=principal.user.id,
            supersedes_plan_id=payload.supersedes_plan_id,
        )
    except ImportPlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    enqueue_for_item(
        database,
        item=item,
        kind="MATERIALIZE_FILE",
        payload={"plan_id": str(plan.id)},
        idempotency_key=f"materialize:{plan.id}",
        max_attempts=1,
    )
    database.commit()
    database.refresh(plan)
    return plan


@router.post(
    "/{batch_id}/files",
    response_model=ItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_batch_file(
    batch_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
    file: Annotated[UploadFile, File()],
    relative_path: Annotated[str | None, Form()] = None,
) -> IngestionItem:
    settings = get_settings()
    batch = require_batch_access(database, batch_id, principal)
    require_batch_import_access(principal, batch)
    if batch is None or batch.source_kind != "upload":
        raise HTTPException(status_code=404, detail="upload batch not found")
    if batch.total_files >= settings.max_batch_files:
        raise HTTPException(status_code=413, detail="too many files in one batch")
    destination = settings.resolved_upload_root() / str(batch.id)
    try:
        stored = await save_upload(
            file,
            destination,
            max_bytes=settings.max_upload_bytes,
        )
        item = add_stored_item(
            database,
            batch=batch,
            path=stored.path,
            original_name=stored.original_name,
            relative_path=safe_relative_path(relative_path, stored.original_name),
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
        )
    except ValueError as exc:
        database.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        database.rollback()
        raise HTTPException(
            status_code=409,
            detail="该文件已在当前村入库或处理中，无需重复上传",
        )
    increment = database.execute(
        update(IngestionBatch)
        .where(
            IngestionBatch.id == batch.id,
            IngestionBatch.total_files < settings.max_batch_files,
        )
        .values(total_files=IngestionBatch.total_files + 1)
    )
    if getattr(increment, "rowcount", 0) != 1:
        database.rollback()
        stored.path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="too many files in one batch")
    database.commit()
    database.refresh(item)
    return item


@router.post("/directory", response_model=BatchRead, status_code=status.HTTP_201_CREATED)
def import_directory(
    payload: DirectoryBatchCreate,
    database: Database,
    principal: CurrentPrincipal,
) -> IngestionBatch:
    target_unit = resolve_import_unit(
        database,
        principal,
        payload.administrative_unit_id,
    )
    settings = get_settings()
    try:
        source = resolve_import_directory(payload.directory, settings.resolved_import_roots())
        files = discover_files(source, recursive=payload.recursive, limit=settings.max_batch_files)
    except (ImportPathError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not files:
        raise HTTPException(status_code=422, detail="no supported files found")

    batch = IngestionBatch(
        name=payload.name,
        source_kind="directory",
        tenant_id=target_unit.tenant_id,
        administrative_unit_id=target_unit.id,
        created_by_user_id=principal.user.id,
    )
    database.add(batch)
    database.flush()
    destination = settings.resolved_upload_root() / str(batch.id)
    try:
        for source_file in files:
            stored = copy_local_file(source_file, destination, max_bytes=settings.max_upload_bytes)
            add_stored_item(
                database,
                batch=batch,
                path=stored.path,
                original_name=stored.original_name,
                relative_path=str(source_file.relative_to(source)),
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
            )
    except (OSError, ValueError) as exc:
        database.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    batch.total_files = len(
        database.scalars(select(IngestionItem).where(IngestionItem.batch_id == batch.id)).all()
    )
    if batch.total_files == 0:
        database.rollback()
        raise HTTPException(
            status_code=409,
            detail="目录中的文件均已在当前村入库或处理中",
        )
    database.commit()
    database.refresh(batch)
    return batch
