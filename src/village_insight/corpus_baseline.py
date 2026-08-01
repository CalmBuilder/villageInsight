from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from village_insight.parsing.candidates import select_header_candidates
from village_insight.parsing.router import ParserRouter
from village_insight.storage import SUPPORTED_EXTENSIONS
from village_insight.templates.matching import layout_fingerprint

MANIFEST_CONTRACT = "ingestion-corpus-manifest/v1"
PHYSICAL_BASELINE_CONTRACT = "ingestion-physical-baseline/v1"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _text_evidence(value: str) -> dict[str, Any]:
    """Return comparable evidence without copying potentially sensitive text."""
    return {
        "sha256": hashlib.sha256(value.encode()).hexdigest(),
        "length": len(value),
    }


def build_baseline(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved_root = root.resolve()
    paths = sorted(
        path
        for path in resolved_root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    hashes = {path: _file_sha256(path) for path in paths}
    duplicate_counts = Counter(hashes.values())
    manifest_files: list[dict[str, Any]] = []
    physical_files: list[dict[str, Any]] = []

    for path in paths:
        relative_path = path.relative_to(resolved_root)
        source_sha256 = hashes[path]
        profile = ParserRouter().profile(path)
        manifest_files.append(
            {
                "relative_path": relative_path.as_posix(),
                "village": relative_path.parts[0],
                "extension": path.suffix.lower(),
                "source_sha256": source_sha256,
                "size_bytes": path.stat().st_size,
                "physical_format": profile.detection.format,
                "signature": profile.detection.signature,
                "extension_matches": profile.detection.extension_matches,
                "status": "profiled",
                "error_code": None,
                "sheet_count": len(profile.sheets),
                "parser_name": profile.parser_name,
                "parser_version": profile.parser_version,
                "exact_duplicate": duplicate_counts[source_sha256] > 1,
                "empty": path.stat().st_size == 0,
                "temporary": path.name.startswith(("~$", ".~lock.")),
            }
        )
        sheet_rows: list[dict[str, Any]] = []
        for sheet in profile.sheets:
            selected_headers = select_header_candidates(sheet.header_candidates)
            sheet_rows.append(
                {
                    "index": sheet.index,
                    "name_evidence": _text_evidence(sheet.name),
                    "hidden": sheet.hidden,
                    "declared_bounds": (
                        sheet.declared_bounds.model_dump(mode="json")
                        if sheet.declared_bounds
                        else None
                    ),
                    "observed_bounds": (
                        sheet.observed_bounds.model_dump(mode="json")
                        if sheet.observed_bounds
                        else None
                    ),
                    "nonempty_cell_count": len(sheet.cells),
                    "merge_ranges": [merge.range for merge in sheet.merges],
                    "hidden_row_numbers": [
                        row.row for row in sheet.row_properties if row.hidden
                    ],
                    "hidden_column_numbers": [
                        column.column
                        for column in sheet.column_properties
                        if column.hidden
                    ],
                    "region_candidates": [
                        {
                            "bounds": region.bounds.model_dump(mode="json"),
                            "nonempty_cell_count": len(region.nonempty_cell_ids),
                            "density": region.density,
                            "source": region.source,
                        }
                        for region in sheet.region_candidates
                    ],
                    "selected_header_candidates": [
                        {
                            "header_rows": candidate.header_rows,
                            "region_bounds": next(
                                region.bounds.range
                                for region in sheet.region_candidates
                                if region.id == candidate.region_id
                            ),
                            "confidence": candidate.confidence,
                            "columns": [
                                {
                                    "column": column.column,
                                    "source_column_id": column.source_column_id,
                                    "header_path_evidence": _text_evidence(
                                        " / ".join(column.header_path)
                                    ),
                                    "evidence_cell_ids": column.evidence_cell_ids,
                                }
                                for column in candidate.columns
                            ],
                        }
                        for candidate in selected_headers
                    ],
                }
            )
        physical_files.append(
            {
                "relative_path": relative_path.as_posix(),
                "source_sha256": source_sha256,
                "layout_fingerprint": layout_fingerprint(profile),
                "sheets": sheet_rows,
            }
        )

    manifest_core = {
        "contract_version": MANIFEST_CONTRACT,
        "root_label": root.name,
        "files": manifest_files,
    }
    manifest = {
        **manifest_core,
        "summary": {
            "file_count": len(manifest_files),
            "village_counts": dict(
                sorted(Counter(row["village"] for row in manifest_files).items())
            ),
            "extension_counts": dict(
                sorted(Counter(row["extension"] for row in manifest_files).items())
            ),
            "profiled_count": sum(
                row["status"] == "profiled" for row in manifest_files
            ),
            "duplicate_count": sum(
                bool(row["exact_duplicate"]) for row in manifest_files
            ),
        },
    }
    manifest["manifest_sha256"] = _json_sha256(manifest_core)
    physical_core = {
        "contract_version": PHYSICAL_BASELINE_CONTRACT,
        "manifest_sha256": manifest["manifest_sha256"],
        "files": physical_files,
    }
    physical_baseline = {
        **physical_core,
        "physical_baseline_sha256": _json_sha256(physical_core),
    }
    return manifest, physical_baseline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a value-safe immutable ingestion corpus baseline."
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--physical-baseline", type=Path, required=True)
    arguments = parser.parse_args()
    manifest, physical = build_baseline(arguments.root)
    for path, value in (
        (arguments.manifest, manifest),
        (arguments.physical_baseline, physical),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "manifest_sha256": manifest["manifest_sha256"],
                "physical_baseline_sha256": physical["physical_baseline_sha256"],
                "summary": manifest["summary"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
