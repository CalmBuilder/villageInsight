from pathlib import Path

from openpyxl import Workbook

from village_insight.corpus import analyze_corpus


def _write_workbook(path: Path, rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()


def test_corpus_analysis_deduplicates_and_clusters_layouts(tmp_path: Path) -> None:
    first = tmp_path / "first.xlsx"
    duplicate = tmp_path / "duplicate.xlsx"
    longer = tmp_path / "longer.xlsx"
    _write_workbook(first, [["姓名", "人数"], ["张三", 2]])
    duplicate.write_bytes(first.read_bytes())
    _write_workbook(
        longer,
        [["姓名", "人数"], ["张三", 2], ["李四", 3], ["王五", 4]],
    )
    (tmp_path / "broken.xlsx").write_bytes(b"not a workbook")
    (tmp_path / "ignored.md").write_text("not structured", encoding="utf-8")

    checkpoint = tmp_path / "checkpoint.jsonl"
    report = analyze_corpus(tmp_path, workers=2, checkpoint_path=checkpoint)

    assert report["summary"] == {
        "source_file_count": 5,
        "structured_candidate_file_count": 4,
        "ignored_file_count": 1,
        "unique_content_count": 3,
        "exact_duplicate_file_count": 1,
        "profiled_unique_content_count": 2,
        "failed_unique_content_count": 1,
        "layout_cluster_count": 1,
        "extension_counts": {".md": 1, ".xlsx": 4},
        "structured_extension_counts": {".xlsx": 4},
        "ignored_extension_counts": {".md": 1},
    }
    assert report["clusters"][0]["source_file_count"] == 3
    assert report["clusters"][0]["unique_content_count"] == 2
    assert report["clusters"][0]["shared_headers"] == ["人数", "姓名"]
    assert len(checkpoint.read_text(encoding="utf-8").splitlines()) == 3

    resumed = analyze_corpus(tmp_path, workers=1, checkpoint_path=checkpoint)

    assert resumed == report
    assert len(checkpoint.read_text(encoding="utf-8").splitlines()) == 3
