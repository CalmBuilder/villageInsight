from pathlib import Path

from openpyxl import Workbook

from village_insight.corpus_baseline import build_baseline


def _write_workbook(path: Path, *, value: str) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "受控名称"
    sheet.append(["姓名", "人数"])
    sheet.append([value, 1])
    workbook.save(path)
    workbook.close()


def test_baseline_is_stable_and_does_not_copy_cell_or_sheet_values(
    tmp_path: Path,
) -> None:
    village = tmp_path / "测试村"
    village.mkdir()
    source = village / "人员.xlsx"
    _write_workbook(source, value="敏感姓名样例")

    first_manifest, first_physical = build_baseline(tmp_path)
    second_manifest, second_physical = build_baseline(tmp_path)

    assert first_manifest == second_manifest
    assert first_physical == second_physical
    serialized = str(first_physical)
    assert "敏感姓名样例" not in serialized
    assert "受控名称" not in serialized
    assert first_manifest["summary"]["file_count"] == 1
    assert first_manifest["summary"]["profiled_count"] == 1
