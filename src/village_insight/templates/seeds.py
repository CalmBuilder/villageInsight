from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    DocumentTemplate,
    ProposalStatus,
    SemanticField,
    SemanticFieldVersion,
    TemplateProposal,
    TemplateStatus,
    TemplateVersion,
    utcnow,
)
from village_insight.db.session import get_session_factory
from village_insight.templates.matching import MATCHER_VERSION

SEED_CONTRACT_VERSION = "template-seed-manifest/v1"


def _normalized_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _catalog_aliases(database: Session) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = defaultdict(set)
    rows = database.execute(
        select(SemanticField, SemanticFieldVersion).where(
            SemanticField.id == SemanticFieldVersion.field_id,
            SemanticField.published_version == SemanticFieldVersion.version,
            SemanticFieldVersion.status == TemplateStatus.PUBLISHED,
        )
    )
    for field, version in rows:
        for label in [version.name, *version.aliases]:
            aliases[_normalized_label(label)].add(field.code)
    published_templates = database.scalars(
        select(TemplateVersion)
        .join(DocumentTemplate)
        .where(
            TemplateVersion.status == TemplateStatus.PUBLISHED,
            DocumentTemplate.published_version == TemplateVersion.version,
        )
    )
    for version in published_templates:
        for binding in version.definition.get("field_bindings", []):
            path = [str(part) for part in binding.get("header_path", []) if str(part)]
            for label in [*path, " / ".join(path)]:
                aliases[_normalized_label(label)].add(str(binding["semantic_field_code"]))
    return aliases


def generate_seed_manifest(
    database: Session,
    report: dict[str, Any],
) -> dict[str, Any]:
    aliases = _catalog_aliases(database)
    published = {
        version.layout_fingerprint: {
            "template_id": str(version.template_id),
            "template_version": version.version,
            "template_code": version.template.code,
        }
        for version in database.scalars(
            select(TemplateVersion)
            .join(DocumentTemplate)
            .where(
                TemplateVersion.status == TemplateStatus.PUBLISHED,
                DocumentTemplate.published_version == TemplateVersion.version,
            )
        )
    }
    candidates = []
    for cluster in report["clusters"]:
        fingerprint = str(cluster["layout_fingerprint"])
        exact = published.get(fingerprint)
        evidence = cluster["representative_evidence"]
        fields = []
        for column in evidence["header_columns"]:
            path = [str(part) for part in column["header_path"]]
            keys = {_normalized_label(path[-1]), _normalized_label(" / ".join(path))}
            matched_codes = sorted({code for key in keys for code in aliases.get(key, set())})
            fields.append(
                {
                    "source_column_id": column["source_column_id"],
                    "header_path": path,
                    "evidence_cell_ids": column["evidence_cell_ids"],
                    "action": ("reuse_candidate" if len(matched_codes) == 1 else "review_required"),
                    "semantic_field_candidates": matched_codes,
                    "suggested_semantic_field_code": (
                        matched_codes[0] if len(matched_codes) == 1 else None
                    ),
                    "role_hint": " / ".join(path[:-1]) or None,
                }
            )
        candidates.append(
            {
                "seed_id": f"layout:{fingerprint}",
                "review_status": ("published_match" if exact is not None else "pending"),
                "layout_fingerprint": fingerprint,
                "source_file_count": cluster["source_file_count"],
                "unique_content_count": cluster["unique_content_count"],
                "representative_path": cluster["representative_path"],
                "representative_source_sha256": evidence["source_sha256"],
                "suggested_name": Path(cluster["representative_path"]).stem[:200],
                "published_match": exact,
                "layout_plan_candidates": evidence["layout_candidates"],
                "field_decisions": fields,
                "members": cluster["members"],
                "review": {
                    "actor": None,
                    "comment": None,
                    "reviewed_at": None,
                },
            }
        )
    report_payload = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest: dict[str, Any] = {
        "contract_version": SEED_CONTRACT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "matcher_version": MATCHER_VERSION,
        "source_report_sha256": hashlib.sha256(report_payload).hexdigest(),
        "summary": {
            **report["summary"],
            "published_match_cluster_count": sum(
                candidate["review_status"] == "published_match" for candidate in candidates
            ),
            "pending_review_cluster_count": sum(
                candidate["review_status"] == "pending" for candidate in candidates
            ),
            "reuse_candidate_field_count": sum(
                field["action"] == "reuse_candidate"
                for candidate in candidates
                for field in candidate["field_decisions"]
            ),
            "review_required_field_count": sum(
                field["action"] == "review_required"
                for candidate in candidates
                for field in candidate["field_decisions"]
            ),
        },
        "template_seeds": candidates,
    }
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return manifest


def import_pending_seed_manifest(
    database: Session,
    manifest: dict[str, Any],
) -> dict[str, int]:
    if manifest.get("contract_version") != SEED_CONTRACT_VERSION:
        raise ValueError("unsupported template seed manifest contract")
    created = 0
    existing = 0
    skipped_published = 0
    superseded = 0
    source_report_sha256 = str(manifest["source_report_sha256"])
    prior_pending = database.scalars(
        select(TemplateProposal).where(
            TemplateProposal.source == "bootstrap",
            TemplateProposal.status == ProposalStatus.PENDING,
        )
    )
    for prior_proposal in prior_pending:
        if prior_proposal.proposal.get("source_report_sha256") == source_report_sha256:
            continue
        prior_proposal.status = ProposalStatus.REJECTED
        prior_proposal.resolution_comment = f"Superseded by corpus report {source_report_sha256}."
        prior_proposal.resolved_at = utcnow()
        superseded += 1
    for seed in manifest["template_seeds"]:
        if seed["review_status"] == "published_match":
            skipped_published += 1
            continue
        if seed["review_status"] != "pending":
            raise ValueError(f"seed {seed['seed_id']} must be pending or already published")
        idempotency_key = f"bootstrap:{source_report_sha256[:24]}:{seed['layout_fingerprint']}"
        existing_proposal = database.scalar(
            select(TemplateProposal).where(TemplateProposal.idempotency_key == idempotency_key)
        )
        if existing_proposal is not None:
            existing += 1
            continue
        database.add(
            TemplateProposal(
                idempotency_key=idempotency_key,
                source="bootstrap",
                model_name=None,
                prompt_version=SEED_CONTRACT_VERSION,
                confidence=None,
                proposal={
                    **seed,
                    "manifest_sha256": manifest["manifest_sha256"],
                    "source_report_sha256": source_report_sha256,
                },
                status=ProposalStatus.PENDING,
            )
        )
        created += 1
    database.commit()
    return {
        "created": created,
        "existing": existing,
        "skipped_published": skipped_published,
        "superseded": superseded,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage reviewable template seeds.")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("report", type=Path)
    generate.add_argument("--output", type=Path, required=True)
    import_pending = subparsers.add_parser("import-pending")
    import_pending.add_argument("manifest", type=Path)
    arguments = parser.parse_args()
    if arguments.operation == "generate":
        report = json.loads(arguments.report.read_text(encoding="utf-8"))
        with get_session_factory()() as database:
            manifest = generate_seed_manifest(database, report)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(manifest["summary"], ensure_ascii=False))
        return
    manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
    with get_session_factory()() as database:
        result = import_pending_seed_manifest(database, manifest)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
