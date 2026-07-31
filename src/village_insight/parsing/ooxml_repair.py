from __future__ import annotations

import io
import re
import zipfile

_SHARED_STRINGS = "xl/sharedStrings.xml"
_CONTENT_TYPE_OVERRIDE = re.compile(
    rb'<Override[^>]*PartName="[^"]*sharedStrings\.xml"[^>]*/>',
    re.IGNORECASE,
)
_WORKBOOK_RELATIONSHIP = re.compile(
    rb'<Relationship[^>]*Type="[^"]*sharedStrings"[^>]*/>',
    re.IGNORECASE,
)
_SHARED_STRING_CELL = re.compile(rb'\bt="s"')


def repair_safe_ooxml_packaging(payload: bytes) -> tuple[bytes, list[str]]:
    """Repair only packaging defects that cannot change worksheet cell values."""
    source = io.BytesIO(payload)
    if not zipfile.is_zipfile(source):
        return payload, []

    with zipfile.ZipFile(source) as archive:
        files = {
            info.filename.replace("\\", "/"): archive.read(info.filename)
            for info in archive.infolist()
        }

    shared_string_paths = [name for name in files if name.lower().endswith("sharedstrings.xml")]
    if shared_string_paths:
        path = shared_string_paths[0]
        if path == _SHARED_STRINGS:
            return payload, []
        files[_SHARED_STRINGS] = files.pop(path)
        return _write_archive(files), ["OOXML_REPAIR: normalized sharedStrings part path"]

    content_types = files.get("[Content_Types].xml", b"")
    relationships = files.get("xl/_rels/workbook.xml.rels", b"")
    references_shared_strings = (
        b"sharedstrings.xml" in content_types.lower() or b"sharedstrings" in relationships.lower()
    )
    if not references_shared_strings:
        return payload, []

    worksheet_payloads = (
        value
        for name, value in files.items()
        if name.startswith("xl/worksheets/") and name.endswith(".xml")
    )
    if any(_SHARED_STRING_CELL.search(sheet) for sheet in worksheet_payloads):
        return payload, [
            "OOXML_REPAIR_BLOCKED: workbook references missing shared strings "
            "that are still used by worksheet cells"
        ]

    files["[Content_Types].xml"] = _CONTENT_TYPE_OVERRIDE.sub(b"", content_types)
    files["xl/_rels/workbook.xml.rels"] = _WORKBOOK_RELATIONSHIP.sub(
        b"",
        relationships,
    )
    return _write_archive(files), [
        "OOXML_REPAIR: removed unused missing sharedStrings manifest references"
    ]


def _write_archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()
