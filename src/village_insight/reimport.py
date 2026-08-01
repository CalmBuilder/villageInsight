from __future__ import annotations

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from village_insight.db.models import (
    ApprovedImportPlan,
    BatchStatus,
    BuildResultDeletionStatus,
    DatasetRecord,
    DocumentProfile,
    EvidenceStatus,
    FormalImportStatus,
    GovernanceResolution,
    HermesRecognitionRecord,
    ImportExecution,
    IngestionBuildResultDeletion,
    IngestionItem,
    ItemStatus,
    Job,
    JobStatus,
    QualityIssue,
    RecordIndexValue,
    RecordValueLineage,
    RegionTemplateMatch,
    TemplateMatch,
    TemplateProposal,
    utcnow,
)
from village_insight.jobs.queue import enqueue_for_item
from village_insight.parsing.identity import file_sha256
from village_insight.source_paths import SourcePathError, resolve_source_path


class ReimportError(RuntimeError):
    pass


def reset_item_for_reimport(
    database: Session,
    item_id: uuid.UUID,
    *,
    restore_deleted: bool = False,
    restored_by_user_id: uuid.UUID | None = None,
) -> IngestionItem:
    item = database.scalar(
        select(IngestionItem).where(IngestionItem.id == item_id).with_for_update()
    )
    if item is None:
        raise ReimportError("文件不存在")
    deleted_result = (
        item.status == ItemStatus.RESULT_DELETED
        or item.build_result_deletion_status == BuildResultDeletionStatus.DELETED
    )
    if deleted_result and not restore_deleted:
        raise ReimportError("文件结果已删除，必须显式选择恢复后重新入库")
    deletion_record = None
    if deleted_result:
        deletion_record = database.scalar(
            select(IngestionBuildResultDeletion)
            .where(IngestionBuildResultDeletion.item_id == item.id)
            .with_for_update()
        )
        if deletion_record is None or deletion_record.status != "completed":
            raise ReimportError("删除审计记录不完整，不能恢复重新入库")
    try:
        source_path = resolve_source_path(item.source_path)
    except SourcePathError as exc:
        raise ReimportError("源文件已丢失或校验失败，不能重新入库") from exc
    if file_sha256(source_path) != item.source_sha256:
        raise ReimportError("源文件内容已变化，请使用替换文件功能")
    running_job = database.scalar(
        select(Job.id)
        .where(
            Job.item_id == item.id,
            Job.status == JobStatus.RUNNING,
        )
        .with_for_update()
    )
    if running_job is not None:
        raise ReimportError("文件正在处理中，请完成后再重新入库")

    old_formal_status = item.formal_import_status
    plan_ids = list(
        database.scalars(select(ApprovedImportPlan.id).where(ApprovedImportPlan.item_id == item.id))
    )

    record_ids = select(DatasetRecord.id).where(DatasetRecord.item_id == item.id).scalar_subquery()
    index_value_ids = (
        select(RecordIndexValue.id)
        .where(RecordIndexValue.record_id.in_(record_ids))
        .scalar_subquery()
    )
    database.execute(
        delete(RecordValueLineage).where(
            RecordValueLineage.record_index_value_id.in_(index_value_ids)
        )
    )
    database.execute(delete(RecordIndexValue).where(RecordIndexValue.record_id.in_(record_ids)))
    database.execute(delete(DatasetRecord).where(DatasetRecord.item_id == item.id))
    database.execute(delete(QualityIssue).where(QualityIssue.item_id == item.id))
    if plan_ids:
        database.execute(
            delete(ImportExecution).where(ImportExecution.approved_plan_id.in_(plan_ids))
        )
        database.execute(
            update(GovernanceResolution)
            .where(GovernanceResolution.approved_plan_id.in_(plan_ids))
            .values(approved_plan_id=None)
        )
        database.execute(
            update(ApprovedImportPlan)
            .where(ApprovedImportPlan.item_id == item.id)
            .values(supersedes_plan_id=None)
        )
        database.execute(delete(ApprovedImportPlan).where(ApprovedImportPlan.item_id == item.id))
    governed_proposal_ids = select(GovernanceResolution.proposal_id)
    database.execute(
        delete(TemplateProposal).where(
            TemplateProposal.source_item_id == item.id,
            TemplateProposal.id.not_in(governed_proposal_ids),
        )
    )
    database.execute(
        delete(HermesRecognitionRecord).where(HermesRecognitionRecord.item_id == item.id)
    )
    database.execute(delete(RegionTemplateMatch).where(RegionTemplateMatch.item_id == item.id))
    database.execute(delete(TemplateMatch).where(TemplateMatch.item_id == item.id))
    database.execute(delete(DocumentProfile).where(DocumentProfile.item_id == item.id))
    database.execute(delete(Job).where(Job.item_id == item.id))

    item.status = ItemStatus.PENDING
    item.evidence_status = EvidenceStatus.PENDING
    item.formal_import_status = FormalImportStatus.PENDING
    item.parser_name = None
    item.error_code = None
    item.error_message = None
    if deletion_record is not None:
        restored_at = utcnow()
        deletion_record.status = "restored"
        deletion_record.manifest = {
            **(deletion_record.manifest or {}),
            "restoration": {
                "restored_at": restored_at.isoformat(),
                "restored_by_user_id": (
                    str(restored_by_user_id) if restored_by_user_id is not None else None
                ),
                "action": "reimport_same_immutable_source",
            },
        }
        item.build_result_deletion_status = BuildResultDeletionStatus.ACTIVE
        item.build_result_deleted_at = None
        item.build_result_deleted_by_user_id = None
    item.updated_at = utcnow()
    batch = item.batch
    if old_formal_status in {FormalImportStatus.IMPORTED, FormalImportStatus.PARTIAL}:
        batch.completed_files = max(0, batch.completed_files - 1)
    elif old_formal_status == FormalImportStatus.FAILED:
        batch.failed_files = max(0, batch.failed_files - 1)
    elif old_formal_status == FormalImportStatus.DELETED:
        batch.deleted_files = max(0, batch.deleted_files - 1)
    batch.status = BatchStatus.RUNNING
    batch.updated_at = utcnow()
    enqueue_for_item(
        database,
        item=item,
        kind="PROFILE_FILE",
        payload={"reimport": True},
        idempotency_key=f"profile:{item.id}:reimport:{uuid.uuid4()}",
    )
    database.flush()
    return item
