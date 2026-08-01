from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from village_insight.config import Settings
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

PASSWORD_HASH = PasswordHash((Argon2Hasher(),))

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    MembershipRole.TENANT_ADMIN: frozenset(
        {
            "imports.create.any_village",
            "imports.read.tenant",
            "records.read.tenant",
            "questions.ask.tenant",
        }
    ),
    MembershipRole.VILLAGE_OPERATOR: frozenset(
        {
            "imports.create",
            "imports.read.village",
            "records.read.village",
            "questions.ask.village",
        }
    ),
    MembershipRole.PLATFORM_ADMIN: frozenset(
        {
            "platform.tenants.manage",
            "platform.users.manage",
            "governance.review",
            "governance.catalog",
            "governance.metrics",
            "governance.settings",
        }
    ),
}


@dataclass(frozen=True)
class Principal:
    user: User
    tenant: Tenant
    membership: TenantMembership
    scope_unit: AdministrativeUnit | None
    include_descendants: bool
    permissions: frozenset[str]
    allowed_unit_ids: frozenset[uuid.UUID]

    def has(self, permission: str) -> bool:
        return permission in self.permissions


@dataclass(frozen=True)
class BootstrapIdentityResult:
    usernames: tuple[str, str]
    created_users: int


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return PASSWORD_HASH.verify(password, password_hash)


def create_session(
    database: Session,
    *,
    user: User,
    membership: TenantMembership,
    settings: Settings,
) -> tuple[AuthSession, str]:
    raw_token = secrets.token_urlsafe(48)
    now = utcnow()
    auth_session = AuthSession(
        user_id=user.id,
        membership_id=membership.id,
        token_hash=hash_session_token(raw_token),
        expires_at=now + timedelta(hours=settings.session_lifetime_hours),
        created_at=now,
        last_seen_at=now,
    )
    database.add(auth_session)
    database.flush()
    return auth_session, raw_token


def resolve_principal(database: Session, raw_token: str) -> Principal | None:
    now = utcnow()
    auth_session = database.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_session_token(raw_token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    if auth_session is None:
        return None
    user = database.get(User, auth_session.user_id)
    membership = database.get(TenantMembership, auth_session.membership_id)
    if (
        user is None
        or membership is None
        or user.status != UserStatus.ACTIVE
        or membership.status != UserStatus.ACTIVE
        or membership.user_id != user.id
    ):
        return None
    tenant = database.get(Tenant, membership.tenant_id)
    if tenant is None or tenant.status != UserStatus.ACTIVE:
        return None

    scopes = list(
        database.scalars(
            select(MembershipScope).where(
                MembershipScope.membership_id == membership.id
            )
        )
    )
    if membership.role in {
        MembershipRole.PLATFORM_ADMIN,
    }:
        if scopes:
            return None
        if tenant.kind != TenantKind.PLATFORM:
            return None
        return Principal(
            user=user,
            tenant=tenant,
            membership=membership,
            scope_unit=None,
            include_descendants=False,
            permissions=ROLE_PERMISSIONS[membership.role],
            allowed_unit_ids=frozenset(),
        )
    if len(scopes) != 1:
        return None
    scope = scopes[0]
    unit = database.get(AdministrativeUnit, scope.administrative_unit_id)
    if (
        unit is None
        or unit.tenant_id != tenant.id
        or unit.status != UserStatus.ACTIVE
    ):
        return None
    if (
        membership.role in {
            MembershipRole.TENANT_ADMIN,
        }
        and (
            unit.unit_type != AdministrativeUnitType.TOWNSHIP
            or not scope.include_descendants
            or unit.parent_id is not None
        )
    ):
        return None
    if (
        membership.role == MembershipRole.VILLAGE_OPERATOR
        and (
            unit.unit_type != AdministrativeUnitType.VILLAGE
            or scope.include_descendants
        )
    ):
        return None

    allowed_ids = {unit.id}
    if scope.include_descendants:
        allowed_ids.update(
            database.scalars(
                select(AdministrativeUnit.id).where(
                    AdministrativeUnit.tenant_id == tenant.id,
                    AdministrativeUnit.parent_id == unit.id,
                    AdministrativeUnit.status == UserStatus.ACTIVE,
                )
            )
        )
    return Principal(
        user=user,
        tenant=tenant,
        membership=membership,
        scope_unit=unit,
        include_descendants=scope.include_descendants,
        permissions=ROLE_PERMISSIONS[membership.role],
        allowed_unit_ids=frozenset(allowed_ids),
    )


def _bootstrap_lock(database: Session) -> None:
    if database.bind is not None and database.bind.dialect.name == "postgresql":
        database.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": 0x56494C4C414745},
        )


def _bootstrap_tenant(
    database: Session,
    *,
    name: str,
    kind: str,
) -> Tenant:
    tenant = database.scalar(select(Tenant).where(Tenant.name == name))
    if tenant is None:
        tenant = Tenant(name=name, kind=kind, status=UserStatus.ACTIVE)
        database.add(tenant)
        database.flush()
        return tenant
    if tenant.kind != kind or tenant.status != UserStatus.ACTIVE:
        raise RuntimeError(f"初始化租户 {name} 已存在，但类型或状态不符合预期")
    return tenant


def _bootstrap_unit(
    database: Session,
    *,
    tenant: Tenant,
    name: str,
    unit_type: str,
    parent: AdministrativeUnit | None,
) -> AdministrativeUnit:
    unit = database.scalar(
        select(AdministrativeUnit).where(
            AdministrativeUnit.tenant_id == tenant.id,
            AdministrativeUnit.parent_id == (parent.id if parent else None),
            AdministrativeUnit.unit_type == unit_type,
            AdministrativeUnit.name == name,
        )
    )
    if unit is None:
        unit = AdministrativeUnit(
            tenant_id=tenant.id,
            parent_id=parent.id if parent else None,
            unit_type=unit_type,
            name=name,
            status=UserStatus.ACTIVE,
        )
        database.add(unit)
        database.flush()
        return unit
    if unit.status != UserStatus.ACTIVE:
        raise RuntimeError(f"初始化行政单元 {name} 已存在，但当前已停用")
    return unit


def _bootstrap_user(
    database: Session,
    *,
    username: str,
    display_name: str,
    password: str,
) -> tuple[User, bool]:
    user = database.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(
            username=username,
            display_name=display_name,
            password_hash=PASSWORD_HASH.hash(password),
            status=UserStatus.ACTIVE,
        )
        database.add(user)
        database.flush()
        return user, True
    if user.status != UserStatus.ACTIVE:
        raise RuntimeError(f"初始化账号 {username} 已存在，但当前已停用")
    return user, False


def _bootstrap_membership(
    database: Session,
    *,
    user: User,
    tenant: Tenant,
    role: str,
    unit: AdministrativeUnit | None,
) -> None:
    memberships = list(
        database.scalars(
            select(TenantMembership).where(TenantMembership.user_id == user.id)
        )
    )
    if memberships:
        if len(memberships) != 1:
            raise RuntimeError(f"初始化账号 {user.username} 已绑定多个租户，拒绝自动修改")
        membership = memberships[0]
        if (
            membership.tenant_id != tenant.id
            or membership.role != role
            or membership.status != UserStatus.ACTIVE
        ):
            raise RuntimeError(
                f"初始化账号 {user.username} 已绑定其他租户、角色或状态，拒绝自动修改"
            )
    else:
        membership = TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            status=UserStatus.ACTIVE,
        )
        database.add(membership)
        database.flush()

    scopes = list(
        database.scalars(
            select(MembershipScope).where(
                MembershipScope.membership_id == membership.id
            )
        )
    )
    if unit is None:
        if scopes:
            raise RuntimeError(f"平台管理员 {user.username} 不应绑定行政范围")
        return
    if not scopes:
        database.add(
            MembershipScope(
                membership_id=membership.id,
                administrative_unit_id=unit.id,
                include_descendants=False,
            )
        )
        return
    if (
        len(scopes) != 1
        or scopes[0].administrative_unit_id != unit.id
        or scopes[0].include_descendants
    ):
        raise RuntimeError(f"初始化账号 {user.username} 的行政范围与预期不一致")


def ensure_bootstrap_identity(
    database: Session,
    settings: Settings,
) -> BootstrapIdentityResult:
    values = (
        settings.bootstrap_platform_tenant_name,
        settings.bootstrap_admin_username,
        settings.bootstrap_operator_username,
        settings.bootstrap_tenant_name,
        settings.bootstrap_township_name,
        settings.bootstrap_village_name,
        settings.bootstrap_password,
    )
    if not all(values):
        raise RuntimeError(
            "初始化平台租户、账号、业务租户、乡镇、村和密码必须完整配置"
        )
    if len(settings.bootstrap_password or "") < 12:
        raise RuntimeError("初始化密码必须至少包含 12 个字符")
    if settings.bootstrap_admin_username == settings.bootstrap_operator_username:
        raise RuntimeError("平台管理员和演示操作员必须使用不同用户名")

    _bootstrap_lock(database)
    platform_tenant = _bootstrap_tenant(
        database,
        name=settings.bootstrap_platform_tenant_name,
        kind=TenantKind.PLATFORM,
    )
    business_tenant = _bootstrap_tenant(
        database,
        name=settings.bootstrap_tenant_name or "",
        kind=TenantKind.BUSINESS,
    )
    township = _bootstrap_unit(
        database,
        tenant=business_tenant,
        name=settings.bootstrap_township_name or "",
        unit_type=AdministrativeUnitType.TOWNSHIP,
        parent=None,
    )
    village = _bootstrap_unit(
        database,
        tenant=business_tenant,
        name=settings.bootstrap_village_name or "",
        unit_type=AdministrativeUnitType.VILLAGE,
        parent=township,
    )
    admin, admin_created = _bootstrap_user(
        database,
        username=settings.bootstrap_admin_username,
        display_name="平台管理员",
        password=settings.bootstrap_password or "",
    )
    operator, operator_created = _bootstrap_user(
        database,
        username=settings.bootstrap_operator_username,
        display_name="演示数据操作员",
        password=settings.bootstrap_password or "",
    )
    _bootstrap_membership(
        database,
        user=admin,
        tenant=platform_tenant,
        role=MembershipRole.PLATFORM_ADMIN,
        unit=None,
    )
    _bootstrap_membership(
        database,
        user=operator,
        tenant=business_tenant,
        role=MembershipRole.VILLAGE_OPERATOR,
        unit=village,
    )
    database.commit()
    return BootstrapIdentityResult(
        usernames=(admin.username, operator.username),
        created_users=int(admin_created) + int(operator_created),
    )
