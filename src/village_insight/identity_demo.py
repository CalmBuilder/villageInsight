from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    AdministrativeUnit,
    AdministrativeUnitType,
    MembershipRole,
    MembershipScope,
    Tenant,
    TenantKind,
    TenantMembership,
    User,
    UserStatus,
)
from village_insight.db.session import get_session_factory
from village_insight.identity import PASSWORD_HASH

DEMO_TENANT_NAME = "X租户"
ADMIN_TENANT_NAME = "管理员租户"
DEMO_TOWNSHIP_NAME = "X租户"
DEMO_VILLAGES = (
    "新场村",
    "法乐村",
    "七里坝",
    "董地村",
    "陡滩村",
    "燕云村",
    "官庄村",
    "龙塘村",
    "先进社区",
    "木渣黑社区",
    "红星村",
    "群慧村",
    "胜丰村",
)


@dataclass(frozen=True)
class DemoIdentityResult:
    tenant_id: str
    usernames: tuple[str, ...]
    created_users: int


def _get_or_create_user(
    database: Session,
    *,
    username: str,
    display_name: str,
    initial_password: str,
) -> tuple[User, bool]:
    user = database.scalar(select(User).where(User.username == username))
    if user is not None:
        user.status = UserStatus.ACTIVE
        return user, False
    user = User(
        username=username,
        display_name=display_name,
        password_hash=PASSWORD_HASH.hash(initial_password),
        status=UserStatus.ACTIVE,
    )
    database.add(user)
    database.flush()
    return user, True


def _bind_account(
    database: Session,
    *,
    tenant: Tenant,
    user: User,
    role: str,
    unit: AdministrativeUnit | None,
    include_descendants: bool,
    allow_rebind: bool = False,
) -> None:
    memberships = list(
        database.scalars(
            select(TenantMembership).where(TenantMembership.user_id == user.id)
        )
    )
    other_memberships = [
        membership
        for membership in memberships
        if membership.tenant_id != tenant.id
    ]
    if other_memberships and not allow_rebind:
        raise RuntimeError(f"测试用户名 {user.username} 已属于其他租户，拒绝自动改绑")
    membership = next(iter(memberships), None)
    if membership is None:
        membership = TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            status=UserStatus.ACTIVE,
        )
        database.add(membership)
        database.flush()
    else:
        membership.tenant_id = tenant.id
        membership.role = role
        membership.status = UserStatus.ACTIVE
    database.execute(
        delete(MembershipScope).where(
            MembershipScope.membership_id == membership.id
        )
    )
    if unit is not None:
        database.add(
            MembershipScope(
                membership_id=membership.id,
                administrative_unit_id=unit.id,
                include_descendants=include_descendants,
            )
        )


def ensure_demo_identities(database: Session) -> DemoIdentityResult:
    tenant = database.scalar(select(Tenant).where(Tenant.name == DEMO_TENANT_NAME))
    if tenant is None:
        tenant = Tenant(
            name=DEMO_TENANT_NAME,
            kind=TenantKind.BUSINESS,
            status=UserStatus.ACTIVE,
        )
        database.add(tenant)
        database.flush()
    else:
        tenant.kind = TenantKind.BUSINESS
        tenant.status = UserStatus.ACTIVE

    admin_tenant = database.scalar(
        select(Tenant).where(Tenant.name == ADMIN_TENANT_NAME)
    )
    if admin_tenant is None:
        admin_tenant = Tenant(
            name=ADMIN_TENANT_NAME,
            kind=TenantKind.PLATFORM,
            status=UserStatus.ACTIVE,
        )
        database.add(admin_tenant)
        database.flush()
    else:
        admin_tenant.kind = TenantKind.PLATFORM
        admin_tenant.status = UserStatus.ACTIVE

    township = database.scalar(
        select(AdministrativeUnit).where(
            AdministrativeUnit.tenant_id == tenant.id,
            AdministrativeUnit.parent_id.is_(None),
            AdministrativeUnit.unit_type == AdministrativeUnitType.TOWNSHIP,
            AdministrativeUnit.name == DEMO_TOWNSHIP_NAME,
        )
    )
    if township is None:
        township = AdministrativeUnit(
            tenant_id=tenant.id,
            unit_type=AdministrativeUnitType.TOWNSHIP,
            name=DEMO_TOWNSHIP_NAME,
            administrative_code="demo-x-township",
            status=UserStatus.ACTIVE,
        )
        database.add(township)
        database.flush()

    villages: dict[str, AdministrativeUnit] = {}
    for index, village_name in enumerate(DEMO_VILLAGES, start=1):
        village = database.scalar(
            select(AdministrativeUnit).where(
                AdministrativeUnit.tenant_id == tenant.id,
                AdministrativeUnit.parent_id == township.id,
                AdministrativeUnit.unit_type == AdministrativeUnitType.VILLAGE,
                AdministrativeUnit.name == village_name,
            )
        )
        if village is None:
            village = AdministrativeUnit(
                tenant_id=tenant.id,
                parent_id=township.id,
                unit_type=AdministrativeUnitType.VILLAGE,
                name=village_name,
                administrative_code=f"demo-x-village-{index}",
                status=UserStatus.ACTIVE,
            )
            database.add(village)
            database.flush()
        villages[village_name] = village

    accounts = [
        ("x", "X租户管理员", "demo", MembershipRole.TENANT_ADMIN, township, True),
        *[
            (
                village_name,
                village_name,
                "demo",
                MembershipRole.VILLAGE_OPERATOR,
                villages[village_name],
                False,
            )
            for village_name in DEMO_VILLAGES
        ],
    ]
    created_users = 0
    for username, display_name, password, role, unit, include_descendants in accounts:
        user, created = _get_or_create_user(
            database,
            username=username,
            display_name=display_name,
            initial_password=password,
        )
        created_users += int(created)
        _bind_account(
            database,
            tenant=tenant,
            user=user,
            role=role,
            unit=unit,
            include_descendants=include_descendants,
        )
    admin, created = _get_or_create_user(
        database,
        username="admin",
        display_name="总管理员",
        initial_password="admin",
    )
    created_users += int(created)
    _bind_account(
        database,
        tenant=admin_tenant,
        user=admin,
        role=MembershipRole.PLATFORM_ADMIN,
        unit=None,
        include_descendants=False,
        allow_rebind=True,
    )
    database.commit()
    return DemoIdentityResult(
        tenant_id=str(tenant.id),
        usernames=tuple(account[0] for account in accounts) + ("admin",),
        created_users=created_users,
    )


def main() -> None:
    with get_session_factory()() as database:
        result = ensure_demo_identities(database)
    print(
        f"demo identities ready: tenant={result.tenant_id}, "
        f"users={len(result.usernames)}, newly_created={result.created_users}"
    )


if __name__ == "__main__":
    main()
