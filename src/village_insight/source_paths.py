from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from village_insight.config import get_settings
from village_insight.parsing.identity import file_sha256

SOURCE_MANIFEST_SCHEMA_VERSION = "village-insight-source-manifest.v1"


class SourcePathError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceObject:
    object_path: str
    sha256: str
    size_bytes: int


class SourcePathResolver:
    def __init__(self, manifest_path: Path | None = None) -> None:
        self.manifest_path = manifest_path.resolve() if manifest_path is not None else None
        self._objects_by_original_path = self._load_manifest()
        self._verified: set[tuple[Path, str, int, int]] = set()

    def _load_manifest(self) -> dict[str, SourceObject]:
        if self.manifest_path is None:
            return {}
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourcePathError("SOURCE_MANIFEST_INVALID") from exc
        if not isinstance(payload, dict):
            raise SourcePathError("SOURCE_MANIFEST_INVALID")
        if payload.get("schema_version") != SOURCE_MANIFEST_SCHEMA_VERSION:
            raise SourcePathError("SOURCE_MANIFEST_VERSION_UNSUPPORTED")
        references = payload.get("references")
        if not isinstance(references, list):
            raise SourcePathError("SOURCE_MANIFEST_INVALID")

        objects: dict[str, SourceObject] = {}
        for reference in references:
            if not isinstance(reference, dict):
                raise SourcePathError("SOURCE_MANIFEST_INVALID")
            original_path = reference.get("original_path")
            object_path = reference.get("object_path")
            sha256 = reference.get("sha256")
            size_bytes = reference.get("size_bytes")
            if (
                not isinstance(original_path, str)
                or not original_path
                or not isinstance(object_path, str)
                or not object_path
                or not isinstance(sha256, str)
                or len(sha256) != 64
                or not isinstance(size_bytes, int)
                or size_bytes < 0
            ):
                raise SourcePathError("SOURCE_MANIFEST_INVALID")
            candidate = SourceObject(
                object_path=object_path,
                sha256=sha256.lower(),
                size_bytes=size_bytes,
            )
            existing = objects.get(original_path)
            if existing is not None and existing != candidate:
                raise SourcePathError("SOURCE_MANIFEST_CONFLICT")
            objects[original_path] = candidate
        return objects

    def resolve(self, raw_path: str | Path) -> Path:
        original_path = str(raw_path)
        direct = Path(original_path).expanduser()
        if direct.is_file():
            return direct.resolve()

        source_object = self._objects_by_original_path.get(original_path)
        if source_object is None or self.manifest_path is None:
            raise SourcePathError("SOURCE_PATH_UNAVAILABLE")
        relative_object_path = Path(source_object.object_path)
        if (
            relative_object_path.is_absolute()
            or relative_object_path.parts != ("objects", source_object.sha256)
        ):
            raise SourcePathError("SOURCE_OBJECT_PATH_INVALID")

        object_root = (self.manifest_path.parent / "objects").resolve()
        unresolved_candidate = self.manifest_path.parent / relative_object_path
        if (
            (self.manifest_path.parent / "objects").is_symlink()
            or unresolved_candidate.is_symlink()
        ):
            raise SourcePathError("SOURCE_OBJECT_SYMLINK_FORBIDDEN")
        candidate = unresolved_candidate.resolve()
        try:
            candidate.relative_to(object_root)
        except ValueError as exc:
            raise SourcePathError("SOURCE_OBJECT_OUTSIDE_ROOT") from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise SourcePathError("SOURCE_OBJECT_UNAVAILABLE")

        stat = candidate.stat()
        if stat.st_size != source_object.size_bytes:
            raise SourcePathError("SOURCE_OBJECT_SIZE_MISMATCH")
        cache_key = (candidate, source_object.sha256, stat.st_size, stat.st_mtime_ns)
        if cache_key not in self._verified:
            if file_sha256(candidate) != source_object.sha256:
                raise SourcePathError("SOURCE_OBJECT_HASH_MISMATCH")
            self._verified.add(cache_key)
        return candidate


def source_path_digest(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


@lru_cache(maxsize=8)
def _resolver_for_manifest(manifest_path: str | None) -> SourcePathResolver:
    return SourcePathResolver(Path(manifest_path) if manifest_path is not None else None)


def get_source_path_resolver() -> SourcePathResolver:
    manifest = get_settings().resolved_source_path_manifest()
    return _resolver_for_manifest(str(manifest) if manifest is not None else None)


def resolve_source_path(raw_path: str | Path) -> Path:
    return get_source_path_resolver().resolve(raw_path)


def clear_source_path_resolver_cache() -> None:
    _resolver_for_manifest.cache_clear()


def iter_absolute_strings(value: Any, json_path: str = "$") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            escaped_key = str(key).replace("~", "~0").replace("/", "~1")
            found.extend(iter_absolute_strings(child, f"{json_path}/{escaped_key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(iter_absolute_strings(child, f"{json_path}/{index}"))
    elif isinstance(value, str) and Path(value).is_absolute():
        found.append((json_path, value))
    return found
