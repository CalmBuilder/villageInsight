from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from village_insight.db.session import get_session_factory

CONTRACT_VERSION = "four-layer-source-review/v1"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _structure_decisions(database: Session) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    rows = database.execute(
        text(
            "SELECT response_payload "
            "FROM hermes_recognition_cache "
            "WHERE prompt_version = 'template-diff/v19' "
            "AND schema_version = 'workbook-structure-checkpoint/v2' "
            "ORDER BY created_at"
        )
    )
    for (payload,) in rows:
        structure = (payload or {}).get("structure_decision") or {}
        for decision in structure.get("layout_decisions", []):
            region_id = str(decision.get("region_candidate_id") or "")
            if not region_id:
                continue
            stable = {
                key: decision.get(key)
                for key in (
                    "classification",
                    "materialize",
                    "confidence",
                    "header_candidate_id",
                    "data_start_row",
                    "data_end_row",
                )
            }
            previous = decisions.get(region_id)
            if previous is not None and previous != stable:
                raise ValueError(
                    "conflicting structure checkpoints for one Region; "
                    f"region_id={region_id}"
                )
            decisions[region_id] = stable
    return decisions


def _plan_and_profile_by_sha(
    database: Session,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    plans = {
        str(row.source_sha256): row.layout_plan
        for row in database.execute(
            text(
                "SELECT DISTINCT ON (source_sha256) source_sha256, layout_plan "
                "FROM approved_import_plans "
                "ORDER BY source_sha256, revision DESC"
            )
        )
    }
    profiles = {
        str(row.source_sha256): row.profile
        for row in database.execute(
            text(
                "SELECT i.source_sha256, p.profile "
                "FROM ingestion_items i "
                "JOIN document_profiles p ON p.item_id = i.id"
            )
        )
    }
    return plans, profiles


def build_source_review(database: Session, *, v3_directory: Path) -> dict[str, Any]:
    routes = _load_json(v3_directory / "workbook-routes.json")
    plans, profiles = _plan_and_profile_by_sha(database)
    structure_by_region = _structure_decisions(database)
    reviewed_routes: list[dict[str, Any]] = []
    create_count = 0
    discard_count = 0

    for route in routes:
        unresolved = route.get("unresolved_regions") or []
        if not unresolved:
            continue
        members = route.get("members") or []
        if len(members) != 1:
            raise ValueError("an unresolved v3 route must have one representative member")
        member = members[0]
        source_sha256 = str(member["source_sha256"])
        plan = plans.get(source_sha256)
        profile = profiles.get(source_sha256)
        if plan is None or profile is None:
            raise ValueError(
                "run evidence is missing for an unresolved v3 route; "
                f"source_sha256={source_sha256}"
            )
        plan_by_region = {
            str(decision["region_candidate_id"]): decision
            for decision in plan.get("decisions", [])
        }
        decisions: list[dict[str, Any]] = []
        for unresolved_region in unresolved:
            region_id = str(unresolved_region["region_id"])
            sheet_index = int(unresolved_region["sheet_index"])
            sheet = profile["sheets"][sheet_index]
            region = next(
                (
                    candidate
                    for candidate in sheet["region_candidates"]
                    if candidate["id"] == region_id
                ),
                None,
            )
            if region is None:
                raise ValueError(f"unresolved Region is absent from profile; region_id={region_id}")
            structure = structure_by_region.get(region_id)
            if structure is None:
                raise ValueError(
                    "unresolved Region has no deterministic structure checkpoint; "
                    f"region_id={region_id}"
                )
            approved = plan_by_region.get(region_id)
            if approved is not None:
                if (
                    approved.get("classification") != "table"
                    or not approved.get("materialize")
                    or not approved.get("header_candidate_id")
                ):
                    raise ValueError(
                        "source review cannot safely convert a non-table approved Region; "
                        f"region_id={region_id}"
                    )
                codex_decision = {
                    "action": "create_template",
                    "reason": "run006_approved_materialized_table",
                    "layout_mode": "table",
                    "header_candidate_id": approved["header_candidate_id"],
                    "data_start_row": int(approved["data_start_row"]),
                    "data_end_row": int(approved["data_end_row"]),
                    "excluded_rows": [
                        int(row) for row in approved.get("excluded_rows", [])
                    ],
                }
                create_count += 1
            else:
                if structure.get("classification") != "noise" or structure.get(
                    "materialize"
                ):
                    raise ValueError(
                        "Region is neither approved for materialization nor explicitly noise; "
                        f"region_id={region_id}"
                    )
                codex_decision = {
                    "action": "discard_false_positive",
                    "reason": "run006_structure_checkpoint_explicit_noise",
                }
                discard_count += 1
            decisions.append(
                {
                    "sheet_index": sheet_index,
                    "region_candidate_id": region_id,
                    "region_range": region["bounds"]["range"],
                    "codex_decision": codex_decision,
                    "hermes_second_opinion": {
                        "classification": structure.get("classification"),
                        "materialize": structure.get("materialize"),
                        "confidence_basis_points": round(
                            float(structure.get("confidence") or 0) * 10_000
                        ),
                        "evidence_source": "run006_structure_checkpoint",
                    },
                }
            )
        reviewed_routes.append(
            {
                "route_code": route["code"],
                "source_path": member["representative_path"],
                "source_sha256": source_sha256,
                "decisions": decisions,
            }
        )

    unresolved_count = sum(
        len(route.get("unresolved_regions") or []) for route in routes
    )
    if create_count + discard_count != unresolved_count:
        raise ValueError("source review did not close every unresolved Region")
    return {
        "contract_version": CONTRACT_VERSION,
        "summary": {
            "input_unresolved_region_count": unresolved_count,
            "created_template_region_count": create_count,
            "discarded_false_positive_region_count": discard_count,
            "remaining_unresolved_region_count": 0,
            "decision_basis": (
                "run006 approved materialization plans plus validated structure checkpoints"
            ),
        },
        "routes": reviewed_routes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v3-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    with get_session_factory()() as database:
        review = build_source_review(database, v3_directory=arguments.v3_directory)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(review["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
