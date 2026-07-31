from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REGRESSION_CONTRACT_VERSION = "four-layer-real-file-regression/v1"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_real_file_regression(
    *,
    seed_directory: Path,
    fresh_corpus_report: dict[str, Any],
) -> dict[str, Any]:
    generation = _read_json(seed_directory / "generation-manifest.json")
    coverage_document = _read_json(seed_directory / "coverage-manifest.json")
    routes = _read_json(seed_directory / "workbook-routes.json")
    expected_by_path = {
        str(row["source_path"]): (
            str(row["layout_fingerprint"]),
            (
                str(row["workbook_route_code"])
                if row.get("workbook_route_code")
                else None
            ),
        )
        for row in coverage_document["coverage"]
    }
    route_by_code = {
        str(route["code"]): route
        for route in routes
    }
    route_by_fingerprint = {
        str(route["route_fingerprint"]): route
        for route in routes
    }
    fresh_by_path: dict[str, str] = {}
    for cluster in fresh_corpus_report["clusters"]:
        fingerprint = str(cluster["layout_fingerprint"])
        for source_path in cluster["source_paths"]:
            fresh_by_path[str(source_path)] = fingerprint
    failed_paths = {
        str(source_path)
        for failure in fresh_corpus_report["failures"]
        for source_path in failure.get(
            "source_paths",
            [failure["representative_path"]],
        )
    }

    rows: list[dict[str, Any]] = []
    for source_path, (
        expected_fingerprint,
        expected_route_code,
    ) in sorted(expected_by_path.items()):
        actual_fingerprint = fresh_by_path.get(source_path)
        route = (
            route_by_code.get(expected_route_code)
            if expected_route_code is not None
            else route_by_fingerprint.get(actual_fingerprint or "")
        )
        if source_path in failed_paths:
            decision = "fresh_profile_failed"
        elif actual_fingerprint is None:
            decision = "source_missing_from_fresh_report"
        elif actual_fingerprint != expected_fingerprint:
            decision = "layout_fingerprint_changed"
        elif route is None:
            decision = "workbook_route_missing"
        else:
            decision = "exact_route_hit"
        rows.append(
            {
                "source_path": source_path,
                "expected_layout_fingerprint": expected_fingerprint,
                "actual_layout_fingerprint": actual_fingerprint,
                "workbook_route_code": route["code"] if route is not None else None,
                "route_has_unresolved_regions": bool(
                    route is not None and route.get("unresolved_regions")
                ),
                "decision": decision,
            }
        )

    total = len(rows)
    exact_route_hits = sum(row["decision"] == "exact_route_hit" for row in rows)
    fully_resolved_route_hits = sum(
        row["decision"] == "exact_route_hit"
        and not row["route_has_unresolved_regions"]
        for row in rows
    )
    unresolved_regions = sum(
        len(route.get("unresolved_regions", []))
        for route in routes
    )
    seeded_regions = int(generation["summary"]["region_template_count"])
    region_denominator = seeded_regions + unresolved_regions
    exact_route_basis_points = (
        round(10_000 * exact_route_hits / total) if total else 0
    )
    fully_resolved_basis_points = (
        round(10_000 * fully_resolved_route_hits / total) if total else 0
    )
    executable_region_basis_points = (
        round(10_000 * seeded_regions / region_denominator)
        if region_denominator
        else 0
    )
    return {
        "contract_version": REGRESSION_CONTRACT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "seed_generation_sha256": generation["generation_sha256"],
        "fresh_corpus_contract_version": fresh_corpus_report["contract_version"],
        "acceptance_threshold_basis_points": 9_500,
        "metrics": {
            "expected_real_file_count": total,
            "fresh_profiled_real_file_count": sum(
                source_path in fresh_by_path for source_path in expected_by_path
            ),
            "exact_workbook_route_hit_count": exact_route_hits,
            "exact_workbook_route_hit_basis_points": exact_route_basis_points,
            "fully_resolved_workbook_route_hit_count": fully_resolved_route_hits,
            "fully_resolved_workbook_route_hit_basis_points": fully_resolved_basis_points,
            "seeded_region_template_count": seeded_regions,
            "unresolved_region_count": unresolved_regions,
            "executable_region_template_basis_points": executable_region_basis_points,
            "decision_counts": dict(
                sorted(Counter(str(row["decision"]) for row in rows).items())
            ),
        },
        "acceptance": {
            "known_real_file_route_hit_passed": exact_route_basis_points >= 9_500,
            "fully_resolved_route_hit_passed": fully_resolved_basis_points >= 9_500,
            "executable_region_template_passed": executable_region_basis_points >= 9_500,
        },
        "scope_note": (
            "This regression reparses known real files and verifies deterministic route reuse. "
            "It does not represent leave-one-village-out generalization."
        ),
        "files": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate four-layer templates against a freshly parsed real corpus."
    )
    parser.add_argument("--seed-directory", type=Path, required=True)
    parser.add_argument("--corpus-report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = evaluate_real_file_regression(
        seed_directory=arguments.seed_directory,
        fresh_corpus_report=_read_json(arguments.corpus_report),
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is None:
        print(serialized, end="")
        return
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(serialized, encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
