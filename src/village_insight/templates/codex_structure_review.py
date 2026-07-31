from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from village_insight.parsing.contracts import (
    HeaderCandidate,
    RegionCandidate,
    SheetProfile,
)
from village_insight.parsing.router import ParserRouter

CONTRACT_VERSION = "codex-structure-review/v1"
REVIEWER_VERSION = "codex-four-layer-source-review/v1"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _header(
    sheet: SheetProfile,
    region: RegionCandidate,
    rows: list[int],
) -> HeaderCandidate | None:
    candidates = [
        candidate
        for candidate in sheet.header_candidates
        if candidate.region_id == region.id
    ]
    exact = [candidate for candidate in candidates if candidate.header_rows == rows]
    if exact:
        return max(exact, key=lambda candidate: candidate.confidence)
    return None


def _form_fields(sheet: SheetProfile) -> list[dict[str, Any]]:
    cells = {(cell.row, cell.column): cell for cell in sheet.cells}
    selectors: list[dict[str, Any]] = []
    for row in range(3, 12):
        for label_column in (1, 3, 6):
            label = cells.get((row, label_column))
            if label is None or not isinstance(label.display_value, str):
                continue
            text = " ".join(label.display_value.split()).strip()
            if not text:
                continue
            selectors.append(
                {
                    "label": text,
                    "label_coordinate": label.coordinate,
                    "label_row": row,
                    "label_column": label_column,
                    "value_row": row,
                    "value_column": label_column + 1,
                    "required": text in {"姓 名", "姓名", "身份证号"},
                }
            )
    return selectors


def _standard_decision(
    *,
    sheet: SheetProfile,
    region: RegionCandidate,
    header_rows: list[int],
    data_start_row: int,
    data_end_row: int,
    excluded_rows: list[int] | None = None,
    layout_mode: str = "table",
) -> dict[str, Any]:
    header = _header(sheet, region, header_rows)
    if header is None:
        manual_columns = [
            {
                "column": cell.column,
                "label": " ".join(str(cell.display_value).split()),
                "source_cell_id": cell.id,
                "coordinate": cell.coordinate,
            }
            for cell in sheet.cells
            if cell.row == header_rows[-1] and cell.display_value not in (None, "")
        ]
        return {
            "action": "create_template",
            "layout_mode": "explicit_header_table",
            "classification": "table",
            "header_candidate_id": None,
            "header_rows": header_rows,
            "manual_columns": manual_columns,
            "data_start_row": data_start_row,
            "data_end_row": data_end_row,
            "excluded_rows": excluded_rows or [],
            "confidence": 1.0,
            "basis": "codex_source_cell_review",
        }
    return {
        "action": "create_template",
        "layout_mode": layout_mode,
        "classification": "matrix" if layout_mode == "matrix" else "table",
        "header_candidate_id": header.id,
        "header_rows": header_rows,
        "data_start_row": data_start_row,
        "data_end_row": data_end_row,
        "excluded_rows": excluded_rows or [],
        "confidence": 1.0,
        "basis": "codex_source_cell_review",
    }


def _decision_for(
    *,
    path: Path,
    sheet: SheetProfile,
    region: RegionCandidate,
) -> dict[str, Any]:
    name = path.name
    if "稳定就业补贴申报表" in name:
        if sheet.index in {0, 1, 3}:
            header = _header(sheet, region, [3])
            if header is None:
                raise ValueError(f"form header row 3 is unavailable: {path}#{sheet.name}")
            return {
                "action": "create_template",
                "layout_mode": "form",
                "classification": "form",
                "header_candidate_id": header.id,
                "header_rows": [3],
                "data_start_row": 3,
                "data_end_row": 11,
                "excluded_rows": [],
                "field_selectors": _form_fields(sheet),
                "confidence": 1.0,
                "basis": "codex_label_value_pair_review",
            }
        return {
            "action": "discard_false_positive",
            "reason": "data_rows_inside_an_existing_stable_employment_table",
            "confidence": 1.0,
            "basis": "codex_overlap_review",
        }
    if "11月6日村委会会议决议医保任务数" in name:
        if sheet.index == 0 and region.bounds.min_column == 7:
            start = max(region.bounds.min_row, 13)
            header = _header(sheet, region, [region.bounds.min_row])
            if header is None:
                raise ValueError(
                    f"headerless evidence anchor is unavailable: {path}#{region.id}"
                )
            return {
                "action": "create_template",
                "layout_mode": "headerless_table",
                "classification": "table",
                "header_candidate_id": header.id,
                "header_rows": [],
                "data_start_row": start,
                "data_end_row": region.bounds.max_row,
                "excluded_rows": [],
                "synthetic_columns": [
                    {"column": 7, "label": "村干部姓名", "required": True},
                    {"column": 8, "label": "任务数", "required": True},
                ],
                "confidence": 1.0,
                "basis": "codex_headerless_side_table_review",
            }
        return {
            "action": "discard_false_positive",
            "reason": "calculation_cells_inside_or_beside_an_existing_table",
            "confidence": 1.0,
            "basis": "codex_overlap_review",
        }
    if "七里坝村老年协会" in name and sheet.index == 1:
        return _standard_decision(
            sheet=sheet,
            region=region,
            header_rows=[3],
            data_start_row=4,
            data_end_row=11,
        )
    if "村干部基本报酬" in name and sheet.index == 2:
        return _standard_decision(
            sheet=sheet,
            region=region,
            header_rows=[3],
            data_start_row=4,
            data_end_row=4,
        )
    if "党员缴纳党费情况登记表" in name:
        return _standard_decision(
            sheet=sheet,
            region=region,
            header_rows=[4, 5],
            data_start_row=6,
            data_end_row=54,
            layout_mode="matrix",
        )
    if "矛盾纠纷管理台账" in name:
        return _standard_decision(
            sheet=sheet,
            region=region,
            header_rows=[2],
            data_start_row=3,
            data_end_row=2,
        )
    return {
        "action": "discard_false_positive",
        "reason": "candidate_is_data_title_footer_or_overlap_of_an_existing_region",
        "confidence": 1.0,
        "basis": "codex_overlap_review",
    }


def build_review(
    *,
    package_directory: Path,
    hermes_evidence_path: Path,
) -> dict[str, Any]:
    routes = _read_json(package_directory / "workbook-routes.json")
    hermes_payload = _read_json(hermes_evidence_path)
    hermes_by_route = {
        str(row["route_code"]): row
        for row in hermes_payload.get("decisions", [])
        if isinstance(row, dict) and row.get("route_code")
    }
    rows: list[dict[str, Any]] = []
    for route in routes:
        unresolved = route.get("unresolved_regions", [])
        if not unresolved:
            continue
        route_code = str(route["code"])
        hermes_route = hermes_by_route.get(route_code)
        if hermes_route is None:
            raise ValueError(f"Hermes evidence is missing for {route_code}")
        path = Path(str(hermes_route["source_path"]))
        profile = ParserRouter().profile(path)
        region_lookup = {
            region.id: (sheet, region)
            for sheet in profile.sheets
            for region in sheet.region_candidates
        }
        hermes_regions = {
            str(row["unresolved_region"]["region_id"]): row
            for row in hermes_route["region_decisions"]
        }
        decisions: list[dict[str, Any]] = []
        for unresolved_region in unresolved:
            region_id = str(unresolved_region["region_id"])
            source = region_lookup.get(region_id)
            if source is None:
                raise ValueError(f"source Region is unavailable: {region_id}")
            sheet, region = source
            decision = _decision_for(path=path, sheet=sheet, region=region)
            decisions.append(
                {
                    "sheet_index": sheet.index,
                    "sheet_name": sheet.name,
                    "region_candidate_id": region.id,
                    "region_range": region.bounds.range,
                    "codex_decision": decision,
                    "hermes_second_opinion": hermes_regions.get(region_id),
                }
            )
        rows.append(
            {
                "route_code": route_code,
                "source_path": str(path),
                "source_sha256": profile.source_sha256,
                "decisions": decisions,
            }
        )
    flat: list[dict[str, Any]] = [
        decision
        for row in rows
        for decision in row["decisions"]
        if isinstance(decision, dict)
    ]
    return {
        "contract_version": CONTRACT_VERSION,
        "reviewer": "codex",
        "reviewer_version": REVIEWER_VERSION,
        "authority": {
            "primary_analysis": "codex_source_review",
            "second_opinion": "hermes",
            "final_gate": "deterministic_backend_validation",
        },
        "summary": {
            "route_count": len(rows),
            "decision_count": len(flat),
            "create_template_count": sum(
                decision["codex_decision"]["action"] == "create_template"
                for decision in flat
            ),
            "discard_false_positive_count": sum(
                decision["codex_decision"]["action"] == "discard_false_positive"
                for decision in flat
            ),
        },
        "routes": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-directory", type=Path, required=True)
    parser.add_argument("--hermes-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    review = build_review(
        package_directory=arguments.package_directory,
        hermes_evidence_path=arguments.hermes_evidence,
    )
    _write_json(arguments.output, review)
    print(json.dumps(review["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
