from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.engine import Row
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select

from village_insight.api.dependencies import CurrentPrincipal, Database
from village_insight.db.models import (
    AdministrativeUnit,
    DatasetRecord,
    DocumentSheetCatalog,
    IngestionItem,
    MembershipRole,
)
from village_insight.db.schema import (
    DatasetRecordFilePage,
    DatasetRecordFileRead,
    DatasetRecordGroupPage,
    DatasetRecordGroupRead,
    DatasetRecordPage,
    DatasetRecordRead,
)

router = APIRouter(prefix="/records", tags=["records"])


def _scope_filters(principal: CurrentPrincipal) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = []
    if principal.membership.role == MembershipRole.PLATFORM_ADMIN:
        return filters
    if principal.membership.role == MembershipRole.TENANT_ADMIN:
        filters.append(DatasetRecord.tenant_id == principal.tenant.id)
    elif principal.has("records.read.village"):
        filters.append(DatasetRecord.tenant_id == principal.tenant.id)
        filters.append(
            DatasetRecord.administrative_unit_id.in_(principal.allowed_unit_ids)
        )
    else:
        filters.append(DatasetRecord.tenant_id == principal.tenant.id)
    return filters


def _sheet_names(
    database: Database,
    item_ids: set[uuid.UUID],
) -> dict[tuple[uuid.UUID, str], str]:
    if not item_ids:
        return {}
    return {
        (item_id, sheet_id): sheet_name
        for item_id, sheet_id, sheet_name in database.execute(
            select(
                DocumentSheetCatalog.item_id,
                DocumentSheetCatalog.sheet_id,
                DocumentSheetCatalog.sheet_name,
            ).where(DocumentSheetCatalog.item_id.in_(item_ids))
        )
    }


def _group_read(
    row: Row[tuple[object, ...]],
    sheet_names: dict[tuple[uuid.UUID, str], str],
) -> DatasetRecordGroupRead:
    (
        item_id,
        file_name,
        unit_name,
        sheet_id,
        region_id,
        record_type,
        record_count,
        passed_count,
        failed_count,
        pending_rebuild_count,
        min_source_row,
        max_source_row,
        latest_created_at,
    ) = row
    assert isinstance(item_id, uuid.UUID)
    assert isinstance(sheet_id, str)
    return DatasetRecordGroupRead(
        item_id=item_id,
        source_file_name=str(file_name),
        administrative_unit_name=str(unit_name),
        sheet_id=sheet_id,
        sheet_name=sheet_names.get((item_id, sheet_id), sheet_id),
        region_id=str(region_id),
        record_type=str(record_type),
        record_count=cast(int, record_count),
        passed_count=cast(int, passed_count),
        failed_count=cast(int, failed_count),
        pending_rebuild_count=cast(int, pending_rebuild_count),
        min_source_row=cast(int, min_source_row),
        max_source_row=cast(int, max_source_row),
        latest_created_at=cast(datetime, latest_created_at),
    )


def _group_query(
    filters: list[ColumnElement[bool]],
) -> Select[tuple[object, ...]]:
    return (
        select(
            DatasetRecord.item_id,
            IngestionItem.original_name,
            AdministrativeUnit.name,
            DatasetRecord.sheet_id,
            DatasetRecord.region_id,
            DatasetRecord.record_type,
            func.count(DatasetRecord.id),
            func.count(DatasetRecord.id).filter(
                DatasetRecord.quality_status == "passed"
            ),
            func.count(DatasetRecord.id).filter(
                DatasetRecord.quality_status == "failed"
            ),
            func.count(DatasetRecord.id).filter(
                DatasetRecord.mapping_status == "pending_rebuild"
            ),
            func.min(DatasetRecord.source_row),
            func.max(DatasetRecord.source_row),
            func.max(DatasetRecord.created_at),
        )
        .join(IngestionItem, IngestionItem.id == DatasetRecord.item_id)
        .join(
            AdministrativeUnit,
            AdministrativeUnit.id == DatasetRecord.administrative_unit_id,
        )
        .where(*filters)
        .group_by(
            DatasetRecord.item_id,
            DatasetRecord.administrative_unit_id,
            DatasetRecord.sheet_id,
            DatasetRecord.region_id,
            DatasetRecord.record_type,
            IngestionItem.original_name,
            AdministrativeUnit.name,
        )
    )


@router.get("/tree", response_model=DatasetRecordFilePage)
def list_record_tree(
    database: Database,
    principal: CurrentPrincipal,
    *,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
    quality_status: str | None = None,
) -> DatasetRecordFilePage:
    """Page file roots and include every Sheet/Region child for those files."""
    if not (
        principal.has("records.read.village") or principal.has("governance.review")
    ):
        raise HTTPException(status_code=403, detail="没有正式记录访问权限")
    filters = _scope_filters(principal)
    if quality_status:
        filters.append(DatasetRecord.quality_status == quality_status)

    total = database.scalar(
        select(func.count(func.distinct(DatasetRecord.item_id))).where(*filters)
    ) or 0
    file_rows = list(
        database.execute(
            select(
                DatasetRecord.item_id,
                IngestionItem.original_name,
                AdministrativeUnit.name,
                func.count(DatasetRecord.id),
                func.count(DatasetRecord.id).filter(
                    DatasetRecord.quality_status == "passed"
                ),
                func.count(DatasetRecord.id).filter(
                    DatasetRecord.quality_status == "failed"
                ),
                func.count(DatasetRecord.id).filter(
                    DatasetRecord.mapping_status == "pending_rebuild"
                ),
                func.max(DatasetRecord.created_at),
            )
            .join(IngestionItem, IngestionItem.id == DatasetRecord.item_id)
            .join(
                AdministrativeUnit,
                AdministrativeUnit.id == DatasetRecord.administrative_unit_id,
            )
            .where(*filters)
            .group_by(
                DatasetRecord.item_id,
                IngestionItem.original_name,
                AdministrativeUnit.name,
            )
            .order_by(func.max(DatasetRecord.created_at).desc(), DatasetRecord.item_id)
            .offset(offset)
            .limit(limit)
        )
    )
    item_ids = {row[0] for row in file_rows}
    child_rows = list(
        database.execute(
            _group_query([
                *filters,
                DatasetRecord.item_id.in_(item_ids),
            ]).order_by(
                DatasetRecord.item_id,
                DatasetRecord.sheet_id,
                DatasetRecord.region_id,
                DatasetRecord.record_type,
            )
        )
    ) if item_ids else []
    sheet_names = _sheet_names(database, item_ids)
    children_by_item: dict[uuid.UUID, list[DatasetRecordGroupRead]] = {
        item_id: [] for item_id in item_ids
    }
    for child_row in child_rows:
        child = _group_read(child_row, sheet_names)
        children_by_item[child.item_id].append(child)

    return DatasetRecordFilePage(
        items=[
            DatasetRecordFileRead(
                item_id=item_id,
                source_file_name=file_name,
                administrative_unit_name=unit_name,
                record_count=record_count,
                passed_count=passed_count,
                failed_count=failed_count,
                pending_rebuild_count=pending_rebuild_count,
                dataset_count=len(children_by_item[item_id]),
                latest_created_at=latest_created_at,
                children=children_by_item[item_id],
            )
            for (
                item_id,
                file_name,
                unit_name,
                record_count,
                passed_count,
                failed_count,
                pending_rebuild_count,
                latest_created_at,
            ) in file_rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/groups", response_model=DatasetRecordGroupPage)
def list_record_groups(
    database: Database,
    principal: CurrentPrincipal,
    *,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    quality_status: str | None = None,
) -> DatasetRecordGroupPage:
    if not (
        principal.has("records.read.village") or principal.has("governance.review")
    ):
        raise HTTPException(status_code=403, detail="没有正式记录访问权限")
    filters = _scope_filters(principal)
    if quality_status:
        filters.append(DatasetRecord.quality_status == quality_status)
    group_columns = (
        DatasetRecord.item_id,
        DatasetRecord.administrative_unit_id,
        DatasetRecord.sheet_id,
        DatasetRecord.region_id,
        DatasetRecord.record_type,
    )
    grouped = (
        select(*group_columns)
        .where(*filters)
        .group_by(*group_columns)
        .subquery()
    )
    total = database.scalar(select(func.count()).select_from(grouped)) or 0
    rows = list(database.execute(
        _group_query(filters)
        .order_by(func.max(DatasetRecord.created_at).desc(), DatasetRecord.item_id)
        .offset(offset)
        .limit(limit)
    ))
    item_ids = {row[0] for row in rows}
    sheet_names = _sheet_names(database, item_ids)
    return DatasetRecordGroupPage(
        items=[_group_read(row, sheet_names) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("", response_model=DatasetRecordPage)
def list_records(
    database: Database,
    principal: CurrentPrincipal,
    *,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    mapping_status: str | None = None,
    quality_status: str | None = None,
    item_id: uuid.UUID | None = None,
    sheet_id: str | None = None,
    region_id: str | None = None,
    record_type: str | None = None,
) -> DatasetRecordPage:
    if not (
        principal.has("records.read.village") or principal.has("governance.review")
    ):
        raise HTTPException(status_code=403, detail="没有正式记录访问权限")
    filters = _scope_filters(principal)
    if mapping_status:
        filters.append(DatasetRecord.mapping_status == mapping_status)
    if quality_status:
        filters.append(DatasetRecord.quality_status == quality_status)
    if item_id:
        filters.append(DatasetRecord.item_id == item_id)
    if sheet_id:
        filters.append(DatasetRecord.sheet_id == sheet_id)
    if region_id:
        filters.append(DatasetRecord.region_id == region_id)
    if record_type:
        filters.append(DatasetRecord.record_type == record_type)
    total = database.scalar(select(func.count()).select_from(DatasetRecord).where(*filters))
    rows = list(
        database.execute(
            select(
                DatasetRecord,
                IngestionItem.original_name,
                AdministrativeUnit.name,
            )
            .join(IngestionItem, IngestionItem.id == DatasetRecord.item_id)
            .join(
                AdministrativeUnit,
                AdministrativeUnit.id == DatasetRecord.administrative_unit_id,
            )
            .where(*filters)
            .order_by(DatasetRecord.created_at.desc(), DatasetRecord.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return DatasetRecordPage(
        items=[
            DatasetRecordRead.model_validate(record).model_copy(
                update={
                    "source_file_name": file_name,
                    "administrative_unit_name": unit_name,
                }
            )
            for record, file_name, unit_name in rows
        ],
        total=total or 0,
        limit=limit,
        offset=offset,
    )
