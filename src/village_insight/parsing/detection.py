from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from village_insight.parsing.contracts import DetectionResult, DocumentFormat

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"
_MEDIA_TYPES: dict[DocumentFormat, str] = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "excel_html": "application/vnd.ms-excel",
    "csv": "text/csv",
}


class DocumentDetectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _xlsx_signature(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            names = {name.replace("\\", "/") for name in archive.namelist()}
    except (OSError, zipfile.BadZipFile):
        return False
    return "[Content_Types].xml" in names and "xl/workbook.xml" in names


def _decode_text(payload: bytes) -> tuple[str, str] | None:
    if b"\x00" in payload:
        return None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None


def _csv_signature(payload: bytes) -> tuple[bool, str]:
    decoded = _decode_text(payload)
    if decoded is None:
        return False, ""
    text, encoding = decoded
    sample = text[:65536]
    if not sample.strip():
        return False, encoding
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return False, encoding
    rows = list(csv.reader(io.StringIO(sample), dialect))
    widths = [len(row) for row in rows[:20] if row]
    return len(widths) >= 2 and max(widths, default=0) >= 2, encoding


def _excel_html_signature(payload: bytes) -> tuple[bool, str]:
    decoded = _decode_text(payload)
    if decoded is None:
        return False, ""
    text, encoding = decoded
    normalized = text.lstrip("\ufeff\t\r\n ").lower()
    is_excel_html = (
        normalized.startswith(("<!doctype html", "<html"))
        and "<table" in normalized
        and (
            "urn:schemas-microsoft-com:office:excel" in normalized
            or "xmlns:x=" in normalized
        )
    )
    return is_excel_html, encoding


def detect_document(path: Path) -> DetectionResult:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DocumentDetectionError("DOCUMENT_READ_FAILED", str(exc)) from exc
    if not payload:
        raise DocumentDetectionError("EMPTY_DOCUMENT", "document is empty")

    signature: str
    document_format: DocumentFormat
    if payload.startswith(_ZIP_MAGIC):
        if not _xlsx_signature(path):
            raise DocumentDetectionError(
                "UNSUPPORTED_ZIP_DOCUMENT",
                "ZIP container is not an OOXML spreadsheet",
            )
        document_format = "xlsx"
        signature = "ooxml-zip"
    elif payload.startswith(_OLE_MAGIC):
        document_format = "xls"
        signature = "ole-compound"
    else:
        is_excel_html, encoding = _excel_html_signature(payload)
        if is_excel_html:
            document_format = "excel_html"
            signature = f"excel-html:{encoding}"
        else:
            is_csv, encoding = _csv_signature(payload)
        if not is_excel_html and not is_csv:
            raise DocumentDetectionError(
                "UNSUPPORTED_DOCUMENT_FORMAT",
                "content is not a supported XLSX, XLS, Excel HTML, or delimited text document",
            )
        if not is_excel_html:
            document_format = "csv"
            signature = f"delimited-text:{encoding}"

    extension = path.suffix.lower().lstrip(".")
    expected_extensions = {
        "xlsx": {"xlsx"},
        "xls": {"xls"},
        "excel_html": {"xls", "html", "htm"},
        "csv": {"csv"},
    }
    extension_matches = extension in expected_extensions[document_format]
    warnings = []
    if not extension_matches:
        warnings.append(
            f"EXTENSION_MISMATCH: extension .{extension or '<none>'} contains "
            f"{document_format} content"
        )
    return DetectionResult(
        format=document_format,
        media_type=_MEDIA_TYPES[document_format],
        signature=signature,
        extension=extension,
        extension_matches=extension_matches,
        warnings=warnings,
    )
