import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import IngestionBatch, IngestionItem
from village_insight.jobs.queue import (
    claim,
    defer_for_operator_action,
    enqueue,
    enqueue_for_item,
    release_for_shutdown,
    renew,
    utcnow,
)
from village_insight.worker import validate_job_scope


def test_running_job_lease_can_only_be_renewed_by_owner() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        enqueue(
            database,
            kind="TEST",
            payload={},
            idempotency_key="test-renew",
        )
        database.commit()
        with database.begin():
            job = claim(database, worker_id="worker-a", lease_seconds=1)
        assert job is not None
        original_expiry = job.lease_expires_at

        assert (
            renew(
                database,
                job_id=job.id,
                worker_id="worker-b",
                lease_seconds=30,
            )
            is False
        )
        assert renew(
            database,
            job_id=job.id,
            worker_id="worker-a",
            lease_seconds=30,
        )
        database.commit()

        database.refresh(job)
        assert job.lease_expires_at > original_expiry
        assert job.lease_expires_at - original_expiry > timedelta(seconds=20)


def test_worker_lane_claims_only_allowed_job_kinds() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        enqueue(
            database,
            kind="RECOGNIZE_TEMPLATE_DIFF",
            payload={},
            idempotency_key="hermes-first",
        )
        enqueue(
            database,
            kind="PROFILE_FILE",
            payload={},
            idempotency_key="profile-second",
        )
        database.commit()

        with database.begin():
            claimed = claim(
                database,
                worker_id="parse-worker",
                lease_seconds=30,
                allowed_kinds=("PROFILE_FILE", "MATCH_TEMPLATE"),
            )

        assert claimed is not None
        assert claimed.kind == "PROFILE_FILE"


def test_last_attempt_with_expired_lease_is_reclaimed_after_worker_restart() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        job = enqueue(
            database,
            kind="RECOGNIZE_TEMPLATE_DIFF",
            payload={},
            idempotency_key="hermes-restart-reclaim",
            max_attempts=1,
        )
        database.commit()
        with database.begin():
            claimed = claim(
                database,
                worker_id="worker-before-restart",
                lease_seconds=30,
            )
        assert claimed is not None
        claimed.lease_expires_at = utcnow() - timedelta(seconds=1)
        database.commit()

        with database.begin():
            reclaimed = claim(
                database,
                worker_id="worker-after-restart",
                lease_seconds=30,
            )

        assert reclaimed is not None
        assert reclaimed.id == job.id
        assert reclaimed.attempts == 1
        assert reclaimed.lease_owner == "worker-after-restart"


def test_worker_shutdown_release_does_not_spend_retry_budget() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        job = enqueue(
            database,
            kind="RECOGNIZE_TEMPLATE_DIFF",
            payload={},
            idempotency_key="hermes-graceful-shutdown",
            max_attempts=1,
        )
        database.commit()
        with database.begin():
            claimed = claim(
                database,
                worker_id="worker-before-shutdown",
                lease_seconds=30,
            )
        assert claimed is not None
        assert claimed.attempts == 1
        database.commit()

        with database.begin():
            assert release_for_shutdown(
                database,
                job_id=job.id,
                worker_id="worker-before-shutdown",
                reason="Hermes recognition task was cancelled",
            )

        database.refresh(job)
        assert job.status == "pending"
        assert job.attempts == 0
        assert job.lease_owner is None
        database.commit()
        with database.begin():
            reclaimed = claim(
                database,
                worker_id="worker-after-shutdown",
                lease_seconds=30,
            )
        assert reclaimed is not None
        assert reclaimed.id == job.id
        assert reclaimed.attempts == 1


def test_operator_action_deferral_opens_circuit_without_spending_attempt() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        job = enqueue(
            database,
            kind="RECOGNIZE_TEMPLATE_DIFF",
            payload={},
            idempotency_key="hermes-provider-action",
        )
        database.commit()
        with database.begin():
            claimed = claim(
                database,
                worker_id="hermes-worker",
                lease_seconds=30,
            )
        assert claimed is not None
        with database.begin():
            assert defer_for_operator_action(
                database,
                job_id=job.id,
                worker_id="hermes-worker",
                reason="HermesOperatorActionRequiredError code=provider_action",
                retry_delay_seconds=900,
            )

        database.refresh(job)
        assert job.status == "pending"
        assert job.attempts == 0
        assert job.lease_owner is None
        assert job.available_at > job.created_at + timedelta(seconds=850)


def test_ingestion_job_scope_mismatch_is_rejected() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        batch = IngestionBatch(name="scope-check")
        database.add(batch)
        database.flush()
        item = IngestionItem(
            tenant_id=batch.tenant_id,
            administrative_unit_id=batch.administrative_unit_id,
            created_by_user_id=batch.created_by_user_id,
            batch_id=batch.id,
            original_name="scope.xlsx",
            source_path="/tmp/scope.xlsx",
            source_sha256=hashlib.sha256(b"scope").hexdigest(),
            size_bytes=5,
        )
        database.add(item)
        database.flush()
        job = enqueue_for_item(
            database,
            item=item,
            kind="PROFILE_FILE",
            payload={},
            idempotency_key=f"profile:{item.id}",
        )
        database.flush()
        job.administrative_unit_id = uuid.uuid4()
        database.flush()

        with pytest.raises(ValueError, match="scope does not match"):
            validate_job_scope(database, job)
