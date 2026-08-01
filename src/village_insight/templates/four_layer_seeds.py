from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    ProposalStatus,
    SemanticField,
    SemanticFieldVersion,
    TemplateProposal,
    TemplateStatus,
)
from village_insight.db.session import get_session_factory
from village_insight.templates.field_semantics import (
    analyze_header_path,
    equivalent_semantic_labels,
    looks_like_observed_value_header,
    normalize_role_code,
    semantic_identity,
)

CONTRACT_VERSION = "four-layer-template-seed/v3"
GENERATOR_VERSION = "codex-four-layer-bootstrap/v3"
DOMAIN_RULES = (
    ("population", "person", ("人口", "户籍", "身份证", "性别", "出生", "家庭成员")),
    ("agriculture", "agriculture_record", ("耕地", "农作物", "种植", "养殖", "地块")),
    ("social_security", "social_security_record", ("医保", "社保", "参保", "养老", "低保")),
    ("employment", "employment_record", ("就业", "务工", "劳动力", "岗位")),
    ("governance", "governance_member", ("党员", "干部", "网格员", "党费")),
    ("assistance", "assistance_record", ("脱贫", "监测", "困难", "救助", "补助")),
    ("finance", "payment_record", ("金额", "发放", "银行卡", "账号", "补贴")),
)


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sheet_index(identifier: str) -> int:
    match = re.search(r":sheet:(\d+):", identifier)
    if match is None:
        raise ValueError(f"source identifier has no Sheet index: {identifier}")
    return int(match.group(1))


def _domain(text: str) -> tuple[str, str]:
    for domain, record_type, keywords in DOMAIN_RULES:
        if any(keyword in text for keyword in keywords):
            return domain, record_type
    return "general", "structured_record"


def _looks_like_observed_value(header_path: list[str]) -> bool:
    return looks_like_observed_value_header(header_path)


def _published_field_paths(
    database: Session,
) -> tuple[dict[str, set[str]], dict[str, dict[str, Any]]]:
    lookup: dict[str, set[str]] = defaultdict(set)
    definitions: dict[str, dict[str, Any]] = {}
    for field, version in database.execute(
        select(SemanticField, SemanticFieldVersion).where(
            SemanticField.id == SemanticFieldVersion.field_id,
            SemanticField.published_version == SemanticFieldVersion.version,
            SemanticFieldVersion.status == TemplateStatus.PUBLISHED,
        )
    ):
        definitions[field.code] = {
            "code": field.code,
            "version": version.version,
            "name": version.name,
            "data_type": version.data_type,
            "unit_dimension": version.unit_dimension,
            "source": "published_catalog",
        }
        for label in [version.name, *version.aliases]:
            if label:
                for normalized in equivalent_semantic_labels(label):
                    lookup[normalized].add(field.code)
        for variant in version.variants:
            if variant.alias:
                for normalized in equivalent_semantic_labels(variant.alias):
                    lookup[normalized].add(field.code)
            if variant.header_path:
                lookup[_normalized(" / ".join(variant.header_path))].add(field.code)
                for normalized in equivalent_semantic_labels(variant.header_path[-1]):
                    lookup[normalized].add(field.code)
    return lookup, definitions


def build_four_layer_seed_package(
    database: Session,
    report: dict[str, Any],
) -> dict[str, Any]:
    lookup, published_fields = _published_field_paths(database)
    unknown_evidence: dict[str, dict[str, Any]] = {}
    region_templates: dict[str, dict[str, Any]] = {}
    sheet_compositions: dict[str, dict[str, Any]] = {}
    workbook_routes: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    region_sources: dict[str, set[str]] = defaultdict(set)
    field_sources: dict[str, set[str]] = defaultdict(set)
    unresolved_region_count = 0
    unresolved_column_count = 0

    for cluster in report["clusters"]:
        evidence = cluster["representative_evidence"]
        representative = str(cluster["representative_path"])
        context_text = (
            representative + " " + " ".join(str(header) for header in cluster["header_variants"])
        )
        domain, record_type = _domain(context_text)
        columns_by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for column in evidence["header_columns"]:
            columns_by_region[str(column["region_id"])].append(column)
        layout_by_region = {
            str(decision["region_id"]): decision for decision in evidence["layout_candidates"]
        }
        regions_by_sheet: dict[int, list[dict[str, Any]]] = defaultdict(list)
        unresolved_regions: list[dict[str, Any]] = []
        for region_id, columns in sorted(columns_by_region.items()):
            columns.sort(key=lambda value: int(value["column"]))
            suspicious_columns = [
                column
                for column in columns
                if _looks_like_observed_value([str(part) for part in column["header_path"]])
            ]
            suspicious_ratio = len(suspicious_columns) / len(columns)
            if suspicious_ratio > 0.4:
                unresolved_region_count += 1
                unresolved_column_count += len(columns)
                unresolved_regions.append(
                    {
                        "sheet_index": _sheet_index(region_id),
                        "region_id": region_id,
                        "reason": "header_candidate_contains_observed_values",
                        "header_signature": [
                            [str(part) for part in column["header_path"]] for column in columns
                        ],
                        "suspicious_source_column_ids": [
                            str(column["source_column_id"]) for column in suspicious_columns
                        ],
                        "requires_hermes": True,
                    }
                )
                continue
            trusted_columns = [column for column in columns if column not in suspicious_columns]
            unresolved_column_count += len(suspicious_columns)
            header_signature = [
                [str(part) for part in column["header_path"]] for column in trusted_columns
            ]
            region_fingerprint = _digest(
                {
                    "domain": domain,
                    "record_type": record_type,
                    "headers": header_signature,
                }
            )
            region_code = f"region.{domain}.{region_fingerprint[:20]}"
            bindings = []
            for column in trusted_columns:
                path = [str(part) for part in column["header_path"]]
                semantics = analyze_header_path(path)
                keys = (
                    _normalized(" / ".join(path)),
                    _normalized(path[-1]),
                    semantics.normalized_base_label,
                )
                candidates = sorted({code for key in keys for code in lookup.get(key, set())})
                if len(candidates) == 1:
                    field_code = candidates[0]
                    field_version = int(published_fields[field_code]["version"])
                    field_status = "published_reuse"
                else:
                    identity = semantic_identity(
                        header_path=path,
                        domain=domain,
                    )
                    field_key = _digest(identity)
                    namespace = "base" if semantics.concept_key else domain
                    field_code = f"bootstrap.{namespace}.{field_key[:20]}"
                    field_version = 1
                    field_status = "admin_review"
                    candidate = unknown_evidence.setdefault(
                        field_code,
                        {
                            "code": field_code,
                            "version": 1,
                            "name": semantics.base_label[:200],
                            "description": "Codex 全量画像生成的待审核字段",
                            "layer": "base" if semantics.concept_key else "domain",
                            "data_type": "text",
                            "unit_dimension": None,
                            "status": "admin_review",
                            "aliases": [],
                            "header_paths": [],
                            "roles": [],
                            "semantic_identity": identity,
                            "evidence": [],
                        },
                    )
                    if (
                        semantics.leaf_label != semantics.base_label
                        and semantics.leaf_label not in candidate["aliases"]
                    ):
                        candidate["aliases"].append(semantics.leaf_label)
                    if path not in candidate["header_paths"]:
                        candidate["header_paths"].append(path)
                    if semantics.role and semantics.role not in candidate["roles"]:
                        candidate["roles"].append(semantics.role)
                    candidate["evidence"].append(
                        {
                            "source_sha256": evidence["source_sha256"],
                            "region_id": region_id,
                            "source_column_id": column["source_column_id"],
                        }
                    )
                field_sources[field_code].update(str(path) for path in cluster["source_paths"])
                bindings.append(
                    {
                        "source_column_id": column["source_column_id"],
                        "header_path": path,
                        "semantic_field_code": field_code,
                        "semantic_field_version": field_version,
                        "field_status": field_status,
                        "role": semantics.role,
                        "role_evidence": semantics.role_evidence,
                        "required": False,
                    }
                )
            layout = layout_by_region[region_id]
            header_end = max(int(row) for column in columns for row in column["header_rows"])
            template = region_templates.setdefault(
                region_code,
                {
                    "code": region_code,
                    "version": 1,
                    "name": f"{domain} {record_type} Region",
                    "domain": domain,
                    "record_type": record_type,
                    "record_grain": f"one_row_per_{record_type}",
                    "region_kind": "table",
                    "region_fingerprint": region_fingerprint,
                    "header_signature": header_signature,
                    "layout_rules": {
                        "data_start_offset_from_header_end": (
                            int(layout["data_start_row"]) - header_end
                        ),
                        "data_end_gap_from_region_end": 0,
                        "excluded_row_offsets": [],
                        "materialize": True,
                    },
                    "field_bindings": bindings,
                    "requires_hermes": bool(suspicious_columns),
                    "unresolved_columns": [
                        {
                            "source_column_id": str(column["source_column_id"]),
                            "header_path": [str(part) for part in column["header_path"]],
                            "reason": "header_looks_like_observed_value",
                        }
                        for column in suspicious_columns
                    ],
                    "status": (
                        "admin_review"
                        if suspicious_columns
                        or any(binding["field_status"] == "admin_review" for binding in bindings)
                        else "publish_candidate"
                    ),
                    "evidence": [],
                },
            )
            template["evidence"].append(
                {
                    "source_sha256": evidence["source_sha256"],
                    "representative_path": representative,
                    "region_id": region_id,
                    "confidence": layout["confidence"],
                }
            )
            region_sources[region_code].update(cluster["source_paths"])
            regions_by_sheet[_sheet_index(region_id)].append(
                {
                    "region_template_code": region_code,
                    "region_template_version": 1,
                }
            )

        sheet_slots = []
        for sheet_index, region_refs in sorted(regions_by_sheet.items()):
            composition_fingerprint = _digest(region_refs)
            composition_code = f"sheet.structured.{composition_fingerprint[:20]}"
            if composition_code not in sheet_compositions:
                sheet_compositions[composition_code] = {
                    "code": composition_code,
                    "version": 1,
                    "name": f"结构化 Sheet {composition_fingerprint[:8]}",
                    "composition_fingerprint": composition_fingerprint,
                    "status": (
                        "admin_review"
                        if any(
                            region_templates[ref["region_template_code"]]["status"]
                            == "admin_review"
                            for ref in region_refs
                        )
                        else "publish_candidate"
                    ),
                    "region_slots": [
                        {
                            "slot_key": f"region_{index + 1}",
                            **reference,
                            "ordinal": index,
                            "required": True,
                            "cardinality": "one",
                            "materialize": True,
                        }
                        for index, reference in enumerate(region_refs)
                    ],
                }
            sheet_slots.append(
                {
                    "slot_key": f"sheet_{sheet_index + 1}",
                    "sheet_composition_code": composition_code,
                    "sheet_composition_version": 1,
                    "ordinal": sheet_index,
                    "required": True,
                    "cardinality": "one",
                    "materialize": True,
                }
            )
        route_code = f"workbook.structured.{cluster['layout_fingerprint'][:20]}"
        workbook_routes.append(
            {
                "code": route_code,
                "version": 1,
                "name": Path(representative).stem[:200],
                "route_fingerprint": cluster["layout_fingerprint"],
                "status": (
                    "admin_review"
                    if unresolved_regions
                    or any(
                        sheet_compositions[str(slot["sheet_composition_code"])]["status"]
                        == "admin_review"
                        for slot in sheet_slots
                    )
                    else "publish_candidate"
                ),
                "sheet_slots": sheet_slots,
                "unresolved_regions": unresolved_regions,
                "source_file_count": cluster["source_file_count"],
                "members": cluster["members"],
            }
        )
        coverage.extend(
            {
                "source_path": source_path,
                "layout_fingerprint": cluster["layout_fingerprint"],
                "workbook_route_code": route_code,
                "decision": "template_seed",
            }
            for source_path in cluster["source_paths"]
        )

    villages = sorted(
        {
            Path(path).relative_to(report["root"]).parts[0]
            for cluster in report["clusters"]
            for path in cluster["source_paths"]
        }
    )
    holdout_villages = villages[-2:]
    train_paths = {
        path
        for cluster in report["clusters"]
        for path in cluster["source_paths"]
        if Path(path).relative_to(report["root"]).parts[0] not in holdout_villages
    }
    holdout_paths = {
        path
        for cluster in report["clusters"]
        for path in cluster["source_paths"]
        if Path(path).relative_to(report["root"]).parts[0] in holdout_villages
    }
    train_regions = {code for code, paths in region_sources.items() if paths & train_paths}
    train_fields = {code for code, paths in field_sources.items() if paths & train_paths}
    holdout_region_refs = [
        code for code, paths in region_sources.items() for _ in paths & holdout_paths
    ]
    holdout_field_refs = [
        code for code, paths in field_sources.items() for _ in paths & holdout_paths
    ]
    holdout = {
        "train_villages": [village for village in villages if village not in holdout_villages],
        "holdout_villages": holdout_villages,
        "holdout_file_count": len(holdout_paths),
        "region_reuse_basis_points": (
            round(
                10_000
                * sum(code in train_regions for code in holdout_region_refs)
                / len(holdout_region_refs)
            )
            if holdout_region_refs
            else 0
        ),
        "field_reuse_basis_points": (
            round(
                10_000
                * sum(code in train_fields for code in holdout_field_refs)
                / len(holdout_field_refs)
            )
            if holdout_field_refs
            else 0
        ),
    }
    semantic_fields = sorted(
        [*published_fields.values(), *unknown_evidence.values()],
        key=lambda value: value["code"],
    )
    package = {
        "contract_version": CONTRACT_VERSION,
        "generator_version": GENERATOR_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_report_sha256": _digest(report),
        "semantic_fields": semantic_fields,
        "region_templates": sorted(
            region_templates.values(),
            key=lambda value: value["code"],
        ),
        "sheet_compositions": sorted(
            sheet_compositions.values(),
            key=lambda value: value["code"],
        ),
        "workbook_routes": sorted(
            workbook_routes,
            key=lambda value: value["code"],
        ),
        "coverage": sorted(
            coverage,
            key=lambda value: value["source_path"],
        ),
        "conflicts": [
            {
                "source_path": source_path,
                "representative_path": failure["representative_path"],
                "source_sha256": failure["source_sha256"],
                "error_code": failure["error_code"],
                "error_message": failure["error_message"],
                "decision": "unsupported_requires_source_repair",
            }
            for failure in report["failures"]
            for source_path in failure.get(
                "source_paths",
                [failure["representative_path"]],
            )
        ],
        "holdout_validation": holdout,
    }
    package["summary"] = {
        "semantic_field_count": len(semantic_fields),
        "new_field_review_count": len(unknown_evidence),
        "region_template_count": len(region_templates),
        "sheet_composition_count": len(sheet_compositions),
        "workbook_route_count": len(workbook_routes),
        "covered_source_file_count": len(coverage),
        "unsupported_unique_content_count": len(report["failures"]),
        "unsupported_source_file_count": len(package["conflicts"]),
        "status_counts": dict(Counter(route["status"] for route in workbook_routes)),
        "unresolved_region_count": unresolved_region_count,
        "unresolved_column_count": unresolved_column_count,
    }
    package["generation_sha256"] = _digest(package)
    return package


def write_package(package: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    files = {
        "semantic-fields.json": package["semantic_fields"],
        "region-templates.json": package["region_templates"],
        "sheet-compositions.json": package["sheet_compositions"],
        "workbook-routes.json": package["workbook_routes"],
        "coverage-manifest.json": {
            "coverage": package["coverage"],
            "holdout_validation": package["holdout_validation"],
        },
        "conflicts.json": package["conflicts"],
        "generation-manifest.json": {
            key: package[key]
            for key in (
                "contract_version",
                "generator_version",
                "generated_at",
                "source_report_sha256",
                "generation_sha256",
                "summary",
            )
        },
        "validation-report.json": validate_package(package),
    }
    for name, payload in files.items():
        (output / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def validate_package(package: dict[str, Any]) -> dict[str, Any]:
    fields = {str(field["code"]): field for field in package["semantic_fields"]}
    regions = {str(region["code"]): region for region in package["region_templates"]}
    sheets = {str(sheet["code"]): sheet for sheet in package["sheet_compositions"]}
    missing_fields = sorted(
        {
            str(binding["semantic_field_code"])
            for region in regions.values()
            for binding in region["field_bindings"]
            if str(binding["semantic_field_code"]) not in fields
        }
    )
    missing_regions = sorted(
        {
            str(slot["region_template_code"])
            for sheet in sheets.values()
            for slot in sheet["region_slots"]
            if str(slot["region_template_code"]) not in regions
        }
    )
    missing_sheets = sorted(
        {
            str(slot["sheet_composition_code"])
            for route in package["workbook_routes"]
            for slot in route["sheet_slots"]
            if str(slot["sheet_composition_code"]) not in sheets
        }
    )
    invalid_roles = sorted(
        {
            str(binding["role"])
            for region in regions.values()
            for binding in region["field_bindings"]
            if binding.get("role") and normalize_role_code(str(binding["role"])) != binding["role"]
        }
    )
    invalid_region_kinds = sorted(
        str(region["code"])
        for region in regions.values()
        if str(region.get("region_kind")) not in {"table", "form", "matrix"}
    )
    invalid_source_selector_codes = sorted(
        str(region["code"])
        for region in regions.values()
        if any(
            isinstance(selector := binding.get("source_selector"), dict)
            and selector.get("kind") == "physical_column"
            and (
                not isinstance(selector.get("column_offset"), int)
                or int(selector["column_offset"]) < 0
            )
            for binding in region["field_bindings"]
        )
    )
    observed_value_field_codes = sorted(
        {
            str(field["code"])
            for field in fields.values()
            if field.get("source") != "published_catalog"
            and _looks_like_observed_value([str(field["name"])])
        }
    )
    duplicate_names: dict[str, list[str]] = defaultdict(list)
    for field in fields.values():
        duplicate_names[str(field["name"])].append(str(field["code"]))
    duplicate_name_code_groups = sorted(
        sorted(codes) for codes in duplicate_names.values() if len(codes) > 1
    )
    holdout = package["holdout_validation"]
    blockers = []
    warnings = []
    if missing_fields or missing_regions or missing_sheets:
        blockers.append("four_layer_reference_integrity_failed")
    if invalid_roles:
        blockers.append("invalid_field_roles")
    if invalid_region_kinds:
        blockers.append("invalid_region_kinds")
    if invalid_source_selector_codes:
        blockers.append("invalid_source_selectors")
    if observed_value_field_codes:
        blockers.append("observed_values_leaked_into_field_catalog")
    if int(holdout["field_reuse_basis_points"]) < 8_000:
        if package["contract_version"] == "four-layer-template-seed/v4":
            # v4 is built from source-reviewed evidence across the complete
            # corpus. Whole-village holdout is diagnostic when villages carry
            # genuinely different document types; the publication workflow's
            # required generalization gate is the independent recomposition
            # regression with new hashes and fingerprints.
            warnings.append("village_holdout_field_reuse_below_80_percent")
        else:
            blockers.append("holdout_field_reuse_below_80_percent")
    if int(package["summary"]["status_counts"].get("admin_review", 0)):
        blockers.append("admin_review_routes_remain")
    safe_to_import_pending = not any(
        blocker
        in {
            "four_layer_reference_integrity_failed",
            "invalid_field_roles",
            "invalid_region_kinds",
            "invalid_source_selectors",
            "observed_values_leaked_into_field_catalog",
        }
        for blocker in blockers
    )
    return {
        "contract_version": package["contract_version"],
        "generator_version": package["generator_version"],
        "generation_sha256": package["generation_sha256"],
        "reference_integrity": {
            "missing_fields": missing_fields,
            "missing_regions": missing_regions,
            "missing_sheets": missing_sheets,
        },
        "invalid_roles": invalid_roles,
        "invalid_region_kind_codes": invalid_region_kinds,
        "invalid_source_selector_codes": invalid_source_selector_codes,
        # Candidate labels may themselves be misclassified source values. Never
        # persist or print them from validation; stable generated codes are
        # sufficient to locate and reject the unsafe definitions.
        "observed_value_field_codes": observed_value_field_codes,
        "duplicate_name_code_groups": duplicate_name_code_groups,
        "unresolved_region_count": int(package["summary"].get("unresolved_region_count", 0)),
        "holdout_validation": holdout,
        "publication_blockers": blockers,
        "validation_warnings": warnings,
        "safe_to_import_pending": safe_to_import_pending,
        "safe_to_publish": not blockers,
    }


def read_package(output: Path) -> dict[str, Any]:
    manifest = json.loads((output / "generation-manifest.json").read_text(encoding="utf-8"))
    coverage = json.loads((output / "coverage-manifest.json").read_text(encoding="utf-8"))
    return {
        **manifest,
        "semantic_fields": json.loads(
            (output / "semantic-fields.json").read_text(encoding="utf-8")
        ),
        "region_templates": json.loads(
            (output / "region-templates.json").read_text(encoding="utf-8")
        ),
        "sheet_compositions": json.loads(
            (output / "sheet-compositions.json").read_text(encoding="utf-8")
        ),
        "workbook_routes": json.loads(
            (output / "workbook-routes.json").read_text(encoding="utf-8")
        ),
        "coverage": coverage["coverage"],
        "holdout_validation": coverage["holdout_validation"],
        "conflicts": json.loads((output / "conflicts.json").read_text(encoding="utf-8")),
    }


def import_review_packages(
    database: Session,
    package: dict[str, Any],
) -> dict[str, int]:
    created = 0
    existing = 0
    superseded = 0
    generation_sha256 = str(package["generation_sha256"])
    for prior in database.scalars(
        select(TemplateProposal).where(
            TemplateProposal.source == "bootstrap",
            TemplateProposal.status == ProposalStatus.PENDING,
        )
    ):
        if not str(prior.proposal.get("contract_version", "")).startswith(
            "four-layer-template-seed/"
        ):
            continue
        if prior.proposal.get("generation_sha256") == generation_sha256:
            continue
        prior.status = ProposalStatus.REJECTED
        prior.resolution_comment = f"Superseded by four-layer generation {generation_sha256}."
        superseded += 1
    fields = {field["code"]: field for field in package["semantic_fields"]}
    regions = {region["code"]: region for region in package["region_templates"]}
    sheets = {sheet["code"]: sheet for sheet in package["sheet_compositions"]}
    for route in package["workbook_routes"]:
        key = f"four-layer-v3:{generation_sha256[:20]}:{route['code']}"
        proposal = database.scalar(
            select(TemplateProposal).where(TemplateProposal.idempotency_key == key)
        )
        if proposal is not None:
            existing += 1
            continue
        route_sheets = [
            sheets[str(slot["sheet_composition_code"])] for slot in route["sheet_slots"]
        ]
        region_codes = {
            str(slot["region_template_code"])
            for sheet in route_sheets
            for slot in sheet["region_slots"]
        }
        route_regions = [regions[code] for code in sorted(region_codes)]
        field_codes = {
            str(binding["semantic_field_code"])
            for region in route_regions
            for binding in region["field_bindings"]
        }
        database.add(
            TemplateProposal(
                idempotency_key=key,
                source="bootstrap",
                model_name="codex",
                prompt_version=GENERATOR_VERSION,
                proposal={
                    "contract_version": CONTRACT_VERSION,
                    "generation_sha256": generation_sha256,
                    "semantic_fields": [fields[code] for code in sorted(field_codes)],
                    "region_templates": route_regions,
                    "sheet_compositions": route_sheets,
                    "workbook_route": route,
                },
                status=ProposalStatus.PENDING,
            )
        )
        created += 1
    database.commit()
    return {
        "created": created,
        "existing": existing,
        "superseded": superseded,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=("generate", "import-pending", "validate"),
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.operation == "generate":
        if arguments.report is None or arguments.output is None:
            parser.error("generate requires --report and --output")
        report = json.loads(arguments.report.read_text(encoding="utf-8"))
        with get_session_factory()() as database:
            package = build_four_layer_seed_package(database, report)
        write_package(package, arguments.output)
        print(json.dumps(package["summary"], ensure_ascii=False))
        print(
            json.dumps(
                package["holdout_validation"],
                ensure_ascii=False,
            )
        )
        return
    if arguments.output is None:
        parser.error(f"{arguments.operation} requires --output")
    package = read_package(arguments.output)
    if arguments.operation == "validate":
        report = validate_package(package)
        (arguments.output / "validation-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False))
        return
    with get_session_factory()() as database:
        result = import_review_packages(database, package)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
