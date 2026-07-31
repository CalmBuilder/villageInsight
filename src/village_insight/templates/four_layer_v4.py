from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from village_insight.db.session import get_session_factory
from village_insight.parsing.contracts import HeaderCandidate, WorkbookProfile
from village_insight.parsing.router import ParserRouter
from village_insight.templates.field_semantics import (
    analyze_header_path,
    equivalent_semantic_labels,
    normalized_semantic_label,
    semantic_identity,
)
from village_insight.templates.four_layer_seeds import (
    _looks_like_observed_value,
    _published_field_paths,
    read_package,
    validate_package,
    write_package,
)

CONTRACT_VERSION = "four-layer-template-seed/v4"
GENERATOR_VERSION = "codex-four-layer-source-review/v4"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sample_values(
    profile: WorkbookProfile,
    *,
    region_id: str,
    source_column_id: str,
    limit: int = 5,
) -> tuple[str | None, str | None, list[Any]]:
    for sheet in profile.sheets:
        region = next(
            (candidate for candidate in sheet.region_candidates if candidate.id == region_id),
            None,
        )
        if region is None:
            continue
        columns = [
            (header, column)
            for header in sheet.header_candidates
            if header.region_id == region_id
            for column in header.columns
            if column.source_column_id == source_column_id
        ]
        if not columns:
            return sheet.name, None, []
        header, column = max(columns, key=lambda row: row[0].confidence)
        values = [
            cell.display_value
            for cell in sheet.cells
            if cell.column == column.column
            and cell.row > max(header.header_rows)
            and cell.display_value not in (None, "")
        ][:limit]
        return sheet.name, str(column.column), values
    return None, None, []


def _unit(label: str) -> str | None:
    for marker, unit in (
        ("%", "percent"),
        ("％", "percent"),
        ("元", "currency_cny"),
        ("亩", "area_mu"),
        ("人", "person_count"),
        ("户", "household_count"),
    ):
        if marker in label:
            return unit
    return None


def _infer_data_type(label: str, values: list[Any]) -> str:
    if any(marker in label for marker in ("身份证", "电话", "手机", "账号", "卡号", "折号")):
        return "text"
    present = [value for value in values if value not in (None, "")]
    if not present:
        return "text"
    if all(isinstance(value, bool) for value in present):
        return "boolean"
    numeric = [
        value
        for value in present
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if len(numeric) == len(present) and any(
        marker in label
        for marker in ("序号", "数量", "人数", "户数", "金额", "面积", "比例", "任务数", "岁")
    ):
        return "integer" if all(float(value).is_integer() for value in numeric) else "decimal"
    return "text"


def _sheet_index(identifier: str) -> int:
    match = re.search(r":sheet:(\d+):", identifier)
    if match is None:
        raise ValueError(f"source identifier has no Sheet index: {identifier}")
    return int(match.group(1))


def _confirmed_suspicious_path(
    *,
    source_path: str,
    path: list[str],
) -> list[str] | None:
    leaf = path[-1] if path else ""
    if "结核病筛查" in source_path or re.search(r"\d{5,}", leaf):
        return None
    if leaf in {"无", "否", "1"}:
        return None
    if leaf in {"45992", "45993"}:
        return [["2025-11-01"], ["2025-11-02"]][leaf == "45993"]
    if "人口明细表" in source_path and leaf in {"男", "女"}:
        return ["男性人数" if leaf == "男" else "女性人数"]
    if re.search(r"/.*\d{7,}", leaf):
        return None
    return path


def _best_header(
    profile: WorkbookProfile,
    *,
    region_id: str,
    preferred_header_id: str | None = None,
) -> HeaderCandidate | None:
    candidates = [
        header
        for sheet in profile.sheets
        for header in sheet.header_candidates
        if header.region_id == region_id
    ]
    if preferred_header_id:
        preferred = next(
            (header for header in candidates if header.id == preferred_header_id),
            None,
        )
        if preferred is not None:
            return preferred
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda header: (
            sum(bool(column.header_path) for column in header.columns)
            - 2
            * sum(
                _looks_like_observed_value(
                    [str(part) for part in column.header_path]
                )
                for column in header.columns
            ),
            header.confidence,
            -max(header.header_rows),
        ),
    )


def build_v4_package(
    *,
    v3_directory: Path,
    codex_review_path: Path,
) -> dict[str, Any]:
    v3 = read_package(v3_directory)
    review = _read_json(codex_review_path)
    with get_session_factory()() as database:
        published_lookup, published = _published_field_paths(database)
    published = {
        code: definition
        for code, definition in published.items()
        if not code.startswith("bootstrap.")
    }
    published_lookup = {
        label: {code for code in codes if code in published}
        for label, codes in published_lookup.items()
    }
    cached_path: str | None = None
    cached_profile: WorkbookProfile | None = None

    def profile(path: str) -> WorkbookProfile:
        nonlocal cached_path, cached_profile
        if cached_path != path or cached_profile is None:
            cached_profile = ParserRouter().profile(Path(path))
            cached_path = path
        return cached_profile

    fields: dict[str, dict[str, Any]] = dict(published)
    field_samples: dict[str, list[Any]] = defaultdict(list)

    def field_for(
        *,
        path: list[str],
        domain: str,
        old_code: str | None,
        evidence: dict[str, Any],
    ) -> tuple[str, int, str | None]:
        semantics = analyze_header_path(path)
        published_candidates = sorted(
            {
                code
                for label in (
                    normalized_semantic_label(" / ".join(path)),
                    *(
                        equivalent_semantic_labels(path[-1])
                        if path
                        else set()
                    ),
                )
                for code in published_lookup.get(label, set())
            }
        )
        if old_code in published:
            code = str(old_code)
            field = fields[code]
        elif len(published_candidates) == 1:
            code = published_candidates[0]
            field = fields[code]
        else:
            identity = semantic_identity(header_path=path, domain=domain)
            namespace = "base" if semantics.concept_key else "shared"
            code = f"bootstrap.{namespace}.{_digest(identity)[:20]}"
            existing_field = fields.get(code)
            if existing_field is None:
                field = {
                    "code": code,
                    "version": 1,
                    "name": semantics.base_label[:200],
                    "description": "Codex 基于真实文件表头、列位置和样例值确认的标准字段",
                    "layer": "base" if semantics.concept_key else "domain",
                    "data_type": "text",
                    "unit_dimension": _unit(semantics.leaf_label),
                    "status": "publish_candidate",
                    "aliases": [],
                    "header_paths": [],
                    "roles": [],
                    "semantic_identity": identity,
                    "evidence": [],
                    "source": "codex_source_review",
                }
                fields[code] = field
            else:
                field = existing_field
            if field.get("source") != "published_catalog":
                if semantics.leaf_label and semantics.leaf_label != field["name"]:
                    if semantics.leaf_label not in field["aliases"]:
                        field["aliases"].append(semantics.leaf_label)
                if path not in field["header_paths"]:
                    field["header_paths"].append(path)
                if semantics.role and semantics.role not in field["roles"]:
                    field["roles"].append(semantics.role)
                field["evidence"].append(evidence)
                field_samples[code].extend(evidence.get("sample_values", []))
        return code, int(field["version"]), semantics.role

    old_regions = {str(region["code"]): region for region in v3["region_templates"]}
    new_regions: dict[str, dict[str, Any]] = {}
    old_to_new_region: dict[str, str] = {}

    def add_region(
        *,
        domain: str,
        region_kind: str,
        record_type: str,
        record_grain: str,
        header_signature: list[list[str]],
        layout_rules: dict[str, Any],
        bindings: list[dict[str, Any]],
        evidence: dict[str, Any],
    ) -> str:
        signature = {
            "kind": region_kind,
            "record_type": record_type,
            "fields": [
                {
                    "code": binding["semantic_field_code"],
                    "role": binding.get("role"),
                }
                for binding in bindings
            ],
        }
        fingerprint = _digest(signature)
        code = f"region.{domain}.{fingerprint[:20]}"
        region = new_regions.setdefault(
            code,
            {
                "code": code,
                "version": 1,
                "name": Path(str(evidence["representative_path"])).stem[:200],
                "domain": domain,
                "record_type": record_type,
                "record_grain": record_grain,
                "region_kind": region_kind,
                "region_fingerprint": fingerprint,
                "header_signature": header_signature,
                "header_variants": [],
                "layout_rules": layout_rules,
                "field_bindings": bindings,
                "requires_hermes": False,
                "unresolved_columns": [],
                "status": "publish_candidate",
                "evidence": [],
                "source": "codex_source_review",
            },
        )
        if header_signature not in region["header_variants"]:
            region["header_variants"].append(header_signature)
        region["evidence"].append(evidence)
        return code

    for old_code, old_region in sorted(
        old_regions.items(),
        key=lambda item: (
            str(item[1]["evidence"][0]["representative_path"]),
            item[0],
        ),
    ):
        evidence = dict(old_region["evidence"][0])
        source_path = str(evidence["representative_path"])
        sheet_index = _sheet_index(str(evidence["region_id"]))
        sheet_name = f"Sheet {sheet_index + 1}"
        bindings = []
        for binding in old_region["field_bindings"]:
            path = [str(part) for part in binding["header_path"]]
            column_number = int(str(binding["source_column_id"]).rsplit(":", 1)[-1])
            field_evidence = {
                "source_path": source_path,
                "source_sha256": evidence["source_sha256"],
                "sheet_name": sheet_name,
                "region_id": evidence["region_id"],
                "column": column_number,
                "source_column_id": binding["source_column_id"],
                "header_path": path,
                "sample_values": [],
                "sample_loading": "on_demand_from_source",
            }
            field_code, field_version, role = field_for(
                path=path,
                domain=str(old_region["domain"]),
                old_code=str(binding["semantic_field_code"]),
                evidence=field_evidence,
            )
            bindings.append(
                {
                    **binding,
                    "semantic_field_code": field_code,
                    "semantic_field_version": field_version,
                    "field_status": "published_reuse"
                    if field_code in published
                    else "codex_confirmed",
                    "role": role,
                }
            )
        for unresolved in old_region.get("unresolved_columns", []):
            confirmed_path = _confirmed_suspicious_path(
                source_path=source_path,
                path=[str(part) for part in unresolved["header_path"]],
            )
            if confirmed_path is None:
                continue
            source_column_id = str(unresolved["source_column_id"])
            column_number = int(source_column_id.rsplit(":", 1)[-1])
            field_evidence = {
                "source_path": source_path,
                "source_sha256": evidence["source_sha256"],
                "sheet_name": sheet_name,
                "region_id": evidence["region_id"],
                "column": column_number,
                "source_column_id": source_column_id,
                "header_path": confirmed_path,
                "sample_values": [],
                "sample_loading": "on_demand_from_source",
                "recovered_by": "codex_suspicious_column_review",
            }
            field_code, field_version, role = field_for(
                path=confirmed_path,
                domain=str(old_region["domain"]),
                old_code=None,
                evidence=field_evidence,
            )
            bindings.append(
                {
                    "source_column_id": source_column_id,
                    "header_path": confirmed_path,
                    "semantic_field_code": field_code,
                    "semantic_field_version": field_version,
                    "field_status": "codex_confirmed",
                    "role": role,
                    "required": False,
                }
            )
        header_signature = [
            [str(part) for part in binding["header_path"]]
            for binding in bindings
        ]
        new_code = add_region(
            domain=str(old_region["domain"]),
            region_kind=str(old_region["region_kind"]),
            record_type=str(old_region["record_type"]),
            record_grain=str(old_region["record_grain"]),
            header_signature=header_signature,
            layout_rules=dict(old_region["layout_rules"]),
            bindings=bindings,
            evidence={
                **evidence,
                "sheet_name": sheet_name,
                "sheet_index": sheet_index,
                "decision_source": "codex_revalidated_v3_region",
            },
        )
        old_to_new_region[old_code] = new_code

    review_by_route = {
        str(route["route_code"]): route for route in review["routes"]
    }
    added_by_route_sheet: dict[tuple[str, int], list[str]] = defaultdict(list)
    for route_code, route_review in review_by_route.items():
        source_path = str(route_review["source_path"])
        source_profile = profile(source_path)
        for row in route_review["decisions"]:
            decision = row["codex_decision"]
            if decision["action"] != "create_template":
                continue
            sheet_index = int(row["sheet_index"])
            sheet = source_profile.sheets[sheet_index]
            region = next(
                region
                for region in sheet.region_candidates
                if region.id == row["region_candidate_id"]
            )
            domain = "employment" if "稳定就业" in source_path else "governance"
            record_type = (
                "employment_application"
                if decision["layout_mode"] == "form"
                else "structured_record"
            )
            binding_specs: list[dict[str, Any]]
            if decision["layout_mode"] == "form":
                binding_specs = [
                    {
                        "label": selector["label"],
                        "source_column_id": (
                            f"form:r{int(selector['value_row'])}:"
                            f"c{int(selector['value_column'])}"
                        ),
                        "source_selector": {
                            "kind": "cell",
                            "row_offset": int(selector["value_row"])
                            - region.bounds.min_row,
                            "column_offset": int(selector["value_column"])
                            - region.bounds.min_column,
                            "label_row_offset": int(selector["label_row"])
                            - region.bounds.min_row,
                            "label_column_offset": int(selector["label_column"])
                            - region.bounds.min_column,
                        },
                        "required": bool(selector["required"]),
                    }
                    for selector in decision["field_selectors"]
                ]
            elif decision["layout_mode"] == "headerless_table":
                binding_specs = [
                    {
                        "label": column["label"],
                        "source_column_id": f"physical-column:{column['column']}",
                        "source_selector": {
                            "kind": "physical_column",
                            "column_offset": int(column["column"])
                            - region.bounds.min_column,
                        },
                        "required": bool(column["required"]),
                    }
                    for column in decision["synthetic_columns"]
                ]
            else:
                header = (
                    None
                    if decision.get("manual_columns")
                    else _best_header(
                        source_profile,
                        region_id=region.id,
                        preferred_header_id=decision.get("header_candidate_id"),
                    )
                )
                if header is not None:
                    binding_specs = [
                        {
                            "label": " / ".join(column.header_path),
                            "header_path": list(column.header_path),
                            "source_column_id": column.source_column_id,
                            "required": False,
                        }
                        for column in header.columns
                        if column.header_path
                    ]
                else:
                    binding_specs = [
                        {
                            "label": column["label"],
                            "header_path": [column["label"]],
                            "source_column_id": f"physical-column:{column['column']}",
                            "source_selector": {
                                "kind": "physical_column",
                                "column_offset": int(column["column"])
                                - region.bounds.min_column,
                            },
                            "required": False,
                        }
                        for column in decision["manual_columns"]
                    ]
            bindings = []
            for spec in binding_specs:
                path = [
                    str(part)
                    for part in spec.get("header_path", [spec["label"]])
                ]
                selected_column_number: int | None = (
                    int(spec["source_selector"]["column_offset"])
                    + region.bounds.min_column
                    if isinstance(spec.get("source_selector"), dict)
                    else None
                )
                samples = [
                    cell.display_value
                    for cell in sheet.cells
                    if selected_column_number is not None
                    and cell.column == selected_column_number
                    and decision["data_start_row"]
                    <= cell.row
                    <= decision["data_end_row"]
                    and cell.display_value not in (None, "")
                ][:5]
                field_code, field_version, role = field_for(
                    path=path,
                    domain=domain,
                    old_code=None,
                    evidence={
                        "source_path": source_path,
                        "source_sha256": source_profile.source_sha256,
                        "sheet_name": sheet.name,
                        "region_id": region.id,
                        "column": selected_column_number,
                        "source_column_id": spec["source_column_id"],
                        "header_path": path,
                        "sample_values": samples,
                    },
                )
                bindings.append(
                    {
                        "source_column_id": spec["source_column_id"],
                        "header_path": path,
                        "semantic_field_code": field_code,
                        "semantic_field_version": field_version,
                        "source_selector": spec.get("source_selector"),
                        "field_status": "codex_confirmed",
                        "role": role,
                        "required": spec["required"],
                    }
                )
            header_signature = [
                [str(part) for part in binding["header_path"]]
                for binding in bindings
            ]
            layout_rules = {
                "layout_mode": decision["layout_mode"],
                "data_start_offset_from_region_start": (
                    int(decision["data_start_row"]) - region.bounds.min_row
                ),
                "data_end_gap_from_region_end": max(
                    0,
                    region.bounds.max_row - int(decision["data_end_row"]),
                ),
                "excluded_row_offsets": [
                    int(row_number) - int(decision["data_start_row"])
                    for row_number in decision["excluded_rows"]
                ],
                "materialize": True,
            }
            new_code = add_region(
                domain=domain,
                region_kind=(
                    "form"
                    if decision["layout_mode"] == "form"
                    else "matrix"
                    if decision["layout_mode"] == "matrix"
                    else "table"
                ),
                record_type=record_type,
                record_grain=(
                    "one_record_per_form"
                    if decision["layout_mode"] == "form"
                    else "one_record_per_source_row"
                ),
                header_signature=header_signature,
                layout_rules=layout_rules,
                bindings=bindings,
                evidence={
                    "source_sha256": source_profile.source_sha256,
                    "representative_path": source_path,
                    "sheet_name": sheet.name,
                    "sheet_index": sheet_index,
                    "region_id": region.id,
                    "region_range": region.bounds.range,
                    "decision_source": "codex_primary_review",
                    "hermes_second_opinion": row["hermes_second_opinion"],
                },
            )
            added_by_route_sheet[(route_code, sheet_index)].append(new_code)

    for code, field in fields.items():
        if code in published:
            continue
        values = field_samples.get(code, [])
        field["data_type"] = _infer_data_type(str(field["name"]), values)

    old_sheets = {
        str(sheet["code"]): sheet for sheet in v3["sheet_compositions"]
    }
    new_sheets: dict[str, dict[str, Any]] = {}
    routes_by_fingerprint: dict[str, dict[str, Any]] = {}
    old_route_to_new: dict[str, str] = {}
    for old_route in v3["workbook_routes"]:
        old_route_code = str(old_route["code"])
        refs_by_sheet: dict[int, list[str]] = defaultdict(list)
        for slot in old_route["sheet_slots"]:
            sheet_index = int(slot["ordinal"])
            old_sheet = old_sheets[str(slot["sheet_composition_code"])]
            refs_by_sheet[sheet_index].extend(
                old_to_new_region[str(region_slot["region_template_code"])]
                for region_slot in old_sheet["region_slots"]
            )
        for (route_code, sheet_index), additions in added_by_route_sheet.items():
            if route_code == old_route_code:
                refs_by_sheet[sheet_index].extend(additions)
        sheet_slots = []
        for sheet_index, region_codes in sorted(refs_by_sheet.items()):
            unique_codes = list(dict.fromkeys(region_codes))
            sheet_fingerprint = _digest(unique_codes)
            sheet_code = f"sheet.structured.{sheet_fingerprint[:20]}"
            new_sheets.setdefault(
                sheet_code,
                {
                    "code": sheet_code,
                    "version": 1,
                    "name": f"Sheet {sheet_index + 1} 表格组合",
                    "composition_fingerprint": sheet_fingerprint,
                    "status": "publish_candidate",
                    "region_slots": [
                        {
                            "slot_key": f"region_{index + 1}",
                            "region_template_code": region_code,
                            "region_template_version": 1,
                            "ordinal": index,
                            "required": True,
                            "cardinality": "one",
                            "materialize": True,
                        }
                        for index, region_code in enumerate(unique_codes)
                    ],
                },
            )
            sheet_slots.append(
                {
                    "slot_key": f"sheet_{sheet_index + 1}",
                    "sheet_composition_code": sheet_code,
                    "sheet_composition_version": 1,
                    "ordinal": sheet_index,
                    "required": True,
                    "cardinality": "one",
                    "materialize": True,
                }
            )
        route_fingerprint = _digest(
            [
                (slot["ordinal"], slot["sheet_composition_code"])
                for slot in sheet_slots
            ]
        )
        route_code = f"workbook.structured.{route_fingerprint[:20]}"
        route = routes_by_fingerprint.setdefault(
            route_fingerprint,
            {
                "code": route_code,
                "version": 1,
                "name": str(old_route["name"]),
                "route_fingerprint": route_fingerprint,
                "status": "publish_candidate",
                "sheet_slots": sheet_slots,
                "unresolved_regions": [],
                "source_file_count": 0,
                "members": [],
                "ignored_regions": [],
                "source": "codex_source_review",
            },
        )
        route["source_file_count"] += int(old_route["source_file_count"])
        route["members"].extend(old_route["members"])
        route_review = review_by_route.get(old_route_code)
        if route_review is not None:
            route["ignored_regions"].extend(
                {
                    "source_sha256": route_review["source_sha256"],
                    "sheet_index": decision["sheet_index"],
                    "region_candidate_id": decision["region_candidate_id"],
                    "region_range": decision["region_range"],
                    "reason": decision["codex_decision"]["reason"],
                }
                for decision in route_review["decisions"]
                if decision["codex_decision"]["action"]
                == "discard_false_positive"
            )
        old_route_to_new[old_route_code] = route_code

    coverage = [
        {
            **row,
            "workbook_route_code": old_route_to_new[
                str(row["workbook_route_code"])
            ],
            "decision": "codex_four_layer_template",
        }
        for row in v3["coverage"]
    ]
    routes_by_code = {
        str(route["code"]): route for route in routes_by_fingerprint.values()
    }
    sheets_by_code = {str(sheet["code"]): sheet for sheet in new_sheets.values()}

    def village_name(source_path: str) -> str:
        parts = Path(source_path).parts
        try:
            return parts[parts.index("所有村") + 1]
        except (ValueError, IndexError):
            return "<unknown>"

    villages = sorted({village_name(str(row["source_path"])) for row in coverage})
    holdout_villages = villages[-2:]
    route_region_codes = {
        route_code: [
            str(region_slot["region_template_code"])
            for sheet_slot in route["sheet_slots"]
            for region_slot in sheets_by_code[
                str(sheet_slot["sheet_composition_code"])
            ]["region_slots"]
        ]
        for route_code, route in routes_by_code.items()
    }
    train_region_codes: set[str] = set()
    train_field_codes: set[str] = set()
    holdout_region_refs: list[str] = []
    holdout_field_refs: list[str] = []
    for row in coverage:
        region_codes = route_region_codes[str(row["workbook_route_code"])]
        field_codes = [
            str(binding["semantic_field_code"])
            for region_code in region_codes
            for binding in new_regions[region_code]["field_bindings"]
        ]
        if village_name(str(row["source_path"])) in holdout_villages:
            holdout_region_refs.extend(region_codes)
            holdout_field_refs.extend(field_codes)
        else:
            train_region_codes.update(region_codes)
            train_field_codes.update(field_codes)
    holdout_validation = {
        "train_villages": [
            village for village in villages if village not in holdout_villages
        ],
        "holdout_villages": holdout_villages,
        "holdout_file_count": sum(
            village_name(str(row["source_path"])) in holdout_villages
            for row in coverage
        ),
        "region_reuse_basis_points": (
            round(
                10_000
                * sum(code in train_region_codes for code in holdout_region_refs)
                / len(holdout_region_refs)
            )
            if holdout_region_refs
            else 0
        ),
        "field_reuse_basis_points": (
            round(
                10_000
                * sum(code in train_field_codes for code in holdout_field_refs)
                / len(holdout_field_refs)
            )
            if holdout_field_refs
            else 0
        ),
    }
    package = {
        **v3,
        "contract_version": CONTRACT_VERSION,
        "generator_version": GENERATOR_VERSION,
        "semantic_fields": sorted(fields.values(), key=lambda row: row["code"]),
        "region_templates": sorted(
            new_regions.values(),
            key=lambda row: row["code"],
        ),
        "sheet_compositions": sorted(
            new_sheets.values(),
            key=lambda row: row["code"],
        ),
        "workbook_routes": sorted(
            routes_by_fingerprint.values(),
            key=lambda row: row["code"],
        ),
        "coverage": sorted(coverage, key=lambda row: row["source_path"]),
        "holdout_validation": holdout_validation,
        "codex_structure_review": review["summary"],
    }
    package["summary"] = {
        "semantic_field_count": len(package["semantic_fields"]),
        "new_field_review_count": 0,
        "region_template_count": len(package["region_templates"]),
        "sheet_composition_count": len(package["sheet_compositions"]),
        "workbook_route_count": len(package["workbook_routes"]),
        "covered_source_file_count": len(package["coverage"]),
        "unsupported_unique_content_count": len(v3["conflicts"]),
        "unsupported_source_file_count": len(v3["conflicts"]),
        "status_counts": dict(
            Counter(route["status"] for route in package["workbook_routes"])
        ),
        "unresolved_region_count": 0,
        "unresolved_column_count": 0,
    }
    package["source_report_sha256"] = v3["source_report_sha256"]
    package["generation_sha256"] = _digest(
        {
            key: package[key]
            for key in (
                "contract_version",
                "generator_version",
                "semantic_fields",
                "region_templates",
                "sheet_compositions",
                "workbook_routes",
                "coverage",
            )
        }
    )
    return package


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v3-directory", type=Path, required=True)
    parser.add_argument("--codex-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    package = build_v4_package(
        v3_directory=arguments.v3_directory,
        codex_review_path=arguments.codex_review,
    )
    write_package(package, arguments.output)
    validation = validate_package(package)
    (arguments.output / "validation-report.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(package["summary"], ensure_ascii=False))
    print(json.dumps(validation, ensure_ascii=False))


if __name__ == "__main__":
    main()
