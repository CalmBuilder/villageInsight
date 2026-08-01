import json
from pathlib import Path

from village_insight.templates.regression import evaluate_real_file_regression


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_real_file_regression_distinguishes_route_hit_from_executable_coverage(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "generation-manifest.json",
        {
            "generation_sha256": "generation",
            "summary": {"region_template_count": 19},
        },
    )
    _write(
        tmp_path / "coverage-manifest.json",
        {
            "coverage": [
                {"source_path": "/data/known.xlsx", "layout_fingerprint": "a"},
                {"source_path": "/data/unresolved.xlsx", "layout_fingerprint": "b"},
            ]
        },
    )
    _write(
        tmp_path / "workbook-routes.json",
        [
            {"code": "route.a", "route_fingerprint": "a", "unresolved_regions": []},
            {
                "code": "route.b",
                "route_fingerprint": "b",
                "unresolved_regions": [{"region_id": "region-1"}],
            },
        ],
    )
    corpus = {
        "contract_version": "corpus-analysis/v1",
        "clusters": [
            {"layout_fingerprint": "a", "source_paths": ["/data/known.xlsx"]},
            {"layout_fingerprint": "b", "source_paths": ["/data/unresolved.xlsx"]},
        ],
        "failures": [],
    }

    report = evaluate_real_file_regression(
        seed_directory=tmp_path,
        fresh_corpus_report=corpus,
    )

    assert report["metrics"]["exact_workbook_route_hit_basis_points"] == 10_000
    assert report["metrics"]["fully_resolved_workbook_route_hit_basis_points"] == 5_000
    assert report["metrics"]["executable_region_template_basis_points"] == 9_500
    assert report["acceptance"] == {
        "known_real_file_route_hit_passed": True,
        "fully_resolved_route_hit_passed": False,
        "executable_region_template_passed": True,
    }
