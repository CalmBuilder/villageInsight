from pathlib import Path

import pytest

from village_insight.storage import ImportPathError, resolve_import_directory


def test_import_directory_must_be_in_allowlist(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    denied = tmp_path / "denied"
    allowed.mkdir()
    denied.mkdir()

    assert resolve_import_directory(str(allowed), (allowed.resolve(),)) == allowed.resolve()
    with pytest.raises(ImportPathError, match="outside"):
        resolve_import_directory(str(denied), (allowed.resolve(),))
