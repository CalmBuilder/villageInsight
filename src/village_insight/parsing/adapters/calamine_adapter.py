from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl.utils import get_column_letter
from python_calamine import CalamineWorkbook

from village_insight.parsing.adapters.openpyxl_adapter import json_value
from village_insight.parsing.candidates import (
    build_header_candidates,
    build_region_candidates,
    observed_bounds,
)
from village_insight.parsing.contracts import (
    CellEvidence,
    DetectionResult,
    DocumentFormat,
    SheetProfile,
    WorkbookProfile,
)
from village_insight.parsing.identity import cell_id, file_sha256, sheet_id, workbook_id


class CalamineXlsAdapter:
    name = "python-calamine"

    def supports(self, document_format: DocumentFormat) -> bool:
        return document_format == "xls"

    def profile(self, path: Path, detection: DetectionResult) -> WorkbookProfile:
        source_sha256 = file_sha256(path)
        parent_workbook_id = workbook_id(source_sha256)
        workbook = CalamineWorkbook.from_path(path)
        sheets: list[SheetProfile] = []
        for index, name in enumerate(workbook.sheet_names):
            parent_sheet_id = sheet_id(parent_workbook_id, index)
            rows: list[list[Any]] = workbook.get_sheet_by_name(name).to_python()
            cells: list[CellEvidence] = []
            for row_index, row in enumerate(rows, start=1):
                for column_index, value in enumerate(row, start=1):
                    if value is None or value == "":
                        continue
                    normalized = json_value(value)
                    cells.append(
                        CellEvidence(
                            id=cell_id(parent_sheet_id, row_index, column_index),
                            coordinate=f"{get_column_letter(column_index)}{row_index}",
                            row=row_index,
                            column=column_index,
                            raw_value=normalized,
                            display_value=normalized,
                            data_type=type(value).__name__,
                        )
                    )
            regions = build_region_candidates(parent_sheet_id, cells, [])
            headers = build_header_candidates(parent_sheet_id, cells, [], regions)
            sheets.append(
                SheetProfile(
                    id=parent_sheet_id,
                    name=name,
                    index=index,
                    hidden=False,
                    declared_bounds=observed_bounds(cells),
                    observed_bounds=observed_bounds(cells),
                    cells=cells,
                    merges=[],
                    row_properties=[],
                    column_properties=[],
                    region_candidates=regions,
                    header_candidates=headers,
                    warnings=[
                        "XLS_LIMITATION: formulas, merged ranges, styles and hidden "
                        "state are not exposed by the calamine evidence adapter"
                    ],
                )
            )
        return WorkbookProfile(
            workbook_id=parent_workbook_id,
            source_sha256=source_sha256,
            parser_name=self.name,
            parser_version="0.8.2+layout-v3",
            file_name=path.name,
            detection=detection,
            sheets=sheets,
            warnings=list(detection.warnings),
        )
