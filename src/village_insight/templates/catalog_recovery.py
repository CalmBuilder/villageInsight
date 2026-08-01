from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from village_insight.db.session import get_session_factory
from village_insight.templates.catalog_bundle import (
    TABLE_NAMES,
    apply_catalog_bundle,
    create_catalog_bundle,
    read_catalog_bundle,
    validate_catalog_bundle,
    write_catalog_bundle,
)
from village_insight.templates.catalog_snapshot import restore_snapshot

MANIFEST_RELATIVE_PATH = Path("config/four-layer-recovery-baselines.json")
RECOVERY_ROOT = Path("recovery/four-layer-baselines")
PRE_RESTORE_ROOT = Path("backups/four-layer-pre-restore")
MAX_GITHUB_FILE_BYTES = 100_000_000
COUNT_KEYS = (
    "semantic_fields",
    "region_templates",
    "sheet_compositions",
    "workbook_routes",
)


@dataclass(frozen=True)
class RecoveryPoint:
    name: str
    label: str
    status: str
    bundle_path: Path
    file_sha256: str
    bundle_sha256: str
    snapshot_sha256: str
    counts: dict[str, int]
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryManifest:
    default_baseline: str
    baselines: dict[str, RecoveryPoint]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_bundle_path(project_root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError("recovery bundle path must be relative")
    recovery_root = (project_root / RECOVERY_ROOT).resolve()
    resolved = (project_root / candidate).resolve()
    if not resolved.is_relative_to(recovery_root):
        raise ValueError("recovery bundle must remain under recovery/four-layer-baselines/")
    return resolved


def load_manifest(project_root: Path) -> RecoveryManifest:
    path = project_root / MANIFEST_RELATIVE_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        raise ValueError("unsupported four-layer recovery manifest schema")
    raw_baselines = payload.get("baselines")
    if not isinstance(raw_baselines, dict) or not raw_baselines:
        raise ValueError("recovery manifest has no baselines")
    baselines: dict[str, RecoveryPoint] = {}
    for name, raw_value in raw_baselines.items():
        if not isinstance(raw_value, dict):
            raise ValueError(f"invalid recovery baseline: {name}")
        raw = raw_value
        counts = raw.get("counts")
        if not isinstance(counts, dict) or set(counts) != set(COUNT_KEYS):
            raise ValueError(f"invalid recovery counts for baseline: {name}")
        baselines[name] = RecoveryPoint(
            name=name,
            label=str(raw["label"]),
            status=str(raw["status"]),
            bundle_path=_resolve_bundle_path(project_root, str(raw["bundle_path"])),
            file_sha256=str(raw["file_sha256"]),
            bundle_sha256=str(raw["bundle_sha256"]),
            snapshot_sha256=str(raw["snapshot_sha256"]),
            counts={key: int(counts[key]) for key in COUNT_KEYS},
            evidence=tuple(str(item) for item in raw.get("evidence", [])),
        )
    default = payload.get("default_baseline")
    if default not in baselines:
        raise ValueError("default recovery baseline is not defined")
    if baselines[default].status != "approved":
        raise ValueError("default recovery baseline must be approved")
    return RecoveryManifest(default_baseline=str(default), baselines=baselines)


def validate_recovery_point(point: RecoveryPoint) -> dict[str, Any]:
    if not point.bundle_path.is_file():
        raise ValueError(f"recovery bundle does not exist: {point.bundle_path}")
    size = point.bundle_path.stat().st_size
    if size >= MAX_GITHUB_FILE_BYTES:
        raise ValueError(
            f"recovery bundle exceeds GitHub single-file limit: {size} bytes"
        )
    if _sha256_file(point.bundle_path) != point.file_sha256:
        raise ValueError(f"recovery bundle file checksum mismatch: {point.name}")
    bundle = read_catalog_bundle(point.bundle_path)
    validate_catalog_bundle(bundle)
    snapshot = bundle["catalog_snapshot"]
    checks = {
        "bundle_sha256": (bundle.get("bundle_sha256"), point.bundle_sha256),
        "snapshot_sha256": (
            snapshot.get("snapshot_sha256"),
            point.snapshot_sha256,
        ),
        "counts": (snapshot.get("counts"), point.counts),
    }
    for key, (actual, expected) in checks.items():
        if actual != expected:
            raise ValueError(f"recovery bundle metadata mismatch: {point.name}.{key}")
    return bundle


def _acquire_restore_lock(database: Any) -> None:
    if database.get_bind().dialect.name == "postgresql":
        database.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('village_insight_four_layer_restore'))"
            )
        )


def _bundle_result_summary(result: dict[str, dict[str, int]]) -> str:
    lines = []
    for table_name in TABLE_NAMES:
        row = result[table_name]
        removed = row.get("removed_extra", 0)
        lines.append(
            f"  {table_name}: inserted={row['inserted']}, updated={row['updated']}, "
            f"removed_extra={removed}, unchanged={row['unchanged']}"
        )
    return "\n".join(lines)


def _snapshot_result_summary(result: dict[str, dict[str, int]]) -> str:
    lines = []
    for layer in COUNT_KEYS:
        row = result[layer]
        lines.append(
            f"  {layer}: restored={row['restored']}, "
            f"disabled={row['disabled_post_snapshot']}, unchanged={row['unchanged']}"
        )
    return "\n".join(lines)


def _write_pre_restore_bundle(project_root: Path, bundle: dict[str, Any]) -> Path:
    output_directory = project_root / PRE_RESTORE_ROOT
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    output = output_directory / f"pre-restore-{timestamp}.catalog.json.gz"
    write_catalog_bundle(output, bundle)
    return output


def _print_baselines(manifest: RecoveryManifest) -> None:
    print("可用四层模板完整恢复点：")
    for name, point in manifest.baselines.items():
        marker = "（默认）" if name == manifest.default_baseline else ""
        try:
            validate_recovery_point(point)
            size = point.bundle_path.stat().st_size
            readiness = f"校验通过，{size} bytes"
        except (OSError, ValueError, json.JSONDecodeError) as error:
            readiness = f"不可用：{error}"
        counts = "/".join(str(point.counts[key]) for key in COUNT_KEYS)
        print(f"- {name}{marker} [{point.status}] {counts} {readiness}")
        print(f"  {point.label}")


def _apply_and_restore(database: Any, bundle: dict[str, Any]) -> tuple[Any, Any]:
    _acquire_restore_lock(database)
    bundle_result = apply_catalog_bundle(database, bundle=bundle)
    snapshot_result = restore_snapshot(
        database,
        snapshot=bundle["catalog_snapshot"],
    )
    return bundle_result, snapshot_result


def _restore(
    *,
    project_root: Path,
    point: RecoveryPoint,
    dry_run: bool,
    assume_yes: bool,
) -> None:
    bundle = validate_recovery_point(point)
    with get_session_factory()() as database:
        try:
            bundle_preview, snapshot_preview = _apply_and_restore(database, bundle)
        finally:
            database.rollback()
        print(f"恢复点：{point.name} [{point.status}] {point.label}")
        print(f"完整包摘要：{point.bundle_sha256}")
        print("模板数据变更预览：")
        print(_bundle_result_summary(bundle_preview))
        print("发布状态变更预览：")
        print(_snapshot_result_summary(snapshot_preview))
        if dry_run:
            print("dry-run 完成，模板和业务数据均未修改。")
            return
        if not assume_yes:
            expected = f"RESTORE {point.name} {point.bundle_sha256[:12]}"
            answer = input(f"输入 {expected} 确认恢复：").strip()
            if answer != expected:
                print("已取消。")
                return
        try:
            _acquire_restore_lock(database)
            pre_restore = _write_pre_restore_bundle(
                project_root,
                create_catalog_bundle(database),
            )
            bundle_result = apply_catalog_bundle(database, bundle=bundle)
            snapshot_result = restore_snapshot(
                database,
                snapshot=bundle["catalog_snapshot"],
            )
            database.commit()
        except Exception:
            database.rollback()
            raise
        print("四层模板完整数据恢复完成：")
        print(_bundle_result_summary(bundle_result))
        print(_snapshot_result_summary(snapshot_result))
        print(f"恢复前完整模板包：{pre_restore}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and restore a complete named four-layer catalog baseline."
    )
    parser.add_argument("--list", action="store_true", help="列出并校验完整恢复点")
    parser.add_argument("--baseline", help="具名恢复点；默认使用已验收恢复点")
    parser.add_argument("--dry-run", action="store_true", help="只预演，不提交修改")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    arguments = parser.parse_args()
    if arguments.list and (arguments.baseline or arguments.dry_run or arguments.yes):
        parser.error("--list cannot be combined with restore options")
    project_root = Path(__file__).resolve().parents[3]
    manifest = load_manifest(project_root)
    if arguments.list:
        _print_baselines(manifest)
        return
    baseline = arguments.baseline or manifest.default_baseline
    point = manifest.baselines.get(baseline)
    if point is None:
        parser.error(f"unknown baseline: {baseline}; use --list")
    try:
        _restore(
            project_root=project_root,
            point=point,
            dry_run=arguments.dry_run,
            assume_yes=arguments.yes,
        )
    except (OSError, ValueError, json.JSONDecodeError, SQLAlchemyError) as error:
        parser.exit(1, f"恢复点校验或预演失败：{error}\n")


if __name__ == "__main__":
    main()
