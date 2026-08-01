from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text

from village_insight.config import get_settings
from village_insight.db.models import (
    IngestionItem,
    RegionTemplateVersion,
    SemanticFieldVersion,
    SheetCompositionVersion,
    TemplateVersion,
    WorkbookRouteVersion,
)
from village_insight.db.session import get_session_factory
from village_insight.parsing.identity import file_sha256
from village_insight.source_paths import (
    SOURCE_MANIFEST_SCHEMA_VERSION,
    iter_absolute_strings,
    source_path_digest,
)

MIGRATION_MANIFEST_SCHEMA_VERSION = "village-insight-server-transfer.v1"


class ServerTransferError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceReference:
    reference_type: str
    reference_table: str
    record_id: str
    json_path: str
    original_path: str
    expected_sha256: str | None = None
    expected_size_bytes: int | None = None


PATH_METADATA_MODELS: tuple[tuple[str, type[Any], str], ...] = (
    ("semantic_field_evidence", SemanticFieldVersion, "semantic_field_versions"),
    ("region_template_evidence", RegionTemplateVersion, "region_template_versions"),
    ("sheet_composition_evidence", SheetCompositionVersion, "sheet_composition_versions"),
    ("workbook_route_evidence", WorkbookRouteVersion, "workbook_route_versions"),
    ("legacy_template_evidence", TemplateVersion, "template_versions"),
)

COUNT_TABLES = (
    "tenants",
    "administrative_units",
    "users",
    "tenant_memberships",
    "ingestion_batches",
    "ingestion_items",
    "dataset_records",
    "record_index_values",
    "record_value_lineage",
    "quality_issues",
    "semantic_fields",
    "semantic_field_versions",
    "region_templates",
    "region_template_versions",
    "sheet_compositions",
    "sheet_composition_versions",
    "workbook_routes",
    "workbook_route_versions",
)


def _json_dump(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _sha256(path: Path) -> str:
    return file_sha256(path)


def _git_output(project_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _alembic_heads(project_root: Path) -> list[str]:
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    return sorted(ScriptDirectory.from_config(config).get_heads())


def _path_within_roots(path: Path, roots: tuple[Path, ...]) -> Path:
    if path.is_symlink():
        raise ServerTransferError("SOURCE_SYMLINK_FORBIDDEN")
    resolved = path.resolve()
    if not resolved.is_file():
        raise ServerTransferError("SOURCE_FILE_MISSING")
    if not any(resolved.is_relative_to(root) for root in roots):
        raise ServerTransferError("SOURCE_PATH_OUTSIDE_ALLOWED_ROOTS")
    return resolved


def collect_source_references() -> list[SourceReference]:
    references: list[SourceReference] = []
    with get_session_factory()() as database:
        items = database.execute(
            select(
                IngestionItem.id,
                IngestionItem.source_path,
                IngestionItem.source_sha256,
                IngestionItem.size_bytes,
            ).order_by(IngestionItem.id)
        )
        for item_id, source_path, source_sha256, size_bytes in items:
            references.append(
                SourceReference(
                    reference_type="ingestion_item",
                    reference_table="ingestion_items",
                    record_id=str(item_id),
                    json_path="$.source_path",
                    original_path=str(source_path),
                    expected_sha256=str(source_sha256),
                    expected_size_bytes=int(size_bytes),
                )
            )

        for reference_type, model, table_name in PATH_METADATA_MODELS:
            rows = database.execute(select(model.id, model.source_metadata).order_by(model.id))
            for record_id, metadata in rows:
                for json_path, source_path in iter_absolute_strings(metadata or {}):
                    references.append(
                        SourceReference(
                            reference_type=reference_type,
                            reference_table=table_name,
                            record_id=str(record_id),
                            json_path=f"$.source_metadata{json_path.removeprefix('$')}",
                            original_path=source_path,
                        )
                    )
    return sorted(
        references,
        key=lambda item: (
            item.original_path,
            item.reference_table,
            item.record_id,
            item.json_path,
        ),
    )


def _freeze_database_counts() -> dict[str, Any]:
    with get_session_factory()() as database:
        counts = {
            table_name: int(
                database.scalar(text(f'SELECT count(*) FROM "{table_name}"')) or 0
            )
            for table_name in COUNT_TABLES
        }
        item_statuses = {
            str(status): int(count)
            for status, count in database.execute(
                select(IngestionItem.status, func.count())
                .group_by(IngestionItem.status)
                .order_by(IngestionItem.status)
            )
        }
        formal_statuses = {
            str(status): int(count)
            for status, count in database.execute(
                select(IngestionItem.formal_import_status, func.count())
                .group_by(IngestionItem.formal_import_status)
                .order_by(IngestionItem.formal_import_status)
            )
        }
        job_statuses = {
            str(status): int(count)
            for status, count in database.execute(
                text("SELECT status, count(*) FROM jobs GROUP BY status ORDER BY status")
            )
        }
        alembic_current = list(
            database.scalars(text("SELECT version_num FROM alembic_version ORDER BY version_num"))
        )
        database_size_bytes = int(
            database.scalar(text("SELECT pg_database_size(current_database())")) or 0
        )
        postgres_version = str(database.scalar(text("SHOW server_version")) or "")
    return {
        "tables": counts,
        "ingestion_item_statuses": item_statuses,
        "formal_import_statuses": formal_statuses,
        "job_statuses": job_statuses,
        "alembic_current": alembic_current,
        "database_size_bytes": database_size_bytes,
        "postgres_server_version": postgres_version,
    }


def _copy_source_objects(
    references: list[SourceReference],
    *,
    source_directory: Path,
    allowed_roots: tuple[Path, ...],
) -> dict[str, Any]:
    objects_directory = source_directory / "objects"
    objects_directory.mkdir(parents=True)
    objects_directory.chmod(0o700)
    manifest_references: list[dict[str, Any]] = []
    objects: dict[str, dict[str, Any]] = {}
    original_paths: dict[str, tuple[str, int]] = {}

    for reference in references:
        existing_identity = original_paths.get(reference.original_path)
        if existing_identity is None:
            source = _path_within_roots(Path(reference.original_path), allowed_roots)
            size_bytes = source.stat().st_size
            sha256 = _sha256(source)
            original_paths[reference.original_path] = (sha256, size_bytes)
        else:
            sha256, size_bytes = existing_identity
        if (
            reference.expected_size_bytes is not None
            and size_bytes != reference.expected_size_bytes
        ):
            raise ServerTransferError("SOURCE_SIZE_MISMATCH")
        if reference.expected_sha256 is not None and sha256 != reference.expected_sha256:
            raise ServerTransferError("SOURCE_HASH_MISMATCH")

        target = objects_directory / sha256
        if sha256 not in objects:
            if not target.exists():
                source = _path_within_roots(Path(reference.original_path), allowed_roots)
                shutil.copyfile(source, target)
                target.chmod(0o600)
            if target.stat().st_size != size_bytes or _sha256(target) != sha256:
                raise ServerTransferError("SOURCE_OBJECT_COPY_FAILED")
            objects[sha256] = {
                "sha256": sha256,
                "size_bytes": size_bytes,
                "object_path": f"objects/{sha256}",
            }
        manifest_references.append(
            {
                "reference_type": reference.reference_type,
                "reference_table": reference.reference_table,
                "record_id": reference.record_id,
                "json_path": reference.json_path,
                "original_path": reference.original_path,
                "original_path_sha256": source_path_digest(reference.original_path),
                "sha256": sha256,
                "size_bytes": size_bytes,
                "object_path": f"objects/{sha256}",
            }
        )

    manifest = {
        "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "objects": sorted(objects.values(), key=lambda item: item["sha256"]),
        "references": manifest_references,
        "summary": {
            "logical_references": len(manifest_references),
            "distinct_original_paths": len(original_paths),
            "unique_objects": len(objects),
            "unique_object_bytes": sum(int(item["size_bytes"]) for item in objects.values()),
            "reference_type_counts": dict(
                sorted(Counter(item["reference_type"] for item in manifest_references).items())
            ),
        },
    }
    _json_dump(source_directory / "source-manifest.json", manifest)
    return manifest


def _create_database_dump(
    destination: Path,
    *,
    project_root: Path,
    postgres_service: str,
    database_user: str,
    database_name: str,
) -> None:
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        postgres_service,
        "pg_dump",
        "-U",
        database_user,
        "-d",
        database_name,
        "-Fc",
        "--no-owner",
        "--no-privileges",
    ]
    with destination.open("wb") as output:
        result = subprocess.run(command, cwd=project_root, stdout=output, check=False)
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise ServerTransferError("PG_DUMP_FAILED")
    destination.chmod(0o600)


def _write_checksums(bundle_directory: Path) -> None:
    paths = sorted(
        path
        for path in bundle_directory.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    lines = [f"{_sha256(path)}  {path.relative_to(bundle_directory).as_posix()}" for path in paths]
    checksum_path = bundle_directory / "SHA256SUMS"
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    checksum_path.chmod(0o600)


def create_bundle(args: argparse.Namespace) -> Path:
    project_root = Path(args.project_root).resolve()
    destination = Path(args.output).resolve()
    if destination.exists():
        raise ServerTransferError("OUTPUT_ALREADY_EXISTS")
    destination.mkdir(parents=True, mode=0o700)

    try:
        git_commit = _git_output(project_root, "rev-parse", "HEAD")
        git_status = _git_output(project_root, "status", "--porcelain=v1", "--untracked-files=all")
        if git_status and not args.allow_dirty:
            raise ServerTransferError("GIT_WORKTREE_NOT_CLEAN")

        allowed_roots = tuple(root.resolve() for root in args.allowed_source_root)
        references = collect_source_references()
        if not references:
            raise ServerTransferError("NO_SOURCE_REFERENCES")
        sources_directory = destination / "sources"
        sources_directory.mkdir(mode=0o700)
        source_manifest = _copy_source_objects(
            references,
            source_directory=sources_directory,
            allowed_roots=allowed_roots,
        )

        database_directory = destination / "database"
        database_directory.mkdir(mode=0o700)
        dump_path = database_directory / "village_insight.dump"
        _create_database_dump(
            dump_path,
            project_root=project_root,
            postgres_service=args.postgres_service,
            database_user=args.database_user,
            database_name=args.database_name,
        )

        secrets_directory = destination / "secrets"
        secrets_directory.mkdir(mode=0o700)
        secret_key_path = args.settings_key.resolve()
        if not secret_key_path.is_file() or secret_key_path.is_symlink():
            raise ServerTransferError("SETTINGS_KEY_MISSING")
        shutil.copyfile(secret_key_path, secrets_directory / "settings.key")
        (secrets_directory / "settings.key").chmod(0o600)

        reports: dict[str, Any] = {}
        if args.four_layer_report is not None:
            source_report = args.four_layer_report.resolve()
            if not source_report.is_file():
                raise ServerTransferError("FOUR_LAYER_REPORT_MISSING")
            reports_directory = destination / "reports"
            reports_directory.mkdir(mode=0o700)
            target_report = reports_directory / "four-layer-report.json"
            shutil.copyfile(source_report, target_report)
            target_report.chmod(0o600)
            reports["four_layer"] = {
                "path": target_report.relative_to(destination).as_posix(),
                "sha256": _sha256(target_report),
                "size_bytes": target_report.stat().st_size,
            }

        database_snapshot = _freeze_database_counts()
        migration_manifest = {
            "schema_version": MIGRATION_MANIFEST_SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "publish_ready": not bool(git_status),
            "project": {
                "git_commit": git_commit,
                "git_worktree_clean": not bool(git_status),
                "git_status_sha256": hashlib.sha256(git_status.encode("utf-8")).hexdigest(),
                "alembic_heads": _alembic_heads(project_root),
            },
            "database": {
                **database_snapshot,
                "dump_path": dump_path.relative_to(destination).as_posix(),
                "dump_sha256": _sha256(dump_path),
                "dump_size_bytes": dump_path.stat().st_size,
            },
            "sources": {
                **source_manifest["summary"],
                "manifest_path": "sources/source-manifest.json",
                "manifest_sha256": _sha256(sources_directory / "source-manifest.json"),
            },
            "reports": reports,
            "settings_key": {
                "present": True,
                "path": "secrets/settings.key",
                "size_bytes": (secrets_directory / "settings.key").stat().st_size,
            },
        }
        _json_dump(destination / "migration-manifest.json", migration_manifest)
        _write_checksums(destination)
    except Exception:
        if args.keep_failed:
            (destination / "FAILED").write_text("bundle generation failed\n", encoding="utf-8")
        else:
            shutil.rmtree(destination)
        raise
    return destination


def _safe_bundle_file(bundle_directory: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ServerTransferError("BUNDLE_PATH_INVALID")
    target = (bundle_directory / relative).resolve()
    if not target.is_relative_to(bundle_directory.resolve()):
        raise ServerTransferError("BUNDLE_PATH_OUTSIDE_ROOT")
    if target.is_symlink() or not target.is_file():
        raise ServerTransferError("BUNDLE_FILE_MISSING")
    return target


def verify_bundle(
    bundle_directory: Path,
    *,
    project_root: Path | None = None,
    postgres_service: str = "postgres",
    verify_pg_restore: bool = False,
) -> dict[str, Any]:
    bundle_directory = bundle_directory.resolve()
    checksums_path = _safe_bundle_file(bundle_directory, "SHA256SUMS")
    checksum_lines = checksums_path.read_text(encoding="utf-8").splitlines()
    if not checksum_lines:
        raise ServerTransferError("CHECKSUMS_EMPTY")
    verified_files = 0
    for line in checksum_lines:
        try:
            expected_sha256, relative_path = line.split("  ", 1)
        except ValueError as exc:
            raise ServerTransferError("CHECKSUM_LINE_INVALID") from exc
        target = _safe_bundle_file(bundle_directory, relative_path)
        if _sha256(target) != expected_sha256:
            raise ServerTransferError("BUNDLE_CHECKSUM_MISMATCH")
        verified_files += 1

    migration_manifest_path = _safe_bundle_file(bundle_directory, "migration-manifest.json")
    source_manifest_path = _safe_bundle_file(bundle_directory, "sources/source-manifest.json")
    try:
        migration_manifest = json.loads(migration_manifest_path.read_text(encoding="utf-8"))
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServerTransferError("MANIFEST_INVALID") from exc
    if migration_manifest.get("schema_version") != MIGRATION_MANIFEST_SCHEMA_VERSION:
        raise ServerTransferError("MIGRATION_MANIFEST_VERSION_UNSUPPORTED")
    if source_manifest.get("schema_version") != SOURCE_MANIFEST_SCHEMA_VERSION:
        raise ServerTransferError("SOURCE_MANIFEST_VERSION_UNSUPPORTED")

    references = source_manifest.get("references")
    objects = source_manifest.get("objects")
    if not isinstance(references, list) or not isinstance(objects, list):
        raise ServerTransferError("SOURCE_MANIFEST_INVALID")
    expected_objects: set[str] = set()
    for item in objects:
        if not isinstance(item, dict):
            raise ServerTransferError("SOURCE_MANIFEST_INVALID")
        sha256 = item.get("sha256")
        size_bytes = item.get("size_bytes")
        object_path = item.get("object_path")
        if not isinstance(sha256, str) or object_path != f"objects/{sha256}":
            raise ServerTransferError("SOURCE_OBJECT_INVALID")
        target = _safe_bundle_file(bundle_directory / "sources", object_path)
        if not isinstance(size_bytes, int) or target.stat().st_size != size_bytes:
            raise ServerTransferError("SOURCE_OBJECT_SIZE_MISMATCH")
        if _sha256(target) != sha256:
            raise ServerTransferError("SOURCE_OBJECT_HASH_MISMATCH")
        expected_objects.add(sha256)
    actual_objects = {
        path.name
        for path in (bundle_directory / "sources" / "objects").iterdir()
        if path.is_file() and not path.is_symlink()
    }
    if actual_objects != expected_objects:
        raise ServerTransferError("SOURCE_OBJECT_SET_MISMATCH")

    reference_identity: dict[str, tuple[str, int, str]] = {}
    for reference in references:
        if not isinstance(reference, dict):
            raise ServerTransferError("SOURCE_REFERENCE_INVALID")
        original_path = reference.get("original_path")
        sha256 = reference.get("sha256")
        size_bytes = reference.get("size_bytes")
        object_path = reference.get("object_path")
        if (
            not isinstance(original_path, str)
            or not isinstance(sha256, str)
            or not isinstance(size_bytes, int)
            or object_path != f"objects/{sha256}"
            or sha256 not in expected_objects
        ):
            raise ServerTransferError("SOURCE_REFERENCE_INVALID")
        identity = (sha256, size_bytes, object_path)
        existing = reference_identity.get(original_path)
        if existing is not None and existing != identity:
            raise ServerTransferError("SOURCE_REFERENCE_CONFLICT")
        reference_identity[original_path] = identity

    dump_relative_path = migration_manifest.get("database", {}).get("dump_path")
    if not isinstance(dump_relative_path, str):
        raise ServerTransferError("DATABASE_DUMP_REFERENCE_INVALID")
    dump_path = _safe_bundle_file(bundle_directory, dump_relative_path)
    with dump_path.open("rb") as dump_file:
        dump_header = dump_file.read(5)
    if dump_header != b"PGDMP":
        raise ServerTransferError("DATABASE_DUMP_FORMAT_INVALID")
    if verify_pg_restore:
        if project_root is None:
            raise ServerTransferError("PROJECT_ROOT_REQUIRED")
        with dump_path.open("rb") as dump_input:
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    postgres_service,
                    "pg_restore",
                    "--list",
                ],
                cwd=project_root,
                stdin=dump_input,
                stdout=subprocess.DEVNULL,
                check=False,
            )
        if result.returncode != 0:
            raise ServerTransferError("PG_RESTORE_LIST_FAILED")

    return {
        "verified_files": verified_files,
        "logical_references": len(references),
        "distinct_original_paths": len(reference_identity),
        "unique_objects": len(expected_objects),
        "dump_size_bytes": dump_path.stat().st_size,
        "publish_ready": bool(migration_manifest.get("publish_ready")),
    }


def create_bundle_main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Create an audited server transfer bundle.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--allowed-source-root", type=Path, action="append")
    parser.add_argument("--settings-key", type=Path, default=settings.resolved_secret_key_path())
    parser.add_argument("--four-layer-report", type=Path)
    parser.add_argument("--postgres-service", default="postgres")
    parser.add_argument("--database-user", default="village_insight")
    parser.add_argument("--database-name", default="village_insight")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--keep-failed", action="store_true")
    args = parser.parse_args()
    if not args.allowed_source_root:
        args.allowed_source_root = [
            args.project_root / "data" / "uploads",
            args.project_root / "docs" / "datafiles",
        ]
    destination = create_bundle(args)
    print(destination)


def verify_bundle_main() -> None:
    parser = argparse.ArgumentParser(description="Verify a server transfer bundle offline.")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--postgres-service", default="postgres")
    parser.add_argument("--verify-pg-restore", action="store_true")
    args = parser.parse_args()
    result = verify_bundle(
        args.bundle,
        project_root=args.project_root,
        postgres_service=args.postgres_service,
        verify_pg_restore=args.verify_pg_restore,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
