from __future__ import annotations

import json
from pathlib import Path

import pytest

from village_insight.server_transfer import (
    MIGRATION_MANIFEST_SCHEMA_VERSION,
    ServerTransferError,
    SourceReference,
    _copy_source_objects,
    _write_checksums,
    verify_bundle,
)
from village_insight.source_paths import SourcePathResolver


def _build_minimal_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source_root = tmp_path / "source-root"
    source_root.mkdir()
    first = source_root / "first.xlsx"
    second = source_root / "second.xlsx"
    first.write_bytes(b"same-content")
    second.write_bytes(b"same-content")
    source_directory = bundle / "sources"
    source_directory.mkdir()
    manifest = _copy_source_objects(
        [
            SourceReference(
                reference_type="ingestion_item",
                reference_table="ingestion_items",
                record_id="one",
                json_path="$.source_path",
                original_path=str(first),
            ),
            SourceReference(
                reference_type="region_template_evidence",
                reference_table="region_template_versions",
                record_id="two",
                json_path="$.source_metadata/evidence/0/representative_path",
                original_path=str(second),
            ),
        ],
        source_directory=source_directory,
        allowed_roots=(source_root.resolve(),),
    )
    assert manifest["summary"]["logical_references"] == 2
    assert manifest["summary"]["distinct_original_paths"] == 2
    assert manifest["summary"]["unique_objects"] == 1

    database_directory = bundle / "database"
    database_directory.mkdir()
    (database_directory / "village_insight.dump").write_bytes(b"PGDMPfake")
    secrets_directory = bundle / "secrets"
    secrets_directory.mkdir()
    (secrets_directory / "settings.key").write_bytes(b"key")
    (bundle / "migration-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": MIGRATION_MANIFEST_SCHEMA_VERSION,
                "publish_ready": True,
                "database": {"dump_path": "database/village_insight.dump"},
            }
        ),
        encoding="utf-8",
    )
    _write_checksums(bundle)
    return bundle


def test_bundle_deduplicates_content_and_preserves_path_mappings(tmp_path: Path) -> None:
    bundle = _build_minimal_bundle(tmp_path)
    source_manifest = bundle / "sources" / "source-manifest.json"
    payload = json.loads(source_manifest.read_text(encoding="utf-8"))
    original_paths = [item["original_path"] for item in payload["references"]]

    resolver = SourcePathResolver(source_manifest)

    assert resolver.resolve(original_paths[0]).read_bytes() == b"same-content"
    assert resolver.resolve(original_paths[1]).read_bytes() == b"same-content"


def test_offline_bundle_verification_checks_all_objects(tmp_path: Path) -> None:
    bundle = _build_minimal_bundle(tmp_path)

    result = verify_bundle(bundle)

    assert result == {
        "verified_files": 5,
        "logical_references": 2,
        "distinct_original_paths": 2,
        "unique_objects": 1,
        "dump_size_bytes": 9,
        "publish_ready": True,
    }


def test_offline_bundle_verification_rejects_tampering(tmp_path: Path) -> None:
    bundle = _build_minimal_bundle(tmp_path)
    object_path = next((bundle / "sources" / "objects").iterdir())
    object_path.write_bytes(b"tampered")

    with pytest.raises(ServerTransferError, match="BUNDLE_CHECKSUM_MISMATCH"):
        verify_bundle(bundle)
