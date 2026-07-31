from __future__ import annotations

import argparse
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    ApprovedImportPlan,
    DocumentProfile,
    DocumentTemplate,
    ImportExecution,
    TemplateVersion,
)
from village_insight.db.session import get_session_factory
from village_insight.parsing.profile_storage import load_workbook_profile
from village_insight.templates.lifecycle import transition_template
from village_insight.templates.matching import MATCHER_VERSION, layout_fingerprint


def upgrade_published_fingerprints(
    database: Session,
    *,
    actor: str = "system:matcher-upgrade",
) -> list[dict[str, object]]:
    upgraded: list[dict[str, object]] = []
    templates = list(database.scalars(select(DocumentTemplate).order_by(DocumentTemplate.code)))
    for template in templates:
        if template.published_version is None:
            continue
        current = database.scalar(
            select(TemplateVersion).where(
                TemplateVersion.template_id == template.id,
                TemplateVersion.version == template.published_version,
            )
        )
        if current is None:
            continue
        source_item_id = current.source_metadata.get("source_item_id")
        if not source_item_id:
            continue
        profile_record = database.get(DocumentProfile, uuid.UUID(str(source_item_id)))
        if profile_record is None:
            continue
        successful_plan = database.scalar(
            select(ApprovedImportPlan)
            .join(
                ImportExecution,
                ImportExecution.approved_plan_id == ApprovedImportPlan.id,
            )
            .where(
                ApprovedImportPlan.item_id == profile_record.item_id,
                ApprovedImportPlan.template_id == template.id,
                ImportExecution.status == "completed",
            )
            .order_by(ApprovedImportPlan.revision.desc())
            .limit(1)
        )
        approved_layout = current.source_metadata.get("approved_layout_plan")
        if successful_plan is not None:
            approved_layout = successful_plan.layout_plan.get(
                "decisions",
                approved_layout,
            )
        fingerprint = layout_fingerprint(load_workbook_profile(profile_record))
        if (
            current.layout_fingerprint == fingerprint
            and current.source_metadata.get("matcher_version") == MATCHER_VERSION
            and current.source_metadata.get("approved_layout_plan") == approved_layout
        ):
            continue
        version = TemplateVersion(
            template_id=template.id,
            version=max(item.version for item in template.versions) + 1,
            name=current.name,
            description=current.description,
            layout_fingerprint=fingerprint,
            definition=current.definition,
            source="rule",
            source_metadata={
                **current.source_metadata,
                "approved_layout_plan": approved_layout,
                "matcher_version": MATCHER_VERSION,
                "supersedes_template_version": current.version,
                "fingerprint_upgrade_only": (
                    current.source_metadata.get("approved_layout_plan") == approved_layout
                ),
                "upgraded_by": actor,
            },
        )
        database.add(version)
        database.flush()
        for action in ("confirm", "submit_review", "approve"):
            transition_template(
                database,
                template=template,
                version=version,
                action=action,
                actor=actor,
                comment=(
                    f"Recomputed immutable layout fingerprint with {MATCHER_VERSION}; "
                    "semantic bindings are unchanged."
                ),
            )
        upgraded.append(
            {
                "template_code": template.code,
                "from_version": current.version,
                "to_version": version.version,
                "layout_fingerprint": fingerprint,
            }
        )
    database.commit()
    return upgraded


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply safe catalog maintenance operations.")
    parser.add_argument(
        "operation",
        choices=["upgrade-layout-fingerprints"],
    )
    parser.add_argument("--actor", default="system:matcher-upgrade")
    arguments = parser.parse_args()
    with get_session_factory()() as database:
        rows = upgrade_published_fingerprints(database, actor=arguments.actor)
    print(json.dumps({"updated": len(rows), "templates": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
