from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from village_insight.api.dependencies import CurrentPrincipal, Database
from village_insight.db.models import (
    AdministrativeUnit,
    AdministrativeUnitType,
    AuthSession,
    MembershipRole,
    MembershipScope,
    Tenant,
    TenantKind,
    TenantMembership,
    User,
    UserStatus,
    utcnow,
)
from village_insight.identity import PASSWORD_HASH

router = APIRouter(prefix="/admin", tags=["admin"])


def require_platform_admin(principal: CurrentPrincipal) -> None:
    if principal.membership.role != MembershipRole.PLATFORM_ADMIN:
        raise HTTPException(status_code=403, detail="只有总管理员可以执行此操作")


class UnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    parent_id: uuid.UUID | None
    unit_type: str
    name: str
    status: str


class TenantRead(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    status: str
    created_at: datetime
    units: list[UnitRead]


class TenantPage(BaseModel):
    items: list[TenantRead]
    total: int
    limit: int
    offset: int


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    township_name: str = Field(min_length=1, max_length=200)


class TenantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None


class UnitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    unit_type: str
    parent_id: uuid.UUID | None = None


class UnitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None


class AdminUserRead(BaseModel):
    user_id: uuid.UUID
    username: str
    display_name: str
    user_status: str
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_kind: str
    membership_id: uuid.UUID
    membership_status: str
    role: str
    scope_unit_id: uuid.UUID | None
    scope_unit_name: str | None


class AdminUserPage(BaseModel):
    items: list[AdminUserRead]
    total: int
    limit: int
    offset: int


class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=4, max_length=256)
    tenant_id: uuid.UUID
    role: str
    scope_unit_id: uuid.UUID | None = None


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=1, max_length=160)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None
    tenant_id: uuid.UUID | None = None
    role: str | None = None
    scope_unit_id: uuid.UUID | None = None
    password: str | None = Field(default=None, min_length=4, max_length=256)


def _tenant_read(database: Database, tenant: Tenant) -> TenantRead:
    units = list(
        database.scalars(
            select(AdministrativeUnit)
            .where(AdministrativeUnit.tenant_id == tenant.id)
            .order_by(AdministrativeUnit.unit_type, AdministrativeUnit.name)
        )
    )
    return TenantRead(
        id=tenant.id,
        name=tenant.name,
        kind=tenant.kind,
        status=tenant.status,
        created_at=tenant.created_at,
        units=[UnitRead.model_validate(unit) for unit in units],
    )


def _validate_status(value: str) -> str:
    if value not in {UserStatus.ACTIVE, UserStatus.DISABLED}:
        raise HTTPException(status_code=422, detail="状态只能是 active 或 disabled")
    return value


def _configure_membership_scope(
    database: Database,
    *,
    membership: TenantMembership,
    tenant: Tenant,
    role: str,
    scope_unit_id: uuid.UUID | None,
) -> None:
    allowed_roles = {
        MembershipRole.TENANT_ADMIN,
        MembershipRole.VILLAGE_OPERATOR,
        MembershipRole.PLATFORM_ADMIN,
    }
    if role not in allowed_roles:
        raise HTTPException(status_code=422, detail="不支持的用户角色")
    is_platform_role = role in {
        MembershipRole.PLATFORM_ADMIN,
    }
    if is_platform_role:
        if tenant.kind != TenantKind.PLATFORM or scope_unit_id is not None:
            raise HTTPException(status_code=422, detail="平台角色只能属于管理员租户且不能绑定村镇")
    else:
        if tenant.kind != TenantKind.BUSINESS or scope_unit_id is None:
            raise HTTPException(status_code=422, detail="业务角色必须绑定业务租户行政区划")
        unit = database.get(AdministrativeUnit, scope_unit_id)
        if unit is None or unit.tenant_id != tenant.id or unit.status != UserStatus.ACTIVE:
            raise HTTPException(status_code=422, detail="行政区划不属于目标租户")
        if role == MembershipRole.TENANT_ADMIN and (
            unit.unit_type != AdministrativeUnitType.TOWNSHIP
            or unit.parent_id is not None
        ):
            raise HTTPException(status_code=422, detail="乡镇账号必须绑定租户根乡镇")
        if (
            role == MembershipRole.VILLAGE_OPERATOR
            and unit.unit_type != AdministrativeUnitType.VILLAGE
        ):
            raise HTTPException(status_code=422, detail="村级账号必须绑定一个村")
    membership.role = role
    database.execute(
        delete(MembershipScope).where(
            MembershipScope.membership_id == membership.id
        )
    )
    if not is_platform_role:
        assert scope_unit_id is not None
        database.add(
            MembershipScope(
                membership_id=membership.id,
                administrative_unit_id=scope_unit_id,
                include_descendants=role == MembershipRole.TENANT_ADMIN,
            )
        )


def _revoke_user_sessions(database: Database, user_id: uuid.UUID) -> None:
    for session in database.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    ):
        session.revoked_at = utcnow()


@router.get("/tenants", response_model=TenantPage)
def list_tenants(
    database: Database,
    principal: CurrentPrincipal,
    search: str = Query(default="", max_length=200),
    status_filter: str = Query(default="all", alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TenantPage:
    require_platform_admin(principal)
    filters: list[ColumnElement[bool]] = []
    if search.strip():
        filters.append(Tenant.name.ilike(f"%{search.strip()}%"))
    if status_filter != "all":
        filters.append(Tenant.status == _validate_status(status_filter))
    total = database.scalar(select(func.count()).select_from(Tenant).where(*filters)) or 0
    tenants = list(
        database.scalars(
            select(Tenant)
            .where(*filters)
            .order_by(Tenant.kind, Tenant.name, Tenant.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return TenantPage(
        items=[_tenant_read(database, tenant) for tenant in tenants],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/tenants", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate,
    database: Database,
    principal: CurrentPrincipal,
) -> TenantRead:
    require_platform_admin(principal)
    if database.scalar(select(Tenant).where(Tenant.name == payload.name)):
        raise HTTPException(status_code=409, detail="租户名称已存在")
    tenant = Tenant(name=payload.name, kind=TenantKind.BUSINESS)
    database.add(tenant)
    database.flush()
    database.add(
        AdministrativeUnit(
            tenant_id=tenant.id,
            unit_type=AdministrativeUnitType.TOWNSHIP,
            name=payload.township_name,
        )
    )
    database.commit()
    database.refresh(tenant)
    return _tenant_read(database, tenant)


@router.patch("/tenants/{tenant_id}", response_model=TenantRead)
def update_tenant(
    tenant_id: uuid.UUID,
    payload: TenantUpdate,
    database: Database,
    principal: CurrentPrincipal,
) -> TenantRead:
    require_platform_admin(principal)
    tenant = database.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="租户不存在")
    if payload.name is not None and payload.name != tenant.name:
        if database.scalar(select(Tenant).where(Tenant.name == payload.name)):
            raise HTTPException(status_code=409, detail="租户名称已存在")
        tenant.name = payload.name
    if payload.status is not None:
        if tenant.kind == TenantKind.PLATFORM and payload.status == UserStatus.DISABLED:
            raise HTTPException(status_code=409, detail="管理员租户不能停用")
        tenant.status = _validate_status(payload.status)
    database.commit()
    database.refresh(tenant)
    return _tenant_read(database, tenant)


@router.delete("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant(
    tenant_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> None:
    require_platform_admin(principal)
    tenant = database.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="租户不存在")
    if tenant.kind == TenantKind.PLATFORM:
        raise HTTPException(status_code=409, detail="管理员租户不能删除")
    tenant.status = UserStatus.DISABLED
    memberships = list(
        database.scalars(
            select(TenantMembership).where(TenantMembership.tenant_id == tenant.id)
        )
    )
    for membership in memberships:
        membership.status = UserStatus.DISABLED
        _revoke_user_sessions(database, membership.user_id)
    database.commit()


@router.post(
    "/tenants/{tenant_id}/units",
    response_model=UnitRead,
    status_code=status.HTTP_201_CREATED,
)
def create_unit(
    tenant_id: uuid.UUID,
    payload: UnitCreate,
    database: Database,
    principal: CurrentPrincipal,
) -> AdministrativeUnit:
    require_platform_admin(principal)
    tenant = database.get(Tenant, tenant_id)
    if tenant is None or tenant.kind != TenantKind.BUSINESS:
        raise HTTPException(status_code=404, detail="业务租户不存在")
    if payload.unit_type not in {
        AdministrativeUnitType.TOWNSHIP,
        AdministrativeUnitType.VILLAGE,
    }:
        raise HTTPException(status_code=422, detail="行政区划类型无效")
    if payload.unit_type == AdministrativeUnitType.TOWNSHIP:
        if payload.parent_id is not None:
            raise HTTPException(status_code=422, detail="根乡镇不能设置上级")
        if database.scalar(
            select(AdministrativeUnit).where(
                AdministrativeUnit.tenant_id == tenant.id,
                AdministrativeUnit.unit_type == AdministrativeUnitType.TOWNSHIP,
                AdministrativeUnit.parent_id.is_(None),
            )
        ):
            raise HTTPException(status_code=409, detail="租户已经存在根乡镇")
    else:
        parent = database.get(AdministrativeUnit, payload.parent_id)
        if (
            parent is None
            or parent.tenant_id != tenant.id
            or parent.unit_type != AdministrativeUnitType.TOWNSHIP
        ):
            raise HTTPException(status_code=422, detail="村必须属于当前租户根乡镇")
    unit = AdministrativeUnit(
        tenant_id=tenant.id,
        parent_id=payload.parent_id,
        unit_type=payload.unit_type,
        name=payload.name,
    )
    database.add(unit)
    database.commit()
    database.refresh(unit)
    return unit


@router.patch("/units/{unit_id}", response_model=UnitRead)
def update_unit(
    unit_id: uuid.UUID,
    payload: UnitUpdate,
    database: Database,
    principal: CurrentPrincipal,
) -> AdministrativeUnit:
    require_platform_admin(principal)
    unit = database.get(AdministrativeUnit, unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="行政区划不存在")
    if payload.name is not None:
        unit.name = payload.name
    if payload.status is not None:
        unit.status = _validate_status(payload.status)
    database.commit()
    database.refresh(unit)
    return unit


@router.delete("/units/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_unit(
    unit_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> None:
    require_platform_admin(principal)
    unit = database.get(AdministrativeUnit, unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="行政区划不存在")
    unit.status = UserStatus.DISABLED
    database.commit()


def _user_read(database: Database, user: User, membership: TenantMembership) -> AdminUserRead:
    tenant = database.get(Tenant, membership.tenant_id)
    scope = database.scalar(
        select(MembershipScope).where(MembershipScope.membership_id == membership.id)
    )
    unit = (
        database.get(AdministrativeUnit, scope.administrative_unit_id)
        if scope is not None
        else None
    )
    assert tenant is not None
    return AdminUserRead(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        user_status=user.status,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        tenant_kind=tenant.kind,
        membership_id=membership.id,
        membership_status=membership.status,
        role=membership.role,
        scope_unit_id=unit.id if unit else None,
        scope_unit_name=unit.name if unit else None,
    )


@router.get("/users", response_model=AdminUserPage)
def list_users(
    database: Database,
    principal: CurrentPrincipal,
    search: str = Query(default="", max_length=200),
    status_filter: str = Query(default="all", alias="status"),
    role: str = Query(default="all"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> AdminUserPage:
    require_platform_admin(principal)
    filters: list[ColumnElement[bool]] = []
    if search.strip():
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                User.display_name.ilike(pattern),
                User.username.ilike(pattern),
                Tenant.name.ilike(pattern),
                AdministrativeUnit.name.ilike(pattern),
            )
        )
    if status_filter != "all":
        filters.append(User.status == _validate_status(status_filter))
    if role != "all":
        if role not in {
            MembershipRole.PLATFORM_ADMIN,
            MembershipRole.TENANT_ADMIN,
            MembershipRole.VILLAGE_OPERATOR,
        }:
            raise HTTPException(status_code=422, detail="用户角色筛选无效")
        filters.append(TenantMembership.role == role)
    base = (
        select(User, TenantMembership)
        .join(TenantMembership, TenantMembership.user_id == User.id)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .outerjoin(MembershipScope, MembershipScope.membership_id == TenantMembership.id)
        .outerjoin(
            AdministrativeUnit,
            AdministrativeUnit.id == MembershipScope.administrative_unit_id,
        )
        .where(*filters)
    )
    total = database.scalar(
        select(func.count()).select_from(base.order_by(None).subquery())
    ) or 0
    rows = database.execute(
        base.with_only_columns(
            User,
            TenantMembership,
            Tenant,
            AdministrativeUnit,
        )
        .order_by(User.username, User.id)
        .offset(offset)
        .limit(limit)
    )
    return AdminUserPage(
        items=[
            AdminUserRead(
                user_id=user.id,
                username=user.username,
                display_name=user.display_name,
                user_status=user.status,
                tenant_id=tenant.id,
                tenant_name=tenant.name,
                tenant_kind=tenant.kind,
                membership_id=membership.id,
                membership_status=membership.status,
                role=membership.role,
                scope_unit_id=unit.id if unit else None,
                scope_unit_name=unit.name if unit else None,
            )
            for user, membership, tenant, unit in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/users", response_model=AdminUserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: AdminUserCreate,
    database: Database,
    principal: CurrentPrincipal,
) -> AdminUserRead:
    require_platform_admin(principal)
    if database.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail="用户名已存在")
    tenant = database.get(Tenant, payload.tenant_id)
    if tenant is None or tenant.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=422, detail="目标租户不存在或已停用")
    user = User(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=PASSWORD_HASH.hash(payload.password),
    )
    database.add(user)
    database.flush()
    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        role=payload.role,
    )
    database.add(membership)
    database.flush()
    _configure_membership_scope(
        database,
        membership=membership,
        tenant=tenant,
        role=payload.role,
        scope_unit_id=payload.scope_unit_id,
    )
    database.commit()
    return _user_read(database, user, membership)


@router.patch("/users/{user_id}", response_model=AdminUserRead)
def update_user(
    user_id: uuid.UUID,
    payload: AdminUserUpdate,
    database: Database,
    principal: CurrentPrincipal,
) -> AdminUserRead:
    require_platform_admin(principal)
    user = database.get(User, user_id)
    membership = database.scalar(
        select(TenantMembership).where(TenantMembership.user_id == user_id)
    )
    if user is None or membership is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == principal.user.id and (
        (
            payload.role is not None
            and payload.role != MembershipRole.PLATFORM_ADMIN
        )
        or (
            payload.tenant_id is not None
            and payload.tenant_id != principal.tenant.id
        )
    ):
        raise HTTPException(status_code=409, detail="不能移除当前登录管理员的平台身份")
    if payload.username is not None and payload.username != user.username:
        if database.scalar(select(User).where(User.username == payload.username)):
            raise HTTPException(status_code=409, detail="用户名已存在")
        user.username = payload.username
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.status is not None:
        if user.id == principal.user.id and payload.status == UserStatus.DISABLED:
            raise HTTPException(status_code=409, detail="不能停用当前登录管理员")
        user.status = _validate_status(payload.status)
        membership.status = user.status
    if payload.password is not None:
        user.password_hash = PASSWORD_HASH.hash(payload.password)
    if (
        payload.tenant_id is not None
        or payload.role is not None
        or "scope_unit_id" in payload.model_fields_set
    ):
        current_scope = database.scalar(
            select(MembershipScope).where(
                MembershipScope.membership_id == membership.id
            )
        )
        target_tenant = database.get(
            Tenant,
            payload.tenant_id or membership.tenant_id,
        )
        if target_tenant is None:
            raise HTTPException(status_code=422, detail="目标租户不存在")
        membership.tenant_id = target_tenant.id
        _configure_membership_scope(
            database,
            membership=membership,
            tenant=target_tenant,
            role=payload.role or membership.role,
            scope_unit_id=(
                payload.scope_unit_id
                if "scope_unit_id" in payload.model_fields_set
                else (
                    current_scope.administrative_unit_id
                    if current_scope is not None
                    else None
                )
            ),
        )
    _revoke_user_sessions(database, user.id)
    database.commit()
    return _user_read(database, user, membership)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: uuid.UUID,
    database: Database,
    principal: CurrentPrincipal,
) -> None:
    require_platform_admin(principal)
    if user_id == principal.user.id:
        raise HTTPException(status_code=409, detail="不能删除当前登录管理员")
    user = database.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.status = UserStatus.DISABLED
    for membership in database.scalars(
        select(TenantMembership).where(TenantMembership.user_id == user.id)
    ):
        membership.status = UserStatus.DISABLED
    _revoke_user_sessions(database, user.id)
    database.commit()
