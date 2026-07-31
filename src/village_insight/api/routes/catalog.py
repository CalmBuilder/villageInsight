from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, selectinload

from village_insight.api.dependencies import Database, GovernorPrincipal, require_governor
from village_insight.db.models import (
    DocumentTemplate,
    IngestionItem,
    IngestionItemSupersession,
    MetricDefinition,
    QueryFactSetDefinition,
    RegionTemplate,
    RegionTemplateVersion,
    SemanticField,
    SemanticFieldVersion,
    SemanticManifestDefinition,
    SheetComposition,
    SheetCompositionRegionSlot,
    SheetCompositionVersion,
    TemplateProposal,
    TemplateVersion,
    WorkbookRoute,
    WorkbookRouteSheetSlot,
    WorkbookRouteVersion,
)
from village_insight.db.schema import (
    MetricDefinitionCreate,
    MetricDefinitionRead,
    QueryFactSetDefinitionCreate,
    QueryFactSetDefinitionRead,
    RegionSourcePreviewRead,
    RegionTemplateCreate,
    RegionTemplateRead,
    RegionTemplateVersionInput,
    ReviewCommand,
    SemanticFieldCreate,
    SemanticFieldDetailRead,
    SemanticFieldRead,
    SemanticFieldTemplateReferenceRead,
    SemanticFieldVersionHistoryRead,
    SemanticFieldVersionInput,
    SemanticManifestDefinitionCreate,
    SemanticManifestDefinitionRead,
    SheetCompositionCreate,
    SheetCompositionRead,
    SheetCompositionVersionInput,
    SourceSupersessionCreate,
    SourceSupersessionRead,
    TemplateCreate,
    TemplateProposalRead,
    TemplateRead,
    TemplateVersionInput,
    WorkbookRouteCreate,
    WorkbookRouteRead,
    WorkbookRouteSourcePreviewRead,
    WorkbookRouteVersionInput,
)
from village_insight.parsing.router import ParserRouter
from village_insight.query_governance import (
    QueryGovernanceError,
    contract_fingerprint,
    publish_fact_set,
    publish_metric,
    publish_semantic_manifest,
)
from village_insight.question_source_versions import (
    SourceSupersessionError,
    declare_source_supersession,
)
from village_insight.templates.field_variants import build_field_variant
from village_insight.templates.lifecycle import (
    LifecycleError,
    publish_field,
    publish_region_template,
    publish_sheet_composition,
    publish_workbook_route,
    transition_template,
)

router = APIRouter(tags=["catalog"], dependencies=[Depends(require_governor)])


class CatalogDirectoryPage(BaseModel):
    items: list[dict[str, Any]]
    counts: dict[str, int]
    total: int
    limit: int
    offset: int


def _source_supersession_read(
    declaration: IngestionItemSupersession,
    superseded_name: str,
    replacement_name: str,
) -> SourceSupersessionRead:
    return SourceSupersessionRead(
        id=declaration.id,
        administrative_unit_id=declaration.administrative_unit_id,
        superseded_item_id=declaration.superseded_item_id,
        superseded_file_name=superseded_name,
        replacement_item_id=declaration.replacement_item_id,
        replacement_file_name=replacement_name,
        reason=declaration.reason,
        declared_by_user_id=declaration.declared_by_user_id,
        created_at=declaration.created_at,
    )


@router.get(
    "/source-supersessions",
    response_model=list[SourceSupersessionRead],
)
def list_source_supersessions(
    database: Database,
    principal: GovernorPrincipal,
) -> list[SourceSupersessionRead]:
    if not principal.allowed_unit_ids:
        return []
    superseded = aliased(IngestionItem)
    replacement = aliased(IngestionItem)
    rows = database.execute(
        select(
            IngestionItemSupersession,
            superseded.original_name,
            replacement.original_name,
        )
        .join(
            superseded,
            superseded.id == IngestionItemSupersession.superseded_item_id,
        )
        .join(
            replacement,
            replacement.id == IngestionItemSupersession.replacement_item_id,
        )
        .where(
            IngestionItemSupersession.tenant_id == principal.tenant.id,
            IngestionItemSupersession.administrative_unit_id.in_(
                principal.allowed_unit_ids
            ),
        )
        .order_by(IngestionItemSupersession.created_at.desc())
    ).all()
    return [
        _source_supersession_read(
            declaration,
            superseded_name,
            replacement_name,
        )
        for declaration, superseded_name, replacement_name in rows
    ]


@router.post(
    "/source-supersessions",
    response_model=SourceSupersessionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_source_supersession(
    payload: SourceSupersessionCreate,
    database: Database,
    principal: GovernorPrincipal,
) -> SourceSupersessionRead:
    try:
        declaration = declare_source_supersession(
            database,
            tenant_id=principal.tenant.id,
            allowed_administrative_unit_ids=frozenset(
                principal.allowed_unit_ids
            ),
            superseded_item_id=payload.superseded_item_id,
            replacement_item_id=payload.replacement_item_id,
            declared_by_user_id=principal.user.id,
            reason=payload.reason,
        )
        database.commit()
    except SourceSupersessionError as exc:
        database.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    database.refresh(declaration)
    superseded = database.get(IngestionItem, declaration.superseded_item_id)
    replacement = database.get(IngestionItem, declaration.replacement_item_id)
    assert superseded is not None and replacement is not None
    return _source_supersession_read(
        declaration,
        superseded.original_name,
        replacement.original_name,
    )


def _column_letter(column_number: int | None) -> str:
    if column_number is None or column_number < 1:
        return "—"
    result = ""
    value = column_number
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _evidence_location(path: Path) -> str:
    parts = path.parts
    try:
        datafiles_index = parts.index("datafiles")
    except ValueError:
        return path.name
    return "/".join(parts[datafiles_index + 1 :])


def _binding_column_number(binding: dict[str, object]) -> int | None:
    selector = binding.get("source_selector")
    if isinstance(selector, dict):
        kind = selector.get("kind")
        if kind == "physical_column":
            physical_column = selector.get("column")
            if isinstance(physical_column, int):
                return physical_column
    source_column_id = str(binding.get("source_column_id") or "")
    if source_column_id.startswith("physical-column:"):
        try:
            return int(source_column_id.rsplit(":", 1)[1])
        except ValueError:
            return None
    marker = ":column:"
    if marker not in source_column_id:
        return None
    try:
        return int(source_column_id.rsplit(marker, 1)[1])
    except ValueError:
        return None


def _display_value(value: object) -> str:
    if value is None:
        return ""
    rendered = str(value).strip()
    return rendered[:120]


def _route_source_paths(metadata: dict[str, Any]) -> list[Path]:
    paths: dict[str, Path] = {}
    members = metadata.get("members", [])
    if not isinstance(members, list):
        return []
    for member in members:
        if not isinstance(member, dict):
            continue
        raw_paths = member.get("source_paths", [])
        if not isinstance(raw_paths, list):
            raw_paths = []
        representative = member.get("representative_path")
        if representative:
            raw_paths = [representative, *raw_paths]
        for raw_path in raw_paths:
            path = Path(str(raw_path)).resolve()
            paths.setdefault(str(path), path)
    return list(paths.values())


@router.get("/template-seeds", response_model=list[TemplateProposalRead])
def list_template_seeds(
    database: Database,
    proposal_status: str = Query(default="pending", alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[TemplateProposal]:
    return list(
        database.scalars(
            select(TemplateProposal)
            .where(
                TemplateProposal.source == "bootstrap",
                TemplateProposal.status == proposal_status,
            )
            .order_by(TemplateProposal.created_at, TemplateProposal.id)
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/template-seeds/{proposal_id}", response_model=TemplateProposalRead)
def get_template_seed(
    proposal_id: uuid.UUID,
    database: Database,
) -> TemplateProposal:
    proposal = database.get(TemplateProposal, proposal_id)
    if proposal is None or proposal.source != "bootstrap":
        raise HTTPException(status_code=404, detail="template seed not found")
    return proposal


@router.get("/metrics", response_model=list[MetricDefinitionRead])
def list_metrics(database: Database) -> list[MetricDefinition]:
    return list(database.scalars(select(MetricDefinition).order_by(MetricDefinition.code)))


@router.post(
    "/metrics",
    response_model=MetricDefinitionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_metric(
    payload: MetricDefinitionCreate,
    database: Database,
) -> MetricDefinition:
    field = database.scalar(
        select(SemanticFieldVersion)
        .join(SemanticField)
        .where(
            SemanticField.code == payload.semantic_field_code,
            SemanticFieldVersion.version == payload.semantic_field_version,
            SemanticFieldVersion.status == "published",
        )
    )
    if field is None:
        raise HTTPException(status_code=422, detail="published metric field not found")
    metric = MetricDefinition(
        **payload.model_dump(),
        status="draft",
        published_at=None,
    )
    database.add(metric)
    try:
        database.commit()
    except IntegrityError as exc:
        database.rollback()
        raise HTTPException(
            status_code=409,
            detail="metric code and version already exist",
        ) from exc
    database.refresh(metric)
    return metric


@router.post(
    "/metrics/{metric_id}/publish",
    response_model=MetricDefinitionRead,
)
def publish_query_metric(
    metric_id: uuid.UUID,
    database: Database,
) -> MetricDefinition:
    metric = database.get(MetricDefinition, metric_id)
    if metric is None:
        raise HTTPException(status_code=404, detail="metric not found")
    try:
        publish_metric(database, metric)
        database.commit()
    except QueryGovernanceError as exc:
        database.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    database.refresh(metric)
    return metric


@router.get(
    "/query-fact-sets",
    response_model=list[QueryFactSetDefinitionRead],
)
def list_query_fact_sets(
    database: Database,
) -> list[QueryFactSetDefinition]:
    return list(
        database.scalars(
            select(QueryFactSetDefinition).order_by(
                QueryFactSetDefinition.code,
                QueryFactSetDefinition.version.desc(),
            )
        )
    )


@router.post(
    "/query-fact-sets",
    response_model=QueryFactSetDefinitionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_query_fact_set(
    payload: QueryFactSetDefinitionCreate,
    database: Database,
) -> QueryFactSetDefinition:
    values = payload.model_dump()
    definition = QueryFactSetDefinition(
        **values,
        status="draft",
        definition_fingerprint=contract_fingerprint(values),
    )
    database.add(definition)
    try:
        database.commit()
    except IntegrityError as exc:
        database.rollback()
        raise HTTPException(
            status_code=409,
            detail="fact set version or definition already exists",
        ) from exc
    database.refresh(definition)
    return definition


@router.post(
    "/query-fact-sets/{fact_set_id}/publish",
    response_model=QueryFactSetDefinitionRead,
)
def publish_query_fact_set(
    fact_set_id: uuid.UUID,
    database: Database,
) -> QueryFactSetDefinition:
    definition = database.get(QueryFactSetDefinition, fact_set_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="fact set not found")
    try:
        publish_fact_set(database, definition)
        database.commit()
    except QueryGovernanceError as exc:
        database.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    database.refresh(definition)
    return definition


@router.get(
    "/semantic-manifests",
    response_model=list[SemanticManifestDefinitionRead],
)
def list_semantic_manifests(
    database: Database,
) -> list[SemanticManifestDefinition]:
    return list(
        database.scalars(
            select(SemanticManifestDefinition).order_by(
                SemanticManifestDefinition.code,
                SemanticManifestDefinition.version.desc(),
            )
        )
    )


@router.post(
    "/semantic-manifests",
    response_model=SemanticManifestDefinitionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_semantic_manifest(
    payload: SemanticManifestDefinitionCreate,
    database: Database,
) -> SemanticManifestDefinition:
    values = payload.model_dump()
    definition = SemanticManifestDefinition(
        **values,
        status="draft",
        manifest_fingerprint=contract_fingerprint(values),
    )
    database.add(definition)
    try:
        database.commit()
    except IntegrityError as exc:
        database.rollback()
        raise HTTPException(
            status_code=409,
            detail="semantic manifest version or definition already exists",
        ) from exc
    database.refresh(definition)
    return definition


@router.post(
    "/semantic-manifests/{manifest_id}/publish",
    response_model=SemanticManifestDefinitionRead,
)
def publish_query_semantic_manifest(
    manifest_id: uuid.UUID,
    database: Database,
) -> SemanticManifestDefinition:
    definition = database.get(SemanticManifestDefinition, manifest_id)
    if definition is None:
        raise HTTPException(status_code=404, detail="semantic manifest not found")
    try:
        publish_semantic_manifest(database, definition)
        database.commit()
    except QueryGovernanceError as exc:
        database.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    database.refresh(definition)
    return definition


def field_read(field: SemanticField) -> SemanticFieldRead:
    version = max(field.versions, key=lambda item: item.version)
    return SemanticFieldRead(
        id=field.id,
        code=field.code,
        version=version.version,
        status=version.status,
        published_version=field.published_version,
        name=version.name,
        description=version.description,
        layer=version.layer,
        data_type=version.data_type,
        unit_dimension=version.unit_dimension,
        aliases=version.aliases,
        validators=version.validators,
        variants=version.variants,
        created_at=field.created_at,
        updated_at=field.updated_at,
    )


def template_read(template: DocumentTemplate) -> TemplateRead:
    version = max(template.versions, key=lambda item: item.version)
    return TemplateRead(
        id=template.id,
        code=template.code,
        name=version.name,
        description=version.description,
        version=version.version,
        status=version.status,
        layout_fingerprint=version.layout_fingerprint,
        definition=version.definition,
        source=version.source,
        source_metadata=version.source_metadata,
        published_version=template.published_version,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def region_template_read(template: RegionTemplate) -> RegionTemplateRead:
    version = max(template.versions, key=lambda item: item.version)
    return RegionTemplateRead(
        id=template.id,
        code=template.code,
        version=version.version,
        status=version.status,
        published_version=template.published_version,
        name=version.name,
        description=version.description,
        region_fingerprint=version.region_fingerprint,
        definition={
            "contract_version": "region-template/v1",
            "domain": version.domain,
            "region_kind": version.region_kind,
            "record_type": version.record_type,
            "record_grain": version.record_grain,
            "header_signature": version.header_signature,
            "layout_rules": version.layout_rules,
            "field_bindings": version.field_bindings,
            "identity_policy": version.identity_policy,
            "quality_rules": version.quality_rules,
        },
        source=version.source,
        source_metadata=version.source_metadata,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def _new_field_version(
    *,
    version_number: int,
    payload: SemanticFieldCreate | SemanticFieldVersionInput,
) -> SemanticFieldVersion:
    values = payload.model_dump(exclude={"code", "variants"})
    version = SemanticFieldVersion(version=version_number, **values)
    candidates = [
        build_field_variant(
            {
                "kind": "alias",
                "alias": alias,
                "source": "manual",
                "confidence_basis_points": 10_000,
                "evidence": {"created_with_field_version": version_number},
            }
        )
        for alias in [payload.name, *payload.aliases]
        if alias
    ]
    candidates.extend(build_field_variant(variant.model_dump()) for variant in payload.variants)
    by_key = {variant.variant_key: variant for variant in candidates}
    version.variants.extend(by_key.values())
    return version


def _new_region_template_version(
    *,
    version_number: int,
    payload: RegionTemplateCreate | RegionTemplateVersionInput,
) -> RegionTemplateVersion:
    definition = payload.definition
    return RegionTemplateVersion(
        version=version_number,
        name=payload.name,
        description=payload.description,
        region_fingerprint=payload.region_fingerprint,
        domain=definition.domain,
        record_type=definition.record_type,
        record_grain=definition.record_grain,
        region_kind=definition.region_kind,
        header_signature=definition.header_signature,
        layout_rules=definition.layout_rules,
        field_bindings=[binding.model_dump(mode="json") for binding in definition.field_bindings],
        identity_policy=definition.identity_policy,
        quality_rules=definition.quality_rules,
        source=payload.source,
        source_metadata=payload.source_metadata,
    )


def _new_sheet_composition_version(
    *,
    version_number: int,
    payload: SheetCompositionCreate | SheetCompositionVersionInput,
) -> SheetCompositionVersion:
    version = SheetCompositionVersion(
        version=version_number,
        name=payload.name,
        description=payload.description,
        composition_fingerprint=payload.composition_fingerprint,
        matching_rules=payload.matching_rules,
        source=payload.source,
        source_metadata=payload.source_metadata,
    )
    version.region_slots.extend(
        SheetCompositionRegionSlot(**slot.model_dump()) for slot in payload.region_slots
    )
    return version


def sheet_composition_read(
    composition: SheetComposition,
) -> SheetCompositionRead:
    version = max(composition.versions, key=lambda item: item.version)
    return SheetCompositionRead(
        id=composition.id,
        code=composition.code,
        version=version.version,
        status=version.status,
        published_version=composition.published_version,
        name=version.name,
        description=version.description,
        composition_fingerprint=version.composition_fingerprint,
        region_slots=version.region_slots,
        matching_rules=version.matching_rules,
        source=version.source,
        source_metadata=version.source_metadata,
        created_at=composition.created_at,
        updated_at=composition.updated_at,
    )


def _new_workbook_route_version(
    *,
    version_number: int,
    payload: WorkbookRouteCreate | WorkbookRouteVersionInput,
) -> WorkbookRouteVersion:
    version = WorkbookRouteVersion(
        version=version_number,
        name=payload.name,
        description=payload.description,
        route_fingerprint=payload.route_fingerprint,
        matching_rules=payload.matching_rules,
        source=payload.source,
        source_metadata=payload.source_metadata,
    )
    version.sheet_slots.extend(
        WorkbookRouteSheetSlot(**slot.model_dump()) for slot in payload.sheet_slots
    )
    return version


def workbook_route_read(route: WorkbookRoute) -> WorkbookRouteRead:
    version = max(route.versions, key=lambda item: item.version)
    return WorkbookRouteRead(
        id=route.id,
        code=route.code,
        version=version.version,
        status=version.status,
        published_version=route.published_version,
        name=version.name,
        description=version.description,
        route_fingerprint=version.route_fingerprint,
        sheet_slots=version.sheet_slots,
        matching_rules=version.matching_rules,
        source=version.source,
        source_metadata=version.source_metadata,
        created_at=route.created_at,
        updated_at=route.updated_at,
    )


@router.get("/catalog/directory", response_model=CatalogDirectoryPage)
def list_catalog_directory(
    database: Database,
    section: str = Query(default="fields"),
    search: str = Query(default="", max_length=200),
    status_filter: str = Query(default="all", alias="status"),
    layer: str = Query(default="all"),
    data_type: str = Query(default="all"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> CatalogDirectoryPage:
    def metric_read(item: MetricDefinition) -> MetricDefinitionRead:
        return MetricDefinitionRead.model_validate(item)

    counts = {
        "fields": database.scalar(select(func.count()).select_from(SemanticField)) or 0,
        "metrics": database.scalar(select(func.count()).select_from(MetricDefinition)) or 0,
        "regions": database.scalar(select(func.count()).select_from(RegionTemplate)) or 0,
        "compositions": database.scalar(select(func.count()).select_from(SheetComposition)) or 0,
        "routes": database.scalar(select(func.count()).select_from(WorkbookRoute)) or 0,
        "legacy": database.scalar(select(func.count()).select_from(DocumentTemplate)) or 0,
    }
    route_source_paths = {
        str(path)
        for metadata in database.scalars(
            select(WorkbookRouteVersion.source_metadata)
            .join(
                WorkbookRoute,
                WorkbookRoute.id == WorkbookRouteVersion.workbook_route_id,
            )
            .where(WorkbookRoute.published_version == WorkbookRouteVersion.version)
        )
        for path in _route_source_paths(metadata)
    }
    counts["route_source_files"] = len(route_source_paths)
    if section == "fields":
        values = list(
            database.scalars(
                select(SemanticField)
                .options(
                    selectinload(SemanticField.versions).selectinload(
                        SemanticFieldVersion.variants
                    )
                )
                .order_by(SemanticField.code)
            )
        )
        reader: Any = field_read
    elif section == "metrics":
        values = list(
            database.scalars(
                select(MetricDefinition).order_by(MetricDefinition.code)
            )
        )
        reader = metric_read
    elif section == "regions":
        values = list(
            database.scalars(
                select(RegionTemplate)
                .options(selectinload(RegionTemplate.versions))
                .order_by(RegionTemplate.code)
            )
        )
        reader = region_template_read
    elif section == "compositions":
        values = list(
            database.scalars(
                select(SheetComposition)
                .options(
                    selectinload(SheetComposition.versions).selectinload(
                        SheetCompositionVersion.region_slots
                    )
                )
                .order_by(SheetComposition.code)
            )
        )
        reader = sheet_composition_read
    elif section == "routes":
        values = list(
            database.scalars(
                select(WorkbookRoute)
                .options(
                    selectinload(WorkbookRoute.versions).selectinload(
                        WorkbookRouteVersion.sheet_slots
                    )
                )
                .order_by(WorkbookRoute.code)
            )
        )
        reader = workbook_route_read
    elif section == "legacy":
        values = list(
            database.scalars(
                select(DocumentTemplate)
                .options(selectinload(DocumentTemplate.versions))
                .order_by(DocumentTemplate.code)
            )
        )
        reader = template_read
    else:
        raise HTTPException(status_code=422, detail="目录分类无效")
    rendered = [reader(value).model_dump(mode="json") for value in values]
    if section == "fields":
        for item in rendered:
            item["variant_count"] = len(item.get("variants", []))
            item["aliases"] = []
            item["validators"] = []
            item["variants"] = []
    term = search.strip().casefold()
    filtered = [
        item
        for item in rendered
        if (
            not term
            or term
            in " ".join(
                str(item.get(key, ""))
                for key in ("name", "code", "description")
            ).casefold()
        )
        and (status_filter == "all" or item.get("status") == status_filter)
        and (section != "fields" or layer == "all" or item.get("layer") == layer)
        and (
            section != "fields"
            or data_type == "all"
            or item.get("data_type") == data_type
        )
    ]
    return CatalogDirectoryPage(
        items=filtered[offset : offset + limit],
        counts=counts,
        total=len(filtered),
        limit=limit,
        offset=offset,
    )


@router.get("/fields", response_model=list[SemanticFieldRead])
def list_fields(database: Database) -> list[SemanticFieldRead]:
    fields = database.scalars(
        select(SemanticField)
        .options(selectinload(SemanticField.versions).selectinload(SemanticFieldVersion.variants))
        .order_by(SemanticField.code)
    )
    return [field_read(field) for field in fields]


@router.get("/fields/{field_id}/details", response_model=SemanticFieldDetailRead)
def get_field_details(
    field_id: uuid.UUID,
    database: Database,
) -> SemanticFieldDetailRead:
    field = database.scalar(
        select(SemanticField)
        .where(SemanticField.id == field_id)
        .options(
            selectinload(SemanticField.versions).selectinload(
                SemanticFieldVersion.variants
            )
        )
    )
    if field is None:
        raise HTTPException(status_code=404, detail="field not found")

    references: list[SemanticFieldTemplateReferenceRead] = []
    templates = database.scalars(
        select(RegionTemplate)
        .options(selectinload(RegionTemplate.versions))
        .order_by(RegionTemplate.code)
    )
    for template in templates:
        for version in sorted(template.versions, key=lambda item: item.version):
            if not any(
                binding.get("semantic_field_code") == field.code
                for binding in version.field_bindings
            ):
                continue
            references.append(
                SemanticFieldTemplateReferenceRead(
                    template_id=template.id,
                    template_code=template.code,
                    template_name=version.name,
                    template_version=version.version,
                    template_status=version.status,
                )
            )

    return SemanticFieldDetailRead(
        field=field_read(field),
        versions=[
            SemanticFieldVersionHistoryRead(
                version=version.version,
                status=version.status,
                name=version.name,
                description=version.description,
                layer=version.layer,
                data_type=version.data_type,
                unit_dimension=version.unit_dimension,
                alias_count=len(version.aliases),
                variant_count=len(version.variants),
                created_at=version.created_at,
            )
            for version in sorted(
                field.versions,
                key=lambda item: item.version,
                reverse=True,
            )
        ],
        referenced_by=references,
    )


@router.post("/fields", response_model=SemanticFieldRead, status_code=status.HTTP_201_CREATED)
def create_field(payload: SemanticFieldCreate, database: Database) -> SemanticFieldRead:
    field = SemanticField(code=payload.code)
    field.versions.append(_new_field_version(version_number=1, payload=payload))
    database.add(field)
    try:
        database.commit()
    except IntegrityError as exc:
        database.rollback()
        raise HTTPException(status_code=409, detail="field code already exists") from exc
    database.refresh(field)
    return field_read(field)


@router.post(
    "/fields/{field_id}/versions",
    response_model=SemanticFieldRead,
    status_code=status.HTTP_201_CREATED,
)
def create_field_version(
    field_id: uuid.UUID,
    payload: SemanticFieldVersionInput,
    database: Database,
) -> SemanticFieldRead:
    field = database.scalar(
        select(SemanticField)
        .where(SemanticField.id == field_id)
        .options(selectinload(SemanticField.versions).selectinload(SemanticFieldVersion.variants))
    )
    if field is None:
        raise HTTPException(status_code=404, detail="field not found")
    latest = max(field.versions, key=lambda item: item.version)
    if latest.status not in {"published", "deprecated"}:
        raise HTTPException(status_code=409, detail="an editable field version already exists")
    field.versions.append(_new_field_version(version_number=latest.version + 1, payload=payload))
    database.commit()
    return field_read(field)


@router.post(
    "/fields/{field_id}/versions/{version_number}/publish",
    response_model=SemanticFieldRead,
)
def publish_field_version(
    field_id: uuid.UUID,
    version_number: int,
    command: ReviewCommand,
    database: Database,
    principal: GovernorPrincipal,
) -> SemanticFieldRead:
    field = database.scalar(
        select(SemanticField)
        .where(SemanticField.id == field_id)
        .options(selectinload(SemanticField.versions).selectinload(SemanticFieldVersion.variants))
    )
    if field is None:
        raise HTTPException(status_code=404, detail="field not found")
    version = next(
        (item for item in field.versions if item.version == version_number),
        None,
    )
    if version is None:
        raise HTTPException(status_code=404, detail="field version not found")
    try:
        publish_field(
            database,
            field=field,
            version=version,
            actor=principal.user.username,
            comment=command.comment,
            actor_user_id=principal.user.id,
        )
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    database.commit()
    return field_read(field)


@router.get("/region-templates", response_model=list[RegionTemplateRead])
def list_region_templates(database: Database) -> list[RegionTemplateRead]:
    templates = database.scalars(
        select(RegionTemplate)
        .options(selectinload(RegionTemplate.versions))
        .order_by(RegionTemplate.code)
    )
    return [region_template_read(template) for template in templates]


@router.get(
    "/region-templates/{template_id}/source-preview",
    response_model=RegionSourcePreviewRead,
)
def get_region_template_source_preview(
    template_id: uuid.UUID,
    database: Database,
) -> RegionSourcePreviewRead:
    template = database.scalar(
        select(RegionTemplate)
        .where(RegionTemplate.id == template_id)
        .options(selectinload(RegionTemplate.versions))
    )
    if template is None:
        raise HTTPException(status_code=404, detail="表头模板不存在")
    version = max(template.versions, key=lambda item: item.version)
    raw_evidence = version.source_metadata.get("evidence", [])
    evidence = (
        next(
            (
                item
                for item in raw_evidence
                if isinstance(item, dict) and item.get("representative_path")
            ),
            None,
        )
        if isinstance(raw_evidence, list)
        else None
    )
    if evidence is None:
        raise HTTPException(status_code=409, detail="该模板尚未保存可回看的源文件证据")

    source_path = Path(str(evidence["representative_path"])).resolve()
    if not source_path.is_file():
        raise HTTPException(status_code=409, detail="模板源文件已移动，暂时无法回看")
    try:
        profile = ParserRouter().profile(source_path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"源文件暂时无法解析：{exc}") from exc

    sheet_index = evidence.get("sheet_index", 0)
    if not isinstance(sheet_index, int) or sheet_index < 0 or sheet_index >= len(profile.sheets):
        raise HTTPException(status_code=422, detail="模板记录的 Sheet 已不存在")
    sheet = profile.sheets[sheet_index]
    region_id = str(evidence.get("region_id") or "")
    region = next((item for item in sheet.region_candidates if item.id == region_id), None)
    target_header_paths = {
        tuple(str(part).strip() for part in path if str(part).strip())
        for path in version.header_signature
    }
    candidate_headers = [
        item for item in sheet.header_candidates if item.region_id == region_id
    ]
    header = (
        max(
            candidate_headers,
            key=lambda item: sum(
                tuple(part.strip() for part in column.header_path)
                in target_header_paths
                for column in item.columns
            ),
        )
        if candidate_headers
        else None
    )
    warning = None
    if region is None:
        region = sheet.region_candidates[0] if sheet.region_candidates else None
        warning = "未找到原始数据区编号，已按该 Sheet 的首个数据区回看"
    if header is None and region is not None:
        header = next(
            (
                item
                for item in sheet.header_candidates
                if item.region_id == region.id
            ),
            None,
        )

    bounds = region.bounds if region is not None else sheet.observed_bounds
    if bounds is None:
        raise HTTPException(status_code=422, detail="源 Sheet 没有可回看的非空数据")
    cells_by_position = {(cell.row, cell.column): cell for cell in sheet.cells}
    header_rows = header.header_rows if header is not None else []
    header_end = max(header_rows, default=bounds.min_row - 1)
    data_start_offset = version.layout_rules.get(
        "data_start_offset_from_header_end",
        1,
    )
    if not isinstance(data_start_offset, int):
        data_start_offset = 1
    data_start_row = header_end + data_start_offset

    field_codes = {
        str(binding.get("semantic_field_code"))
        for binding in version.field_bindings
        if binding.get("semantic_field_code")
    }
    field_names: dict[str, str] = {}
    if field_codes:
        field_versions = database.execute(
            select(SemanticField.code, SemanticFieldVersion.name)
            .join(SemanticFieldVersion)
            .where(
                SemanticField.code.in_(field_codes),
                SemanticFieldVersion.status == "published",
            )
        )
        field_names = {code: name for code, name in field_versions}

    columns: list[dict[str, object]] = []
    for binding in version.field_bindings:
        header_path = [
            str(item).strip()
            for item in binding.get("header_path", [])
            if str(item).strip()
        ]
        selector = binding.get("source_selector")
        column_number = _binding_column_number(binding)
        sample_values: list[str] = []
        if isinstance(selector, dict) and selector.get("kind") == "cell":
            row_offset = selector.get("row_offset")
            column_offset = selector.get("column_offset")
            if isinstance(row_offset, int) and isinstance(column_offset, int):
                row = bounds.min_row + row_offset
                column_number = bounds.min_column + column_offset
                cell = cells_by_position.get((row, column_number))
                value = _display_value(cell.display_value if cell else None)
                if value:
                    sample_values.append(value)
        elif column_number is not None:
            for row in range(max(bounds.min_row, data_start_row), bounds.max_row + 1):
                cell = cells_by_position.get((row, column_number))
                value = _display_value(cell.display_value if cell else None)
                if value and value not in sample_values:
                    sample_values.append(value)
                if len(sample_values) >= 4:
                    break

        semantic_field_code = str(binding.get("semantic_field_code") or "")
        columns.append(
            {
                "excel_column": _column_letter(column_number),
                "column_number": column_number,
                "header_path": header_path,
                "source_header": header_path[-1] if header_path else "无表头列",
                "sample_values": sample_values,
                "semantic_field_code": semantic_field_code,
                "semantic_field_name": field_names.get(
                    semantic_field_code,
                    header_path[-1] if header_path else semantic_field_code,
                ),
                "match_status": str(binding.get("field_status") or "confirmed"),
                "role": str(binding["role"]) if binding.get("role") else None,
            }
        )

    layout_mode = version.layout_rules.get("layout_mode")
    return RegionSourcePreviewRead(
        template_id=template.id,
        template_name=version.name,
        source_file=profile.file_name,
        source_location=_evidence_location(source_path),
        sheet_name=sheet.name,
        sheet_index=sheet.index,
        source_range=bounds.range,
        header_rows=header_rows,
        layout_mode=str(layout_mode or version.region_kind),
        evidence_count=len(raw_evidence) if isinstance(raw_evidence, list) else 1,
        columns=columns,
        warning=warning,
    )


@router.post(
    "/region-templates",
    response_model=RegionTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
def create_region_template(
    payload: RegionTemplateCreate,
    database: Database,
) -> RegionTemplateRead:
    template = RegionTemplate(code=payload.code)
    template.versions.append(_new_region_template_version(version_number=1, payload=payload))
    database.add(template)
    try:
        database.commit()
    except IntegrityError as exc:
        database.rollback()
        raise HTTPException(
            status_code=409,
            detail="region template code already exists",
        ) from exc
    database.refresh(template)
    return region_template_read(template)


@router.post(
    "/region-templates/{template_id}/versions",
    response_model=RegionTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
def create_region_template_version(
    template_id: uuid.UUID,
    payload: RegionTemplateVersionInput,
    database: Database,
) -> RegionTemplateRead:
    template = database.scalar(
        select(RegionTemplate)
        .where(RegionTemplate.id == template_id)
        .options(selectinload(RegionTemplate.versions))
    )
    if template is None:
        raise HTTPException(status_code=404, detail="region template not found")
    latest = max(template.versions, key=lambda item: item.version)
    if latest.status not in {"published", "deprecated"}:
        raise HTTPException(
            status_code=409,
            detail="an editable region template version already exists",
        )
    template.versions.append(
        _new_region_template_version(
            version_number=latest.version + 1,
            payload=payload,
        )
    )
    database.commit()
    return region_template_read(template)


@router.post(
    "/region-templates/{template_id}/versions/{version_number}/publish",
    response_model=RegionTemplateRead,
)
def publish_region_template_version(
    template_id: uuid.UUID,
    version_number: int,
    command: ReviewCommand,
    database: Database,
    principal: GovernorPrincipal,
) -> RegionTemplateRead:
    template = database.scalar(
        select(RegionTemplate)
        .where(RegionTemplate.id == template_id)
        .options(selectinload(RegionTemplate.versions))
    )
    if template is None:
        raise HTTPException(status_code=404, detail="region template not found")
    version = next(
        (item for item in template.versions if item.version == version_number),
        None,
    )
    if version is None:
        raise HTTPException(
            status_code=404,
            detail="region template version not found",
        )
    try:
        publish_region_template(
            database,
            template=template,
            version=version,
            actor=principal.user.username,
            comment=command.comment,
            actor_user_id=principal.user.id,
        )
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    database.commit()
    return region_template_read(template)


@router.get(
    "/sheet-compositions",
    response_model=list[SheetCompositionRead],
)
def list_sheet_compositions(
    database: Database,
) -> list[SheetCompositionRead]:
    compositions = database.scalars(
        select(SheetComposition)
        .options(
            selectinload(SheetComposition.versions).selectinload(
                SheetCompositionVersion.region_slots
            )
        )
        .order_by(SheetComposition.code)
    )
    return [sheet_composition_read(composition) for composition in compositions]


@router.post(
    "/sheet-compositions",
    response_model=SheetCompositionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_sheet_composition(
    payload: SheetCompositionCreate,
    database: Database,
) -> SheetCompositionRead:
    composition = SheetComposition(code=payload.code)
    composition.versions.append(_new_sheet_composition_version(version_number=1, payload=payload))
    database.add(composition)
    try:
        database.commit()
    except IntegrityError as exc:
        database.rollback()
        raise HTTPException(
            status_code=409,
            detail="sheet composition code already exists",
        ) from exc
    database.refresh(composition)
    return sheet_composition_read(composition)


@router.post(
    "/sheet-compositions/{composition_id}/versions",
    response_model=SheetCompositionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_sheet_composition_version(
    composition_id: uuid.UUID,
    payload: SheetCompositionVersionInput,
    database: Database,
) -> SheetCompositionRead:
    composition = database.scalar(
        select(SheetComposition)
        .where(SheetComposition.id == composition_id)
        .options(
            selectinload(SheetComposition.versions).selectinload(
                SheetCompositionVersion.region_slots
            )
        )
    )
    if composition is None:
        raise HTTPException(status_code=404, detail="sheet composition not found")
    latest = max(composition.versions, key=lambda item: item.version)
    if latest.status not in {"published", "deprecated"}:
        raise HTTPException(
            status_code=409,
            detail="an editable sheet composition version already exists",
        )
    composition.versions.append(
        _new_sheet_composition_version(
            version_number=latest.version + 1,
            payload=payload,
        )
    )
    database.commit()
    return sheet_composition_read(composition)


@router.post(
    "/sheet-compositions/{composition_id}/versions/{version_number}/publish",
    response_model=SheetCompositionRead,
)
def publish_sheet_composition_version(
    composition_id: uuid.UUID,
    version_number: int,
    command: ReviewCommand,
    database: Database,
    principal: GovernorPrincipal,
) -> SheetCompositionRead:
    composition = database.scalar(
        select(SheetComposition)
        .where(SheetComposition.id == composition_id)
        .options(
            selectinload(SheetComposition.versions).selectinload(
                SheetCompositionVersion.region_slots
            )
        )
    )
    if composition is None:
        raise HTTPException(status_code=404, detail="sheet composition not found")
    version = next(
        (item for item in composition.versions if item.version == version_number),
        None,
    )
    if version is None:
        raise HTTPException(
            status_code=404,
            detail="sheet composition version not found",
        )
    try:
        publish_sheet_composition(
            database,
            composition=composition,
            version=version,
            actor=principal.user.username,
            comment=command.comment,
            actor_user_id=principal.user.id,
        )
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    database.commit()
    return sheet_composition_read(composition)


@router.get("/workbook-routes", response_model=list[WorkbookRouteRead])
def list_workbook_routes(database: Database) -> list[WorkbookRouteRead]:
    routes = database.scalars(
        select(WorkbookRoute)
        .options(
            selectinload(WorkbookRoute.versions).selectinload(WorkbookRouteVersion.sheet_slots)
        )
        .order_by(WorkbookRoute.code)
    )
    return [workbook_route_read(route) for route in routes]


@router.get(
    "/workbook-routes/{route_id}/source-preview",
    response_model=WorkbookRouteSourcePreviewRead,
)
def get_workbook_route_source_preview(
    route_id: uuid.UUID,
    database: Database,
) -> WorkbookRouteSourcePreviewRead:
    route = database.scalar(
        select(WorkbookRoute)
        .where(WorkbookRoute.id == route_id)
        .options(
            selectinload(WorkbookRoute.versions).selectinload(
                WorkbookRouteVersion.sheet_slots
            )
        )
    )
    if route is None:
        raise HTTPException(status_code=404, detail="文件模板不存在")
    version = next(
        (
            item
            for item in route.versions
            if item.version == route.published_version
        ),
        max(route.versions, key=lambda item: item.version),
    )
    source_paths = _route_source_paths(version.source_metadata)
    source_files = [
        {"name": path.name, "location": _evidence_location(path)}
        for path in source_paths
    ]
    source_profile = None
    warning = None
    available_source = next((path for path in source_paths if path.is_file()), None)
    if available_source is not None:
        try:
            source_profile = ParserRouter().profile(available_source)
        except (OSError, ValueError) as exc:
            warning = f"代表文件暂时无法解析：{exc}"
    elif source_paths:
        warning = "模板来源文件已移动，Sheet 名称暂时无法回看"
    else:
        warning = "该文件模板尚未保存可回看的真实文件证据"

    sheets: list[dict[str, object]] = []
    for slot in sorted(version.sheet_slots, key=lambda item: item.ordinal):
        composition = database.scalar(
            select(SheetComposition)
            .where(SheetComposition.id == slot.sheet_composition_id)
            .options(
                selectinload(SheetComposition.versions).selectinload(
                    SheetCompositionVersion.region_slots
                )
            )
        )
        composition_version = (
            next(
                (
                    item
                    for item in composition.versions
                    if item.version == slot.sheet_composition_version
                ),
                None,
            )
            if composition is not None
            else None
        )
        sheet_name = (
            source_profile.sheets[slot.ordinal].name
            if source_profile is not None
            and slot.ordinal < len(source_profile.sheets)
            else f"第 {slot.ordinal + 1} 个 Sheet"
        )
        sheets.append(
            {
                "sheet_index": slot.ordinal,
                "sheet_name": sheet_name,
                "table_count": (
                    len(composition_version.region_slots)
                    if composition_version is not None
                    else 0
                ),
                "required": slot.required,
            }
        )
    return WorkbookRouteSourcePreviewRead(
        route_id=route.id,
        route_name=version.name,
        source_file_count=len(source_paths),
        source_files=source_files,
        sheets=sheets,
        warning=warning,
    )


@router.post(
    "/workbook-routes",
    response_model=WorkbookRouteRead,
    status_code=status.HTTP_201_CREATED,
)
def create_workbook_route(
    payload: WorkbookRouteCreate,
    database: Database,
) -> WorkbookRouteRead:
    route = WorkbookRoute(code=payload.code)
    route.versions.append(_new_workbook_route_version(version_number=1, payload=payload))
    database.add(route)
    try:
        database.commit()
    except IntegrityError as exc:
        database.rollback()
        raise HTTPException(
            status_code=409,
            detail="workbook route code already exists",
        ) from exc
    database.refresh(route)
    return workbook_route_read(route)


@router.post(
    "/workbook-routes/{route_id}/versions",
    response_model=WorkbookRouteRead,
    status_code=status.HTTP_201_CREATED,
)
def create_workbook_route_version(
    route_id: uuid.UUID,
    payload: WorkbookRouteVersionInput,
    database: Database,
) -> WorkbookRouteRead:
    route = database.scalar(
        select(WorkbookRoute)
        .where(WorkbookRoute.id == route_id)
        .options(
            selectinload(WorkbookRoute.versions).selectinload(WorkbookRouteVersion.sheet_slots)
        )
    )
    if route is None:
        raise HTTPException(status_code=404, detail="workbook route not found")
    latest = max(route.versions, key=lambda item: item.version)
    if latest.status not in {"published", "deprecated"}:
        raise HTTPException(
            status_code=409,
            detail="an editable workbook route version already exists",
        )
    route.versions.append(
        _new_workbook_route_version(
            version_number=latest.version + 1,
            payload=payload,
        )
    )
    database.commit()
    return workbook_route_read(route)


@router.post(
    "/workbook-routes/{route_id}/versions/{version_number}/publish",
    response_model=WorkbookRouteRead,
)
def publish_workbook_route_version(
    route_id: uuid.UUID,
    version_number: int,
    command: ReviewCommand,
    database: Database,
    principal: GovernorPrincipal,
) -> WorkbookRouteRead:
    route = database.scalar(
        select(WorkbookRoute)
        .where(WorkbookRoute.id == route_id)
        .options(
            selectinload(WorkbookRoute.versions).selectinload(WorkbookRouteVersion.sheet_slots)
        )
    )
    if route is None:
        raise HTTPException(status_code=404, detail="workbook route not found")
    version = next(
        (item for item in route.versions if item.version == version_number),
        None,
    )
    if version is None:
        raise HTTPException(
            status_code=404,
            detail="workbook route version not found",
        )
    try:
        publish_workbook_route(
            database,
            route=route,
            version=version,
            actor=principal.user.username,
            comment=command.comment,
            actor_user_id=principal.user.id,
        )
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    database.commit()
    return workbook_route_read(route)


@router.get("/templates", response_model=list[TemplateRead])
def list_templates(database: Database) -> list[TemplateRead]:
    templates = database.scalars(
        select(DocumentTemplate)
        .options(selectinload(DocumentTemplate.versions))
        .order_by(DocumentTemplate.code)
    )
    return [template_read(template) for template in templates]


@router.post("/templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
def create_template(payload: TemplateCreate, database: Database) -> TemplateRead:
    template = DocumentTemplate(code=payload.code)
    template.versions.append(
        TemplateVersion(
            version=1,
            name=payload.name,
            description=payload.description,
            layout_fingerprint=payload.layout_fingerprint,
            definition=payload.definition.model_dump(mode="json"),
            source=payload.source,
            source_metadata=payload.source_metadata,
        )
    )
    database.add(template)
    try:
        database.commit()
    except IntegrityError as exc:
        database.rollback()
        raise HTTPException(status_code=409, detail="template code already exists") from exc
    database.refresh(template)
    return template_read(template)


@router.post(
    "/templates/{template_id}/versions",
    response_model=TemplateRead,
    status_code=status.HTTP_201_CREATED,
)
def create_template_version(
    template_id: uuid.UUID,
    payload: TemplateVersionInput,
    database: Database,
) -> TemplateRead:
    template = database.scalar(
        select(DocumentTemplate)
        .where(DocumentTemplate.id == template_id)
        .options(selectinload(DocumentTemplate.versions))
    )
    if template is None:
        raise HTTPException(status_code=404, detail="template not found")
    latest = max(template.versions, key=lambda item: item.version)
    if latest.status not in {"published", "deprecated"}:
        raise HTTPException(
            status_code=409,
            detail="an editable template version already exists",
        )
    template.versions.append(
        TemplateVersion(
            version=latest.version + 1,
            name=payload.name,
            description=payload.description,
            layout_fingerprint=payload.layout_fingerprint,
            definition=payload.definition.model_dump(mode="json"),
            source=payload.source,
            source_metadata=payload.source_metadata,
        )
    )
    database.commit()
    return template_read(template)


def run_template_command(
    template_id: uuid.UUID,
    version_number: int,
    command: ReviewCommand,
    database: Database,
    principal: GovernorPrincipal,
    action: str,
) -> TemplateRead:
    template = database.scalar(
        select(DocumentTemplate)
        .where(DocumentTemplate.id == template_id)
        .options(selectinload(DocumentTemplate.versions))
    )
    if template is None:
        raise HTTPException(status_code=404, detail="template not found")
    version = next(
        (item for item in template.versions if item.version == version_number),
        None,
    )
    if version is None:
        raise HTTPException(status_code=404, detail="template version not found")
    try:
        transition_template(
            database,
            template=template,
            version=version,
            action=action,
            actor=principal.user.username,
            comment=command.comment,
            actor_user_id=principal.user.id,
        )
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    database.commit()
    return template_read(template)


@router.post(
    "/templates/{template_id}/versions/{version_number}/confirm",
    response_model=TemplateRead,
)
def confirm_template(
    template_id: uuid.UUID,
    version_number: int,
    command: ReviewCommand,
    database: Database,
    principal: GovernorPrincipal,
) -> TemplateRead:
    return run_template_command(
        template_id, version_number, command, database, principal, "confirm"
    )


@router.post(
    "/templates/{template_id}/versions/{version_number}/submit-review",
    response_model=TemplateRead,
)
def submit_template_review(
    template_id: uuid.UUID,
    version_number: int,
    command: ReviewCommand,
    database: Database,
    principal: GovernorPrincipal,
) -> TemplateRead:
    return run_template_command(
        template_id,
        version_number,
        command,
        database,
        principal,
        "submit_review",
    )


@router.post(
    "/templates/{template_id}/versions/{version_number}/approve",
    response_model=TemplateRead,
)
def approve_template(
    template_id: uuid.UUID,
    version_number: int,
    command: ReviewCommand,
    database: Database,
    principal: GovernorPrincipal,
) -> TemplateRead:
    return run_template_command(
        template_id, version_number, command, database, principal, "approve"
    )


@router.post(
    "/templates/{template_id}/versions/{version_number}/reject",
    response_model=TemplateRead,
)
def reject_template(
    template_id: uuid.UUID,
    version_number: int,
    command: ReviewCommand,
    database: Database,
    principal: GovernorPrincipal,
) -> TemplateRead:
    return run_template_command(template_id, version_number, command, database, principal, "reject")


@router.post(
    "/templates/{template_id}/versions/{version_number}/deprecate",
    response_model=TemplateRead,
)
def deprecate_template(
    template_id: uuid.UUID,
    version_number: int,
    command: ReviewCommand,
    database: Database,
    principal: GovernorPrincipal,
) -> TemplateRead:
    return run_template_command(
        template_id, version_number, command, database, principal, "deprecate"
    )
