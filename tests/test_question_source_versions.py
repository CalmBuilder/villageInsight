from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    AdministrativeUnit,
    DatasetRecord,
    IngestionItem,
    Tenant,
)
from village_insight.question_source_versions import (
    SourceSupersessionError,
    declare_source_supersession,
)


def _item(
    *,
    tenant_id: uuid.UUID,
    unit_id: uuid.UUID,
    user_id: uuid.UUID,
    name: str,
    created_at: datetime,
) -> IngestionItem:
    return IngestionItem(
        tenant_id=tenant_id,
        administrative_unit_id=unit_id,
        created_by_user_id=user_id,
        batch_id=uuid.uuid4(),
        original_name=name,
        source_path=f"/evidence/{name}",
        source_sha256=uuid.uuid4().hex * 2,
        size_bytes=100,
        status="imported",
        created_at=created_at,
    )


def _approved_record(item: IngestionItem) -> DatasetRecord:
    return DatasetRecord(
        tenant_id=item.tenant_id,
        administrative_unit_id=item.administrative_unit_id,
        ingestion_batch_id=item.batch_id,
        approved_plan_id=uuid.uuid4(),
        item_id=item.id,
        template_id=uuid.uuid4(),
        template_version=1,
        record_type="population",
        sheet_id="sheet-1",
        region_id="region-1",
        source_row=2,
        quality_status="passed",
    )


def test_declare_source_supersession_requires_governed_approved_versions() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tenant_id = uuid.uuid4()
    unit_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
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
        old = _item(
            tenant_id=tenant_id,
            unit_id=unit_id,
            user_id=user_id,
            name="人口旧版.xlsx",
            created_at=now - timedelta(days=1),
        )
        new = _item(
            tenant_id=tenant_id,
            unit_id=unit_id,
            user_id=user_id,
            name="人口新版.xlsx",
            created_at=now,
        )
        database.add_all([old, new])
        database.flush()
        database.add_all([_approved_record(old), _approved_record(new)])
        database.flush()

        declaration = declare_source_supersession(
            database,
            tenant_id=tenant_id,
            allowed_administrative_unit_ids=frozenset({unit_id}),
            superseded_item_id=old.id,
            replacement_item_id=new.id,
            declared_by_user_id=user_id,
            reason="新版完整替代旧版",
        )

        assert declaration.superseded_item_id == old.id
        assert declaration.replacement_item_id == new.id
        with pytest.raises(SourceSupersessionError, match="already has"):
            declare_source_supersession(
                database,
                tenant_id=tenant_id,
                allowed_administrative_unit_ids=frozenset({unit_id}),
                superseded_item_id=old.id,
                replacement_item_id=new.id,
                declared_by_user_id=user_id,
                reason="重复声明",
            )
