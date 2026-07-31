from __future__ import annotations

import csv
from pathlib import Path

from openpyxl.utils import get_column_letter

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


class CsvAdapter:
    name = "stdlib-csv"
    version = "1"

    def supports(self, document_format: DocumentFormat) -> bool:
        return document_format == "csv"

    def profile(self, path: Path, detection: DetectionResult) -> WorkbookProfile:
        source_sha256 = file_sha256(path)
        parent_workbook_id = workbook_id(source_sha256)
        parent_sheet_id = sheet_id(parent_workbook_id, 0)
        raw = path.read_bytes()
        encoding = detection.signature.rsplit(":", maxsplit=1)[-1]
        text = raw.decode(encoding)
        dialect = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|")
        cells: list[CellEvidence] = []
        for row_index, row in enumerate(csv.reader(text.splitlines(), dialect), start=1):
            for column_index, value in enumerate(row, start=1):
                if value == "":
                    continue
                cells.append(
                    CellEvidence(
                        id=cell_id(parent_sheet_id, row_index, column_index),
                        coordinate=f"{get_column_letter(column_index)}{row_index}",
                        row=row_index,
                        column=column_index,
                        raw_value=value,
                        display_value=value,
                        data_type="string",
                    )
                )
        regions = build_region_candidates(parent_sheet_id, cells, [])
        headers = build_header_candidates(parent_sheet_id, cells, [], regions)
        sheet = SheetProfile(
            id=parent_sheet_id,
            name="CSV",
            index=0,
            hidden=False,
            declared_bounds=observed_bounds(cells),
            observed_bounds=observed_bounds(cells),
            cells=cells,
            merges=[],
            row_properties=[],
            column_properties=[],
            region_candidates=regions,
            header_candidates=headers,
        )
        return WorkbookProfile(
            workbook_id=parent_workbook_id,
            source_sha256=source_sha256,
            parser_name=self.name,
            parser_version=f"{self.version}+layout-v3",
            file_name=path.name,
            detection=detection,
            sheets=[sheet],
            warnings=list(detection.warnings),
        )
