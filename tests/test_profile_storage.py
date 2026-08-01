from pathlib import Path

from village_insight.db.models import DocumentProfile
from village_insight.parsing.profile_storage import (
    PROFILE_ENCODING_GZIP_JSON,
    load_workbook_profile,
    store_workbook_profile,
)
from village_insight.parsing.router import ParserRouter


def test_large_profile_uses_compressed_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "large.csv"
    source.write_text("姓名,人数\n张三,2\n", encoding="utf-8")
    profile = ParserRouter().profile(source)
    record = DocumentProfile(
        contract_version=profile.contract_version,
        source_sha256=profile.source_sha256,
        parser_name=profile.parser_name,
        parser_version=profile.parser_version,
        profile={},
    )
    monkeypatch.setattr(
        "village_insight.parsing.profile_storage.COMPRESSED_PROFILE_CELL_THRESHOLD",
        1,
    )

    store_workbook_profile(record, profile)

    assert record.profile_encoding == PROFILE_ENCODING_GZIP_JSON
    assert record.profile_payload is not None
    assert record.profile["sheets"][0]["cells"] == []
    restored = load_workbook_profile(record)
    assert restored == profile
