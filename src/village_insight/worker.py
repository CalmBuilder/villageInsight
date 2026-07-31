from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from village_insight.config import get_settings
from village_insight.db.models import (
    BatchStatus,
    DocumentProfile,
    DocumentSheetCatalog,
    EvidenceStatus,
    FieldMatch,
    FormalImportStatus,
    IngestionBatch,
    IngestionItem,
    ItemStatus,
    Job,
    JobStatus,
    QualityIssue,
    TemplateMatch,
)
from village_insight.db.session import get_session_factory
from village_insight.hermes.configuration import resolve_configuration
from village_insight.hermes.recognition import (
    PROMPT_VERSION,
    TemplateDiffResult,
    build_diff_request,
    create_provisional_template,
    publish_unambiguous_new_fields,
    published_semantic_catalog,
    recognize_differences,
)
from village_insight.hermes.runtime import EmbeddedHermesRuntime
from village_insight.jobs.queue import claim, enqueue_for_item, fail, renew, succeed
from village_insight.logging import configure_logging
from village_insight.materialization import materialize_plan
from village_insight.parsing.profile_storage import (
    load_workbook_profile,
    store_workbook_profile,
)
from village_insight.parsing.router import ParserRouter
from village_insight.resources import read_memory_snapshot
from village_insight.templates.import_plans import (
    ImportPlanError,
    approve_hybrid_region_plan,
    approve_matched_region_plan,
)
from village_insight.templates.matching import match_profile

logger = logging.getLogger(__name__)

LANE_JOB_KINDS: dict[str, tuple[str, ...] | None] = {
    "all": None,
    "parse": ("PROFILE_FILE", "MATCH_TEMPLATE"),
    "hermes": ("RECOGNIZE_TEMPLATE_DIFF",),
    "materialize": ("MATERIALIZE_FILE",),
}


@contextmanager
def lease_heartbeat(
    *,
    job_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int,
) -> Iterator[None]:
    stopped = threading.Event()
    interval = max(0.5, lease_seconds / 3)
    session_factory = get_session_factory()

    def heartbeat() -> None:
        while not stopped.wait(interval):
            try:
                with session_factory() as heartbeat_database:
                    with heartbeat_database.begin():
                        retained = renew(
                            heartbeat_database,
                            job_id=job_id,
                            worker_id=worker_id,
                            lease_seconds=lease_seconds,
                        )
                if not retained:
                    logger.error(
                        "job lease heartbeat lost ownership",
                        extra={"job_id": str(job_id), "worker_id": worker_id},
                    )
                    return
            except Exception:
                logger.exception(
                    "job lease heartbeat failed",
                    extra={"job_id": str(job_id), "worker_id": worker_id},
                )

    thread = threading.Thread(
        target=heartbeat,
        name=f"job-lease-{str(job_id)[:8]}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=interval + 1)


def refresh_batch(database: Session, batch_id: uuid.UUID) -> None:
    batch = database.get(IngestionBatch, batch_id)
    if batch is None:
        return
    rows = (
        database.execute(
            select(IngestionItem.formal_import_status, func.count())
            .where(IngestionItem.batch_id == batch_id)
            .group_by(IngestionItem.formal_import_status)
        )
        .tuples()
        .all()
    )
    counts: dict[str, int] = dict(rows)
    batch.completed_files = counts.get(FormalImportStatus.IMPORTED, 0)
    partial_files = counts.get(FormalImportStatus.PARTIAL, 0) + counts.get(
        FormalImportStatus.PENDING_REBUILD,
        0,
    )
    active_files = counts.get(FormalImportStatus.NEEDS_REVIEW, 0) + counts.get(
        FormalImportStatus.MATERIALIZING,
        0,
    )
    batch.failed_files = counts.get(FormalImportStatus.FAILED, 0)
    finished = batch.completed_files + partial_files + batch.failed_files
    if finished == 0:
        batch.status = BatchStatus.RUNNING if active_files else BatchStatus.PENDING
    elif finished < batch.total_files:
        batch.status = BatchStatus.RUNNING
    elif batch.failed_files == batch.total_files:
        batch.status = BatchStatus.FAILED
    elif batch.failed_files or partial_files:
        batch.status = BatchStatus.PARTIAL
    else:
        batch.status = BatchStatus.COMPLETED


def profile_file(database: Session, item_id: uuid.UUID) -> None:
    item = database.get(IngestionItem, item_id)
    if item is None:
        raise ValueError(f"ingestion item not found: {item_id}")
    item.status = ItemStatus.PROFILING
    database.commit()
    try:
        profile = ParserRouter().profile(Path(item.source_path))
    except Exception as exc:
        item = database.get(IngestionItem, item_id)
        if item is not None:
            item.status = ItemStatus.FAILED
            item.evidence_status = EvidenceStatus.FAILED
            item.formal_import_status = FormalImportStatus.FAILED
            item.error_code = str(getattr(exc, "code", "PROFILE_FAILED"))[:80]
            item.error_message = str(exc)[:4000]
            refresh_batch(database, item.batch_id)
            database.commit()
        raise

    item = database.get(IngestionItem, item_id)
    if item is None:
        raise ValueError(f"ingestion item disappeared: {item_id}")
    profile_record = database.get(DocumentProfile, item.id)
    if profile_record is None:
        profile_record = DocumentProfile(
            item_id=item.id,
            contract_version=profile.contract_version,
            source_sha256=profile.source_sha256,
            parser_name=profile.parser_name,
            parser_version=profile.parser_version,
            profile={},
        )
        database.add(profile_record)
    else:
        profile_record.contract_version = profile.contract_version
        profile_record.source_sha256 = profile.source_sha256
        profile_record.parser_name = profile.parser_name
        profile_record.parser_version = profile.parser_version
    store_workbook_profile(profile_record, profile)
    database.execute(
        delete(DocumentSheetCatalog).where(DocumentSheetCatalog.item_id == item.id)
    )
    database.add_all(
        [
            DocumentSheetCatalog(
                item_id=item.id,
                sheet_id=sheet.id,
                sheet_name=sheet.name,
                sheet_order=sheet.index,
                region_count=len(sheet.region_candidates),
            )
            for sheet in profile.sheets
        ]
    )
    item.parser_name = profile.parser_name
    item.evidence_status = EvidenceStatus.STORED
    item.status = ItemStatus.MATCHING
    item.error_code = None
    item.error_message = None
    enqueue_for_item(
        database,
        item=item,
        kind="MATCH_TEMPLATE",
        payload={},
        idempotency_key=f"match:{item.id}:{profile.contract_version}:{profile.source_sha256}",
    )
    refresh_batch(database, item.batch_id)
    database.commit()


def match_template(database: Session, item_id: uuid.UUID) -> None:
    item = database.get(IngestionItem, item_id)
    profile_record = database.get(DocumentProfile, item_id)
    if item is None or profile_record is None:
        raise ValueError(f"profiled ingestion item not found: {item_id}")
    item.status = ItemStatus.MATCHING
    database.commit()
    try:
        profile = load_workbook_profile(profile_record)
        match = match_profile(database, item_id=item.id, profile=profile)
    except Exception as exc:
        item = database.get(IngestionItem, item_id)
        if item is not None:
            item.status = ItemStatus.FAILED
            item.formal_import_status = FormalImportStatus.FAILED
            item.error_code = "TEMPLATE_MATCH_FAILED"
            item.error_message = str(exc)[:4000]
            refresh_batch(database, item.batch_id)
            database.commit()
        raise
    item = database.get(IngestionItem, item_id)
    if item is None:
        raise ValueError(f"ingestion item disappeared: {item_id}")
    if match.requires_hermes:
        item.status = ItemStatus.RECOGNIZING
        item.formal_import_status = FormalImportStatus.NEEDS_REVIEW
        enqueue_for_item(
            database,
            item=item,
            kind="RECOGNIZE_TEMPLATE_DIFF",
            payload={},
            idempotency_key=(
                f"recognize:{item.id}:{match.matcher_version}:"
                f"{match.layout_fingerprint}:{match.template_version or 0}:"
                f"{PROMPT_VERSION}"
            ),
        )
    else:
        try:
            plan = approve_matched_region_plan(database, item=item)
        except ImportPlanError as exc:
            item.status = ItemStatus.NEEDS_REVIEW
            item.formal_import_status = FormalImportStatus.NEEDS_REVIEW
            item.error_code = "AUTO_IMPORT_PLAN_BLOCKED"
            item.error_message = str(exc)[:4000]
        else:
            enqueue_for_item(
                database,
                item=item,
                kind="MATERIALIZE_FILE",
                payload={"plan_id": str(plan.id)},
                idempotency_key=f"materialize:{plan.id}",
                max_attempts=1,
            )
            item.error_code = None
            item.error_message = None
    if match.requires_hermes:
        item.error_code = None
        item.error_message = None
    refresh_batch(database, item.batch_id)
    database.commit()


def recognize_template_diff(database: Session, item_id: uuid.UUID) -> None:
    settings = get_settings()
    item = database.get(IngestionItem, item_id)
    profile_record = database.get(DocumentProfile, item_id)
    match = database.get(TemplateMatch, item_id)
    if item is None or profile_record is None or match is None:
        raise ValueError(f"matched ingestion item not found: {item_id}")
    item.status = ItemStatus.RECOGNIZING
    database.commit()
    profile = load_workbook_profile(profile_record)
    request = build_diff_request(
        profile,
        match,
        semantic_catalog=published_semantic_catalog(database),
        field_matches=list(
            database.scalars(
                select(FieldMatch).where(FieldMatch.item_id == item.id)
            )
        ),
    )
    resolved = resolve_configuration(database, settings)
    connection = resolved.connection
    runtime = EmbeddedHermesRuntime(settings, connection)
    proposal = asyncio.run(
        recognize_differences(
            database,
            item_id=item.id,
            request=request,
            profile=profile,
            runtime=runtime,
            provider=connection.provider,
            model=connection.fast_model or connection.model,
            reasoning_model=connection.reasoning_model or connection.model,
        )
    )
    item = database.get(IngestionItem, item_id)
    if item is None:
        raise ValueError(f"ingestion item disappeared: {item_id}")
    publish_unambiguous_new_fields(
        database,
        request=request,
        result=TemplateDiffResult.model_validate(proposal.proposal),
    )
    template, version = create_provisional_template(database, proposal=proposal)
    result = TemplateDiffResult.model_validate(proposal.proposal)
    plan = approve_hybrid_region_plan(
        database,
        item=item,
        provisional_template_id=template.id,
        provisional_template_version=version.version,
        proposal_id=proposal.id,
        hermes_layout_decisions=[
            decision.model_dump(mode="json")
            for decision in result.layout_decisions
        ],
        hermes_field_decisions=[
            decision.model_dump(mode="json")
            for decision in result.field_decisions
        ],
    )
    for reason in result.governance_reason_codes:
        database.add(
            QualityIssue(
                item_id=item.id,
                approved_plan_id=plan.id,
                code=reason,
                severity="warning",
                message=(
                    "Hermes 二次判定后仍存在低置信或语义冲突；"
                    "原始数据已允许部分入库，待管理员治理"
                ),
                evidence={
                    "proposal_id": str(proposal.id),
                    "recognition_passes": result.recognition_passes,
                    "minimum_confidence": result.minimum_confidence,
                    "reason_codes": result.governance_reason_codes,
                },
            )
        )
    enqueue_for_item(
        database,
        item=item,
        kind="MATERIALIZE_FILE",
        payload={"plan_id": str(plan.id)},
        idempotency_key=f"materialize:{plan.id}",
        max_attempts=1,
    )
    item.error_code = None
    item.error_message = None
    refresh_batch(database, item.batch_id)
    database.commit()


def materialize_file(
    database: Session,
    item_id: uuid.UUID,
    plan_id: uuid.UUID,
) -> None:
    item = database.get(IngestionItem, item_id)
    if item is None:
        raise ValueError(f"ingestion item not found: {item_id}")
    item.status = ItemStatus.MATERIALIZING
    item.formal_import_status = FormalImportStatus.MATERIALIZING
    database.commit()
    execution = materialize_plan(database, plan_id)
    item = database.get(IngestionItem, item_id)
    if item is None:
        raise ValueError(f"ingestion item disappeared: {item_id}")
    item.status = ItemStatus.IMPORTED
    item.formal_import_status = (
        FormalImportStatus.IMPORTED
        if execution.status == "completed"
        else FormalImportStatus.PARTIAL
    )
    item.error_code = None
    item.error_message = None
    refresh_batch(database, item.batch_id)
    database.commit()


def validate_job_scope(database: Session, job: Job) -> IngestionItem:
    payload_item_id = job.payload.get("item_id")
    if job.item_id is None or payload_item_id is None:
        raise ValueError("ingestion job is missing its item scope")
    if str(job.item_id) != str(payload_item_id):
        raise ValueError("ingestion job payload item does not match its scope")
    item = database.get(IngestionItem, job.item_id)
    if item is None:
        raise ValueError(f"ingestion item not found: {job.item_id}")
    batch = item.batch
    job_scope = (
        job.tenant_id,
        job.administrative_unit_id,
        job.requested_by_user_id,
        job.batch_id,
    )
    item_scope = (
        item.tenant_id,
        item.administrative_unit_id,
        item.created_by_user_id,
        item.batch_id,
    )
    batch_scope = (
        batch.tenant_id,
        batch.administrative_unit_id,
        batch.created_by_user_id,
        batch.id,
    )
    if job_scope != item_scope or item_scope != batch_scope:
        raise ValueError("ingestion job scope does not match item and batch")
    return item


def process_one(
    worker_id: str,
    *,
    allowed_kinds: tuple[str, ...] | None = None,
) -> bool:
    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as database:
        with database.begin():
            job = claim(
                database,
                worker_id=worker_id,
                lease_seconds=settings.worker_lease_seconds,
                allowed_kinds=allowed_kinds,
            )
        if job is None:
            return False
        job_id = job.id
        job_kind = job.kind
        payload = job.payload
        try:
            with lease_heartbeat(
                job_id=job_id,
                worker_id=worker_id,
                lease_seconds=settings.worker_lease_seconds,
            ):
                validate_job_scope(database, job)
                if job_kind == "PROFILE_FILE":
                    profile_file(database, uuid.UUID(payload["item_id"]))
                elif job_kind == "MATCH_TEMPLATE":
                    match_template(database, uuid.UUID(payload["item_id"]))
                elif job_kind == "RECOGNIZE_TEMPLATE_DIFF":
                    recognize_template_diff(database, uuid.UUID(payload["item_id"]))
                elif job_kind == "MATERIALIZE_FILE":
                    materialize_file(
                        database,
                        uuid.UUID(payload["item_id"]),
                        uuid.UUID(payload["plan_id"]),
                    )
                else:
                    raise ValueError(f"unsupported job kind: {job_kind}")
        except Exception as exc:
            database.rollback()
            with database.begin():
                fail(
                    database,
                    job_id=job_id,
                    worker_id=worker_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                failed_job = database.get(type(job), job_id)
                if (
                    failed_job is not None
                    and failed_job.status == JobStatus.FAILED
                    and job_kind in {"PROFILE_FILE", "MATCH_TEMPLATE"}
                ):
                    failed_item = database.get(
                        IngestionItem,
                        uuid.UUID(payload["item_id"]),
                    )
                    if failed_item is not None:
                        failed_item.status = ItemStatus.FAILED
                        failed_item.formal_import_status = FormalImportStatus.FAILED
                        failed_item.error_code = (
                            "PROFILE_FAILED"
                            if job_kind == "PROFILE_FILE"
                            else "TEMPLATE_MATCH_FAILED"
                        )
                        failed_item.error_message = str(exc)[:4000]
                        refresh_batch(database, failed_item.batch_id)
                if (
                    failed_job is not None
                    and failed_job.status == JobStatus.FAILED
                    and job_kind == "RECOGNIZE_TEMPLATE_DIFF"
                ):
                    failed_item = database.get(
                        IngestionItem,
                        uuid.UUID(payload["item_id"]),
                    )
                    if failed_item is not None:
                        failed_item.status = ItemStatus.NEEDS_REVIEW
                        failed_item.formal_import_status = FormalImportStatus.NEEDS_REVIEW
                        failed_item.error_code = "HERMES_RECOGNITION_FAILED"
                        failed_item.error_message = str(exc)[:4000]
                        refresh_batch(database, failed_item.batch_id)
                if (
                    failed_job is not None
                    and failed_job.status == JobStatus.FAILED
                    and job_kind == "MATERIALIZE_FILE"
                ):
                    failed_item = database.get(
                        IngestionItem,
                        uuid.UUID(payload["item_id"]),
                    )
                    if failed_item is not None:
                        failed_item.status = ItemStatus.FAILED
                        failed_item.formal_import_status = FormalImportStatus.FAILED
                        failed_item.error_code = "MATERIALIZATION_FAILED"
                        failed_item.error_message = str(exc)[:4000]
                        database.add(
                            QualityIssue(
                                item_id=failed_item.id,
                                approved_plan_id=uuid.UUID(payload["plan_id"]),
                                code=str(
                                    getattr(
                                        exc,
                                        "code",
                                        "MATERIALIZATION_FAILED",
                                    )
                                )[:80],
                                severity="error",
                                message=str(exc)[:4000],
                                evidence={"job_id": str(job_id)},
                            )
                        )
                        refresh_batch(database, failed_item.batch_id)
            logger.exception("job failed", extra={"job_id": str(job_id)})
        else:
            with database.begin():
                succeed(database, job_id=job_id, worker_id=worker_id)
        return True


def _run_worker_loop(
    *,
    worker_id: str,
    allowed_kinds: tuple[str, ...] | None,
    stopped: threading.Event,
) -> None:
    settings = get_settings()
    logger.info(
        "worker lane started",
        extra={"worker_id": worker_id, "allowed_kinds": allowed_kinds or ("*",)},
    )
    last_memory_warning = 0.0
    while not stopped.is_set():
        memory = read_memory_snapshot()
        if (
            memory is not None
            and memory.available_mb < settings.worker_min_available_memory_mb
        ):
            now = time.monotonic()
            if now - last_memory_warning >= 60:
                logger.warning(
                    "worker admission paused by memory guard",
                    extra={
                        "worker_id": worker_id,
                        "available_memory_mb": memory.available_mb,
                        "minimum_memory_mb": settings.worker_min_available_memory_mb,
                    },
                )
                last_memory_warning = now
            stopped.wait(settings.worker_poll_seconds)
            continue
        worked = process_one(worker_id, allowed_kinds=allowed_kinds)
        if not worked:
            stopped.wait(settings.worker_poll_seconds)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    parser = argparse.ArgumentParser(description="VillageInsight bounded worker pool")
    parser.add_argument(
        "--lane",
        choices=tuple(LANE_JOB_KINDS),
        default=os.environ.get("WORKER_LANE", "all"),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.environ.get("WORKER_CONCURRENCY", "1")),
    )
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    stopped = threading.Event()
    threads: list[threading.Thread] = []
    for index in range(args.concurrency):
        worker_id = (
            f"{socket.gethostname()}:{args.lane}:{index + 1}:"
            f"{uuid.uuid4().hex[:8]}"
        )
        thread = threading.Thread(
            target=_run_worker_loop,
            kwargs={
                "worker_id": worker_id,
                "allowed_kinds": LANE_JOB_KINDS[args.lane],
                "stopped": stopped,
            },
            name=f"worker-{args.lane}-{index + 1}",
        )
        thread.start()
        threads.append(thread)
    try:
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=0.5)
    except KeyboardInterrupt:
        logger.info("worker pool stopping", extra={"lane": args.lane})
    finally:
        stopped.set()
        for thread in threads:
            thread.join(timeout=max(1.0, settings.worker_poll_seconds + 0.5))


if __name__ == "__main__":
    main()
