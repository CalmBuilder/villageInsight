from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    DatasetRecord,
    IngestionItem,
    IngestionItemSupersession,
)


class SourceSupersessionError(ValueError):
    pass


def source_supersession_map(
    database: Session,
    *,
    tenant_id: uuid.UUID,
    administrative_unit_ids: tuple[uuid.UUID, ...],
    eligible_source_item_ids: tuple[uuid.UUID, ...],
    declared_before: datetime,
) -> dict[uuid.UUID, uuid.UUID]:
    """Return effective old-to-new declarations for an already frozen source set."""

    if not administrative_unit_ids or not eligible_source_item_ids:
        return {}
    rows = database.execute(
        select(
            IngestionItemSupersession.superseded_item_id,
            IngestionItemSupersession.replacement_item_id,
        ).where(
            IngestionItemSupersession.tenant_id == tenant_id,
            IngestionItemSupersession.administrative_unit_id.in_(
                administrative_unit_ids
            ),
            IngestionItemSupersession.created_at <= declared_before,
            IngestionItemSupersession.superseded_item_id.in_(
                eligible_source_item_ids
            ),
        )
    )
    return {superseded: replacement for superseded, replacement in rows}


def default_source_item_ids(
    eligible_source_item_ids: tuple[uuid.UUID, ...],
    supersessions: dict[uuid.UUID, uuid.UUID],
) -> tuple[uuid.UUID, ...]:
    """Exclude every historical source that has an eligible replacement."""

    return tuple(
        item_id
        for item_id in eligible_source_item_ids
        if item_id not in supersessions
    )


def declare_source_supersession(
    database: Session,
    *,
    tenant_id: uuid.UUID,
    allowed_administrative_unit_ids: frozenset[uuid.UUID],
    superseded_item_id: uuid.UUID,
    replacement_item_id: uuid.UUID,
    declared_by_user_id: uuid.UUID,
    reason: str,
) -> IngestionItemSupersession:
    if superseded_item_id == replacement_item_id:
        raise SourceSupersessionError("a source file cannot replace itself")
    superseded = database.get(IngestionItem, superseded_item_id)
    replacement = database.get(IngestionItem, replacement_item_id)
    if superseded is None or replacement is None:
        raise SourceSupersessionError("source file does not exist")
    if (
        superseded.tenant_id != tenant_id
        or replacement.tenant_id != tenant_id
        or superseded.administrative_unit_id
        not in allowed_administrative_unit_ids
        or replacement.administrative_unit_id
        not in allowed_administrative_unit_ids
    ):
        raise SourceSupersessionError("source file is outside the governed scope")
    if superseded.administrative_unit_id != replacement.administrative_unit_id:
        raise SourceSupersessionError(
            "source versions must belong to the same administrative unit"
        )
    if replacement.created_at < superseded.created_at:
        raise SourceSupersessionError(
            "replacement source must not be older than the superseded source"
        )
    passed_count_rows = database.execute(
        select(DatasetRecord.item_id, func.count(DatasetRecord.id))
        .where(
            DatasetRecord.item_id.in_(
                (superseded_item_id, replacement_item_id)
            ),
            DatasetRecord.tenant_id == tenant_id,
            DatasetRecord.administrative_unit_id
            == superseded.administrative_unit_id,
            DatasetRecord.quality_status == "passed",
        )
        .group_by(DatasetRecord.item_id)
    )
    passed_counts: dict[uuid.UUID, int] = {
        item_id: int(count) for item_id, count in passed_count_rows
    }
    if not passed_counts.get(superseded_item_id) or not passed_counts.get(
        replacement_item_id
    ):
        raise SourceSupersessionError(
            "both source versions must contain approved formal records"
        )
    existing = database.scalar(
        select(IngestionItemSupersession).where(
            IngestionItemSupersession.superseded_item_id
            == superseded_item_id
        )
    )
    if existing is not None:
        raise SourceSupersessionError(
            "the superseded source already has a replacement declaration"
        )

    cursor = replacement_item_id
    visited: set[uuid.UUID] = set()
    while cursor not in visited:
        if cursor == superseded_item_id:
            raise SourceSupersessionError(
                "source replacement declarations cannot form a cycle"
            )
        visited.add(cursor)
        next_item = database.scalar(
            select(IngestionItemSupersession.replacement_item_id).where(
                IngestionItemSupersession.superseded_item_id == cursor
            )
        )
        if next_item is None:
            break
        cursor = next_item

    declaration = IngestionItemSupersession(
        tenant_id=tenant_id,
        administrative_unit_id=superseded.administrative_unit_id,
        superseded_item_id=superseded_item_id,
        replacement_item_id=replacement_item_id,
        declared_by_user_id=declared_by_user_id,
        reason=reason.strip(),
    )
    database.add(declaration)
    database.flush()
    return declaration
