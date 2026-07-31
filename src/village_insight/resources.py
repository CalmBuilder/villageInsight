from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemorySnapshot:
    available_mb: int
    total_mb: int


def read_memory_snapshot(path: Path = Path("/proc/meminfo")) -> MemorySnapshot | None:
    try:
        values = {
            key.rstrip(":"): int(raw_value)
            for line in path.read_text(encoding="utf-8").splitlines()
            if len(parts := line.split()) >= 2
            for key, raw_value in [parts[:2]]
        }
    except (OSError, ValueError):
        return None
    available_kb = values.get("MemAvailable")
    total_kb = values.get("MemTotal")
    if available_kb is None or total_kb is None:
        return None
    return MemorySnapshot(
        available_mb=available_kb // 1024,
        total_mb=total_kb // 1024,
    )
