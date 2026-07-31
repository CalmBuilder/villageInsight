from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import select
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


def ensure_bootstrap_identity(database: Session, settings: Settings) -> None:
    values = (
        settings.bootstrap_tenant_name,
        settings.bootstrap_township_name,
        settings.bootstrap_village_name,
        settings.bootstrap_password,
    )
    if not any(values):
        return
    if not all(values):
        raise RuntimeError(
            "bootstrap tenant, township, village, and password must be configured together"
        )
    if len(settings.bootstrap_password or "") < 12:
        raise RuntimeError("bootstrap password must contain at least 12 characters")
    if database.scalar(select(Tenant).where(Tenant.name == settings.bootstrap_tenant_name)):
        return

    tenant = Tenant(name=settings.bootstrap_tenant_name)
    database.add(tenant)
    database.flush()
    township = AdministrativeUnit(
        tenant_id=tenant.id,
        unit_type=AdministrativeUnitType.TOWNSHIP,
        name=settings.bootstrap_township_name,
    )
    database.add(township)
    database.flush()
    village = AdministrativeUnit(
        tenant_id=tenant.id,
        parent_id=township.id,
        unit_type=AdministrativeUnitType.VILLAGE,
        name=settings.bootstrap_village_name,
    )
    database.add(village)
    database.flush()

    accounts = (
        ("tenant-admin", "租户管理员", MembershipRole.TENANT_ADMIN, township, True),
        (
            "village-operator",
            "村级数据员",
            MembershipRole.VILLAGE_OPERATOR,
            village,
            False,
        ),
    )
    for username, display_name, role, unit, include_descendants in accounts:
        user = User(
            username=username,
            display_name=display_name,
            password_hash=PASSWORD_HASH.hash(settings.bootstrap_password or ""),
        )
        database.add(user)
        database.flush()
        membership = TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
        )
        database.add(membership)
        database.flush()
        if unit is not None:
            database.add(
                MembershipScope(
                    membership_id=membership.id,
                    administrative_unit_id=unit.id,
                    include_descendants=include_descendants,
                )
            )
    database.commit()
