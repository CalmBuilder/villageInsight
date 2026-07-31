from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from village_insight.db.models import IngestionItem, Job, JobStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


def enqueue(
    session: Session,
    *,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    max_attempts: int = 3,
    tenant_id: uuid.UUID | None = None,
    administrative_unit_id: uuid.UUID | None = None,
    requested_by_user_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
) -> Job:
    existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    job = Job(
        tenant_id=tenant_id,
        administrative_unit_id=administrative_unit_id,
        requested_by_user_id=requested_by_user_id,
        batch_id=batch_id,
        item_id=item_id,
        kind=kind,
        payload=payload,
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
    )
    session.add(job)
    session.flush()
    return job


def enqueue_for_item(
    session: Session,
    *,
    item: IngestionItem,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    max_attempts: int = 3,
) -> Job:
    batch = item.batch
    item_scope = (
        item.tenant_id,
        item.administrative_unit_id,
        item.created_by_user_id,
    )
    batch_scope = (
        batch.tenant_id,
        batch.administrative_unit_id,
        batch.created_by_user_id,
    )
    if item_scope != batch_scope:
        raise ValueError("ingestion item scope does not match its batch")
    payload_item_id = payload.get("item_id")
    if payload_item_id is not None and str(payload_item_id) != str(item.id):
        raise ValueError("job payload item does not match scoped ingestion item")
    return enqueue(
        session,
        kind=kind,
        payload={**payload, "item_id": str(item.id)},
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        tenant_id=item.tenant_id,
        administrative_unit_id=item.administrative_unit_id,
        requested_by_user_id=item.created_by_user_id,
        batch_id=item.batch_id,
        item_id=item.id,
    )


def claim(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    allowed_kinds: Collection[str] | None = None,
) -> Job | None:
    now = utcnow()
    eligibility = [
        Job.available_at <= now,
        or_(
            (Job.status == JobStatus.PENDING)
            & (Job.attempts < Job.max_attempts),
            (Job.status == JobStatus.RUNNING)
            & (Job.lease_expires_at < now)
            & (Job.attempts <= Job.max_attempts),
        ),
    ]
    if allowed_kinds is not None:
        kinds = tuple(allowed_kinds)
        if not kinds:
            return None
        eligibility.append(Job.kind.in_(kinds))
    statement = (
        select(Job)
        .where(*eligibility)
        .order_by(Job.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = session.scalar(statement)
    if job is None:
        return None
    reclaiming_expired_lease = job.status == JobStatus.RUNNING
    job.status = JobStatus.RUNNING
    job.lease_owner = worker_id
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    if not reclaiming_expired_lease:
        job.attempts += 1
    session.flush()
    return job


def succeed(session: Session, *, job_id: uuid.UUID, worker_id: str) -> bool:
    job = session.get(Job, job_id)
    if job is None or job.lease_owner != worker_id:
        return False
    job.status = JobStatus.SUCCEEDED
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_error = None
    return True


def renew(
    session: Session,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int,
) -> bool:
    job = session.get(Job, job_id)
    if job is None or job.status != JobStatus.RUNNING or job.lease_owner != worker_id:
        return False
    job.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
    return True


def fail(
    session: Session,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    error: str,
    retry_delay_seconds: int = 5,
) -> bool:
    job = session.get(Job, job_id)
    if job is None or job.lease_owner != worker_id:
        return False
    job.last_error = error[:4000]
    job.lease_owner = None
    job.lease_expires_at = None
    if job.attempts >= job.max_attempts:
        job.status = JobStatus.FAILED
    else:
        job.status = JobStatus.PENDING
        job.available_at = utcnow() + timedelta(seconds=retry_delay_seconds)
    return True
