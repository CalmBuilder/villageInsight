from __future__ import annotations

import json
from pathlib import Path

import pytest

from village_insight.templates.catalog_recovery import (
    load_manifest,
    validate_recovery_point,
)


def test_repository_complete_recovery_bundle_is_valid() -> None:
    project_root = Path(__file__).parents[1]
    manifest = load_manifest(project_root)

    assert manifest.default_baseline == "current-205-expanded"
    point = manifest.baselines[manifest.default_baseline]
    bundle = validate_recovery_point(point)
    assert point.bundle_path.stat().st_size < 100_000_000
    assert bundle["catalog_snapshot"]["counts"] == {
        "semantic_fields": 1075,
        "region_templates": 386,
        "sheet_compositions": 328,
        "workbook_routes": 210,
    }


def test_recovery_manifest_rejects_path_outside_tracked_recovery_root(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    payload = {
        "schema_version": 2,
        "default_baseline": "stable",
        "baselines": {
            "stable": {
                "label": "稳定基线",
                "status": "approved",
                "bundle_path": "backups/catalog.json.gz",
                "file_sha256": "a" * 64,
                "bundle_sha256": "b" * 64,
                "snapshot_sha256": "c" * 64,
                "counts": {
                    "semantic_fields": 1,
                    "region_templates": 1,
                    "sheet_compositions": 1,
                    "workbook_routes": 1,
                },
            }
        },
    }
    (config / "four-layer-recovery-baselines.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="must remain under recovery"):
        load_manifest(tmp_path)


def test_recovery_point_rejects_changed_bundle_file(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[1]
    source_manifest = load_manifest(project_root)
    source = source_manifest.baselines[source_manifest.default_baseline]
    destination = tmp_path / "catalog-bundle.json.gz"
    destination.write_bytes(source.bundle_path.read_bytes() + b"changed")
    point = type(source)(
        name=source.name,
        label=source.label,
        status=source.status,
        bundle_path=destination,
        file_sha256=source.file_sha256,
        bundle_sha256=source.bundle_sha256,
        snapshot_sha256=source.snapshot_sha256,
        counts=source.counts,
        evidence=source.evidence,
    )

    with pytest.raises(ValueError, match="file checksum mismatch"):
        validate_recovery_point(point)
