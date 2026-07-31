from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict

_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_OFFICE_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_COLUMN = re.compile(r"^[A-Z]+")
_WHITESPACE = re.compile(r"\s+")


class QuestionBenchmarkError(ValueError):
    """Raised when the benchmark workbook cannot be read safely."""


class QuestionBenchmarkOccurrence(BaseModel):
    """One question occurrence with its immutable workbook coordinates."""

    model_config = ConfigDict(frozen=True)

    sheet_name: str
    source_row: int
    village_name: str
    question: str
    normalized_question: str
    reference_file: str
    duplicate_group: str
    historical: dict[str, str]


class QuestionBenchmarkCase(BaseModel):
    """A normalized question and every occurrence of it in the workbook."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    normalized_question: str
    occurrences: tuple[QuestionBenchmarkOccurrence, ...]


class QuestionBenchmarkCorpus(BaseModel):
    """Canonical in-memory view of the real-question benchmark workbook."""

    model_config = ConfigDict(frozen=True)

    workbook_path: str
    workbook_sha256: str
    sheet_count: int
    question_sheet_count: int
    question_row_count: int
    unique_question_count: int
    excluded_non_question_rows: int
    cases: tuple[QuestionBenchmarkCase, ...]


def normalize_question(value: str) -> str:
    """Normalize layout whitespace without changing business punctuation or values."""

    return _WHITESPACE.sub("", value.strip())


def load_question_benchmark(path: Path) -> QuestionBenchmarkCorpus:
    """Read question cells without loading or executing workbook styles or formulas."""

    resolved = path.resolve()
    if not resolved.is_file():
        raise QuestionBenchmarkError(f"benchmark workbook does not exist: {resolved}")
    workbook_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
    try:
        with ZipFile(resolved) as archive:
            shared_strings = _read_shared_strings(archive)
            sheets = _read_workbook_sheets(archive)
            occurrences: list[QuestionBenchmarkOccurrence] = []
            excluded_non_question_rows = 0
            question_sheet_count = 0
            for sheet_name, sheet_path in sheets:
                rows = _read_sheet_rows(archive, sheet_path, shared_strings)
                header_index = _question_header_index(rows)
                if header_index is None:
                    excluded_non_question_rows += _count_non_header_column_b(rows)
                    continue
                question_sheet_count += 1
                occurrences.extend(
                    _question_occurrences(
                        sheet_name=sheet_name,
                        rows=rows,
                        header_index=header_index,
                    )
                )
    except (BadZipFile, ElementTree.ParseError, KeyError, ValueError) as exc:
        raise QuestionBenchmarkError(
            f"benchmark workbook cannot be parsed safely: {resolved}"
        ) from exc

    grouped: dict[str, list[QuestionBenchmarkOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        grouped[occurrence.normalized_question].append(occurrence)
    cases = tuple(
        QuestionBenchmarkCase(
            case_id=_stable_id("question", normalized_question),
            normalized_question=normalized_question,
            occurrences=tuple(grouped[normalized_question]),
        )
        for normalized_question in sorted(grouped)
    )
    return QuestionBenchmarkCorpus(
        workbook_path=str(resolved),
        workbook_sha256=workbook_sha256,
        sheet_count=len(sheets),
        question_sheet_count=question_sheet_count,
        question_row_count=len(occurrences),
        unique_question_count=len(cases),
        excluded_non_question_rows=excluded_non_question_rows,
        cases=cases,
    )


def _read_shared_strings(archive: ZipFile) -> tuple[str, ...]:
    path = "xl/sharedStrings.xml"
    if path not in archive.namelist():
        return ()
    root = ElementTree.fromstring(archive.read(path))
    return tuple(
        "".join(node.text or "" for node in item.iter(f"{{{_SPREADSHEET_NS}}}t"))
        for item in root
    )


def _read_workbook_sheets(archive: ZipFile) -> tuple[tuple[str, str], ...]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(
        archive.read("xl/_rels/workbook.xml.rels")
    )
    targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships.findall(
            f"{{{_PACKAGE_REL_NS}}}Relationship"
        )
    }
    sheets_node = workbook.find(f"{{{_SPREADSHEET_NS}}}sheets")
    if sheets_node is None:
        raise QuestionBenchmarkError("workbook has no sheets")
    sheets: list[tuple[str, str]] = []
    for sheet in sheets_node:
        relationship_id = sheet.attrib[f"{{{_OFFICE_REL_NS}}}id"]
        target = targets[relationship_id]
        candidate = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
        if not candidate.startswith("xl/worksheets/") or ".." in candidate.split("/"):
            raise QuestionBenchmarkError("worksheet relationship escapes workbook")
        sheets.append((sheet.attrib["name"], candidate))
    return tuple(sheets)


def _read_sheet_rows(
    archive: ZipFile,
    sheet_path: str,
    shared_strings: tuple[str, ...],
) -> tuple[tuple[int, dict[str, str]], ...]:
    root = ElementTree.fromstring(archive.read(sheet_path))
    rows: list[tuple[int, dict[str, str]]] = []
    for row in root.findall(f".//{{{_SPREADSHEET_NS}}}sheetData/{{{_SPREADSHEET_NS}}}row"):
        values: dict[str, str] = {}
        for cell in row.findall(f"{{{_SPREADSHEET_NS}}}c"):
            reference = cell.attrib.get("r", "")
            match = _CELL_COLUMN.match(reference)
            if match is None:
                continue
            values[match.group(0)] = _cell_value(cell, shared_strings).strip()
        rows.append((int(row.attrib["r"]), values))
    return tuple(rows)


def _cell_value(
    cell: ElementTree.Element,
    shared_strings: tuple[str, ...],
) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(
            node.text or "" for node in cell.iter(f"{{{_SPREADSHEET_NS}}}t")
        )
    value = cell.find(f"{{{_SPREADSHEET_NS}}}v")
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        index = int(value.text)
        if index < 0 or index >= len(shared_strings):
            raise QuestionBenchmarkError("shared string index is out of range")
        return shared_strings[index]
    if cell_type == "b":
        return "TRUE" if value.text == "1" else "FALSE"
    # Formula nodes are never evaluated. If Excel stored a cached scalar, it is
    # returned as plain text; otherwise the cell remains empty.
    return value.text


def _question_header_index(
    rows: tuple[tuple[int, dict[str, str]], ...],
) -> int | None:
    for index, (_, values) in enumerate(rows[:10]):
        if "提问" in values.values():
            return index
    return None


def _count_non_header_column_b(
    rows: tuple[tuple[int, dict[str, str]], ...],
) -> int:
    if not rows:
        return 0
    return sum(bool(values.get("B", "").strip()) for _, values in rows[1:])


def _question_occurrences(
    *,
    sheet_name: str,
    rows: tuple[tuple[int, dict[str, str]], ...],
    header_index: int,
) -> list[QuestionBenchmarkOccurrence]:
    _, header_values = rows[header_index]
    headers = {
        column: value.strip()
        for column, value in header_values.items()
        if value.strip()
    }
    question_column = _column_for_header(headers, "提问")
    if question_column is None:
        raise QuestionBenchmarkError("question sheet is missing header: 提问")
    village_column = _column_for_header(headers, "所属村委", required=False)
    reference_column = _column_for_header(headers, "参考表格", required=False)
    historical_columns = {
        column: header
        for column, header in headers.items()
        if (
            "预期" in header
            or "符合" in header
            or header in {"用时", "问答用时"}
        )
    }
    current_village = ""
    current_reference = ""
    occurrences: list[QuestionBenchmarkOccurrence] = []
    for source_row, values in rows[header_index + 1 :]:
        village_value = values.get(village_column, "").strip() if village_column else ""
        if village_value:
            current_village = village_value
            current_reference = ""
        reference_value = (
            values.get(reference_column, "").strip() if reference_column else ""
        )
        if reference_value:
            current_reference = reference_value
        question = values.get(question_column, "").strip()
        if not question:
            continue
        normalized = normalize_question(question)
        duplicate_group = _stable_id("duplicate", normalized)
        historical = {
            header: values.get(column, "").strip()
            for column, header in historical_columns.items()
            if values.get(column, "").strip()
        }
        occurrences.append(
            QuestionBenchmarkOccurrence(
                sheet_name=sheet_name,
                source_row=source_row,
                village_name=current_village,
                question=question,
                normalized_question=normalized,
                reference_file=current_reference,
                duplicate_group=duplicate_group,
                historical=historical,
            )
        )
    return occurrences


def _column_for_header(
    headers: dict[str, str],
    expected: str,
    *,
    required: bool = True,
) -> str | None:
    for column, header in headers.items():
        if header == expected:
            return column
    if required:
        raise QuestionBenchmarkError(f"question sheet is missing header: {expected}")
    return None


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect the immutable real-question benchmark workbook."
    )
    parser.add_argument("workbook", type=Path)
    args = parser.parse_args()
    corpus = load_question_benchmark(args.workbook)
    summary: dict[str, Any] = {
        "workbook_path": corpus.workbook_path,
        "workbook_sha256": corpus.workbook_sha256,
        "sheet_count": corpus.sheet_count,
        "question_sheet_count": corpus.question_sheet_count,
        "question_row_count": corpus.question_row_count,
        "unique_question_count": corpus.unique_question_count,
        "excluded_non_question_rows": corpus.excluded_non_question_rows,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
