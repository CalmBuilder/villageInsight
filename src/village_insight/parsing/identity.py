from __future__ import annotations

import hashlib
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def workbook_id(source_sha256: str) -> str:
    return f"workbook:{source_sha256}"


def sheet_id(parent_workbook_id: str, index: int) -> str:
    return f"{parent_workbook_id}:sheet:{index}"


def cell_id(parent_sheet_id: str, row: int, column: int) -> str:
    return f"{parent_sheet_id}:r{row}:c{column}"
