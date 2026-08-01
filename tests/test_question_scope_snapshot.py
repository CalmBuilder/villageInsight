from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    AdministrativeUnit,
    DatasetRecord,
    IngestionItemSupersession,
    Tenant,
)
from village_insight.question_scope import freeze_question_scope


def _record(
    *,
    tenant_id: uuid.UUID,
    unit_id: uuid.UUID,
    item_id: uuid.UUID,
    created_at: datetime,
) -> DatasetRecord:
    return DatasetRecord(
        tenant_id=tenant_id,
        administrative_unit_id=unit_id,
        ingestion_batch_id=uuid.uuid4(),
        approved_plan_id=uuid.uuid4(),
        item_id=item_id,
        template_id=uuid.uuid4(),
        template_version=1,
        record_type="population",
        sheet_id="sheet-1",
        region_id="region-1",
        source_row=3,
        quality_status="passed",
        created_at=created_at,
    )


def test_question_scope_freezes_sources_and_record_watermark() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    visible_item_id = uuid.uuid4()
    future_item_id = uuid.uuid4()
    watermark = datetime.now(UTC)
    with Session(engine) as database:
        database.add(Tenant(id=tenant_id, name="测试租户"))
        database.add(
            AdministrativeUnit(
                id=unit_id,
                tenant_id=tenant_id,
                unit_type="village",
                name="青禾村",
            )
        )
        database.add_all(
            [
                _record(
                    tenant_id=tenant_id,
                    unit_id=unit_id,
                    item_id=visible_item_id,
                    created_at=watermark - timedelta(seconds=1),
                ),
                _record(
                    tenant_id=tenant_id,
                    unit_id=unit_id,
                    item_id=future_item_id,
                    created_at=watermark + timedelta(seconds=1),
                ),
            ]
        )
        database.commit()

        snapshot = freeze_question_scope(
            database,
            tenant_id=tenant_id,
            administrative_unit_ids=(unit_id,),
            record_created_before=watermark,
        )
        selected_snapshot = freeze_question_scope(
            database,
            tenant_id=tenant_id,
            administrative_unit_ids=(unit_id,),
            record_created_before=watermark,
            selected_source_item_id=future_item_id,
        )

    assert snapshot.source_item_ids == (visible_item_id,)
    assert snapshot.record_created_before == watermark
    assert selected_snapshot.source_item_ids == ()
    assert len(snapshot.source_item_fingerprint) == 64


def test_default_scope_excludes_superseded_source_but_explicit_old_remains() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    user_id = uuid.uuid4()
    old_item_id = uuid.uuid4()
    new_item_id = uuid.uuid4()
    watermark = datetime.now(UTC)
    with Session(engine) as database:
        database.add(Tenant(id=tenant_id, name="测试租户"))
        database.add(
            AdministrativeUnit(
                id=unit_id,
                tenant_id=tenant_id,
                unit_type="village",
                name="青禾村",
            )
        )
        database.add_all(
            [
                _record(
                    tenant_id=tenant_id,
                    unit_id=unit_id,
                    item_id=old_item_id,
                    created_at=watermark - timedelta(seconds=2),
                ),
                _record(
                    tenant_id=tenant_id,
                    unit_id=unit_id,
                    item_id=new_item_id,
                    created_at=watermark - timedelta(seconds=1),
                ),
                IngestionItemSupersession(
                    tenant_id=tenant_id,
                    administrative_unit_id=unit_id,
                    superseded_item_id=old_item_id,
                    replacement_item_id=new_item_id,
                    reason="新版替代",
                    declared_by_user_id=user_id,
                    created_at=watermark - timedelta(milliseconds=500),
                ),
            ]
        )
        database.commit()

        default_snapshot = freeze_question_scope(
            database,
            tenant_id=tenant_id,
            administrative_unit_ids=(unit_id,),
            record_created_before=watermark,
        )
        old_snapshot = freeze_question_scope(
            database,
            tenant_id=tenant_id,
            administrative_unit_ids=(unit_id,),
            record_created_before=watermark,
            selected_source_item_id=old_item_id,
        )

    assert default_snapshot.contract_version == "question-scope-snapshot/v2"
    assert default_snapshot.source_item_ids == (new_item_id,)
    assert default_snapshot.superseded_source_item_ids == (old_item_id,)
    assert old_snapshot.source_item_ids == (old_item_id,)
