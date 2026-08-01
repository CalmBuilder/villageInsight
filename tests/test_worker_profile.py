import asyncio
import hashlib
import threading
import uuid
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    ApprovedImportPlan,
    DocumentProfile,
    DocumentTemplate,
    EvidenceStatus,
    FormalImportStatus,
    IngestionBatch,
    IngestionItem,
    ItemStatus,
    Job,
    TemplateMatch,
    TemplateStatus,
    TemplateVersion,
)
from village_insight.hermes.runtime import HermesUnavailableError
from village_insight.parsing.contracts import WorkbookProfile
from village_insight.templates.matching import layout_fingerprint
from village_insight.worker import (
    await_recognition_with_total_timeout,
    match_template,
    profile_file,
    safe_job_error,
)


def test_job_error_sanitization_never_includes_sql_parameters() -> None:
    error = StatementError(
        "insert failed",
        "INSERT INTO dataset_records(raw_data) VALUES (:raw_data)",
        {"raw_data": {"person_name": "受保护原值"}},
        RuntimeError("database full"),
    )

    message = safe_job_error(error)

    assert message == "StatementError: database operation failed"
    assert "受保护原值" not in message
    assert "INSERT" not in message


def test_recognition_total_timeout_cancels_the_active_stage() -> None:
    cancelled = False

    async def never_finishes():
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    with pytest.raises(HermesUnavailableError, match="total runtime limit"):
        asyncio.run(
            await_recognition_with_total_timeout(
                never_finishes(),
                timeout_seconds=0.01,
            )
        )
    assert cancelled is True


def test_worker_stop_cancels_the_complete_recognition_task() -> None:
    cancelled = False
    stop = threading.Event()

    async def never_finishes():
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    stop.set()
    with pytest.raises(HermesUnavailableError, match="was cancelled"):
        asyncio.run(
            await_recognition_with_total_timeout(
                never_finishes(),
                timeout_seconds=60,
                cancel_event=stop,
            )
        )
    assert cancelled is True


def test_worker_persists_versioned_document_profile(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["姓名", "人数"])
    sheet.append(["张三", 2])
    workbook.save(source)
    workbook.close()
    payload = source.read_bytes()

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        batch = IngestionBatch(name="synthetic")
        database.add(batch)
        database.flush()
        item = IngestionItem(
            id=uuid.uuid4(),
            batch_id=batch.id,
            original_name=source.name,
            source_path=str(source),
            source_sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        database.add(item)
        batch.total_files = 1
        database.commit()

        profile_file(database, item.id)

        database.refresh(item)
        evidence = database.get(DocumentProfile, item.id)
        assert item.status == ItemStatus.MATCHING
        assert item.evidence_status == EvidenceStatus.STORED
        assert item.formal_import_status == FormalImportStatus.PENDING
        assert evidence is not None
        assert evidence.contract_version == "workbook-profile/v2"
        assert evidence.profile["sheets"][0]["cells"][0]["coordinate"] == "A1"

        match_template(database, item.id)
        database.refresh(item)
        match = database.get(TemplateMatch, item.id)
        assert item.status == ItemStatus.RECOGNIZING
        assert item.evidence_status == EvidenceStatus.STORED
        assert item.formal_import_status == FormalImportStatus.NEEDS_REVIEW
        assert batch.completed_files == 0
        assert batch.status == "running"
        assert match is not None
        assert match.match_type == "none"
        assert match.requires_hermes is True
        recognition_jobs = database.query(Job).filter(Job.kind == "RECOGNIZE_TEMPLATE_DIFF")
        assert recognition_jobs.count() == 1


def test_exact_published_template_automatically_creates_import_plan(
    tmp_path: Path,
) -> None:
    source = tmp_path / "reusable.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["姓名", "人数"])
    sheet.append(["张三", 2])
    workbook.save(source)
    workbook.close()
    payload = source.read_bytes()

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        batch = IngestionBatch(name="exact", total_files=1)
        database.add(batch)
        database.flush()
        item = IngestionItem(
            batch_id=batch.id,
            original_name=source.name,
            source_path=str(source),
            source_sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        database.add(item)
        database.commit()

        profile_file(database, item.id)
        profile_record = database.get(DocumentProfile, item.id)
        assert profile_record is not None
        profile = WorkbookProfile.model_validate(profile_record.profile)
        header = min(
            profile.sheets[0].header_candidates,
            key=lambda candidate: len(candidate.header_rows),
        )
        bindings = [
            {
                "source_column_id": column.source_column_id,
                "header_path": column.header_path,
                "semantic_field_code": f"test.field_{index}",
                "semantic_field_version": 1,
            }
            for index, column in enumerate(header.columns, start=1)
        ]
        template = DocumentTemplate(code="test.exact", published_version=1)
        template.versions.append(
            TemplateVersion(
                version=1,
                name="精确复用模版",
                status=TemplateStatus.PUBLISHED,
                layout_fingerprint=layout_fingerprint(profile),
                definition={
                    "contract_version": "document-template/v1",
                    "domain": "test",
                    "region_kind": "table",
                    "record_type": "test_record",
                    "record_grain": "one_row_per_record",
                    "field_bindings": bindings,
                },
                source="manual",
            )
        )
        database.add(template)
        database.commit()

        match_template(database, item.id)

        database.refresh(item)
        match = database.get(TemplateMatch, item.id)
        plan = database.query(ApprovedImportPlan).one()
        materialize_job = database.query(Job).filter(Job.kind == "MATERIALIZE_FILE").one()
        assert match is not None
        assert match.match_type == "exact"
        assert match.requires_hermes is False
        assert item.status == ItemStatus.MATERIALIZING
        assert item.formal_import_status == FormalImportStatus.MATERIALIZING
        assert plan.approved_by == "system:auto-template"
        assert materialize_job.payload["plan_id"] == str(plan.id)
        assert materialize_job.max_attempts == 3
        assert database.query(Job).filter(Job.kind == "RECOGNIZE_TEMPLATE_DIFF").count() == 0
