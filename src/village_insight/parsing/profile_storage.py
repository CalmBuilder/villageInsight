from __future__ import annotations

import gzip
from typing import Any

from village_insight.db.models import DocumentProfile
from village_insight.parsing.contracts import WorkbookProfile

COMPRESSED_PROFILE_CELL_THRESHOLD = 250_000
PROFILE_ENCODING_GZIP_JSON = "gzip+workbook-profile-json"


def _cell_count(profile: WorkbookProfile) -> int:
    return sum(len(sheet.cells) for sheet in profile.sheets)


def _summary_payload(profile: WorkbookProfile) -> dict[str, Any]:
    payload = profile.model_dump(mode="json")
    for sheet in payload["sheets"]:
        sheet["cells"] = []
        sheet["row_properties"] = []
        for region in sheet["region_candidates"]:
            region["nonempty_cell_ids"] = []
    payload["warnings"] = [
        *payload.get("warnings", []),
        "完整单元格证据已压缩存储；接口仅返回结构摘要。",
    ]
    return payload


def store_workbook_profile(
    record: DocumentProfile,
    profile: WorkbookProfile,
) -> None:
    if _cell_count(profile) < COMPRESSED_PROFILE_CELL_THRESHOLD:
        record.profile = profile.model_dump(mode="json")
        record.profile_payload = None
        record.profile_encoding = None
        return
    raw = profile.model_dump_json().encode("utf-8")
    record.profile = _summary_payload(profile)
    record.profile_payload = gzip.compress(raw, compresslevel=3)
    record.profile_encoding = PROFILE_ENCODING_GZIP_JSON


def load_workbook_profile(record: DocumentProfile) -> WorkbookProfile:
    if record.profile_payload is None:
        return WorkbookProfile.model_validate(record.profile)
    if record.profile_encoding != PROFILE_ENCODING_GZIP_JSON:
        raise ValueError(f"unsupported workbook profile encoding: {record.profile_encoding}")
    raw = gzip.decompress(record.profile_payload)
    return WorkbookProfile.model_validate_json(raw)
