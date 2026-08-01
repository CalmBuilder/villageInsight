from __future__ import annotations

from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from village_insight.api.app import app
from village_insight.db.base import Base
from village_insight.db.models import (
    AdministrativeUnit,
    AdministrativeUnitType,
    IngestionBatch,
    IngestionItem,
    MatchType,
    MembershipRole,
    MembershipScope,
    TemplateMatch,
    TemplateProposal,
    Tenant,
    TenantKind,
    TenantMembership,
    User,
)
from village_insight.db.session import get_db
from village_insight.identity import PASSWORD_HASH
from village_insight.identity_demo import DEMO_VILLAGES, ensure_demo_identities


def _seed_identity(database: Session) -> dict[str, object]:
    tenant = Tenant(name="青山镇")
    platform_tenant = Tenant(name="管理员租户", kind=TenantKind.PLATFORM)
    other_tenant = Tenant(name="白云镇")
    database.add_all([tenant, platform_tenant, other_tenant])
    database.flush()
    township = AdministrativeUnit(
        tenant_id=tenant.id,
        unit_type=AdministrativeUnitType.TOWNSHIP,
        name="青山镇",
    )
    database.add(township)
    database.flush()
    village_a = AdministrativeUnit(
        tenant_id=tenant.id,
        parent_id=township.id,
        unit_type=AdministrativeUnitType.VILLAGE,
        name="东村",
    )
    village_b = AdministrativeUnit(
        tenant_id=tenant.id,
        parent_id=township.id,
        unit_type=AdministrativeUnitType.VILLAGE,
        name="西村",
    )
    database.add_all([village_a, village_b])
    database.flush()
    other_township = AdministrativeUnit(
        tenant_id=other_tenant.id,
        unit_type=AdministrativeUnitType.TOWNSHIP,
        name="白云镇",
    )
    database.add(other_township)
    database.flush()
    other_village = AdministrativeUnit(
        tenant_id=other_tenant.id,
        parent_id=other_township.id,
        unit_type=AdministrativeUnitType.VILLAGE,
        name="外租户村",
    )
    database.add(other_village)
    database.flush()

    result: dict[str, object] = {
        "tenant": tenant,
        "township": township,
        "village_a": village_a,
        "village_b": village_b,
        "platform_tenant": platform_tenant,
        "other_village": other_village,
    }
    accounts = (
        ("town", MembershipRole.TENANT_ADMIN, township, True),
        ("village", MembershipRole.VILLAGE_OPERATOR, village_a, False),
        ("admin", MembershipRole.PLATFORM_ADMIN, None, False),
    )
    for username, role, unit, include_descendants in accounts:
        user = User(
            username=username,
            display_name=username,
            password_hash=PASSWORD_HASH.hash("correct horse battery staple"),
        )
        database.add(user)
        database.flush()
        membership = TenantMembership(
            tenant_id=(
                platform_tenant.id
                if role == MembershipRole.PLATFORM_ADMIN
                else tenant.id
            ),
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
        result[username] = user
    database.commit()
    return result


def test_roles_and_village_scope_are_enforced() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database:
        seeded = _seed_identity(database)
        other_batch = IngestionBatch(
            name="西村批次",
            tenant_id=seeded["tenant"].id,  # type: ignore[union-attr]
            administrative_unit_id=seeded["village_b"].id,  # type: ignore[union-attr]
            created_by_user_id=seeded["village"].id,  # type: ignore[union-attr]
        )
        database.add(other_batch)
        database.flush()
        database.add(
            IngestionItem(
                tenant_id=other_batch.tenant_id,
                administrative_unit_id=other_batch.administrative_unit_id,
                created_by_user_id=other_batch.created_by_user_id,
                batch_id=other_batch.id,
                original_name="西村台账.xlsx",
                source_path="/tmp/not-used.xlsx",
                source_sha256="f" * 64,
                size_bytes=1,
            )
        )
        deleted_item = IngestionItem(
            tenant_id=other_batch.tenant_id,
            administrative_unit_id=other_batch.administrative_unit_id,
            created_by_user_id=other_batch.created_by_user_id,
            batch_id=other_batch.id,
            original_name="已删除台账.xlsx",
            source_path="/tmp/not-used-deleted.xlsx",
            source_sha256="e" * 64,
            size_bytes=1,
            status="result_deleted",
            formal_import_status="deleted",
            build_result_deletion_status="deleted",
        )
        database.add(deleted_item)
        database.commit()
        other_batch_id = other_batch.id
        deleted_item_id = deleted_item.id

    def override_database() -> Generator[Session]:
        with Session(engine, expire_on_commit=False) as database:
            yield database

    app.dependency_overrides[get_db] = override_database
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                json={"username": "town", "password": "correct horse battery staple"},
            )
            assert login.status_code == 200
            assert login.json()["scope_unit_type"] == "township"
            assert client.post("/api/batches", json={"name": "未选村批次"}).status_code == 422
            created = client.post(
                "/api/batches",
                json={
                    "name": "租户管理员代传",
                    "administrative_unit_id": str(
                        seeded["village_b"].id  # type: ignore[union-attr]
                    ),
                },
            )
            assert created.status_code == 201
            assert created.json()["created_by_user_id"] == str(
                seeded["town"].id  # type: ignore[union-attr]
            )
            assert client.post(
                "/api/batches",
                json={
                    "name": "跨租户伪造",
                    "administrative_unit_id": str(
                        seeded["other_village"].id  # type: ignore[union-attr]
                    ),
                },
            ).status_code == 404
            files = client.get("/api/files")
            assert files.status_code == 200
            assert any(
                file["batch_id"] == str(other_batch_id)
                for file in files.json()["items"]
            )
            assert all(
                file["id"] != str(deleted_item_id)
                for file in files.json()["items"]
            )
            batch_items = client.get(f"/api/batches/{other_batch_id}/items")
            assert batch_items.status_code == 200
            assert all(
                item["id"] != str(deleted_item_id)
                for item in batch_items.json()
            )
            assert client.get(
                f"/api/batches/{other_batch_id}/items/{deleted_item_id}/profile"
            ).status_code == 409
            assert client.get("/api/admin/tenants").status_code == 403
            client.post("/api/auth/logout")

            login = client.post(
                "/api/auth/login",
                json={
                    "username": "village",
                    "password": "correct horse battery staple",
                },
            )
            assert login.status_code == 200
            created = client.post("/api/batches", json={"name": "东村批次"})
            assert created.status_code == 201
            assert created.json()["administrative_unit_id"] == str(
                seeded["village_a"].id  # type: ignore[union-attr]
            )
            assert client.get(f"/api/batches/{other_batch_id}").status_code == 404
            assert (
                client.post(
                    "/api/batches",
                    json={
                        "name": "伪造范围",
                        "administrative_unit_id": str(
                            seeded["village_b"].id  # type: ignore[union-attr]
                        ),
                    },
                ).status_code
                == 403
            )
            client.post("/api/auth/logout")

            login = client.post(
                "/api/auth/login",
                json={
                    "username": "admin",
                    "password": "correct horse battery staple",
                },
            )
            assert login.status_code == 200
            assert login.json()["tenant_name"] == "管理员租户"
            assert login.json()["upload_units"] == []
            assert client.post("/api/batches", json={"name": "平台上传"}).status_code == 403
            tenants = client.get("/api/admin/tenants")
            assert tenants.status_code == 200
            created_tenant = client.post(
                "/api/admin/tenants",
                json={"name": "新租户", "township_name": "新乡镇"},
            )
            assert created_tenant.status_code == 201
            tenant_body = created_tenant.json()
            tenant_id = tenant_body["id"]
            root_unit_id = tenant_body["units"][0]["id"]
            renamed = client.patch(
                f"/api/admin/tenants/{tenant_id}",
                json={"name": "新租户（已修改）"},
            )
            assert renamed.status_code == 200
            village = client.post(
                f"/api/admin/tenants/{tenant_id}/units",
                json={
                    "name": "测试村",
                    "unit_type": "village",
                    "parent_id": root_unit_id,
                },
            )
            assert village.status_code == 201
            village_id = village.json()["id"]
            assert client.delete(f"/api/admin/units/{village_id}").status_code == 204
            assert client.patch(
                f"/api/admin/units/{village_id}",
                json={"status": "active", "name": "测试村（已修改）"},
            ).status_code == 200
            managed_user = client.post(
                "/api/admin/users",
                json={
                    "username": "new-tenant-admin",
                    "display_name": "新租户管理员",
                    "password": "demo",
                    "tenant_id": tenant_id,
                    "role": "tenant_admin",
                    "scope_unit_id": root_unit_id,
                },
            )
            assert managed_user.status_code == 201
            managed_user_id = managed_user.json()["user_id"]
            assert client.patch(
                f"/api/admin/users/{managed_user_id}",
                json={"display_name": "已修改管理员"},
            ).status_code == 200
            assert client.delete(
                f"/api/admin/users/{managed_user_id}"
            ).status_code == 204
            assert client.patch(
                f"/api/admin/users/{managed_user_id}",
                json={"status": "active"},
            ).status_code == 200
            assert client.delete(f"/api/admin/tenants/{tenant_id}").status_code == 204
            assert client.patch(
                f"/api/admin/tenants/{tenant_id}",
                json={"status": "active"},
            ).status_code == 200
            assert client.delete(
                f"/api/admin/users/{seeded['admin'].id}"  # type: ignore[union-attr]
            ).status_code == 409
            changed = client.post(
                "/api/auth/password",
                json={
                    "current_password": "correct horse battery staple",
                    "new_password": "new secure password",
                },
            )
            assert changed.status_code == 204
            client.post("/api/auth/logout")
            assert (
                client.post(
                    "/api/auth/login",
                    json={
                        "username": "admin",
                        "password": "correct horse battery staple",
                    },
                ).status_code
                == 401
            )
            assert (
                client.post(
                    "/api/auth/login",
                    json={
                        "username": "admin",
                        "password": "new secure password",
                    },
                ).status_code
                == 200
            )
            client.post("/api/auth/logout")

    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)


def test_demo_identities_are_idempotent_without_resetting_passwords() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database:
        first = ensure_demo_identities(database)
        assert first.created_users == len(DEMO_VILLAGES) + 2
        village_names = set(
            database.scalars(
                select(AdministrativeUnit.name).where(
                    AdministrativeUnit.unit_type
                    == AdministrativeUnitType.VILLAGE
                )
            )
        )
        assert village_names == set(DEMO_VILLAGES)
        admin = database.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        assert PASSWORD_HASH.verify("admin", admin.password_hash)
        admin.password_hash = PASSWORD_HASH.hash("changed password")
        database.commit()

        second = ensure_demo_identities(database)
        assert second.created_users == 0
        database.refresh(admin)
        assert PASSWORD_HASH.verify("changed password", admin.password_hash)
        assert not PASSWORD_HASH.verify("admin", admin.password_hash)
    Base.metadata.drop_all(engine)


def test_platform_governor_sees_business_tenant_review_queue() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database:
        seeded = _seed_identity(database)
        batch = IngestionBatch(
            name="东村待治理",
            tenant_id=seeded["tenant"].id,  # type: ignore[union-attr]
            administrative_unit_id=seeded["village_a"].id,  # type: ignore[union-attr]
            created_by_user_id=seeded["village"].id,  # type: ignore[union-attr]
        )
        database.add(batch)
        database.flush()
        item = IngestionItem(
            tenant_id=batch.tenant_id,
            administrative_unit_id=batch.administrative_unit_id,
            created_by_user_id=batch.created_by_user_id,
            batch_id=batch.id,
            original_name="待治理.xlsx",
            source_path="/tmp/review.xlsx",
            source_sha256="a" * 64,
            size_bytes=100,
        )
        database.add(item)
        database.flush()
        database.add(
            TemplateMatch(
                item_id=item.id,
                source_sha256=item.source_sha256,
                profile_contract_version="workbook-profile/v2",
                layout_fingerprint="b" * 64,
                match_type=MatchType.NONE,
                score_basis_points=0,
                differences={"missing_headers": [], "new_headers": ["姓名"]},
                requires_hermes=True,
                matcher_version="layout-matcher/v2",
            )
        )
        database.add(
            TemplateProposal(
                tenant_id=item.tenant_id,
                administrative_unit_id=item.administrative_unit_id,
                created_by_user_id=item.created_by_user_id,
                source="hermes",
                source_item_id=item.id,
                confidence=0.8,
                proposal={"field_decisions": []},
            )
        )
        semantic_partial_item = IngestionItem(
            tenant_id=batch.tenant_id,
            administrative_unit_id=batch.administrative_unit_id,
            created_by_user_id=batch.created_by_user_id,
            batch_id=batch.id,
            original_name="部分语义但无需治理.xlsx",
            source_path="/tmp/semantic-partial.xlsx",
            source_sha256="c" * 64,
            size_bytes=200,
            status="imported",
            formal_import_status="partial",
        )
        database.add(semantic_partial_item)
        database.commit()

    def override_database() -> Generator[Session]:
        with Session(engine, expire_on_commit=False) as database:
            yield database

    app.dependency_overrides[get_db] = override_database
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                json={
                    "username": "admin",
                    "password": "correct horse battery staple",
                },
            )
            assert login.status_code == 200
            response = client.get("/api/reviews")
            assert response.status_code == 200
            assert response.json()["total"] == 1
            assert response.json()["items"][0]["tenant_name"] == "青山镇"
            assert response.json()["items"][0]["administrative_unit_name"] == "东村"
            files = client.get("/api/files")
            assert files.status_code == 200
            assert files.json()["counts"]["imported"] == 1
            assert files.json()["counts"]["partial"] == 1
            assert files.json()["counts"]["review"] == 1
            partial_files = client.get("/api/files", params={"status": "partial"})
            assert partial_files.status_code == 200
            assert partial_files.json()["total"] == 1
            assert partial_files.json()["items"][0]["governance_pending"] is False
            review_files = client.get("/api/files", params={"status": "review"})
            assert review_files.status_code == 200
            assert review_files.json()["total"] == 1
            assert review_files.json()["items"][0]["governance_pending"] is True
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
