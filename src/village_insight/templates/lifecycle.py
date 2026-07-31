from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from village_insight.db.models import (
    DocumentTemplate,
    RegionTemplate,
    RegionTemplateReviewEvent,
    RegionTemplateVersion,
    SemanticField,
    SemanticFieldReviewEvent,
    SemanticFieldVersion,
    SheetComposition,
    SheetCompositionReviewEvent,
    SheetCompositionVersion,
    TemplateReviewEvent,
    TemplateStatus,
    TemplateVersion,
    WorkbookRoute,
    WorkbookRouteReviewEvent,
    WorkbookRouteVersion,
)
from village_insight.templates.contracts import RegionTemplateDefinition, TemplateDefinition
from village_insight.templates.import_plans import (
    ImportPlanError,
    ensure_layout_projection_snapshot,
)
from village_insight.templates.matching import ensure_region_components


class LifecycleError(ValueError):
    pass


_TEMPLATE_TRANSITIONS: dict[str, tuple[str, str]] = {
    "confirm": (TemplateStatus.DRAFT, TemplateStatus.USER_CONFIRMED),
    "submit_review": (TemplateStatus.USER_CONFIRMED, TemplateStatus.ADMIN_REVIEW),
    "approve": (TemplateStatus.ADMIN_REVIEW, TemplateStatus.PUBLISHED),
    "reject": (TemplateStatus.ADMIN_REVIEW, TemplateStatus.DRAFT),
    "deprecate": (TemplateStatus.PUBLISHED, TemplateStatus.DEPRECATED),
}


def validate_template_fields(database: Session, definition: TemplateDefinition) -> None:
    for binding in definition.field_bindings:
        field = database.scalar(
            select(SemanticField).where(SemanticField.code == binding.semantic_field_code)
        )
        if field is None or field.published_version != binding.semantic_field_version:
            raise LifecycleError(
                f"field {binding.semantic_field_code} version "
                f"{binding.semantic_field_version} is not published"
            )


def transition_template(
    database: Session,
    *,
    template: DocumentTemplate,
    version: TemplateVersion,
    action: str,
    actor: str,
    comment: str,
    actor_type: str = "user",
    actor_user_id: uuid.UUID | None = None,
) -> None:
    transition = _TEMPLATE_TRANSITIONS.get(action)
    if transition is None:
        raise LifecycleError(f"unsupported template action: {action}")
    expected, target = transition
    if version.status != expected:
        raise LifecycleError(f"cannot {action} template version in {version.status} state")
    if action == "reject" and not comment.strip():
        raise LifecycleError("reject requires a comment")
    if action == "approve":
        validate_template_fields(
            database,
            TemplateDefinition.model_validate(version.definition),
        )
        try:
            ensure_layout_projection_snapshot(database, version)
            ensure_region_components(database, version)
        except ImportPlanError as exc:
            raise LifecycleError(str(exc)) from exc
        previously_published = database.scalar(
            select(TemplateVersion).where(
                TemplateVersion.template_id == template.id,
                TemplateVersion.status == TemplateStatus.PUBLISHED,
            )
        )
        if previously_published is not None:
            previously_published.status = TemplateStatus.DEPRECATED
        template.published_version = version.version
    elif action == "deprecate":
        template.published_version = None

    previous = version.status
    version.status = target
    database.add(
        TemplateReviewEvent(
            template_version_id=version.id,
            action=action,
            from_status=previous,
            to_status=target,
            actor=actor,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            comment=comment,
        )
    )


def publish_field(
    database: Session,
    *,
    field: SemanticField,
    version: SemanticFieldVersion,
    actor: str,
    comment: str,
    actor_type: str = "user",
    actor_user_id: uuid.UUID | None = None,
) -> None:
    if version.status != TemplateStatus.DRAFT:
        raise LifecycleError(f"cannot publish field version in {version.status} state")
    previous_version = database.scalar(
        select(SemanticFieldVersion).where(
            SemanticFieldVersion.field_id == field.id,
            SemanticFieldVersion.status == TemplateStatus.PUBLISHED,
        )
    )
    if previous_version is not None:
        previous_version.status = TemplateStatus.DEPRECATED
    version.status = TemplateStatus.PUBLISHED
    field.published_version = version.version
    database.add(
        SemanticFieldReviewEvent(
            field_version_id=version.id,
            action="publish",
            from_status=TemplateStatus.DRAFT,
            to_status=TemplateStatus.PUBLISHED,
            actor=actor,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            comment=comment,
        )
    )


def publish_region_template(
    database: Session,
    *,
    template: RegionTemplate,
    version: RegionTemplateVersion,
    actor: str,
    comment: str,
    actor_type: str = "user",
    actor_user_id: uuid.UUID | None = None,
) -> None:
    if version.status != TemplateStatus.DRAFT:
        raise LifecycleError(
            f"cannot publish region template version in {version.status} state"
        )
    definition = RegionTemplateDefinition(
        domain=version.domain,
        region_kind=version.region_kind,
        record_type=version.record_type,
        record_grain=version.record_grain,
        header_signature=version.header_signature,
        layout_rules=version.layout_rules,
        field_bindings=version.field_bindings,
        identity_policy=version.identity_policy,
        quality_rules=version.quality_rules,
    )
    validate_template_fields(
        database,
        TemplateDefinition(
            domain=definition.domain,
            region_kind=definition.region_kind,
            record_type=definition.record_type,
            record_grain=definition.record_grain,
            field_bindings=definition.field_bindings,
        ),
    )
    previous_version = database.scalar(
        select(RegionTemplateVersion).where(
            RegionTemplateVersion.region_template_id == template.id,
            RegionTemplateVersion.status == TemplateStatus.PUBLISHED,
        )
    )
    if previous_version is not None:
        previous_version.status = TemplateStatus.DEPRECATED
    version.status = TemplateStatus.PUBLISHED
    template.published_version = version.version
    database.add(
        RegionTemplateReviewEvent(
            region_template_version_id=version.id,
            action="publish",
            from_status=TemplateStatus.DRAFT,
            to_status=TemplateStatus.PUBLISHED,
            actor=actor,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            comment=comment,
        )
    )


def publish_sheet_composition(
    database: Session,
    *,
    composition: SheetComposition,
    version: SheetCompositionVersion,
    actor: str,
    comment: str,
    actor_type: str = "user",
    actor_user_id: uuid.UUID | None = None,
) -> None:
    if version.status != TemplateStatus.DRAFT:
        raise LifecycleError(
            f"cannot publish sheet composition version in {version.status} state"
        )
    for slot in version.region_slots:
        dependency = database.scalar(
            select(RegionTemplateVersion).where(
                RegionTemplateVersion.region_template_id
                == slot.region_template_id,
                RegionTemplateVersion.version == slot.region_template_version,
                RegionTemplateVersion.status == TemplateStatus.PUBLISHED,
            )
        )
        if dependency is None:
            raise LifecycleError(
                f"Region slot {slot.slot_key} does not reference a published version"
            )
    previous_version = database.scalar(
        select(SheetCompositionVersion).where(
            SheetCompositionVersion.sheet_composition_id == composition.id,
            SheetCompositionVersion.status == TemplateStatus.PUBLISHED,
        )
    )
    if previous_version is not None:
        previous_version.status = TemplateStatus.DEPRECATED
    version.status = TemplateStatus.PUBLISHED
    composition.published_version = version.version
    database.add(
        SheetCompositionReviewEvent(
            sheet_composition_version_id=version.id,
            action="publish",
            from_status=TemplateStatus.DRAFT,
            to_status=TemplateStatus.PUBLISHED,
            actor=actor,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            comment=comment,
        )
    )


def publish_workbook_route(
    database: Session,
    *,
    route: WorkbookRoute,
    version: WorkbookRouteVersion,
    actor: str,
    comment: str,
    actor_type: str = "user",
    actor_user_id: uuid.UUID | None = None,
) -> None:
    if version.status != TemplateStatus.DRAFT:
        raise LifecycleError(
            f"cannot publish workbook route version in {version.status} state"
        )
    for slot in version.sheet_slots:
        dependency = database.scalar(
            select(SheetCompositionVersion).where(
                SheetCompositionVersion.sheet_composition_id
                == slot.sheet_composition_id,
                SheetCompositionVersion.version
                == slot.sheet_composition_version,
                SheetCompositionVersion.status == TemplateStatus.PUBLISHED,
            )
        )
        if dependency is None:
            raise LifecycleError(
                f"Sheet slot {slot.slot_key} does not reference a published version"
            )
    previous_version = database.scalar(
        select(WorkbookRouteVersion).where(
            WorkbookRouteVersion.workbook_route_id == route.id,
            WorkbookRouteVersion.status == TemplateStatus.PUBLISHED,
        )
    )
    if previous_version is not None:
        previous_version.status = TemplateStatus.DEPRECATED
    version.status = TemplateStatus.PUBLISHED
    route.published_version = version.version
    database.add(
        WorkbookRouteReviewEvent(
            workbook_route_version_id=version.id,
            action="publish",
            from_status=TemplateStatus.DRAFT,
            to_status=TemplateStatus.PUBLISHED,
            actor=actor,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            comment=comment,
        )
    )
