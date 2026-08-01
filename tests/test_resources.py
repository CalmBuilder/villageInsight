from pathlib import Path

from village_insight.resources import read_memory_snapshot


def test_read_memory_snapshot_uses_memavailable(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       32768000 kB\n"
        "MemFree:         1000000 kB\n"
        "MemAvailable:   12582912 kB\n",
        encoding="utf-8",
    )

    snapshot = read_memory_snapshot(meminfo)

    assert snapshot is not None
    assert snapshot.total_mb == 32000
    assert snapshot.available_mb == 12288


def test_read_memory_snapshot_returns_none_without_required_fields(
    tmp_path: Path,
) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemFree: 1000 kB\n", encoding="utf-8")

    assert read_memory_snapshot(meminfo) is None
