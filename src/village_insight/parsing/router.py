from __future__ import annotations

from pathlib import Path

from village_insight.parsing.adapters.base import ParserAdapter
from village_insight.parsing.adapters.calamine_adapter import CalamineXlsAdapter
from village_insight.parsing.adapters.csv_adapter import CsvAdapter
from village_insight.parsing.adapters.openpyxl_adapter import OpenPyxlAdapter
from village_insight.parsing.contracts import WorkbookProfile
from village_insight.parsing.detection import detect_document


class UnsupportedDocumentError(ValueError):
    def __init__(self, document_format: str) -> None:
        super().__init__(f"no parser adapter registered for format: {document_format}")
        self.code = "PARSER_ADAPTER_UNAVAILABLE"


class ParserRouter:
    def __init__(self, adapters: tuple[ParserAdapter, ...] | None = None) -> None:
        self.adapters = adapters or (
            OpenPyxlAdapter(),
            CalamineXlsAdapter(),
            CsvAdapter(),
        )

    def profile(self, path: Path) -> WorkbookProfile:
        detection = detect_document(path)
        for adapter in self.adapters:
            if adapter.supports(detection.format):
                return adapter.profile(path, detection)
        raise UnsupportedDocumentError(detection.format)
