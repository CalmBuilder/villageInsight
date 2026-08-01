from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DocumentFormat = Literal["xlsx", "xls", "excel_html", "csv"]


class Bounds(BaseModel):
    min_row: int = Field(ge=1)
    min_column: int = Field(ge=1)
    max_row: int = Field(ge=1)
    max_column: int = Field(ge=1)
    range: str


class DetectionResult(BaseModel):
    format: DocumentFormat
    media_type: str
    signature: str
    extension: str
    extension_matches: bool
    warnings: list[str] = Field(default_factory=list)


class CellEvidence(BaseModel):
    id: str
    coordinate: str
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    raw_value: Any = None
    display_value: Any = None
    formula: str | None = None
    data_type: str | None = None
    number_format: str | None = None
    style_ref: str | None = None
    hidden: bool = False


class MergeEvidence(BaseModel):
    id: str
    range: str
    anchor_cell_id: str
    anchor: str
    anchor_value: Any = None


class RowProperties(BaseModel):
    row: int = Field(ge=1)
    hidden: bool = False
    height: float | None = None


class ColumnProperties(BaseModel):
    column: int = Field(ge=1)
    hidden: bool = False
    width: float | None = None


class RegionCandidate(BaseModel):
    id: str
    kind: Literal["table", "form", "unknown"] = "unknown"
    bounds: Bounds
    nonempty_cell_ids: list[str]
    density: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    source: str


class HeaderColumnCandidate(BaseModel):
    column: int = Field(ge=1)
    source_column_id: str
    header_path: list[str]
    evidence_cell_ids: list[str]


class HeaderCandidate(BaseModel):
    id: str
    region_id: str
    header_rows: list[int]
    columns: list[HeaderColumnCandidate]
    confidence: float = Field(ge=0, le=1)
    source: str


class SheetProfile(BaseModel):
    id: str
    name: str
    index: int = Field(ge=0)
    hidden: bool
    declared_bounds: Bounds | None
    observed_bounds: Bounds | None
    cells: list[CellEvidence]
    merges: list[MergeEvidence]
    row_properties: list[RowProperties]
    column_properties: list[ColumnProperties]
    region_candidates: list[RegionCandidate]
    header_candidates: list[HeaderCandidate]
    warnings: list[str] = Field(default_factory=list)


class WorkbookProfile(BaseModel):
    contract_version: Literal["workbook-profile/v2"] = "workbook-profile/v2"
    workbook_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_name: str
    parser_version: str
    file_name: str
    detection: DetectionResult
    sheets: list[SheetProfile]
    warnings: list[str] = Field(default_factory=list)
