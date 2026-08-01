from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from village_insight.source_paths import (
    SOURCE_MANIFEST_SCHEMA_VERSION,
    SourcePathError,
    SourcePathResolver,
)


def _write_manifest(
    root: Path,
    *,
    original_path: str,
    content: bytes,
    declared_sha256: str | None = None,
    object_path: str | None = None,
) -> Path:
    sha256 = declared_sha256 or hashlib.sha256(content).hexdigest()
    objects = root / "objects"
    objects.mkdir(parents=True)
    target = objects / sha256
    target.write_bytes(content)
    manifest = root / "source-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
                "references": [
                    {
                        "original_path": original_path,
                        "object_path": object_path or f"objects/{sha256}",
                        "sha256": sha256,
                        "size_bytes": len(content),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_existing_source_path_does_not_require_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"source")

    resolved = SourcePathResolver().resolve(source)

    assert resolved == source.resolve()


def test_missing_legacy_path_resolves_to_verified_object(tmp_path: Path) -> None:
    original = "/legacy/project/data/uploads/source.xlsx"
    manifest = _write_manifest(tmp_path, original_path=original, content=b"immutable")

    resolved = SourcePathResolver(manifest).resolve(original)

    assert resolved.read_bytes() == b"immutable"


def test_unknown_legacy_path_is_rejected(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        original_path="/legacy/known.xlsx",
        content=b"immutable",
    )

    with pytest.raises(SourcePathError, match="SOURCE_PATH_UNAVAILABLE"):
        SourcePathResolver(manifest).resolve("/legacy/unknown.xlsx")


def test_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        original_path="/legacy/source.xlsx",
        content=b"immutable",
    )
    (tmp_path / "objects" / hashlib.sha256(b"immutable").hexdigest()).write_bytes(b"changedxx")

    with pytest.raises(SourcePathError, match="SOURCE_OBJECT_HASH_MISMATCH"):
        SourcePathResolver(manifest).resolve("/legacy/source.xlsx")


def test_non_content_addressed_object_path_is_rejected(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        original_path="/legacy/source.xlsx",
        content=b"immutable",
        object_path="../outside",
    )

    with pytest.raises(SourcePathError, match="SOURCE_OBJECT_PATH_INVALID"):
        SourcePathResolver(manifest).resolve("/legacy/source.xlsx")


def test_symlink_object_is_rejected(tmp_path: Path) -> None:
    content = b"immutable"
    sha256 = hashlib.sha256(content).hexdigest()
    outside = tmp_path / "outside"
    outside.write_bytes(content)
    objects = tmp_path / "objects"
    objects.mkdir()
    (objects / sha256).symlink_to(outside)
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
                "references": [
                    {
                        "original_path": "/legacy/source.xlsx",
                        "object_path": f"objects/{sha256}",
                        "sha256": sha256,
                        "size_bytes": len(content),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SourcePathError, match="SOURCE_OBJECT_SYMLINK_FORBIDDEN"):
        SourcePathResolver(manifest).resolve("/legacy/source.xlsx")
