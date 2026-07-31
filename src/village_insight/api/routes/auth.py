from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from village_insight.api.dependencies import CurrentPrincipal, Database
from village_insight.config import get_settings
from village_insight.db.models import (
    AdministrativeUnit,
    AdministrativeUnitType,
    AuthSession,
    MembershipRole,
    Tenant,
    TenantKind,
    TenantMembership,
    User,
    UserStatus,
    utcnow,
)
from village_insight.identity import (
    PASSWORD_HASH,
    create_session,
    hash_session_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=256)
    membership_id: uuid.UUID | None = None


class CurrentUserRead(BaseModel):
    user_id: uuid.UUID
    username: str
    display_name: str
    tenant_id: uuid.UUID
    tenant_name: str
    membership_id: uuid.UUID
    role: str
    scope_unit_id: uuid.UUID | None
    scope_unit_name: str | None
    scope_unit_type: str | None
    include_descendants: bool
    permissions: list[str]
    upload_units: list[UploadUnitRead]


class UploadUnitRead(BaseModel):
    id: uuid.UUID
    name: str
    tenant_id: uuid.UUID
    tenant_name: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


def _principal_read(
    principal: CurrentPrincipal,
    database: Database,
) -> CurrentUserRead:
    upload_units: Sequence[tuple[AdministrativeUnit, Tenant]] = []
    if principal.membership.role == MembershipRole.VILLAGE_OPERATOR:
        if principal.scope_unit is not None:
            upload_units = [(principal.scope_unit, principal.tenant)]
    elif principal.membership.role == MembershipRole.TENANT_ADMIN:
        upload_units = database.execute(
                select(AdministrativeUnit, Tenant)
                .join(Tenant, Tenant.id == AdministrativeUnit.tenant_id)
                .where(
                    Tenant.id == principal.tenant.id,
                    Tenant.kind == TenantKind.BUSINESS,
                    Tenant.status == UserStatus.ACTIVE,
                    AdministrativeUnit.unit_type == AdministrativeUnitType.VILLAGE,
                    AdministrativeUnit.status == UserStatus.ACTIVE,
                )
                .order_by(Tenant.name, AdministrativeUnit.name)
            ).tuples().all()
    return CurrentUserRead(
        user_id=principal.user.id,
        username=principal.user.username,
        display_name=principal.user.display_name,
        tenant_id=principal.tenant.id,
        tenant_name=principal.tenant.name,
        membership_id=principal.membership.id,
        role=principal.membership.role,
        scope_unit_id=principal.scope_unit.id if principal.scope_unit else None,
        scope_unit_name=principal.scope_unit.name if principal.scope_unit else None,
        scope_unit_type=principal.scope_unit.unit_type if principal.scope_unit else None,
        include_descendants=principal.include_descendants,
        permissions=sorted(principal.permissions),
        upload_units=[
            UploadUnitRead(
                id=unit.id,
                name=unit.name,
                tenant_id=tenant.id,
                tenant_name=tenant.name,
            )
            for unit, tenant in upload_units
        ],
    )


@router.post("/login", response_model=CurrentUserRead)
def login(payload: LoginRequest, response: Response, database: Database) -> CurrentUserRead:
    user = database.scalar(
        select(User).where(
            User.username == payload.username,
            User.status == UserStatus.ACTIVE,
        )
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )
    memberships = list(
        database.scalars(
            select(TenantMembership).where(
                TenantMembership.user_id == user.id,
                TenantMembership.status == UserStatus.ACTIVE,
            )
        )
    )
    if payload.membership_id is not None:
        memberships = [
            membership
            for membership in memberships
            if membership.id == payload.membership_id
        ]
    if len(memberships) != 1:
        detail = "账号没有有效业务身份" if not memberships else "请选择一个业务身份"
        raise HTTPException(status_code=409, detail=detail)
    settings = get_settings()
    _, raw_token = create_session(
        database,
        user=user,
        membership=memberships[0],
        settings=settings,
    )
    database.commit()
    from village_insight.identity import resolve_principal

    principal = resolve_principal(database, raw_token)
    if principal is None:
        raise HTTPException(status_code=409, detail="业务身份配置无效")
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_lifetime_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return _principal_read(principal, database)


@router.get("/me", response_model=CurrentUserRead)
def read_me(principal: CurrentPrincipal, database: Database) -> CurrentUserRead:
    return _principal_read(principal, database)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    principal: CurrentPrincipal,
    database: Database,
) -> None:
    if not verify_password(payload.current_password, principal.user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=422, detail="新密码不能与当前密码相同")
    principal.user.password_hash = PASSWORD_HASH.hash(payload.new_password)
    raw_token = request.cookies.get(get_settings().session_cookie_name)
    current_token_hash = hash_session_token(raw_token) if raw_token else None
    for auth_session in database.scalars(
        select(AuthSession).where(
            AuthSession.user_id == principal.user.id,
            AuthSession.revoked_at.is_(None),
        )
    ):
        if auth_session.token_hash != current_token_hash:
            auth_session.revoked_at = utcnow()
    database.commit()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    principal: CurrentPrincipal,
    database: Database,
) -> None:
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        auth_session = database.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == hash_session_token(raw_token),
                AuthSession.user_id == principal.user.id,
            )
        )
        if auth_session:
            auth_session.revoked_at = utcnow()
            database.commit()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
