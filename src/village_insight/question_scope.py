from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from village_insight.db.models import DatasetRecord
from village_insight.question_source_versions import (
    default_source_item_ids,
    source_supersession_map,
)


class FrozenQuestionScope(BaseModel):
    contract_version: str = "question-scope-snapshot/v2"
    tenant_id: uuid.UUID
    administrative_unit_ids: tuple[uuid.UUID, ...]
    source_item_ids: tuple[uuid.UUID, ...]
    selected_source_item_id: uuid.UUID | None = None
    superseded_source_item_ids: tuple[uuid.UUID, ...] = ()
    record_created_before: datetime
    source_item_fingerprint: str


def freeze_question_scope(
    database: Session,
    *,
    tenant_id: uuid.UUID,
    administrative_unit_ids: tuple[uuid.UUID, ...],
    record_created_before: datetime,
    selected_source_item_id: uuid.UUID | None = None,
) -> FrozenQuestionScope:
    """Freeze the exact approved source set visible to one question run."""

    statement = (
        select(DatasetRecord.item_id)
        .where(
            DatasetRecord.tenant_id == tenant_id,
            DatasetRecord.administrative_unit_id.in_(administrative_unit_ids),
            DatasetRecord.quality_status == "passed",
            DatasetRecord.created_at <= record_created_before,
        )
        .distinct()
        .order_by(DatasetRecord.item_id)
    )
    eligible_source_item_ids = tuple(database.scalars(statement))
    supersessions = source_supersession_map(
        database,
        tenant_id=tenant_id,
        administrative_unit_ids=administrative_unit_ids,
        eligible_source_item_ids=eligible_source_item_ids,
        declared_before=record_created_before,
    )
    if selected_source_item_id is None:
        source_item_ids = default_source_item_ids(
            eligible_source_item_ids,
            supersessions,
        )
    elif selected_source_item_id in eligible_source_item_ids:
        source_item_ids = (selected_source_item_id,)
    else:
        source_item_ids = ()
    fingerprint_material = "\n".join(str(item_id) for item_id in source_item_ids)
    return FrozenQuestionScope(
        tenant_id=tenant_id,
        administrative_unit_ids=administrative_unit_ids,
        source_item_ids=source_item_ids,
        selected_source_item_id=selected_source_item_id,
        superseded_source_item_ids=tuple(sorted(supersessions)),
        record_created_before=record_created_before,
        source_item_fingerprint=hashlib.sha256(
            fingerprint_material.encode("utf-8")
        ).hexdigest(),
    )
