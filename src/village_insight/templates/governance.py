from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from village_insight.db.models import (
    DocumentProfile,
    FieldMatch,
    GovernanceFieldResolution,
    GovernanceResolution,
    RegionTemplate,
    RegionTemplateMatch,
    RegionTemplateVersion,
    SemanticField,
    SemanticFieldVersion,
    SemanticIgnoreRule,
    TemplateProposal,
    TemplateStatus,
)
from village_insight.db.schema import GovernanceFieldResolutionInput
from village_insight.parsing.candidates import select_header_candidates
from village_insight.parsing.contracts import WorkbookProfile
from village_insight.parsing.profile_storage import load_workbook_profile
from village_insight.templates.field_variants import build_field_variant
from village_insight.templates.lifecycle import publish_field, publish_region_template
from village_insight.templates.sources import (
    MANUAL_GOVERNANCE_SOURCE,
    source_metadata,
)


class GovernanceError(ValueError):
    pass


@dataclass(frozen=True)
class SourceColumnEvidence:
    sheet_id: str
    sheet_name: str
    region_id: str
    column_index: int
    column_coordinate: str
    header_path: list[str]


@dataclass
class GovernanceCommit:
    resolution: GovernanceResolution
    field_decisions: list[dict[str, Any]]
    field_versions: dict[str, int]


def _column_coordinate(index: int) -> str:
    value = index
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _source_columns(profile: WorkbookProfile) -> dict[str, SourceColumnEvidence]:
    return {
        column.source_column_id: SourceColumnEvidence(
            sheet_id=sheet.id,
            sheet_name=sheet.name,
            region_id=candidate.region_id,
            column_index=column.column,
            column_coordinate=_column_coordinate(column.column),
            header_path=[str(part) for part in column.header_path],
        )
        for sheet in profile.sheets
        for candidate in select_header_candidates(sheet.header_candidates)
        for column in candidate.columns
    }


def _variant_values(variant: Any) -> dict[str, Any]:
    return {
        "kind": variant.kind,
        "alias": variant.alias,
        "header_path": list(variant.header_path),
        "role": variant.role,
        "domain": variant.domain,
        "record_type": variant.record_type,
        "observed_data_type": variant.observed_data_type,
        "unit_dimension": variant.unit_dimension,
        "source": variant.source,
        "confidence_basis_points": variant.confidence_basis_points,
        "evidence": dict(variant.evidence),
    }


def _load_field(database: Session, code: str) -> SemanticField | None:
    return database.scalar(
        select(SemanticField)
        .where(SemanticField.code == code)
        .options(
            selectinload(SemanticField.versions).selectinload(
                SemanticFieldVersion.variants
            )
        )
    )


def _publish_field_learning(
    database: Session,
    *,
    field: SemanticField,
    expected_version: int | None,
    variants: list[dict[str, Any]],
    alias: str | None,
    actor: str,
    actor_user_id: uuid.UUID,
    comment: str,
) -> tuple[int, list[str]]:
    if field.published_version is None:
        raise GovernanceError(f"字段 {field.code} 尚未发布")
    if expected_version is not None and field.published_version != expected_version:
        raise GovernanceError(
            f"字段 {field.code} 已从 v{expected_version} 更新为 "
            f"v{field.published_version}，请刷新候选后重新确认"
        )
    current = next(
        version
        for version in field.versions
        if version.version == field.published_version
    )
    candidates = [build_field_variant(values) for values in variants]
    existing_keys = {variant.variant_key for variant in current.variants}
    new_variants = [
        variant for variant in candidates if variant.variant_key not in existing_keys
    ]
    next_aliases = list(current.aliases)
    if alias and alias not in next_aliases:
        next_aliases.append(alias)
    if not new_variants and next_aliases == current.aliases:
        return current.version, []

    next_version = max(version.version for version in field.versions) + 1
    version = SemanticFieldVersion(
        version=next_version,
        name=current.name,
        description=current.description,
        layer=current.layer,
        data_type=current.data_type,
        unit_dimension=current.unit_dimension,
        aliases=next_aliases,
        validators=list(current.validators),
        source=MANUAL_GOVERNANCE_SOURCE,
        source_metadata=source_metadata(
            source=MANUAL_GOVERNANCE_SOURCE,
            metadata={
                "learned_from_field_version": current.version,
                "actor": actor,
            },
        ),
    )
    copied = [build_field_variant(_variant_values(variant)) for variant in current.variants]
    by_key = {variant.variant_key: variant for variant in [*copied, *new_variants]}
    version.variants.extend(by_key.values())
    field.versions.append(version)
    database.flush()
    publish_field(
        database,
        field=field,
        version=version,
        actor=actor,
        actor_user_id=actor_user_id,
        comment=comment,
    )
    return version.version, [variant.variant_key for variant in new_variants]


def _create_and_publish_field(
    database: Session,
    *,
    decision: GovernanceFieldResolutionInput,
    evidence: SourceColumnEvidence,
    observed_data_type: str | None,
    domain: str,
    record_type: str,
    actor: str,
    actor_user_id: uuid.UUID,
    proposal_id: uuid.UUID,
    item_id: uuid.UUID,
) -> tuple[SemanticField, int, list[str]]:
    code = decision.new_field_code
    if (
        code is None
        or decision.new_field_name is None
        or decision.new_field_layer is None
        or decision.new_field_data_type is None
    ):
        raise GovernanceError("发布新字段需要编码、名称、层级和数据类型")
    if _load_field(database, code) is not None:
        raise GovernanceError(f"字段编码已存在：{code}")
    field = SemanticField(code=code)
    version = SemanticFieldVersion(
        version=1,
        name=decision.new_field_name,
        description="由管理员在字段治理中确认并发布",
        layer=decision.new_field_layer,
        data_type=decision.new_field_data_type,
        unit_dimension=decision.unit,
        aliases=[decision.learn_alias] if decision.learn_alias else [],
        source=MANUAL_GOVERNANCE_SOURCE,
        source_metadata=source_metadata(
            source=MANUAL_GOVERNANCE_SOURCE,
            metadata={
                "proposal_id": str(proposal_id),
                "source_item_id": str(item_id),
            },
        ),
    )
    variant_inputs = _learning_variants(
        decision=decision,
        evidence=evidence,
        observed_data_type=observed_data_type,
        domain=domain,
        record_type=record_type,
        proposal_id=proposal_id,
        item_id=item_id,
    )
    variants = [build_field_variant(values) for values in variant_inputs]
    version.variants.extend({variant.variant_key: variant for variant in variants}.values())
    field.versions.append(version)
    database.add(field)
    database.flush()
    publish_field(
        database,
        field=field,
        version=version,
        actor=actor,
        actor_user_id=actor_user_id,
        comment="管理员确认并发布新字段",
    )
    return field, 1, [variant.variant_key for variant in variants]


def _learning_variants(
    *,
    decision: GovernanceFieldResolutionInput,
    evidence: SourceColumnEvidence,
    observed_data_type: str | None,
    domain: str,
    record_type: str,
    proposal_id: uuid.UUID,
    item_id: uuid.UUID,
) -> list[dict[str, Any]]:
    provenance = {
        "proposal_id": str(proposal_id),
        "source_item_id": str(item_id),
        "source_column_id": decision.source_column_id,
        "sheet_name": evidence.sheet_name,
        "column_coordinate": evidence.column_coordinate,
    }
    common = {
        "domain": domain,
        "record_type": record_type,
        "observed_data_type": observed_data_type,
        "unit_dimension": decision.unit,
        "source": MANUAL_GOVERNANCE_SOURCE,
        "confidence_basis_points": 10_000,
        "evidence": provenance,
    }
    variants: list[dict[str, Any]] = []
    if decision.learn_alias:
        variants.append(
            {
                **common,
                "kind": "alias",
                "alias": decision.learn_alias,
            }
        )
    if decision.learn_path:
        variants.append(
            {
                **common,
                "kind": "role_context" if decision.role else "header_path",
                "header_path": evidence.header_path,
                "role": decision.role,
            }
        )
    elif decision.role:
        variants.append(
            {
                **common,
                "kind": "role_context",
                "role": decision.role,
            }
        )
    return variants


def _remember_ignore_rule(
    database: Session,
    *,
    decision: GovernanceFieldResolutionInput,
    evidence: SourceColumnEvidence,
    observed_data_type: str | None,
    domain: str,
    record_type: str,
    item_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> None:
    if decision.ignore_scope != "context":
        return
    if not decision.ignore_reason:
        raise GovernanceError("记住忽略规则时必须填写原因")
    identity = {
        "header_path": evidence.header_path,
        "domain": domain,
        "record_type": record_type,
        "observed_data_type": observed_data_type,
    }
    rule_key = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    exists = database.scalar(
        select(SemanticIgnoreRule).where(
            SemanticIgnoreRule.rule_key == rule_key,
            SemanticIgnoreRule.status == TemplateStatus.PUBLISHED,
        )
    )
    if exists is not None:
        return
    database.add(
        SemanticIgnoreRule(
            rule_key=rule_key,
            version=1,
            header_path=evidence.header_path,
            parent_path=evidence.header_path[:-1],
            domain=domain,
            record_type=record_type,
            observed_data_type=observed_data_type,
            reason=decision.ignore_reason,
            source_item_id=item_id,
            source_column_id=decision.source_column_id,
            actor_user_id=actor_user_id,
        )
    )


def commit_field_governance(
    database: Session,
    *,
    proposal: TemplateProposal,
    resolutions: list[GovernanceFieldResolutionInput],
    domain: str,
    record_type: str,
    record_grain: str,
    actor: str,
    actor_user_id: uuid.UUID,
    comment: str,
) -> GovernanceCommit:
    existing = database.scalar(
        select(GovernanceResolution).where(
            GovernanceResolution.proposal_id == proposal.id
        )
    )
    if existing is not None:
        raise GovernanceError("该治理建议已经提交")
    if proposal.source_item_id is None:
        raise GovernanceError("治理建议没有来源文件")
    profile_record = database.get(DocumentProfile, proposal.source_item_id)
    if profile_record is None:
        raise GovernanceError("来源文件结构证据不可用")
    profile = load_workbook_profile(profile_record)
    sources = _source_columns(profile)
    matches = {
        match.source_column_id: match
        for match in database.scalars(
            select(FieldMatch).where(FieldMatch.item_id == proposal.source_item_id)
        )
    }
    required = {
        source_column_id
        for source_column_id, match in matches.items()
        if match.requires_hermes
    }
    supplied = {decision.source_column_id for decision in resolutions}
    if len(supplied) != len(resolutions):
        raise GovernanceError("同一来源列不能重复提交")
    missing = required - supplied
    unknown = supplied - required
    if missing:
        raise GovernanceError(f"仍有 {len(missing)} 个待治理字段未确认")
    if unknown:
        raise GovernanceError("提交包含不需要治理或不存在的来源列")

    suggestion_by_column = {
        str(decision.get("source_column_id")): decision
        for decision in proposal.proposal.get("field_decisions", [])
        if isinstance(decision, dict) and decision.get("source_column_id")
    }
    resolution_record = GovernanceResolution(
        proposal_id=proposal.id,
        item_id=proposal.source_item_id,
        domain=domain,
        record_type=record_type,
        record_grain=record_grain,
        actor_user_id=actor_user_id,
        comment=comment,
    )
    database.add(resolution_record)
    database.flush()
    final_decisions: list[dict[str, Any]] = []
    field_versions: dict[str, int] = {}
    submission_expected_versions: dict[str, int | None] = {}
    for decision in resolutions:
        source = sources.get(decision.source_column_id)
        match = matches.get(decision.source_column_id)
        if source is None or match is None:
            raise GovernanceError("来源列证据不完整")
        code: str | None = None
        version: int | None = None
        learned_keys: list[str] = []
        if decision.mode == "reuse_existing":
            if not decision.semantic_field_code:
                raise GovernanceError("复用字段时必须选择标准字段")
            field = _load_field(database, decision.semantic_field_code)
            if field is None:
                raise GovernanceError(f"标准字段不存在：{decision.semantic_field_code}")
            variants = _learning_variants(
                decision=decision,
                evidence=source,
                observed_data_type=match.observed_data_type,
                domain=domain,
                record_type=record_type,
                proposal_id=proposal.id,
                item_id=proposal.source_item_id,
            )
            if field.code in submission_expected_versions:
                if submission_expected_versions[field.code] != decision.expected_field_version:
                    raise GovernanceError(
                        f"同一次治理中字段 {field.code} 的预期版本不一致"
                    )
                effective_expected_version = field.published_version
            else:
                submission_expected_versions[field.code] = decision.expected_field_version
                effective_expected_version = decision.expected_field_version
            version, learned_keys = _publish_field_learning(
                database,
                field=field,
                expected_version=effective_expected_version,
                variants=variants,
                alias=decision.learn_alias,
                actor=actor,
                actor_user_id=actor_user_id,
                comment="管理员确认字段复用并沉淀来源变体",
            )
            code = field.code
            field_versions[code] = version
            final_decisions.append(
                {
                    "source_column_id": decision.source_column_id,
                    "action": "ROLE_VARIANT" if decision.role else "REUSE_FIELD",
                    "semantic_field_code": code,
                    "role": decision.role,
                    "unit": decision.unit,
                    "confidence": 1.0,
                    "requires_review": False,
                }
            )
        elif decision.mode == "create_new":
            field, version, learned_keys = _create_and_publish_field(
                database,
                decision=decision,
                evidence=source,
                observed_data_type=match.observed_data_type,
                domain=domain,
                record_type=record_type,
                actor=actor,
                actor_user_id=actor_user_id,
                proposal_id=proposal.id,
                item_id=proposal.source_item_id,
            )
            code = field.code
            field_versions[code] = version
            final_decisions.append(
                {
                    "source_column_id": decision.source_column_id,
                    "action": "PROPOSE_NEW_FIELD",
                    "proposed_field_code": code,
                    "layer": decision.new_field_layer,
                    "data_type": decision.new_field_data_type,
                    "unit": decision.unit,
                    "role": decision.role,
                    "confidence": 1.0,
                    "requires_review": False,
                }
            )
        else:
            if not decision.ignore_reason:
                raise GovernanceError("忽略列必须填写原因")
            _remember_ignore_rule(
                database,
                decision=decision,
                evidence=source,
                observed_data_type=match.observed_data_type,
                domain=domain,
                record_type=record_type,
                item_id=proposal.source_item_id,
                actor_user_id=actor_user_id,
            )
            final_decisions.append(
                {
                    "source_column_id": decision.source_column_id,
                    "action": "IGNORE_COLUMN",
                    "confidence": 1.0,
                    "requires_review": False,
                }
            )
        database.add(
            GovernanceFieldResolution(
                governance_resolution_id=resolution_record.id,
                item_id=proposal.source_item_id,
                source_column_id=decision.source_column_id,
                sheet_id=source.sheet_id,
                sheet_name=source.sheet_name,
                column_index=source.column_index,
                column_coordinate=source.column_coordinate,
                header_path=source.header_path,
                observed_data_type=match.observed_data_type,
                hermes_suggestion=dict(
                    suggestion_by_column.get(decision.source_column_id, {})
                ),
                resolution=decision.model_dump(mode="json"),
                semantic_field_code=code,
                semantic_field_version=version,
                learned_variant_keys=learned_keys,
                actor_user_id=actor_user_id,
            )
        )
    database.flush()
    return GovernanceCommit(
        resolution=resolution_record,
        field_decisions=final_decisions,
        field_versions=field_versions,
    )


def publish_governed_regions(
    database: Session,
    *,
    proposal: TemplateProposal,
    governance: GovernanceCommit,
    template_name: str,
    actor: str,
    actor_user_id: uuid.UUID,
) -> list[dict[str, Any]]:
    if proposal.source_item_id is None:
        raise GovernanceError("治理建议没有来源文件")
    profile_record = database.get(DocumentProfile, proposal.source_item_id)
    if profile_record is None:
        raise GovernanceError("来源文件结构证据不可用")
    profile = load_workbook_profile(profile_record)
    final_by_column = {
        str(decision["source_column_id"]): decision
        for decision in governance.field_decisions
    }
    field_matches = list(
        database.scalars(
            select(FieldMatch).where(FieldMatch.item_id == proposal.source_item_id)
        )
    )
    region_matches = {
        match.region_id: match
        for match in database.scalars(
            select(RegionTemplateMatch).where(
                RegionTemplateMatch.item_id == proposal.source_item_id
            )
        )
    }
    refs: list[dict[str, Any]] = []
    for layout in proposal.proposal.get("layout_decisions", []):
        if not isinstance(layout, dict) or not layout.get("materialize", True):
            continue
        region_id = str(layout.get("region_candidate_id") or "")
        header_id = str(layout.get("header_candidate_id") or "")
        located = [
            (sheet, candidate)
            for sheet in profile.sheets
            for candidate in select_header_candidates(sheet.header_candidates)
            if candidate.region_id == region_id and candidate.id == header_id
        ]
        if len(located) != 1:
            raise GovernanceError("治理 Region 无法定位唯一表头")
        sheet, header = located[0]
        match = region_matches.get(region_id)
        bindings: list[dict[str, Any]] = []
        for column in header.columns:
            decision = final_by_column.get(column.source_column_id)
            field_match = next(
                (
                    item
                    for item in field_matches
                    if item.source_column_id == column.source_column_id
                ),
                None,
            )
            if decision is not None:
                if decision["action"] == "IGNORE_COLUMN":
                    continue
                code = str(
                    decision.get("semantic_field_code")
                    or decision.get("proposed_field_code")
                )
                version = governance.field_versions.get(code)
                role = decision.get("role")
                unit = decision.get("unit")
            elif (
                field_match is not None
                and not field_match.requires_hermes
                and field_match.semantic_field_code
                and field_match.semantic_field_version
            ):
                code = field_match.semantic_field_code
                field = _load_field(database, code)
                version = (
                    field.published_version
                    if field is not None
                    else field_match.semantic_field_version
                )
                role = field_match.context.get("role")
                unit = None
            else:
                continue
            if version is None:
                field = _load_field(database, code)
                version = field.published_version if field is not None else None
            if version is None:
                raise GovernanceError(f"Region 字段尚未发布：{code}")
            bindings.append(
                {
                    "source_column_id": column.source_column_id,
                    "header_path": [str(part) for part in column.header_path],
                    "semantic_field_code": code,
                    "semantic_field_version": version,
                    "role": role,
                    "unit": unit,
                    "normalizer": None,
                    "required": False,
                }
            )
        if not bindings:
            continue
        region: RegionTemplate | None = None
        if match is not None and match.region_template_id is not None:
            region = database.scalar(
                select(RegionTemplate)
                .where(RegionTemplate.id == match.region_template_id)
                .options(selectinload(RegionTemplate.versions))
            )
        if region is None:
            fingerprint = match.region_fingerprint if match is not None else hashlib.sha256(
                region_id.encode()
            ).hexdigest()
            code = (
                f"region.{governance.resolution.domain}."
                f"{governance.resolution.record_type}.{fingerprint[:12]}"
            )
            region = database.scalar(
                select(RegionTemplate)
                .where(RegionTemplate.code == code)
                .options(selectinload(RegionTemplate.versions))
            )
            if region is None:
                region = RegionTemplate(code=code)
                database.add(region)
                database.flush()
        if any(
            version.status not in {TemplateStatus.PUBLISHED, TemplateStatus.DEPRECATED}
            for version in region.versions
        ):
            raise GovernanceError(f"Region 模板 {region.code} 已有未完成版本")
        version_number = max((version.version for version in region.versions), default=0) + 1
        region_version = RegionTemplateVersion(
            version=version_number,
            name=f"{template_name} · {sheet.name}",
            description="由管理员逐字段治理确认后发布",
            domain=governance.resolution.domain,
            record_type=governance.resolution.record_type,
            record_grain=governance.resolution.record_grain,
            region_kind=str(layout.get("classification") or "table"),
            region_fingerprint=(
                match.region_fingerprint
                if match is not None
                else hashlib.sha256(region_id.encode()).hexdigest()
            ),
            header_signature=[
                [str(part) for part in column.header_path] for column in header.columns
            ],
            layout_rules=dict(layout),
            field_bindings=bindings,
            identity_policy={},
            quality_rules=[],
            source=MANUAL_GOVERNANCE_SOURCE,
            source_metadata=source_metadata(
                source=MANUAL_GOVERNANCE_SOURCE,
                metadata={
                    "proposal_id": str(proposal.id),
                    "source_item_id": str(proposal.source_item_id),
                    "sheet_id": sheet.id,
                    "sheet_name": sheet.name,
                    "region_id": region_id,
                },
            ),
        )
        region.versions.append(region_version)
        database.flush()
        publish_region_template(
            database,
            template=region,
            version=region_version,
            actor=actor,
            actor_user_id=actor_user_id,
            comment="管理员完成逐字段治理并发布 Region 模板",
        )
        refs.append(
            {
                "region_template_id": str(region.id),
                "region_template_version": region_version.version,
                "sheet_id": sheet.id,
                "sheet_name": sheet.name,
                "region_id": region_id,
            }
        )
    governance.resolution.region_template_refs = refs
    database.flush()
    return refs
