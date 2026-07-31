from __future__ import annotations

from pathlib import Path
from typing import Protocol

from village_insight.parsing.contracts import DetectionResult, DocumentFormat, WorkbookProfile


class ParserAdapter(Protocol):
    name: str

    def supports(self, document_format: DocumentFormat) -> bool: ...

    def profile(self, path: Path, detection: DetectionResult) -> WorkbookProfile: ...
