from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "docs/datafiles/所有村"
CONFLICTS_PATH = PROJECT_ROOT / "docs/seeds/four-layer-v2/conflicts.json"
OUTPUT_PATH = (
    PROJECT_ROOT / "docs/batch-preparation/all-villages-import-manifest.json"
)

DIRECTORY_ACCOUNTS = {
    "七里坝": ("七里坝", "七里坝"),
    "先进社区": ("先进社区", "先进社区"),
    "官庄村村民委员会": ("官庄村", "官庄村"),
    "新场村村民委员会": ("新场村", "新场村"),
    "木渣黑社区居民委员会": ("木渣黑社区", "木渣黑社区"),
    "法乐村": ("法乐村", "法乐村"),
    "燕云村": ("燕云村", "燕云村"),
    "红星村": ("红星村", "红星村"),
    "群慧村": ("群慧村", "群慧村"),
    "胜丰村村民委员会": ("胜丰村", "胜丰村"),
    "董地村": ("董地村", "董地村"),
    "龙塘村": ("龙塘村", "龙塘村"),
}
SUPPORTED_SUFFIXES = {".xlsx", ".xls", ".csv"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    conflict_rows = json.loads(CONFLICTS_PATH.read_text(encoding="utf-8"))
    conflicts = {
        str(Path(row["source_path"]).resolve()): row["error_message"]
        for row in conflict_rows
    }
    discovered_directories = {
        path.name for path in SOURCE_ROOT.iterdir() if path.is_dir()
    }
    if discovered_directories != DIRECTORY_ACCOUNTS.keys():
        missing = sorted(discovered_directories - DIRECTORY_ACCOUNTS.keys())
        stale = sorted(DIRECTORY_ACCOUNTS.keys() - discovered_directories)
        raise SystemExit(
            f"目录映射不完整：missing={missing or '-'} stale={stale or '-'}"
        )

    files: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    for directory_name, (village, username) in DIRECTORY_ACCOUNTS.items():
        directory = SOURCE_ROOT / directory_name
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            error = conflicts.get(str(path.resolve()))
            if path.name.startswith("~$"):
                classification = "ignore_temporary_lock"
            elif error == "Workbook is password protected":
                classification = "blocked_password"
            elif error:
                classification = "blocked_invalid_source"
            else:
                classification = "ready"
            counts[classification] = counts.get(classification, 0) + 1
            files.append(
                {
                    "relative_path": str(path.relative_to(SOURCE_ROOT)),
                    "directory": directory_name,
                    "village": village,
                    "username": username,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256(path),
                    "classification": classification,
                    "known_error": error,
                }
            )

    payload = {
        "contract_version": "all-villages-import-manifest/v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_root": str(SOURCE_ROOT),
        "password": "demo",
        "counts": {"total": len(files), **counts},
        "files": files,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["counts"], ensure_ascii=False))
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
