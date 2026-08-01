from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    RegionTemplate,
    RegionTemplateVersion,
    SemanticField,
    SemanticFieldVersion,
    SheetComposition,
    SheetCompositionVersion,
    WorkbookRoute,
    WorkbookRouteVersion,
)
from village_insight.db.session import get_session_factory
from village_insight.templates.catalog_snapshot import restore_snapshot
from village_insight.templates.sources import (
    VALIDATED_BASELINE_SOURCE,
    source_metadata,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LAYERS: dict[str, Any] = {
    "semantic_fields": SemanticField,
    "region_templates": RegionTemplate,
    "sheet_compositions": SheetComposition,
    "workbook_routes": WorkbookRoute,
}
_VERSION_MODELS: tuple[Any, ...] = (
    SemanticFieldVersion,
    RegionTemplateVersion,
    SheetCompositionVersion,
    WorkbookRouteVersion,
)


def _validate_sha256(label: str, value: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA256 digest")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(_file_sha256(child).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _require_exact_catalog(database: Session, snapshot: dict[str, Any]) -> None:
    for layer_name, model in _LAYERS.items():
        expected = {str(row["code"]) for row in snapshot["layers"][layer_name]}
        actual = set(database.scalars(select(model.code)))
        if expected != actual:
            raise ValueError(
                f"restored catalog is not exact for {layer_name}: "
                f"missing={len(expected - actual)}, extra={len(actual - expected)}"
            )


def normalize_baseline_sources(
    database: Session,
    *,
    snapshot: dict[str, Any],
    baseline_directory_sha256: str,
    catalog_dump_sha256: str,
) -> dict[str, int]:
    """Validate an exact restored catalog, then mark its versions as baseline.

    This deliberately cannot be used to bless a current mixed database: every
    catalog code must exactly equal the signed logical snapshot before any
    provenance is changed.
    """
    _validate_sha256("baseline_directory_sha256", baseline_directory_sha256)
    _validate_sha256("catalog_dump_sha256", catalog_dump_sha256)
    _require_exact_catalog(database, snapshot)
    restore_snapshot(database, snapshot=snapshot)

    common_metadata = {
        "baseline_directory_sha256": baseline_directory_sha256,
        "catalog_dump_sha256": catalog_dump_sha256,
        "parent_snapshot_sha256": snapshot["snapshot_sha256"],
    }
    counts: dict[str, int] = {}
    for model in _VERSION_MODELS:
        count = 0
        for version in database.scalars(select(model)):
            legacy_source = version.source
            version.source = VALIDATED_BASELINE_SOURCE
            version.source_metadata = source_metadata(
                source=VALIDATED_BASELINE_SOURCE,
                metadata={**dict(version.source_metadata or {}), **common_metadata},
                legacy_source=legacy_source,
            )
            count += 1
        counts[model.__tablename__] = count
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mark an exact restored four-layer catalog as validated baseline."
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--baseline-directory", type=Path, required=True)
    parser.add_argument("--catalog-dump", type=Path, required=True)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    if not arguments.confirm and not arguments.dry_run:
        parser.error("normalization requires --confirm; use --dry-run to validate only")
    baseline_directory = arguments.baseline_directory.resolve()
    if arguments.snapshot.resolve().parent != baseline_directory:
        parser.error("--snapshot must be a direct child of --baseline-directory")
    if arguments.catalog_dump.resolve().parent != baseline_directory:
        parser.error("--catalog-dump must be a direct child of --baseline-directory")
    snapshot = json.loads(arguments.snapshot.read_text(encoding="utf-8"))
    baseline_directory_sha256 = _directory_sha256(baseline_directory)
    catalog_dump_sha256 = _file_sha256(arguments.catalog_dump)
    with get_session_factory()() as database:
        try:
            counts = normalize_baseline_sources(
                database,
                snapshot=snapshot,
                baseline_directory_sha256=baseline_directory_sha256,
                catalog_dump_sha256=catalog_dump_sha256,
            )
            if arguments.dry_run:
                database.rollback()
            else:
                database.commit()
        except Exception:
            database.rollback()
            raise
    print(
        json.dumps(
            {
                "dry_run": arguments.dry_run,
                "baseline_directory_sha256": baseline_directory_sha256,
                "catalog_dump_sha256": catalog_dump_sha256,
                "version_counts": counts,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
