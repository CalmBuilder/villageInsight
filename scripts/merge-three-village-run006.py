from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from village_insight.api.routes.batches import add_stored_item
from village_insight.config import get_settings
from village_insight.db.models import (
    AdministrativeUnit,
    IngestionBatch,
    IngestionItem,
    MembershipScope,
    Tenant,
    TenantMembership,
    User,
)
from village_insight.db.session import get_session_factory
from village_insight.parsing.identity import file_sha256
from village_insight.reimport import reset_item_for_reimport
from village_insight.storage import copy_local_file

VILLAGES = ("合路村", "燕云村", "陡滩村")
SOURCE_DATABASE = "village_insight_three_villages_20260729_run006"
TARGET_TENANT = "X租户"


def _source_url() -> str:
    configured = os.environ.get("THREE_VILLAGE_SOURCE_DATABASE_URL")
    if configured:
        return configured
    return str(make_url(get_settings().database_url).set(database=SOURCE_DATABASE))


def _source_rows(database: Session) -> list[dict[str, Any]]:
    rows = database.execute(
        select(
            IngestionItem.source_sha256,
            IngestionItem.source_path,
            IngestionItem.original_name,
            IngestionItem.relative_path,
            AdministrativeUnit.name.label("village_name"),
        )
        .join(AdministrativeUnit, AdministrativeUnit.id == IngestionItem.administrative_unit_id)
        .where(
            AdministrativeUnit.name.in_(VILLAGES),
            IngestionItem.status != "result_deleted",
        )
        .order_by(AdministrativeUnit.name, IngestionItem.source_sha256)
    ).mappings()
    return [dict(row) for row in rows]


def _target_context(
    database: Session,
) -> tuple[Tenant, dict[str, AdministrativeUnit], dict[str, User]]:
    tenant = database.scalar(select(Tenant).where(Tenant.name == TARGET_TENANT))
    if tenant is None:
        raise RuntimeError(f"target tenant does not exist: {TARGET_TENANT}")
    units = {
        unit.name: unit
        for unit in database.scalars(
            select(AdministrativeUnit).where(
                AdministrativeUnit.tenant_id == tenant.id,
                AdministrativeUnit.unit_type == "village",
                AdministrativeUnit.name.in_(VILLAGES),
            )
        )
    }
    users = {
        user.username: user
        for user in database.scalars(select(User).where(User.username.in_(VILLAGES)))
    }
    if set(units) != set(VILLAGES) or set(users) != set(VILLAGES):
        raise RuntimeError("target villages or village operator users are incomplete")
    for village_name, user in users.items():
        membership = database.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == user.id,
                TenantMembership.role == "village_operator",
                TenantMembership.status == "active",
            )
        )
        if membership is None:
            raise RuntimeError(f"active village membership is missing: {village_name}")
        scope = database.scalar(
            select(MembershipScope).where(
                MembershipScope.membership_id == membership.id,
                MembershipScope.administrative_unit_id == units[village_name].id,
            )
        )
        if scope is None:
            raise RuntimeError(f"village membership scope is incorrect: {village_name}")
    return tenant, units, users


def build_plan(target: Session, source: Session) -> dict[str, Any]:
    tenant, units, _ = _target_context(target)
    source_rows = _source_rows(source)
    if len(source_rows) != 73:
        raise RuntimeError(f"source database must contain 73 active files, got {len(source_rows)}")
    for row in source_rows:
        path = Path(str(row["source_path"]))
        if not path.is_file() or file_sha256(path) != row["source_sha256"]:
            raise RuntimeError("source file is missing or its SHA-256 changed")
    target_items = {
        (item.administrative_unit_id, item.source_sha256): item
        for item in target.scalars(
            select(IngestionItem).where(
                IngestionItem.tenant_id == tenant.id,
                IngestionItem.administrative_unit_id.in_([unit.id for unit in units.values()]),
            )
        )
    }
    additions: list[dict[str, Any]] = []
    reimports: list[IngestionItem] = []
    restorations: list[IngestionItem] = []
    counts: Counter[str] = Counter()
    for row in source_rows:
        village_name = str(row["village_name"])
        key = (units[village_name].id, str(row["source_sha256"]))
        existing = target_items.get(key)
        if existing is None:
            additions.append(row)
            counts[f"{village_name}:add"] += 1
            continue
        if existing.status == "result_deleted":
            restorations.append(existing)
            counts[f"{village_name}:restore"] += 1
        else:
            reimports.append(existing)
            counts[f"{village_name}:reimport"] += 1
    return {
        "source_file_count": len(source_rows),
        "add_count": len(additions),
        "reimport_count": len(reimports),
        "restore_count": len(restorations),
        "counts": dict(sorted(counts.items())),
        "additions": additions,
        "reimports": reimports,
        "restorations": restorations,
    }


def apply_plan(target: Session, plan: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    tenant, units, users = _target_context(target)
    additions_by_village: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in plan["additions"]:
        additions_by_village[str(row["village_name"])].append(row)
    copied_paths: list[Path] = []
    batch_ids: list[str] = []
    try:
        for village_name, rows in sorted(additions_by_village.items()):
            batch = IngestionBatch(
                name=f"三村稳定模板增量导入-{village_name}",
                source_kind="directory",
                tenant_id=tenant.id,
                administrative_unit_id=units[village_name].id,
                created_by_user_id=users[village_name].id,
            )
            target.add(batch)
            target.flush()
            destination = settings.resolved_upload_root() / str(batch.id)
            for row in rows:
                stored = copy_local_file(
                    Path(str(row["source_path"])),
                    destination,
                    max_bytes=settings.max_upload_bytes,
                )
                copied_paths.append(stored.path)
                item = add_stored_item(
                    target,
                    batch=batch,
                    path=stored.path,
                    original_name=str(row["original_name"]),
                    relative_path=(str(row["relative_path"]) if row["relative_path"] else None),
                    sha256=stored.sha256,
                    size_bytes=stored.size_bytes,
                )
                if item is None:
                    raise RuntimeError("target duplicate appeared while applying merge plan")
            batch.total_files = len(rows)
            batch_ids.append(str(batch.id))
        for item in plan["reimports"]:
            reset_item_for_reimport(target, item.id)
        for item in plan["restorations"]:
            village_name = next(
                name for name, unit in units.items() if unit.id == item.administrative_unit_id
            )
            reset_item_for_reimport(
                target,
                item.id,
                restore_deleted=True,
                restored_by_user_id=users[village_name].id,
            )
        target.commit()
    except Exception:
        target.rollback()
        for path in copied_paths:
            path.unlink(missing_ok=True)
        raise
    return {
        "added": int(plan["add_count"]),
        "reimported": int(plan["reimport_count"]),
        "restored": int(plan["restore_count"]),
        "batch_ids": batch_ids,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    target = get_session_factory()()
    source = Session(create_engine(_source_url(), pool_pre_ping=True))
    try:
        plan = build_plan(target, source)
        summary = {
            key: value
            for key, value in plan.items()
            if key not in {"additions", "reimports", "restorations"}
        }
        if not arguments.apply:
            target.rollback()
            print(json.dumps({"dry_run": True, **summary}, ensure_ascii=False))
            return
        result = apply_plan(target, plan)
        print(json.dumps({"dry_run": False, **summary, **result}, ensure_ascii=False))
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    main()
