from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from village_insight.parsing.candidates import select_header_candidates
from village_insight.parsing.router import ParserRouter
from village_insight.storage import SUPPORTED_EXTENSIONS
from village_insight.templates.matching import MATCHER_VERSION, layout_fingerprint


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _profile_one(path: Path, source_sha256: str) -> dict[str, Any]:
    try:
        profile = ParserRouter().profile(path)
        selected_headers = [
            candidate
            for sheet in profile.sheets
            for candidate in select_header_candidates(sheet.header_candidates)
        ]
        headers = sorted(
            {
                " / ".join(column.header_path)
                for candidate in selected_headers
                for column in candidate.columns
                if column.header_path
            }
        )
        regions = {
            region.id: region for sheet in profile.sheets for region in sheet.region_candidates
        }
        return {
            "profile_contract_version": "corpus-profile/v2",
            "source_sha256": source_sha256,
            "representative_path": str(path),
            "status": "profiled",
            "format": profile.detection.format,
            "parser_name": profile.parser_name,
            "parser_version": profile.parser_version,
            "layout_fingerprint": layout_fingerprint(profile),
            "sheet_count": len(profile.sheets),
            "headers": headers,
            "header_columns": [
                {
                    "source_column_id": column.source_column_id,
                    "region_id": candidate.region_id,
                    "header_candidate_id": candidate.id,
                    "header_rows": candidate.header_rows,
                    "confidence": candidate.confidence,
                    "column": column.column,
                    "header_path": column.header_path,
                    "evidence_cell_ids": column.evidence_cell_ids,
                }
                for candidate in selected_headers
                for column in candidate.columns
                if column.header_path
            ],
            "layout_candidates": [
                {
                    "region_id": candidate.region_id,
                    "header_candidate_id": candidate.id,
                    "data_start_row": max(candidate.header_rows) + 1,
                    "data_end_row": regions[candidate.region_id].bounds.max_row,
                    "excluded_rows": [],
                    "confidence": candidate.confidence,
                }
                for candidate in selected_headers
            ],
        }
    except Exception as exc:
        return {
            "source_sha256": source_sha256,
            "representative_path": str(path),
            "status": "failed",
            "error_code": str(getattr(exc, "code", type(exc).__name__))[:80],
            "error_message": str(exc)[:1000],
        }


def _profile_isolated(
    item: tuple[str, Path],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    source_sha256, path = item
    command = [
        sys.executable,
        "-m",
        "village_insight.corpus",
        "--profile-one",
        str(path),
        "--source-sha256",
        source_sha256,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "source_sha256": source_sha256,
            "representative_path": str(path),
            "status": "failed",
            "error_code": "PROFILE_TIMEOUT",
            "error_message": f"profiling exceeded {timeout_seconds} seconds",
        }
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        return {
            "source_sha256": source_sha256,
            "representative_path": str(path),
            "status": "failed",
            "error_code": "PROFILE_PROCESS_EXITED",
            "error_message": (
                f"isolated profiler exited with code {completed.returncode}"
                + (f": {stderr[-800:]}" if stderr else "")
            ),
        }
    try:
        parsed: object = json.loads(completed.stdout)
        if not isinstance(parsed, dict):
            raise ValueError("isolated profiler output is not an object")
        return {str(key): value for key, value in parsed.items()}
    except (json.JSONDecodeError, ValueError) as exc:
        return {
            "source_sha256": source_sha256,
            "representative_path": str(path),
            "status": "failed",
            "error_code": "PROFILE_INVALID_OUTPUT",
            "error_message": str(exc),
        }


def _read_checkpoint(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    outcomes: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            outcome = json.loads(line)
        except json.JSONDecodeError:
            continue
        source_sha256 = outcome.get("source_sha256")
        if isinstance(source_sha256, str):
            outcomes[source_sha256] = outcome
    return outcomes


def _append_checkpoint(path: Path | None, outcome: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as checkpoint:
        checkpoint.write(json.dumps(outcome, ensure_ascii=False) + "\n")
        checkpoint.flush()


def analyze_corpus(
    root: Path,
    *,
    workers: int = 1,
    checkpoint_path: Path | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    resolved_root = root.expanduser().resolve()
    resolved_checkpoint = checkpoint_path.resolve() if checkpoint_path is not None else None
    all_files = sorted(
        path
        for path in resolved_root.rglob("*")
        if path.is_file() and path.resolve() != resolved_checkpoint
    )
    files = [path for path in all_files if path.suffix.lower() in SUPPORTED_EXTENSIONS]
    ignored_files = [path for path in all_files if path.suffix.lower() not in SUPPORTED_EXTENSIONS]
    hashes_by_path: dict[Path, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        hashes = executor.map(_sha256, files)
        hashes_by_path = dict(zip(files, hashes, strict=True))

    paths_by_hash: dict[str, list[Path]] = defaultdict(list)
    for path, source_sha256 in hashes_by_path.items():
        paths_by_hash[source_sha256].append(path)
    representatives = [
        (source_sha256, sorted(paths)[0]) for source_sha256, paths in sorted(paths_by_hash.items())
    ]
    checkpoint_outcomes = _read_checkpoint(checkpoint_path)
    outcomes_by_hash: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, Path]] = []
    for item in representatives:
        source_sha256, path = item
        outcome = checkpoint_outcomes.get(source_sha256)
        retryable_failure = outcome is not None and outcome.get("error_code") in {
            "PROFILE_PROCESS_EXITED",
            "PROFILE_TIMEOUT",
            "PROFILE_INVALID_OUTPUT",
        }
        if (
            outcome is None
            or outcome.get("representative_path") != str(path)
            or (
                outcome.get("status") == "profiled"
                and outcome.get("profile_contract_version") != "corpus-profile/v2"
            )
            or retryable_failure
        ):
            pending.append(item)
        else:
            outcomes_by_hash[source_sha256] = outcome
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _profile_isolated,
                item,
                timeout_seconds=timeout_seconds,
            ): item[0]
            for item in pending
        }
        for future in as_completed(futures):
            outcome = future.result()
            outcomes_by_hash[futures[future]] = outcome
            _append_checkpoint(checkpoint_path, outcome)
    outcomes = [
        outcomes_by_hash[source_sha256]
        for source_sha256, _ in representatives
    ]

    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    failures: list[dict[str, Any]] = []
    for outcome in outcomes:
        source_paths = [str(path) for path in sorted(paths_by_hash[str(outcome["source_sha256"])])]
        outcome["source_paths"] = source_paths
        outcome["source_path_count"] = len(source_paths)
        if outcome["status"] == "profiled":
            clusters[str(outcome["layout_fingerprint"])].append(outcome)
        else:
            failures.append(outcome)

    cluster_rows = []
    for fingerprint, members in sorted(
        clusters.items(),
        key=lambda item: (-sum(member["source_path_count"] for member in item[1]), item[0]),
    ):
        source_paths = sorted(path for member in members for path in member["source_paths"])
        header_sets = [set(member["headers"]) for member in members]
        shared_headers = sorted(set.intersection(*header_sets)) if header_sets else []
        cluster_rows.append(
            {
                "layout_fingerprint": fingerprint,
                "unique_content_count": len(members),
                "source_file_count": len(source_paths),
                "representative_path": members[0]["representative_path"],
                "source_paths": source_paths,
                "shared_headers": shared_headers,
                "header_variants": sorted(
                    {header for member in members for header in member["headers"]}
                ),
                "representative_evidence": {
                    "source_sha256": members[0]["source_sha256"],
                    "parser_name": members[0]["parser_name"],
                    "parser_version": members[0]["parser_version"],
                    "header_columns": members[0]["header_columns"],
                    "layout_candidates": members[0]["layout_candidates"],
                },
                "members": [
                    {
                        "source_sha256": member["source_sha256"],
                        "representative_path": member["representative_path"],
                        "source_paths": member["source_paths"],
                    }
                    for member in members
                ],
            }
        )

    return {
        "contract_version": "corpus-analysis/v1",
        "root": str(resolved_root),
        "matcher_version": MATCHER_VERSION,
        "summary": {
            "source_file_count": len(all_files),
            "structured_candidate_file_count": len(files),
            "ignored_file_count": len(ignored_files),
            "unique_content_count": len(paths_by_hash),
            "exact_duplicate_file_count": len(files) - len(paths_by_hash),
            "profiled_unique_content_count": len(outcomes) - len(failures),
            "failed_unique_content_count": len(failures),
            "layout_cluster_count": len(cluster_rows),
            "extension_counts": dict(
                sorted(Counter(path.suffix.lower() or "<none>" for path in all_files).items())
            ),
            "structured_extension_counts": dict(
                sorted(Counter(path.suffix.lower() for path in files).items())
            ),
            "ignored_extension_counts": dict(
                sorted(Counter(path.suffix.lower() or "<none>" for path in ignored_files).items())
            ),
        },
        "clusters": cluster_rows,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile and cluster structured documents.")
    parser.add_argument("root", type=Path, nargs="?")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--profile-one", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--source-sha256", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.profile_one is not None:
        if not arguments.source_sha256:
            parser.error("--source-sha256 is required with --profile-one")
        print(
            json.dumps(
                _profile_one(arguments.profile_one, arguments.source_sha256),
                ensure_ascii=False,
            )
        )
        return
    if arguments.root is None:
        parser.error("root is required")
    if arguments.workers < 1:
        parser.error("--workers must be positive")
    if arguments.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
    checkpoint = arguments.checkpoint
    if checkpoint is None and arguments.output is not None:
        checkpoint = arguments.output.with_suffix(arguments.output.suffix + ".checkpoint.jsonl")
    report = analyze_corpus(
        arguments.root,
        workers=arguments.workers,
        checkpoint_path=checkpoint,
        timeout_seconds=arguments.timeout_seconds,
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output is None:
        print(serialized)
        return
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(serialized + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
