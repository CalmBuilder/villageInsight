from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from openpyxl.utils.cell import range_boundaries
from pydantic import BaseModel, Field

from village_insight.config import get_settings
from village_insight.db.models import MatchType, TemplateMatch
from village_insight.db.session import get_session_factory
from village_insight.hermes.configuration import resolve_configuration
from village_insight.hermes.recognition import build_diff_request
from village_insight.hermes.runtime import (
    EmbeddedHermesRuntime,
    HermesCallPolicy,
)
from village_insight.parsing.router import ParserRouter
from village_insight.templates.matching import layout_fingerprint

STRUCTURE_DECISION_CONTRACT = "four-layer-structure-review/v1"


class CompactStructureDecision(BaseModel):
    region_candidate_id: str
    header_candidate_id: str
    materialize: bool
    classification: Literal["table", "form", "matrix", "noise"]
    data_start_row: int = Field(ge=1)
    data_end_row: int = Field(ge=1)
    excluded_rows: list[int] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _unresolved_route_inputs(package_directory: Path) -> list[dict[str, Any]]:
    routes = _read_json(package_directory / "workbook-routes.json")
    inputs: list[dict[str, Any]] = []
    for route in routes:
        unresolved = [
            entry
            for entry in route.get("unresolved_regions", [])
            if isinstance(entry, dict) and entry.get("region_id")
        ]
        if not unresolved:
            continue
        members = route.get("members", [])
        representative = next(
            (
                str(member["representative_path"])
                for member in members
                if isinstance(member, dict) and member.get("representative_path")
            ),
            None,
        )
        if representative is None:
            raise ValueError(f"route {route['code']} has no representative source file")
        inputs.append(
            {
                "route_code": str(route["code"]),
                "source_path": representative,
                "unresolved_regions": unresolved,
            }
        )
    return inputs


def _match_for_unresolved(
    *,
    profile_sha256: str,
    fingerprint: str,
    unresolved_regions: list[dict[str, Any]],
) -> TemplateMatch:
    return TemplateMatch(
        item_id=uuid.uuid5(uuid.NAMESPACE_URL, profile_sha256),
        source_sha256=profile_sha256,
        profile_contract_version="workbook-profile/v2",
        layout_fingerprint=fingerprint,
        match_type=MatchType.NONE,
        score_basis_points=0,
        differences={
            "unmatched_regions": [
                {
                    "region_id": str(entry["region_id"]),
                    "match_type": MatchType.NONE,
                    "differences": {
                        "new_headers": [
                            " / ".join(str(part) for part in path)
                            for path in entry.get("header_signature", [])
                            if isinstance(path, list)
                        ]
                    },
                }
                for entry in unresolved_regions
            ]
        },
        requires_hermes=True,
        matcher_version="four-layer-bootstrap-structure/v1",
        total_regions=len(unresolved_regions),
        matched_regions=0,
        coverage_basis_points=0,
    )


def _compact_structure_payload(request: Any) -> dict[str, Any]:
    sheet_ids = {region.sheet_id for region in request.regions}
    return {
        "profile_contract_version": request.profile_contract_version,
        "parser_name": request.parser_name,
        "parser_version": request.parser_version,
        "sheets": [
            sheet.model_dump(mode="json")
            for sheet in request.sheets
            if sheet.sheet_id in sheet_ids
        ],
        "regions": [
            region.model_dump(mode="json")
            for region in request.regions
        ],
        "headers": [
            header.model_dump(mode="json")
            for header in request.headers
        ],
        "source_samples": [
            sample.model_dump(mode="json")
            for sample in request.source_samples
        ],
        "range_evidence": [
            evidence.model_dump(mode="json")
            for evidence in request.range_evidence
            if evidence.sheet_id in sheet_ids
        ],
    }


def _validate_compact_structure(
    request: Any,
    decision: CompactStructureDecision,
) -> None:
    if len(request.regions) != 1:
        raise ValueError("compact structure review requires exactly one Region")
    region = request.regions[0]
    if decision.region_candidate_id != region.candidate_id:
        raise ValueError("Hermes returned a different Region")
    header_ids = {
        header.header_candidate_id
        for header in request.headers
        if header.region_candidate_id == region.candidate_id
    }
    if decision.header_candidate_id not in header_ids:
        raise ValueError("Hermes returned a header outside the supplied Region")
    _, min_row, _, max_row = range_boundaries(region.range)
    if not (
        min_row
        <= decision.data_start_row
        <= decision.data_end_row
        <= max_row
    ):
        raise ValueError("Hermes returned data rows outside the supplied Region")
    if decision.materialize == (decision.classification == "noise"):
        raise ValueError("Hermes materialize decision conflicts with classification")
    if any(
        row < decision.data_start_row or row > decision.data_end_row
        for row in decision.excluded_rows
    ):
        raise ValueError("Hermes excluded rows outside the accepted data range")


def _header_end_row(request: Any, header_candidate_id: str) -> int:
    rows = [
        int(match.group(1))
        for header in request.headers
        if header.header_candidate_id == header_candidate_id
        for evidence_id in header.evidence_cell_ids
        if (match := re.search(r":r(\d+):c\d+$", evidence_id))
    ]
    if not rows:
        raise ValueError("selected header has no row evidence")
    return max(rows)


def _normalize_compact_structure(
    request: Any,
    decision: CompactStructureDecision,
) -> tuple[CompactStructureDecision, bool]:
    region = request.regions[0]
    _, min_row, _, max_row = range_boundaries(region.range)
    if not decision.materialize or decision.classification == "noise":
        start = min(max(decision.data_start_row, min_row), max_row)
        end = min(max(decision.data_end_row, start), max_row)
        normalized = decision.model_copy(
            update={
                "materialize": False,
                "classification": "noise",
                "data_start_row": start,
                "data_end_row": end,
                "excluded_rows": [],
            }
        )
        return normalized, normalized != decision
    header_end = _header_end_row(request, decision.header_candidate_id)
    first_data_row = min(max(header_end + 1, min_row), max_row)
    start = min(max(decision.data_start_row, first_data_row), max_row)
    end = min(max(decision.data_end_row, start), max_row)
    excluded = sorted(
        {
            row
            for row in decision.excluded_rows
            if start <= row <= end
        }
    )
    normalized = decision.model_copy(
        update={
            "data_start_row": start,
            "data_end_row": end,
            "excluded_rows": excluded,
        }
    )
    return normalized, normalized != decision


async def _review_one_region(
    *,
    runtime: EmbeddedHermesRuntime,
    request: Any,
    task_id: str,
) -> tuple[CompactStructureDecision, CompactStructureDecision, bool]:
    prompt = (
        "你只判断一个复杂 Excel 候选区域。输入已经包含文件中的 Sheet、候选范围、"
        "候选表头、前部行证据和脱敏数据样例。判断它是应入库的表格/表单/矩阵，还是"
        "由普通数据、标题、说明、空白或页脚造成的误识别。只返回一个紧凑决定；不要枚举"
        "每一行角色，不要复述表头或样例，不要输出解释。必须原样使用输入中的 Region "
        "和表头候选 ID。若保留，data_start_row 必须在表头最后一行之后，并给出数据结束"
        "行和需要排除的合计/说明行；若是噪声，materialize=false、classification=noise。"
    )
    payload = json.dumps(_compact_structure_payload(request), ensure_ascii=False)
    model_decision = await runtime.run_json(
        system_prompt=prompt,
        user_prompt=payload,
        output_model=CompactStructureDecision,
        policy=HermesCallPolicy(
            thinking_enabled=False,
            enabled_toolsets=(),
            repair_attempts=1,
            timeout_seconds=90,
            max_tokens=1200,
        ),
        task_id=task_id,
    )
    normalized_decision, normalized = _normalize_compact_structure(
        request,
        model_decision,
    )
    _validate_compact_structure(request, normalized_decision)
    return model_decision, normalized_decision, normalized


async def resolve_unresolved_structures(
    *,
    package_directory: Path,
    output: Path,
) -> dict[str, Any]:
    settings = get_settings()
    with get_session_factory()() as database:
        resolved = resolve_configuration(database, settings)
    runtime = EmbeddedHermesRuntime(settings, resolved.connection)
    prior: dict[str, dict[str, Any]] = {}
    if output.exists():
        payload = _read_json(output)
        prior = {
            str(item["route_code"]): item
            for item in payload.get("decisions", [])
            if isinstance(item, dict) and item.get("route_code")
        }
    decisions = dict(prior)
    for item in _unresolved_route_inputs(package_directory):
        route_code = str(item["route_code"])
        if route_code in decisions and decisions[route_code].get("status") == "resolved":
            continue
        path = Path(str(item["source_path"]))
        try:
            profile = ParserRouter().profile(path)
            region_decisions: list[dict[str, Any]] = []
            for unresolved in item["unresolved_regions"]:
                match = _match_for_unresolved(
                    profile_sha256=profile.source_sha256,
                    fingerprint=layout_fingerprint(profile),
                    unresolved_regions=[unresolved],
                )
                request = build_diff_request(profile, match)
                if len(request.regions) != 1 or not request.headers:
                    raise ValueError(
                        f"source profile cannot reconstruct {unresolved['region_id']}"
                    )
                model_decision, decision, normalized = await _review_one_region(
                    runtime=runtime,
                    request=request,
                    task_id=(
                        "four-layer-structure-"
                        f"{profile.source_sha256[:12]}-"
                        f"{str(unresolved['region_id']).rsplit(':', 1)[-1]}"
                    ),
                )
                region_decisions.append(
                    {
                        "unresolved_region": unresolved,
                        "request": _compact_structure_payload(request),
                        "model_decision": model_decision.model_dump(mode="json"),
                        "decision": decision.model_dump(mode="json"),
                        "normalized_by_backend": normalized,
                    }
                )
            decisions[route_code] = {
                **item,
                "status": "resolved",
                "source_sha256": profile.source_sha256,
                "models": [resolved.connection.fast_model or resolved.connection.model],
                "uncertain": any(
                    row["decision"]["confidence"] < 0.9
                    for row in region_decisions
                ),
                "region_decisions": region_decisions,
            }
        except Exception as exc:  # noqa: BLE001 - checkpoint every source failure
            decisions[route_code] = {
                **item,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        _write_json(
            output,
            {
                "contract_version": STRUCTURE_DECISION_CONTRACT,
                "provider": resolved.connection.provider,
                "fast_model": resolved.connection.fast_model
                or resolved.connection.model,
                "reasoning_model": resolved.connection.reasoning_model,
                "decisions": list(decisions.values()),
            },
        )
    rows = list(decisions.values())
    return {
        "route_count": len(rows),
        "resolved_count": sum(row.get("status") == "resolved" for row in rows),
        "failed_count": sum(row.get("status") == "failed" for row in rows),
        "uncertain_count": sum(
            row.get("status") == "resolved" and bool(row.get("uncertain"))
            for row in rows
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=("resolve-structures",),
    )
    parser.add_argument("--package-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.operation == "resolve-structures":
        result = asyncio.run(
            resolve_unresolved_structures(
                package_directory=arguments.package_directory,
                output=arguments.output,
            )
        )
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
