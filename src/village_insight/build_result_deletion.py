from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    ApprovedImportPlan,
    BatchStatus,
    BuildResultDeletionStatus,
    DatasetRecord,
    DocumentSheetCatalog,
    FieldMatch,
    FormalImportStatus,
    HermesRecognitionRecord,
    ImportExecution,
    IngestionBatch,
    IngestionBuildResultDeletion,
    IngestionItem,
    ItemStatus,
    Job,
    JobStatus,
    QualityIssue,
    RecordIndexValue,
    RecordValueLineage,
    RegionTemplateMatch,
    SheetCompositionMatch,
    TemplateMatch,
    TemplateProposal,
    TemplateStatus,
    TemplateVersion,
    WorkbookRouteMatch,
    utcnow,
)
from village_insight.jobs.queue import enqueue_for_item


class BuildResultDeletionError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


EXPECTED_ROOT_REFERENCES: dict[str, set[tuple[str, str, str | None]]] = {
    "ingestion_items": {
        ("approved_import_plans", "item_id", "CASCADE"),
        ("dataset_records", "item_id", "RESTRICT"),
        ("document_profiles", "item_id", "CASCADE"),
        ("document_sheet_catalog", "item_id", "CASCADE"),
        ("field_matches", "item_id", "CASCADE"),
        ("governance_field_resolutions", "item_id", "RESTRICT"),
        ("governance_resolutions", "item_id", "RESTRICT"),
        ("hermes_recognition_records", "item_id", "CASCADE"),
        ("ingestion_build_result_deletions", "item_id", "RESTRICT"),
        ("ingestion_item_supersessions", "replacement_item_id", "RESTRICT"),
        ("ingestion_item_supersessions", "superseded_item_id", "RESTRICT"),
        ("jobs", "item_id", "CASCADE"),
        ("quality_issues", "item_id", "CASCADE"),
        ("question_conversations", "source_item_id", "RESTRICT"),
        ("question_runs", "source_item_id", "RESTRICT"),
        ("region_template_matches", "item_id", "CASCADE"),
        ("semantic_ignore_rules", "source_item_id", "RESTRICT"),
        ("sheet_composition_matches", "item_id", "CASCADE"),
        ("template_matches", "item_id", "CASCADE"),
        ("template_proposals", "source_item_id", "SET NULL"),
        ("workbook_route_matches", "item_id", "CASCADE"),
    },
    "approved_import_plans": {
        ("approved_import_plans", "supersedes_plan_id", "RESTRICT"),
        ("dataset_records", "approved_plan_id", "RESTRICT"),
        ("governance_resolutions", "approved_plan_id", "SET NULL"),
        ("import_executions", "approved_plan_id", "RESTRICT"),
        ("quality_issues", "approved_plan_id", "CASCADE"),
    },
    "dataset_records": {("record_index_values", "record_id", "CASCADE")},
    "record_index_values": {
        ("record_value_lineage", "record_index_value_id", "CASCADE")
    },
}


def validate_build_result_deletion_policy() -> None:
    actual: dict[str, set[tuple[str, str, str | None]]] = defaultdict(set)
    for table in Base.metadata.tables.values():
        for foreign_key in table.foreign_keys:
            root = foreign_key.column.table.name
            if root in EXPECTED_ROOT_REFERENCES:
                actual[root].add(
                    (table.name, foreign_key.parent.name, foreign_key.ondelete)
                )
    if dict(actual) != EXPECTED_ROOT_REFERENCES:
        raise BuildResultDeletionError(
            "删除策略与当前数据库模型不一致，请先更新并复核删除清单",
            code="BUILD_RESULT_DELETE_POLICY_DRIFT",
        )


def request_build_result_deletion(
    database: Session,
    *,
    item_id: uuid.UUID,
    requested_by_user_id: uuid.UUID,
) -> IngestionBuildResultDeletion:
    validate_build_result_deletion_policy()
    item = database.scalar(
        select(IngestionItem)
        .where(IngestionItem.id == item_id)
        .with_for_update()
    )
    if item is None:
        raise BuildResultDeletionError("文件不存在", code="ITEM_NOT_FOUND")
    existing = database.scalar(
        select(IngestionBuildResultDeletion).where(
            IngestionBuildResultDeletion.item_id == item.id
        )
    )
    if existing is not None and existing.status in {"pending", "deleting", "completed"}:
        return existing

    jobs = list(
        database.scalars(select(Job).where(Job.item_id == item.id).with_for_update())
    )
    if any(job.status == JobStatus.RUNNING for job in jobs):
        raise BuildResultDeletionError(
            "文件正在处理中，请等待当前任务结束后重试",
            code="BUILD_RESULT_DELETE_JOB_RUNNING",
        )
    for job in jobs:
        if job.status == JobStatus.PENDING:
            job.status = JobStatus.CANCELLED
            job.lease_owner = None
            job.lease_expires_at = None
            job.last_error = "BUILD_RESULT_DELETE_REQUESTED"

    if existing is None:
        deletion = IngestionBuildResultDeletion(
            item_id=item.id,
            tenant_id=item.tenant_id,
            administrative_unit_id=item.administrative_unit_id,
            batch_id=item.batch_id,
            requested_by_user_id=requested_by_user_id,
            source_sha256=item.source_sha256,
        )
        database.add(deletion)
        database.flush()
    else:
        deletion = existing
        deletion.status = "pending"
        deletion.error_code = None

    item.build_result_deletion_status = BuildResultDeletionStatus.PENDING
    enqueue_for_item(
        database,
        item=item,
        kind="DELETE_BUILD_RESULT",
        payload={"deletion_id": str(deletion.id)},
        idempotency_key=f"delete-build-result:{deletion.id}",
        max_attempts=3,
    )
    database.flush()
    return deletion


def _count(database: Session, model: type[Any], *conditions: Any) -> int:
    return int(
        database.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
    )


def _delete(database: Session, model: type[Any], *conditions: Any) -> int:
    result = database.execute(
        delete(model).where(*conditions).execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def _refresh_batch(database: Session, batch_id: uuid.UUID) -> None:
    batch = database.get(IngestionBatch, batch_id)
    if batch is None:
        return
    rows = database.execute(
        select(IngestionItem.formal_import_status, func.count())
        .where(IngestionItem.batch_id == batch_id)
        .group_by(IngestionItem.formal_import_status)
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    batch.completed_files = counts.get(FormalImportStatus.IMPORTED, 0)
    batch.failed_files = counts.get(FormalImportStatus.FAILED, 0)
    batch.deleted_files = counts.get(FormalImportStatus.DELETED, 0)
    partial = counts.get(FormalImportStatus.PARTIAL, 0) + counts.get(
        FormalImportStatus.PENDING_REBUILD, 0
    )
    active = batch.total_files - (
        batch.completed_files + batch.failed_files + batch.deleted_files + partial
    )
    if active > 0:
        batch.status = BatchStatus.RUNNING
    elif batch.failed_files == batch.total_files:
        batch.status = BatchStatus.FAILED
    elif batch.failed_files or partial:
        batch.status = BatchStatus.PARTIAL
    else:
        batch.status = BatchStatus.COMPLETED
    batch.updated_at = utcnow()


def delete_build_result(
    database: Session,
    *,
    item_id: uuid.UUID,
    deletion_id: uuid.UUID,
) -> IngestionBuildResultDeletion:
    validate_build_result_deletion_policy()
    item = database.scalar(
        select(IngestionItem)
        .where(IngestionItem.id == item_id)
        .with_for_update()
    )
    deletion_record = database.scalar(
        select(IngestionBuildResultDeletion)
        .where(
            IngestionBuildResultDeletion.id == deletion_id,
            IngestionBuildResultDeletion.item_id == item_id,
        )
        .with_for_update()
    )
    if item is None or deletion_record is None:
        raise BuildResultDeletionError(
            "删除任务作用域无效", code="BUILD_RESULT_DELETE_SCOPE_MISMATCH"
        )
    if deletion_record.status == "completed":
        return deletion_record
    if (
        deletion_record.tenant_id != item.tenant_id
        or deletion_record.administrative_unit_id != item.administrative_unit_id
        or deletion_record.batch_id != item.batch_id
        or deletion_record.source_sha256 != item.source_sha256
    ):
        raise BuildResultDeletionError(
            "删除任务范围与文件不一致", code="BUILD_RESULT_DELETE_SCOPE_MISMATCH"
        )
    other_running = database.scalar(
        select(Job.id).where(
            Job.item_id == item.id,
            Job.status == JobStatus.RUNNING,
            Job.kind != "DELETE_BUILD_RESULT",
        )
    )
    if other_running is not None:
        raise BuildResultDeletionError(
            "存在并发写入任务", code="BUILD_RESULT_DELETE_JOB_RUNNING"
        )

    item.build_result_deletion_status = BuildResultDeletionStatus.DELETING
    deletion_record.status = "deleting"
    plan_ids = list(
        database.scalars(
            select(ApprovedImportPlan.id).where(ApprovedImportPlan.item_id == item.id)
        )
    )
    proposal_ids = list(
        database.scalars(
            select(TemplateProposal.id).where(TemplateProposal.source_item_id == item.id)
        )
    )
    record_ids = select(DatasetRecord.id).where(DatasetRecord.item_id == item.id)
    index_ids = select(RecordIndexValue.id).where(
        RecordIndexValue.record_id.in_(record_ids)
    )

    mismatched_records = _count(
        database,
        DatasetRecord,
        DatasetRecord.item_id == item.id,
        ~DatasetRecord.approved_plan_id.in_(plan_ids),
    ) if plan_ids else _count(database, DatasetRecord, DatasetRecord.item_id == item.id)
    foreign_records = _count(
        database,
        DatasetRecord,
        DatasetRecord.approved_plan_id.in_(plan_ids),
        DatasetRecord.item_id != item.id,
    ) if plan_ids else 0
    scoped_records = _count(
        database,
        DatasetRecord,
        DatasetRecord.item_id == item.id,
        (DatasetRecord.tenant_id != item.tenant_id)
        | (DatasetRecord.administrative_unit_id != item.administrative_unit_id)
        | (DatasetRecord.ingestion_batch_id != item.batch_id),
    )
    if mismatched_records or foreign_records or scoped_records:
        raise BuildResultDeletionError(
            "正式记录与文件范围不一致，已停止删除",
            code="BUILD_RESULT_DELETE_SCOPE_MISMATCH",
        )

    before = {
        "record_value_lineage": _count(
            database,
            RecordValueLineage,
            RecordValueLineage.record_index_value_id.in_(index_ids),
        ),
        "record_index_values": _count(
            database, RecordIndexValue, RecordIndexValue.record_id.in_(record_ids)
        ),
        "dataset_records": _count(
            database, DatasetRecord, DatasetRecord.item_id == item.id
        ),
        "quality_issues": _count(database, QualityIssue, QualityIssue.item_id == item.id),
        "import_executions": (
            _count(
                database,
                ImportExecution,
                ImportExecution.approved_plan_id.in_(plan_ids),
            )
            if plan_ids
            else 0
        ),
        "hermes_recognition_records": _count(
            database, HermesRecognitionRecord, HermesRecognitionRecord.item_id == item.id
        ),
        "field_matches": _count(database, FieldMatch, FieldMatch.item_id == item.id),
        "region_template_matches": _count(
            database, RegionTemplateMatch, RegionTemplateMatch.item_id == item.id
        ),
        "sheet_composition_matches": _count(
            database, SheetCompositionMatch, SheetCompositionMatch.item_id == item.id
        ),
        "workbook_route_matches": _count(
            database, WorkbookRouteMatch, WorkbookRouteMatch.item_id == item.id
        ),
        "template_matches": _count(database, TemplateMatch, TemplateMatch.item_id == item.id),
        "document_sheet_catalog": _count(
            database, DocumentSheetCatalog, DocumentSheetCatalog.item_id == item.id
        ),
    }
    deleted = {
        "record_value_lineage": _delete(
            database,
            RecordValueLineage,
            RecordValueLineage.record_index_value_id.in_(index_ids),
        ),
        "record_index_values": _delete(
            database, RecordIndexValue, RecordIndexValue.record_id.in_(record_ids)
        ),
        "dataset_records": _delete(
            database, DatasetRecord, DatasetRecord.item_id == item.id
        ),
        "quality_issues": _delete(database, QualityIssue, QualityIssue.item_id == item.id),
        "import_executions": (
            _delete(
                database,
                ImportExecution,
                ImportExecution.approved_plan_id.in_(plan_ids),
            )
            if plan_ids
            else 0
        ),
        "hermes_recognition_records": _delete(
            database, HermesRecognitionRecord, HermesRecognitionRecord.item_id == item.id
        ),
        "field_matches": _delete(database, FieldMatch, FieldMatch.item_id == item.id),
        "region_template_matches": _delete(
            database, RegionTemplateMatch, RegionTemplateMatch.item_id == item.id
        ),
        "sheet_composition_matches": _delete(
            database, SheetCompositionMatch, SheetCompositionMatch.item_id == item.id
        ),
        "workbook_route_matches": _delete(
            database, WorkbookRouteMatch, WorkbookRouteMatch.item_id == item.id
        ),
        "template_matches": _delete(database, TemplateMatch, TemplateMatch.item_id == item.id),
        "document_sheet_catalog": _delete(
            database, DocumentSheetCatalog, DocumentSheetCatalog.item_id == item.id
        ),
    }
    if deleted != before:
        raise BuildResultDeletionError(
            "删除数量与预检清单不一致，已回滚",
            code="BUILD_RESULT_DELETE_COUNT_MISMATCH",
        )

    now = utcnow()
    proposals_result = database.execute(
        update(TemplateProposal)
        .where(
            TemplateProposal.source_item_id == item.id,
            TemplateProposal.build_result_retired_at.is_(None),
        )
        .values(
            build_result_retired_at=now,
            build_result_retired_by_deletion_id=deletion_record.id,
        )
        .execution_options(synchronize_session=False)
    )
    proposals_retired = getattr(proposals_result, "rowcount", 0) or 0
    plans_result = database.execute(
        update(ApprovedImportPlan)
        .where(
            ApprovedImportPlan.item_id == item.id,
            ApprovedImportPlan.build_result_retired_at.is_(None),
        )
        .values(
            build_result_retired_at=now,
            build_result_retired_by_deletion_id=deletion_record.id,
        )
        .execution_options(synchronize_session=False)
    )
    plans_retired = getattr(plans_result, "rowcount", 0) or 0
    versions_retired = 0
    for version in database.scalars(
        select(TemplateVersion).where(
            TemplateVersion.source == "hermes_provisional",
            TemplateVersion.status == TemplateStatus.ADMIN_REVIEW,
            TemplateVersion.build_result_retired_at.is_(None),
        )
    ):
        metadata = version.source_metadata or {}
        if str(metadata.get("source_item_id")) != str(item.id):
            continue
        source_proposal_id = metadata.get("proposal_id")
        if source_proposal_id and str(source_proposal_id) not in {
            str(value) for value in proposal_ids
        }:
            try:
                historical_proposal_id = uuid.UUID(str(source_proposal_id))
            except ValueError as exc:
                raise BuildResultDeletionError(
                    "临时模板归属不明确，已停止删除",
                    code="BUILD_RESULT_DELETE_SHARED_ASSET_AMBIGUOUS",
                ) from exc
            historical_proposal = database.get(
                TemplateProposal,
                historical_proposal_id,
            )
            if (
                historical_proposal is not None
                and historical_proposal.source_item_id != item.id
            ):
                raise BuildResultDeletionError(
                    "临时模板归属不明确，已停止删除",
                    code="BUILD_RESULT_DELETE_SHARED_ASSET_AMBIGUOUS",
                )
        shared_plan = database.scalar(
            select(ApprovedImportPlan.id).where(
                ApprovedImportPlan.item_id != item.id,
                ApprovedImportPlan.template_id == version.template_id,
                ApprovedImportPlan.template_version == version.version,
                ApprovedImportPlan.build_result_retired_at.is_(None),
            )
        )
        if shared_plan is not None:
            continue
        version.build_result_retired_at = now
        version.build_result_retired_by_deletion_id = deletion_record.id
        versions_retired += 1

    deletion_record.manifest = {
        "tenant_id": str(item.tenant_id),
        "administrative_unit_id": str(item.administrative_unit_id),
        "batch_id": str(item.batch_id),
        "item_id": str(item.id),
        "source_sha256": item.source_sha256,
        "approved_plan_ids": [str(value) for value in plan_ids],
        "proposal_ids": [str(value) for value in proposal_ids],
        "before_counts": before,
    }
    deletion_record.deleted_counts = deleted
    deletion_record.retired_counts = {
        "template_proposals": int(proposals_retired),
        "approved_import_plans": int(plans_retired),
        "provisional_template_versions": versions_retired,
    }
    deletion_record.status = "completed"
    deletion_record.error_code = None
    deletion_record.completed_at = now
    item.build_result_deletion_status = BuildResultDeletionStatus.DELETED
    item.build_result_deleted_at = now
    item.build_result_deleted_by_user_id = deletion_record.requested_by_user_id
    item.status = ItemStatus.RESULT_DELETED
    item.formal_import_status = FormalImportStatus.DELETED
    item.error_code = None
    item.error_message = None
    item.updated_at = now
    _refresh_batch(database, item.batch_id)
    database.flush()
    return deletion_record
