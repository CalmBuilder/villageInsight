from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from village_insight.api.routes.batches import add_stored_item
from village_insight.db.base import Base
from village_insight.db.models import IngestionBatch, IngestionItem


def _batch(
    database: Session,
    *,
    tenant_id: uuid.UUID,
    village_id: uuid.UUID,
    user_id: uuid.UUID,
) -> IngestionBatch:
    batch = IngestionBatch(
        name=uuid.uuid4().hex,
        tenant_id=tenant_id,
        administrative_unit_id=village_id,
        created_by_user_id=user_id,
    )
    database.add(batch)
    database.flush()
    return batch


def test_same_source_is_idempotent_per_village_but_allowed_across_villages(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    payload = b"same workbook bytes"
    source_sha256 = hashlib.sha256(payload).hexdigest()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    village_a = uuid.uuid4()
    village_b = uuid.uuid4()

    with Session(engine) as database:
        first_batch = _batch(
            database,
            tenant_id=tenant_id,
            village_id=village_a,
            user_id=user_id,
        )
        first_path = tmp_path / "first.xlsx"
        first_path.write_bytes(payload)
        first = add_stored_item(
            database,
            batch=first_batch,
            path=first_path,
            original_name=first_path.name,
            relative_path=None,
            sha256=source_sha256,
            size_bytes=len(payload),
        )
        assert first is not None

        repeated_batch = _batch(
            database,
            tenant_id=tenant_id,
            village_id=village_a,
            user_id=user_id,
        )
        repeated_path = tmp_path / "repeated.xlsx"
        repeated_path.write_bytes(payload)
        repeated = add_stored_item(
            database,
            batch=repeated_batch,
            path=repeated_path,
            original_name=repeated_path.name,
            relative_path=None,
            sha256=source_sha256,
            size_bytes=len(payload),
        )
        assert repeated is None
        assert not repeated_path.exists()

        other_village_batch = _batch(
            database,
            tenant_id=tenant_id,
            village_id=village_b,
            user_id=user_id,
        )
        other_path = tmp_path / "other-village.xlsx"
        other_path.write_bytes(payload)
        other = add_stored_item(
            database,
            batch=other_village_batch,
            path=other_path,
            original_name=other_path.name,
            relative_path=None,
            sha256=source_sha256,
            size_bytes=len(payload),
        )
        assert other is not None
        assert database.scalar(select(func.count()).select_from(IngestionItem)) == 2
