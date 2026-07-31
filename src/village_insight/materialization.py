from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    ApprovedImportPlan,
    DatasetRecord,
    DocumentProfile,
    ImportExecution,
    QualityIssue,
    RecordIndexValue,
    RecordValueLineage,
    RegionTemplateVersion,
    SemanticField,
    SemanticFieldVersion,
    TemplateVersion,
    utcnow,
)
from village_insight.parsing.contracts import (
    CellEvidence,
    HeaderCandidate,
    RegionCandidate,
    WorkbookProfile,
)
from village_insight.parsing.profile_storage import load_workbook_profile
from village_insight.templates.contracts import TemplateDefinition


class MaterializationError(ValueError):
    code = "MATERIALIZATION_FAILED"


class RequiredValueMissingError(MaterializationError):
    code = "REQUIRED_VALUE_MISSING"


class DuplicateIdentityError(MaterializationError):
    code = "DUPLICATE_RECORD_IDENTITY"


_INVISIBLE_NUMERIC_CHARACTERS = str.maketrans(
    {
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\u2060": "",
        "\ufeff": "",
    }
)
_SPREADSHEET_ERROR_PATTERN = re.compile(
    r"^#(?:NULL!|DIV/0!|VALUE!|REF!|NAME\?|NUM!|N/A|GETTING_DATA)$",
    re.IGNORECASE,
)
_FORM_INSTRUCTION_PATTERN = re.compile(
    r"^[（(]?(?:[一二三四五六七八九十]+|\d+)[）).、]"
)
_UNFILLED_FORM_LABELS = {
    "姓名",
    "村",
    "联系电话",
    "联系电话:",
    "联系电话：",
    "责任追究",
}
_UNFILLED_FORM_MARKERS = (
    "签名",
    "本人承诺",
    "事由填写",
)
_UNFILLED_FORM_FIELD_SUFFIXES = (
    "姓名",
)


def _numeric_text(value: Any) -> str:
    return str(value).translate(_INVISIBLE_NUMERIC_CHARACTERS).replace(",", "").strip()


def _is_spreadsheet_error(value: Any) -> bool:
    return isinstance(value, str) and bool(
        _SPREADSHEET_ERROR_PATTERN.fullmatch(value.strip())
    )


def _region_definition(
    version: RegionTemplateVersion,
) -> TemplateDefinition:
    identity_codes = version.identity_policy.get("field_codes", [])
    return TemplateDefinition(
        domain=version.domain,
        region_kind=version.region_kind,
        record_type=version.record_type,
        record_grain=version.record_grain,
        field_bindings=version.field_bindings,
        identity_field_codes=(
            [str(code) for code in identity_codes] if isinstance(identity_codes, list) else []
        ),
    )


def _normalized_value(data_type: str, value: Any) -> tuple[str, Any]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "empty/v1", None
    try:
        if data_type == "text":
            return "text/v1", str(value).strip()
        if data_type == "integer":
            decimal = Decimal(_numeric_text(value))
            if decimal != decimal.to_integral_value():
                raise ValueError("not an integer")
            return "integer/v1", int(decimal)
        if data_type == "decimal":
            return "decimal/v1", Decimal(_numeric_text(value))
        if data_type == "boolean":
            normalized = str(value).strip().lower()
            if normalized in {"true", "1", "是", "有", "yes"}:
                return "boolean/v1", True
            if normalized in {"false", "0", "否", "无", "no"}:
                return "boolean/v1", False
            raise ValueError("not a boolean")
        if data_type == "date":
            parsed = (
                value.date()
                if isinstance(value, datetime)
                else value
                if isinstance(value, date)
                else date.fromisoformat(str(value).strip())
            )
            return "date-iso/v1", parsed
        if data_type == "datetime":
            parsed_datetime = (
                value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
            )
            return "datetime-iso/v1", parsed_datetime
    except (InvalidOperation, ValueError) as exc:
        raise MaterializationError(f"cannot normalize {value!r} as {data_type}") from exc
    raise MaterializationError(f"unsupported semantic data type: {data_type}")


def _typed_values(data_type: str, value: Any) -> dict[str, Any]:
    values = {
        "text_value": None,
        "integer_value": None,
        "decimal_value": None,
        "boolean_value": None,
        "date_value": None,
        "datetime_value": None,
    }
    if value is not None:
        values[f"{data_type}_value"] = value
    return values


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _is_present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _is_unfilled_form_value(value: Any) -> bool:
    """Identify preprinted form prompts that do not contain entered business data."""
    if not isinstance(value, str):
        return False
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return True
    if normalized in _UNFILLED_FORM_LABELS:
        return True
    if len(normalized) <= 12 and normalized.endswith(_UNFILLED_FORM_FIELD_SUFFIXES):
        return True
    if _FORM_INSTRUCTION_PATTERN.match(normalized):
        return True
    if any(marker in normalized for marker in _UNFILLED_FORM_MARKERS):
        return True
    if (
        "起止时间" in normalized
        and "月" in normalized
        and "日" in normalized
        and not re.search(r"\d{1,4}", normalized)
    ):
        return True
    return False


def _is_unfilled_form_row(raw_columns: dict[str, dict[str, Any]]) -> bool:
    values = [
        column.get("source_cell", {}).get("display_value")
        for column in raw_columns.values()
    ]
    return bool(values) and all(_is_unfilled_form_value(value) for value in values)


def _resolve_column(
    profile: WorkbookProfile,
    source_column_id: str,
    header_path: list[str],
    *,
    expected_sheet_id: str | None = None,
    expected_region_id: str | None = None,
    expected_header_id: str | None = None,
) -> tuple[str, HeaderCandidate, RegionCandidate, int]:
    matches: list[tuple[str, HeaderCandidate, RegionCandidate, int]] = []
    normalized_path = [" ".join(part.split()) for part in header_path]
    for sheet in profile.sheets:
        if expected_sheet_id is not None and sheet.id != expected_sheet_id:
            continue
        regions = {region.id: region for region in sheet.region_candidates}
        for candidate in sheet.header_candidates:
            if expected_header_id is not None and candidate.id != expected_header_id:
                continue
            region = regions.get(candidate.region_id)
            if region is None or (
                expected_region_id is not None and region.id != expected_region_id
            ):
                continue
            for column in candidate.columns:
                if (
                    column.source_column_id == source_column_id
                    and [" ".join(part.split()) for part in column.header_path] == normalized_path
                ):
                    matches.append((sheet.id, candidate, region, column.column))
    if not matches:
        raise MaterializationError(
            f"approved source column is absent from current evidence: {source_column_id}"
        )
    return min(matches, key=lambda item: len(item[1].header_rows))


def materialize_plan(
    database: Session,
    plan_id: uuid.UUID,
) -> ImportExecution:
    existing = database.scalar(
        select(ImportExecution).where(ImportExecution.approved_plan_id == plan_id)
    )
    if existing is not None:
        return existing
    plan = database.get(ApprovedImportPlan, plan_id)
    if plan is None:
        raise MaterializationError("approved import plan not found")
    batch = plan.item.batch
    if (
        plan.item.tenant_id,
        plan.item.administrative_unit_id,
        plan.item.created_by_user_id,
    ) != (
        batch.tenant_id,
        batch.administrative_unit_id,
        batch.created_by_user_id,
    ):
        raise MaterializationError("ingestion item scope does not match its batch")
    profile_record = database.get(DocumentProfile, plan.item_id)
    version = (
        database.scalar(
            select(TemplateVersion).where(
                TemplateVersion.template_id == plan.template_id,
                TemplateVersion.version == plan.template_version,
            )
        )
        if plan.template_id is not None and plan.template_version is not None
        else None
    )
    primary_region_version = (
        database.scalar(
            select(RegionTemplateVersion).where(
                RegionTemplateVersion.region_template_id == plan.primary_region_template_id,
                RegionTemplateVersion.version == plan.primary_region_template_version,
            )
        )
        if plan.primary_region_template_id is not None
        and plan.primary_region_template_version is not None
        else None
    )
    if profile_record is None or (version is None and primary_region_version is None):
        raise MaterializationError("approved evidence or template is unavailable")
    if profile_record.source_sha256 != plan.source_sha256:
        raise MaterializationError("approved source hash no longer matches evidence")
    if plan.supersedes_plan_id is not None:
        superseded_record_ids = (
            select(DatasetRecord.id)
            .where(DatasetRecord.approved_plan_id == plan.supersedes_plan_id)
            .scalar_subquery()
        )
        index_value_ids = (
            select(RecordIndexValue.id)
            .where(RecordIndexValue.record_id.in_(superseded_record_ids))
            .scalar_subquery()
        )
        database.execute(
            delete(RecordValueLineage).where(
                RecordValueLineage.record_index_value_id.in_(index_value_ids)
            )
        )
        database.execute(
            delete(RecordIndexValue).where(
                RecordIndexValue.record_id.in_(superseded_record_ids)
            )
        )
        database.execute(
            delete(DatasetRecord).where(
                DatasetRecord.approved_plan_id == plan.supersedes_plan_id
            )
        )
    profile = load_workbook_profile(profile_record)
    primary_definition = (
        TemplateDefinition.model_validate(version.definition)
        if version is not None
        else _region_definition(primary_region_version)
        if primary_region_version is not None
        else None
    )
    legacy_cache: dict[
        tuple[uuid.UUID, int],
        tuple[TemplateVersion, TemplateDefinition],
    ] = {}
    if version is not None and primary_definition is not None:
        legacy_cache[(version.template_id, version.version)] = (
            version,
            primary_definition,
        )
    region_cache: dict[
        tuple[uuid.UUID, int],
        tuple[RegionTemplateVersion, TemplateDefinition],
    ] = {}
    if primary_region_version is not None and primary_definition is not None:
        region_cache[
            (
                primary_region_version.region_template_id,
                primary_region_version.version,
            )
        ] = (primary_region_version, primary_definition)

    def resolve_template(
        layout: dict[str, Any] | None,
    ) -> tuple[
        uuid.UUID | None,
        int | None,
        uuid.UUID | None,
        int | None,
        TemplateDefinition,
    ]:
        region_template_id = (
            uuid.UUID(str(layout["region_template_id"]))
            if layout and layout.get("region_template_id")
            else plan.primary_region_template_id
        )
        region_template_version = (
            int(layout["region_template_version"])
            if layout and layout.get("region_template_version") is not None
            else plan.primary_region_template_version
        )
        if region_template_id is not None and region_template_version is not None:
            region_key = (region_template_id, region_template_version)
            cached_region = region_cache.get(region_key)
            if cached_region is None:
                selected_region = database.scalar(
                    select(RegionTemplateVersion).where(
                        RegionTemplateVersion.region_template_id == region_template_id,
                        RegionTemplateVersion.version == region_template_version,
                    )
                )
                if selected_region is None:
                    raise MaterializationError("independent Region template version is unavailable")
                cached_region = (
                    selected_region,
                    _region_definition(selected_region),
                )
                region_cache[region_key] = cached_region
            legacy_template_id = (
                uuid.UUID(str(layout["template_id"]))
                if layout and layout.get("template_id")
                else None
            )
            legacy_template_version = (
                int(layout["template_version"])
                if layout and layout.get("template_version") is not None
                else None
            )
            return (
                legacy_template_id,
                legacy_template_version,
                region_template_id,
                region_template_version,
                cached_region[1],
            )
        template_id = (
            uuid.UUID(str(layout["template_id"]))
            if layout and layout.get("template_id")
            else plan.template_id
        )
        template_version = (
            int(layout["template_version"])
            if layout and layout.get("template_version") is not None
            else plan.template_version
        )
        if template_id is None or template_version is None:
            raise MaterializationError("layout decision has no template ownership")
        key = (template_id, template_version)
        cached = legacy_cache.get(key)
        if cached is not None:
            return template_id, template_version, None, None, cached[1]
        selected_version = database.scalar(
            select(TemplateVersion).where(
                TemplateVersion.template_id == template_id,
                TemplateVersion.version == template_version,
            )
        )
        if selected_version is None:
            raise MaterializationError("Region template version is unavailable")
        resolved = (
            selected_version,
            TemplateDefinition.model_validate(selected_version.definition),
        )
        legacy_cache[key] = resolved
        return template_id, template_version, None, None, resolved[1]

    execution = ImportExecution(approved_plan_id=plan.id)
    database.add(execution)
    database.flush()

    cells_by_sheet = {
        sheet.id: {(cell.row, cell.column): cell for cell in sheet.cells}
        for sheet in profile.sheets
    }
    decisions = [
        decision for decision in plan.layout_plan.get("decisions", []) if isinstance(decision, dict)
    ]
    approved_header_ids = {
        (
            str(decision.get("sheet_id", "")),
            str(decision.get("region_candidate_id", "")),
        ): str(decision.get("header_candidate_id", ""))
        for decision in decisions
        if decision.get("header_candidate_id")
    }
    resolved_mappings: dict[tuple[str, str], list[tuple[dict[str, Any], HeaderCandidate, int]]] = {}
    for mapping in plan.field_mappings:
        source_column_id = str(mapping["source_column_id"])
        header_path = [str(part) for part in mapping["header_path"]]
        mapping_sheet_id = str(mapping["sheet_id"]) if mapping.get("sheet_id") else None
        mapping_region_id = str(mapping["region_id"]) if mapping.get("region_id") else None
        expected_header_id = approved_header_ids.get(
            (mapping_sheet_id or "", mapping_region_id or "")
        )
        source_selector = mapping.get("source_selector")
        if isinstance(source_selector, dict):
            matches = [
                (sheet.id, header, region)
                for sheet in profile.sheets
                if mapping_sheet_id is None or sheet.id == mapping_sheet_id
                for region in sheet.region_candidates
                if mapping_region_id is None or region.id == mapping_region_id
                for header in sheet.header_candidates
                if header.region_id == region.id
                and (expected_header_id is None or header.id == expected_header_id)
            ]
            if len(matches) != 1:
                raise MaterializationError(
                    "approved source selector does not resolve to one current Region"
                )
            sheet_id, header, region = matches[0]
            column = int(source_selector["column"])
            if not region.bounds.min_column <= column <= region.bounds.max_column:
                raise MaterializationError(
                    "approved source selector column is outside the source Region"
                )
        else:
            sheet_id, header, region, column = _resolve_column(
                profile,
                source_column_id,
                header_path,
                expected_sheet_id=mapping_sheet_id,
                expected_region_id=mapping_region_id,
                expected_header_id=expected_header_id,
            )
        resolved_mappings.setdefault((sheet_id, region.id), []).append((mapping, header, column))

    selections: list[
        tuple[
            str,
            HeaderCandidate,
            RegionCandidate,
            dict[str, Any] | None,
            list[tuple[dict[str, Any], HeaderCandidate, int]],
            uuid.UUID | None,
            int | None,
            uuid.UUID | None,
            int | None,
            TemplateDefinition,
        ]
    ] = []
    if decisions:
        for decision in decisions:
            region_id = str(decision.get("region_candidate_id", ""))
            header_id = str(decision.get("header_candidate_id", ""))
            matches = [
                (sheet.id, header, region)
                for sheet in profile.sheets
                for region in sheet.region_candidates
                for header in sheet.header_candidates
                if region.id == region_id
                and header.id == header_id
                and header.region_id == region.id
            ]
            if len(matches) != 1:
                raise MaterializationError(
                    "approved layout does not resolve to one current header region"
                )
            sheet_id, header, region = matches[0]
            (
                selected_template_id,
                selected_template_version,
                selected_region_template_id,
                selected_region_template_version,
                selected_definition,
            ) = resolve_template(decision)
            selections.append(
                (
                    sheet_id,
                    header,
                    region,
                    decision,
                    resolved_mappings.get((sheet_id, region.id), []),
                    selected_template_id,
                    selected_template_version,
                    selected_region_template_id,
                    selected_region_template_version,
                    selected_definition,
                )
            )
    else:
        if primary_definition is None:
            raise MaterializationError("approved plan has no primary materialization contract")
        for (sheet_id, region_id), mappings in resolved_mappings.items():
            header_ids = {header.id for _, header, _ in mappings}
            if len(header_ids) != 1:
                raise MaterializationError(
                    "mapped columns require an explicit approved header decision"
                )
            sheet = next(sheet for sheet in profile.sheets if sheet.id == sheet_id)
            header = next(header for header in sheet.header_candidates if header.id in header_ids)
            region = next(region for region in sheet.region_candidates if region.id == region_id)
            selections.append(
                (
                    sheet_id,
                    header,
                    region,
                    None,
                    mappings,
                    version.template_id if version is not None else None,
                    version.version if version is not None else None,
                    (
                        primary_region_version.region_template_id
                        if primary_region_version is not None
                        else None
                    ),
                    (
                        primary_region_version.version
                        if primary_region_version is not None
                        else None
                    ),
                    primary_definition,
                )
            )
    if not selections:
        raise MaterializationError("approved plan contains no materializable regions")

    field_versions = {
        (field_code, field_version.version): field_version
        for field_code, field_version in database.execute(
            select(SemanticField.code, SemanticFieldVersion)
            .select_from(SemanticFieldVersion)
            .join(SemanticField, SemanticField.id == SemanticFieldVersion.field_id)
        )
    }
    record_count = 0
    value_count = 0
    issue_count = 0
    incomplete_mapping_count = 0
    seen_identities: set[tuple[Any, ...]] = set()
    for (
        sheet_id,
        header,
        region,
        approved_layout,
        mappings,
        selected_template_id,
        selected_template_version,
        selected_region_template_id,
        selected_region_template_version,
        definition,
    ) in selections:
        if approved_layout is not None and not bool(approved_layout.get("materialize", True)):
            continue
        if approved_layout is None:
            data_start = max(header.header_rows) + 1
            data_end = region.bounds.max_row
            data_start_column = region.bounds.min_column
            data_end_column = region.bounds.max_column
            excluded_rows: set[int] = set()
        else:
            approved_header_id = str(approved_layout.get("header_candidate_id", ""))
            if approved_header_id != header.id:
                raise MaterializationError("approved layout header does not match mapped columns")
            data_start = int(approved_layout["data_start_row"])
            data_end = int(approved_layout["data_end_row"])
            data_start_column = int(
                approved_layout.get("data_start_column", region.bounds.min_column)
            )
            data_end_column = int(approved_layout.get("data_end_column", region.bounds.max_column))
            excluded_rows = {int(row) for row in approved_layout.get("excluded_rows", [])}
            if (
                data_start < region.bounds.min_row
                or data_end > region.bounds.max_row
                or data_end < data_start
                or data_start_column < region.bounds.min_column
                or data_end_column > region.bounds.max_column
                or data_end_column < data_start_column
                or any(row < data_start or row > data_end for row in excluded_rows)
            ):
                raise MaterializationError("approved data range is outside the source region")
        cells = cells_by_sheet[sheet_id]
        raw_columns_by_id = {column.source_column_id: column for column in header.columns}
        columns_by_id = {
            column.source_column_id: column
            for column in header.columns
            if data_start_column <= column.column <= data_end_column
        }
        mapped_column_ids = {str(mapping["source_column_id"]) for mapping, _, _ in mappings}
        if any(
            mapped_header.id != header.id
            or (
                not isinstance(mapping.get("source_selector"), dict)
                and str(mapping["source_column_id"]) not in columns_by_id
            )
            for mapping, mapped_header, _ in mappings
        ):
            raise MaterializationError("approved mappings do not belong to the approved header")
        layout_mode = (
            str(approved_layout.get("layout_mode") or "") if approved_layout is not None else ""
        )
        if layout_mode == "explicit_header_table":
            layout_mode = "table"
        if not layout_mode:
            layout_mode = "form" if definition.region_kind == "form" else "table"
        record_inputs: list[
            tuple[
                int,
                dict[str, Any],
                list[tuple[dict[str, Any], CellEvidence]],
                list[str],
                str,
            ]
        ] = []
        if layout_mode == "form":
            raw_columns = {}
            for (row, column), raw_cell in cells.items():
                if (
                    data_start <= row <= data_end
                    and data_start_column <= column <= data_end_column
                    and row not in excluded_rows
                    and _is_present(raw_cell.display_value)
                ):
                    raw_columns[raw_cell.id] = {
                        "header_path": [],
                        "source_cell": {
                            "id": raw_cell.id,
                            "coordinate": raw_cell.coordinate,
                            "raw_value": _json_value(raw_cell.raw_value),
                            "display_value": _json_value(raw_cell.display_value),
                        },
                    }
            selected = []
            required_missing = []
            for mapping, _, _ in mappings:
                selector = mapping.get("source_selector")
                if not isinstance(selector, dict) or selector.get("kind") != "cell":
                    raise MaterializationError(
                        "form fields require an approved cell source selector"
                    )
                row = int(selector["row"])
                column = int(selector["column"])
                if (
                    not data_start <= row <= data_end
                    or not data_start_column <= column <= data_end_column
                    or row in excluded_rows
                ):
                    raise MaterializationError(
                        "approved form value cell is outside the selected data range"
                    )
                cell = cells.get((row, column))
                if cell is not None and _is_present(cell.display_value):
                    selected.append((mapping, cell))
                elif bool(mapping.get("value_required")):
                    required_missing.append(str(mapping["semantic_field_code"]))
            if raw_columns:
                record_inputs.append(
                    (
                        data_start,
                        raw_columns,
                        selected,
                        required_missing,
                        "complete" if not required_missing else "partial",
                    )
                )
        elif layout_mode == "headerless_table":
            selector_mappings: list[tuple[dict[str, Any], int]] = []
            for mapping, _, _ in mappings:
                selector = mapping.get("source_selector")
                if not isinstance(selector, dict) or selector.get("kind") != "physical_column":
                    raise MaterializationError(
                        "headerless table fields require physical-column selectors"
                    )
                selector_mappings.append((mapping, int(selector["column"])))
            for row in range(data_start, data_end + 1):
                if row in excluded_rows:
                    continue
                raw_columns = {}
                selected = []
                required_missing = []
                for mapping, column in selector_mappings:
                    cell = cells.get((row, column))
                    if cell is not None and _is_present(cell.display_value):
                        source_column_id = str(mapping["source_column_id"])
                        raw_columns[source_column_id] = {
                            "header_path": mapping["header_path"],
                            "source_cell": {
                                "id": cell.id,
                                "coordinate": cell.coordinate,
                                "raw_value": _json_value(cell.raw_value),
                                "display_value": _json_value(cell.display_value),
                            },
                        }
                        selected.append((mapping, cell))
                    elif bool(mapping.get("value_required")):
                        required_missing.append(str(mapping["semantic_field_code"]))
                if raw_columns:
                    record_inputs.append(
                        (
                            row,
                            raw_columns,
                            selected,
                            required_missing,
                            "complete" if not required_missing else "partial",
                        )
                    )
        elif layout_mode in {"table", "matrix"}:
            for row in range(data_start, data_end + 1):
                if row in excluded_rows:
                    continue
                selected = []
                raw_columns = {}
                for source_column_id, raw_column in raw_columns_by_id.items():
                    if not raw_column.header_path:
                        continue
                    cell = cells.get((row, raw_column.column))
                    if cell is None or not _is_present(cell.display_value):
                        continue
                    raw_columns[source_column_id] = {
                        "header_path": raw_column.header_path,
                        "source_cell": {
                            "id": cell.id,
                            "coordinate": cell.coordinate,
                            "raw_value": _json_value(cell.raw_value),
                            "display_value": _json_value(cell.display_value),
                        },
                    }
                if not raw_columns:
                    continue
                required_missing = []
                for mapping, _, column in mappings:
                    selector = mapping.get("source_selector")
                    if isinstance(selector, dict) and selector.get("kind") != "physical_column":
                        raise MaterializationError(
                            "row table mappings only support physical-column selectors"
                        )
                    cell = cells.get((row, column))
                    if cell is not None and _is_present(cell.display_value):
                        selected.append((mapping, cell))
                    elif bool(mapping.get("value_required")):
                        required_missing.append(str(mapping["semantic_field_code"]))
                sequence_values = [
                    cell.display_value
                    for mapping, cell in selected
                    if str(mapping["semantic_field_code"]) == "base.sequence_number"
                ]
                if sequence_values:
                    try:
                        _normalized_value("integer", sequence_values[0])
                    except MaterializationError:
                        # Summary, note, example, repeated-header and footer rows
                        # remain in the immutable profile but are not logical records.
                        continue
                    sequence_source_ids = {
                        str(mapping["source_column_id"])
                        for mapping, _ in selected
                        if str(mapping["semantic_field_code"]) == "base.sequence_number"
                    }
                    if not any(
                        source_column_id not in sequence_source_ids
                        for source_column_id in raw_columns
                    ):
                        # A pre-formatted blank row containing only its sequence
                        # number has not become a business record yet.
                        continue
                if _is_unfilled_form_row(raw_columns):
                    # Printed labels, blank signature prompts and instruction clauses
                    # are evidence about the form, not submitted business records.
                    continue
                semantic_scope_columns = set(raw_columns) & set(columns_by_id)
                mapping_status = (
                    "complete" if semantic_scope_columns.issubset(mapped_column_ids) else "partial"
                )
                record_inputs.append((row, raw_columns, selected, required_missing, mapping_status))
        else:
            raise MaterializationError(f"unsupported approved layout mode: {layout_mode}")
        for row, raw_columns, selected, required_missing, mapping_status in record_inputs:
            values_by_field: dict[str, Any] = {}
            for mapping, cell in selected:
                if not mapping.get("role"):
                    values_by_field[str(mapping["semantic_field_code"])] = cell.display_value
            semantic_fields: dict[str, dict[str, Any]] = {}
            quality_status = "failed" if required_missing else "passed"
            record = DatasetRecord(
                id=uuid.uuid4(),
                tenant_id=plan.item.batch.tenant_id,
                administrative_unit_id=plan.item.batch.administrative_unit_id,
                ingestion_batch_id=plan.item.batch_id,
                approved_plan_id=plan.id,
                plan_source=plan.plan_source,
                item_id=plan.item_id,
                template_id=selected_template_id,
                template_version=selected_template_version,
                region_template_id=selected_region_template_id,
                region_template_version=selected_region_template_version,
                record_type=definition.record_type,
                sheet_id=sheet_id,
                region_id=region.id,
                source_row=row,
                raw_data={
                    "contract_version": "dataset-record-raw/v1",
                    "source_sha256": plan.source_sha256,
                    "sheet_id": sheet_id,
                    "region_id": region.id,
                    "source_row": row,
                    "layout_mode": layout_mode,
                    "columns": raw_columns,
                },
                semantic_data={
                    "contract_version": "dataset-record-semantic/v1",
                    "fields": {},
                },
                mapping_status=mapping_status,
                quality_status=quality_status,
            )
            database.add(record)
            record_count += 1
            if mapping_status == "partial":
                incomplete_mapping_count += 1
            for field_code in required_missing:
                database.add(
                    QualityIssue(
                        item_id=plan.item_id,
                        approved_plan_id=plan.id,
                        code=RequiredValueMissingError.code,
                        severity="error",
                        message=f"required field {field_code} is empty at row {row}",
                        evidence={
                            "dataset_record_id": str(record.id),
                            "sheet_id": sheet_id,
                            "region_id": region.id,
                            "source_row": row,
                            "semantic_field_code": field_code,
                        },
                    )
                )
                issue_count += 1
            if definition.identity_field_codes:
                identity = tuple(
                    str(values_by_field.get(code, "")).strip()
                    for code in definition.identity_field_codes
                )
                scoped_identity = (
                    selected_region_template_id or selected_template_id,
                    selected_region_template_version or selected_template_version,
                    definition.record_type,
                    *identity,
                )
                if any(not value for value in identity):
                    database.add(
                        QualityIssue(
                            item_id=plan.item_id,
                            approved_plan_id=plan.id,
                            code=RequiredValueMissingError.code,
                            severity="error",
                            message=f"identity fields are incomplete at row {row}",
                            evidence={
                                "dataset_record_id": str(record.id),
                                "identity_field_codes": (definition.identity_field_codes),
                            },
                        )
                    )
                    record.quality_status = "failed"
                    issue_count += 1
                elif scoped_identity in seen_identities:
                    database.add(
                        QualityIssue(
                            item_id=plan.item_id,
                            approved_plan_id=plan.id,
                            code=DuplicateIdentityError.code,
                            severity="error",
                            message=f"duplicate identity at row {row}: {identity!r}",
                            evidence={
                                "dataset_record_id": str(record.id),
                                "identity": list(identity),
                            },
                        )
                    )
                    record.quality_status = "failed"
                    issue_count += 1
                else:
                    seen_identities.add(scoped_identity)
            for mapping, cell in selected:
                field_code = str(mapping["semantic_field_code"])
                field_version_number = int(mapping["semantic_field_version"])
                field_version = field_versions.get((field_code, field_version_number))
                if field_version is None:
                    raise MaterializationError(
                        f"semantic field version not found: {field_code}@{field_version_number}"
                    )
                try:
                    normalizer, normalized = _normalized_value(
                        field_version.data_type,
                        cell.display_value,
                    )
                except MaterializationError as exc:
                    if _is_spreadsheet_error(cell.display_value):
                        role = str(mapping.get("role") or "")
                        role_key = role or "$value"
                        field_roles = semantic_fields.setdefault(field_code, {})
                        if role_key in field_roles:
                            raise MaterializationError(
                                f"duplicate semantic field role: {field_code}#{role_key}"
                            ) from exc
                        field_roles[role_key] = {
                            "value": None,
                            "display_value": _json_value(cell.display_value),
                            "data_type": field_version.data_type,
                            "semantic_field_version": field_version_number,
                            "source_column_id": str(mapping["source_column_id"]),
                            "source_cell_id": cell.id,
                            "coordinate": cell.coordinate,
                            "normalizer": "source-spreadsheet-error/v1",
                            "unavailable_reason": "source_spreadsheet_error",
                        }
                        index_value = RecordIndexValue(
                            id=uuid.uuid4(),
                            record_id=record.id,
                            semantic_field_code=field_code,
                            semantic_field_version=field_version_number,
                            role=role,
                            data_type=field_version.data_type,
                            **_typed_values(field_version.data_type, None),
                        )
                        database.add(index_value)
                        database.add(
                            RecordValueLineage(
                                record_index_value_id=index_value.id,
                                source_sha256=plan.source_sha256,
                                sheet_id=sheet_id,
                                source_cell_id=cell.id,
                                coordinate=cell.coordinate,
                                raw_value=cell.raw_value,
                                display_value=cell.display_value,
                                normalizer="source-spreadsheet-error/v1",
                            )
                        )
                        value_count += 1
                        continue
                    database.add(
                        QualityIssue(
                            item_id=plan.item_id,
                            approved_plan_id=plan.id,
                            code=exc.code,
                            severity="error",
                            message=str(exc),
                            evidence={
                                "dataset_record_id": str(record.id),
                                "source_cell_id": cell.id,
                                "coordinate": cell.coordinate,
                                "semantic_field_code": field_code,
                            },
                        )
                    )
                    record.mapping_status = "partial"
                    record.quality_status = "failed"
                    issue_count += 1
                    continue
                role = str(mapping.get("role") or "")
                role_key = role or "$value"
                field_roles = semantic_fields.setdefault(field_code, {})
                if role_key in field_roles:
                    raise MaterializationError(
                        f"duplicate semantic field role: {field_code}#{role_key}"
                    )
                field_roles[role_key] = {
                    "value": _json_value(normalized),
                    "display_value": _json_value(cell.display_value),
                    "data_type": field_version.data_type,
                    "semantic_field_version": field_version_number,
                    "source_column_id": str(mapping["source_column_id"]),
                    "source_cell_id": cell.id,
                    "coordinate": cell.coordinate,
                    "normalizer": normalizer,
                }
                index_value = RecordIndexValue(
                    id=uuid.uuid4(),
                    record_id=record.id,
                    semantic_field_code=field_code,
                    semantic_field_version=field_version_number,
                    role=role,
                    data_type=field_version.data_type,
                    **_typed_values(field_version.data_type, normalized),
                )
                database.add(index_value)
                database.add(
                    RecordValueLineage(
                        record_index_value_id=index_value.id,
                        source_sha256=plan.source_sha256,
                        sheet_id=sheet_id,
                        source_cell_id=cell.id,
                        coordinate=cell.coordinate,
                        raw_value=cell.raw_value,
                        display_value=cell.display_value,
                        normalizer=normalizer,
                    )
                )
                value_count += 1
            record.semantic_data = {
                "contract_version": "dataset-record-semantic/v1",
                "fields": semantic_fields,
            }
            if record_count % 250 == 0:
                database.flush()
    if record_count == 0:
        database.execute(
            delete(QualityIssue).where(
                QualityIssue.approved_plan_id == plan.id,
                QualityIssue.code.in_(
                    {
                        "HERMES_LOW_CONFIDENCE",
                        "HERMES_SEMANTIC_CONFLICT",
                        "HERMES_STRUCTURE_REVIEW_REQUIRED",
                    }
                ),
            )
        )
    execution.status = (
        "partial" if issue_count or incomplete_mapping_count else "completed"
    )
    execution.record_count = record_count
    execution.value_count = value_count
    execution.completed_at = utcnow()
    database.flush()
    return execution
