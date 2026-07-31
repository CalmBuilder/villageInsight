from __future__ import annotations

import hashlib
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".csv"}


class ImportPathError(ValueError):
    pass


@dataclass(frozen=True)
class StoredFile:
    path: Path
    original_name: str
    sha256: str
    size_bytes: int


def resolve_import_directory(candidate: str, allowed_roots: tuple[Path, ...]) -> Path:
    resolved = Path(candidate).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ImportPathError("import path must be a directory")
    if not any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
        raise ImportPathError("import path is outside IMPORT_ROOTS")
    return resolved


def discover_files(directory: Path, *, recursive: bool, limit: int) -> list[Path]:
    iterator: Iterable[Path] = directory.rglob("*") if recursive else directory.glob("*")
    files = sorted(
        path.resolve()
        for path in iterator
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if len(files) > limit:
        raise ImportPathError(f"directory contains more than {limit} supported files")
    return files


def _safe_name(name: str) -> str:
    cleaned = Path(name).name.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("invalid file name")
    return cleaned


def safe_relative_path(value: str | None, fallback: str) -> str:
    if not value:
        return _safe_name(fallback)
    normalized = value.replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part not in {"", ".", ".."}]
    if not parts:
        return _safe_name(fallback)
    return "/".join(_safe_name(part) for part in parts)


def copy_local_file(source: Path, destination_dir: Path, *, max_bytes: int) -> StoredFile:
    size = source.stat().st_size
    if size > max_bytes:
        raise ValueError(f"file exceeds maximum size of {max_bytes} bytes")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{uuid.uuid4().hex}-{_safe_name(source.name)}"
    digest = hashlib.sha256()
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            writer.write(chunk)
    shutil.copystat(source, destination)
    return StoredFile(destination, source.name, digest.hexdigest(), size)


async def save_upload(upload: UploadFile, destination_dir: Path, *, max_bytes: int) -> StoredFile:
    original_name = _safe_name(upload.filename or "")
    if Path(original_name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError("only .xlsx, .xls and .csv files are supported")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{uuid.uuid4().hex}-{original_name}"
    digest = hashlib.sha256()
    size = 0
    try:
        with destination.open("xb") as writer:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"file exceeds maximum size of {max_bytes} bytes")
                digest.update(chunk)
                writer.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return StoredFile(destination, original_name, digest.hexdigest(), size)
