from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from village_insight.templates.contracts import (
    RegionTemplateDefinition,
    TemplateDefinition,
)
from village_insight.templates.sources import TemplateSource


class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    administrative_unit_id: uuid.UUID | None = None


class DirectoryBatchCreate(BatchCreate):
    directory: str = Field(min_length=1)
    recursive: bool = True


class BatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    administrative_unit_id: uuid.UUID
    created_by_user_id: uuid.UUID
    name: str
    source_kind: str
    status: str
    total_files: int
    completed_files: int
    failed_files: int
    deleted_files: int
    created_at: datetime
    updated_at: datetime


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    batch_id: uuid.UUID
    original_name: str
    relative_path: str | None
    size_bytes: int
    status: str
    evidence_status: str
    formal_import_status: str
    parser_name: str | None
    error_code: str | None
    error_message: str | None
    build_result_deletion_status: str
    build_result_deleted_at: datetime | None
    build_result_deleted_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class FileLedgerItemRead(ItemRead):
    tenant_id: uuid.UUID
    tenant_name: str
    administrative_unit_id: uuid.UUID
    administrative_unit_name: str
    created_by_user_id: uuid.UUID
    created_by_display_name: str
    batch_name: str
    batch_source_kind: str
    match_type: str | None
    score_basis_points: int | None
    requires_hermes: bool | None
    total_regions: int | None
    matched_regions: int | None
    coverage_basis_points: int | None
    hermes_call_count: int
    record_count: int
    partial_record_count: int
    governance_pending: bool
    sheet_count: int | None


class FileLedgerPage(BaseModel):
    items: list[FileLedgerItemRead]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]


class BuildResultDeletionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    status: str
    deleted_counts: dict[str, object]
    retired_counts: dict[str, object]
    error_code: str | None
    requested_at: datetime
    completed_at: datetime | None


class TemplateMatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: uuid.UUID
    source_sha256: str
    profile_contract_version: str
    layout_fingerprint: str
    match_type: str
    score_basis_points: int
    template_id: uuid.UUID | None
    template_version: int | None
    differences: dict[str, object]
    requires_hermes: bool
    matcher_version: str
    total_regions: int
    matched_regions: int
    coverage_basis_points: int
    created_at: datetime
    updated_at: datetime


class RegionTemplateMatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    sheet_id: str
    region_id: str
    header_id: str
    region_fingerprint: str
    match_type: str
    score_basis_points: int
    template_region_component_id: uuid.UUID | None
    template_id: uuid.UUID | None
    template_version: int | None
    region_template_id: uuid.UUID | None
    region_template_version: int | None
    differences: dict[str, object]
    requires_hermes: bool
    matcher_version: str
    created_at: datetime
    updated_at: datetime


class FieldMatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    sheet_id: str
    region_id: str
    header_id: str
    source_column_id: str
    header_path: list[str]
    observed_data_type: str | None
    semantic_field_code: str | None
    semantic_field_version: int | None
    match_type: str
    score_basis_points: int
    context: dict[str, object]
    differences: dict[str, object]
    requires_hermes: bool
    matcher_version: str
    created_at: datetime
    updated_at: datetime


class ImportPlanApprove(BaseModel):
    template_id: uuid.UUID
    template_version: int = Field(ge=1)
    layout_plan: dict[str, object] = Field(default_factory=dict)
    field_mappings: list[dict[str, object]] = Field(default_factory=list)
    actor: str = Field(min_length=1, max_length=160)
    comment: str = Field(default="", max_length=4000)
    supersedes_plan_id: uuid.UUID | None = None


class ApprovedImportPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    revision: int
    supersedes_plan_id: uuid.UUID | None
    source_sha256: str
    profile_contract_version: str
    layout_fingerprint: str
    plan_source: str
    proposal_id: uuid.UUID | None
    template_id: uuid.UUID | None
    template_version: int | None
    primary_region_template_id: uuid.UUID | None
    primary_region_template_version: int | None
    layout_plan: dict[str, object]
    field_mappings: list[dict[str, object]]
    approved_by: str
    approved_by_type: str
    approved_by_user_id: uuid.UUID | None
    approval_comment: str
    created_at: datetime


class DatasetRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    administrative_unit_id: uuid.UUID
    ingestion_batch_id: uuid.UUID
    item_id: uuid.UUID
    source_file_name: str = ""
    administrative_unit_name: str = ""
    approved_plan_id: uuid.UUID
    plan_source: str
    template_id: uuid.UUID | None
    template_version: int | None
    region_template_id: uuid.UUID | None
    region_template_version: int | None
    record_type: str
    sheet_id: str
    region_id: str
    source_row: int
    raw_data: dict[str, Any]
    semantic_data: dict[str, Any]
    mapping_status: str
    quality_status: str
    created_at: datetime


class DatasetRecordPage(BaseModel):
    items: list[DatasetRecordRead]
    total: int
    limit: int
    offset: int


class DatasetRecordGroupRead(BaseModel):
    item_id: uuid.UUID
    source_file_name: str
    administrative_unit_name: str
    sheet_id: str
    sheet_name: str
    region_id: str
    record_type: str
    record_count: int
    passed_count: int
    failed_count: int
    pending_rebuild_count: int
    min_source_row: int
    max_source_row: int
    latest_created_at: datetime


class DatasetRecordGroupPage(BaseModel):
    items: list[DatasetRecordGroupRead]
    total: int
    limit: int
    offset: int


class DatasetRecordFileRead(BaseModel):
    item_id: uuid.UUID
    source_file_name: str
    administrative_unit_name: str
    record_count: int
    passed_count: int
    failed_count: int
    pending_rebuild_count: int
    dataset_count: int
    latest_created_at: datetime
    children: list[DatasetRecordGroupRead]


class DatasetRecordFilePage(BaseModel):
    items: list[DatasetRecordFileRead]
    total: int
    limit: int
    offset: int


class TemplateProposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    administrative_unit_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None
    source: str
    source_item_id: uuid.UUID | None
    model_name: str | None
    prompt_version: str | None
    confidence: float | None
    proposal: dict[str, object]
    status: str
    resolution_comment: str
    resolved_by_user_id: uuid.UUID | None
    created_at: datetime
    resolved_at: datetime | None


class ReviewQueueItemRead(BaseModel):
    proposal_id: uuid.UUID
    batch_id: uuid.UUID
    batch_name: str
    tenant_id: uuid.UUID
    tenant_name: str
    administrative_unit_id: uuid.UUID
    administrative_unit_name: str
    created_by_user_id: uuid.UUID
    created_by_display_name: str
    item_id: uuid.UUID
    file_name: str
    relative_path: str | None
    match_type: str
    score_basis_points: int
    confidence: float | None
    reason_codes: list[str]
    proposal: dict[str, object]
    matched_template_code: str | None
    matched_template_name: str | None
    matched_domain: str | None
    matched_record_type: str | None
    matched_record_grain: str | None
    formal_import_status: str
    governance_issue_codes: list[str]
    review_kind: Literal["field", "structure"] = "field"
    field_evidence: list[ReviewFieldEvidenceRead] = Field(default_factory=list)
    field_count: int = 0
    created_at: datetime


class ReviewQueuePage(BaseModel):
    items: list[ReviewQueueItemRead]
    total: int
    limit: int
    offset: int


class ReviewFieldEvidenceRead(BaseModel):
    source_column_id: str
    sheet_id: str
    sheet_name: str
    region_id: str
    column_index: int = Field(ge=1)
    column_coordinate: str
    header_path: list[str]
    parent_path: list[str]
    leaf_header: str
    observed_data_type: str | None
    match_type: str
    score_basis_points: int
    candidates: list[dict[str, object]] = Field(default_factory=list)
    hermes_suggestion: dict[str, object] = Field(default_factory=dict)
    requires_resolution: bool


class GovernanceFieldResolutionInput(BaseModel):
    source_column_id: str = Field(min_length=1, max_length=500)
    mode: Literal["reuse_existing", "create_new", "ignore"]
    semantic_field_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.]{1,159}$",
    )
    expected_field_version: int | None = Field(default=None, ge=1)
    learn_alias: str | None = Field(default=None, min_length=1, max_length=500)
    learn_path: bool = True
    role: str | None = Field(default=None, max_length=120)
    unit: str | None = Field(default=None, max_length=80)
    new_field_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.]{1,159}$",
    )
    new_field_name: str | None = Field(default=None, min_length=1, max_length=200)
    new_field_layer: Literal["base", "domain"] | None = None
    new_field_data_type: (
        Literal["text", "integer", "decimal", "boolean", "date", "datetime"] | None
    ) = None
    ignore_scope: Literal["file", "context"] | None = None
    ignore_reason: str | None = Field(default=None, min_length=1, max_length=240)


class ProposalAcceptCommand(BaseModel):
    actor: str = Field(min_length=1, max_length=160)
    comment: str = Field(default="", max_length=4000)
    template_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.]{1,159}$",
    )
    template_name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=80)
    record_type: str = Field(min_length=1, max_length=120)
    record_grain: str | None = Field(default=None, max_length=120)
    field_resolutions: list[GovernanceFieldResolutionInput] = Field(default_factory=list)


class SemanticFieldVariantInput(BaseModel):
    kind: Literal["alias", "header_path", "role_context"]
    alias: str | None = Field(default=None, min_length=1, max_length=500)
    header_path: list[str] = Field(default_factory=list)
    role: str | None = Field(default=None, max_length=120)
    domain: str | None = Field(default=None, max_length=80)
    record_type: str | None = Field(default=None, max_length=120)
    observed_data_type: (
        Literal["text", "integer", "decimal", "boolean", "date", "datetime"] | None
    ) = None
    unit_dimension: str | None = Field(default=None, max_length=80)
    source: TemplateSource = "manual"
    confidence_basis_points: int = Field(default=10_000, ge=0, le=10_000)
    evidence: dict[str, object] = Field(default_factory=dict)


class SemanticFieldVariantRead(SemanticFieldVariantInput):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_key: str
    normalized_value: str
    parent_path: list[str]
    created_at: datetime


class SemanticFieldVersionInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    layer: Literal["base", "domain"]
    data_type: Literal["text", "integer", "decimal", "boolean", "date", "datetime"]
    unit_dimension: str | None = Field(default=None, max_length=80)
    aliases: list[str] = Field(default_factory=list)
    validators: list[dict[str, object]] = Field(default_factory=list)
    variants: list[SemanticFieldVariantInput] = Field(default_factory=list)
    source: TemplateSource = "manual"
    source_metadata: dict[str, object] = Field(default_factory=dict)


class SemanticFieldCreate(SemanticFieldVersionInput):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,159}$")


class SemanticFieldRead(BaseModel):
    variants: list[SemanticFieldVariantRead] = Field(default_factory=list)
    id: uuid.UUID
    code: str
    version: int
    status: str
    published_version: int | None
    name: str
    description: str
    layer: Literal["base", "domain"]
    data_type: Literal["text", "integer", "decimal", "boolean", "date", "datetime"]
    unit_dimension: str | None
    aliases: list[str]
    validators: list[dict[str, object]]
    source: TemplateSource
    source_metadata: dict[str, object]
    created_at: datetime
    updated_at: datetime


class SemanticFieldVersionHistoryRead(BaseModel):
    version: int
    status: str
    name: str
    description: str
    layer: Literal["base", "domain"]
    data_type: Literal["text", "integer", "decimal", "boolean", "date", "datetime"]
    unit_dimension: str | None
    alias_count: int
    variant_count: int
    source: TemplateSource
    source_metadata: dict[str, object]
    created_at: datetime


class SemanticFieldTemplateReferenceRead(BaseModel):
    template_id: uuid.UUID
    template_code: str
    template_name: str
    template_version: int
    template_status: str


class SemanticFieldDetailRead(BaseModel):
    field: SemanticFieldRead
    versions: list[SemanticFieldVersionHistoryRead]
    referenced_by: list[SemanticFieldTemplateReferenceRead]


class RegionTemplateVersionInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    region_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    definition: RegionTemplateDefinition
    source: TemplateSource = "manual"
    source_metadata: dict[str, object] = Field(default_factory=dict)


class RegionTemplateCreate(RegionTemplateVersionInput):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,159}$")


class RegionTemplateRead(RegionTemplateVersionInput):
    id: uuid.UUID
    code: str
    version: int
    status: str
    published_version: int | None
    created_at: datetime
    updated_at: datetime


class RegionSourcePreviewColumnRead(BaseModel):
    excel_column: str
    column_number: int | None = None
    header_path: list[str]
    source_header: str
    sample_values: list[str]
    semantic_field_code: str
    semantic_field_name: str
    match_status: str
    role: str | None = None


class RegionSourcePreviewRead(BaseModel):
    template_id: uuid.UUID
    template_name: str
    source_file: str
    source_location: str
    sheet_name: str
    sheet_index: int
    source_range: str
    header_rows: list[int]
    layout_mode: str
    evidence_count: int
    columns: list[RegionSourcePreviewColumnRead]
    warning: str | None = None


class WorkbookRouteSourceFileRead(BaseModel):
    name: str
    location: str


class WorkbookRouteSourceSheetRead(BaseModel):
    sheet_index: int
    sheet_name: str
    table_count: int
    required: bool


class WorkbookRouteSourcePreviewRead(BaseModel):
    route_id: uuid.UUID
    route_name: str
    source_file_count: int
    source_files: list[WorkbookRouteSourceFileRead]
    sheets: list[WorkbookRouteSourceSheetRead]
    warning: str | None = None


class SheetCompositionRegionSlotInput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slot_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")
    region_template_id: uuid.UUID
    region_template_version: int = Field(ge=1)
    ordinal: int = Field(ge=0)
    required: bool = True
    cardinality: Literal["one", "zero_or_one", "one_or_more"] = "one"
    materialize: bool = True
    match_hints: dict[str, object] = Field(default_factory=dict)


class SheetCompositionVersionInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    composition_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    region_slots: list[SheetCompositionRegionSlotInput] = Field(min_length=1)
    matching_rules: dict[str, object] = Field(default_factory=dict)
    source: TemplateSource = "manual"
    source_metadata: dict[str, object] = Field(default_factory=dict)


class SheetCompositionCreate(SheetCompositionVersionInput):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,159}$")


class SheetCompositionRead(SheetCompositionVersionInput):
    id: uuid.UUID
    code: str
    version: int
    status: str
    published_version: int | None
    created_at: datetime
    updated_at: datetime


class WorkbookRouteSheetSlotInput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slot_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,119}$")
    sheet_composition_id: uuid.UUID
    sheet_composition_version: int = Field(ge=1)
    ordinal: int = Field(ge=0)
    required: bool = True
    cardinality: Literal["one", "zero_or_one", "one_or_more"] = "one"
    materialize: bool = True
    match_hints: dict[str, object] = Field(default_factory=dict)


class WorkbookRouteVersionInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    route_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    sheet_slots: list[WorkbookRouteSheetSlotInput] = Field(min_length=1)
    matching_rules: dict[str, object] = Field(default_factory=dict)
    source: TemplateSource = "manual"
    source_metadata: dict[str, object] = Field(default_factory=dict)


class WorkbookRouteCreate(WorkbookRouteVersionInput):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,159}$")


class WorkbookRouteRead(WorkbookRouteVersionInput):
    id: uuid.UUID
    code: str
    version: int
    status: str
    published_version: int | None
    created_at: datetime
    updated_at: datetime


class TemplateCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,159}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    layout_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    definition: TemplateDefinition
    source: Literal["manual", "bootstrap", "hermes", "rule"] = "manual"
    source_metadata: dict[str, object] = Field(default_factory=dict)


class TemplateVersionInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    layout_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    definition: TemplateDefinition
    source: Literal["manual", "bootstrap", "hermes", "rule"] = "manual"
    source_metadata: dict[str, object] = Field(default_factory=dict)


class TemplateRead(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str
    version: int
    status: str
    layout_fingerprint: str
    definition: TemplateDefinition
    source: str
    source_metadata: dict[str, object]
    published_version: int | None
    created_at: datetime
    updated_at: datetime


class ReviewCommand(BaseModel):
    actor: str = Field(min_length=1, max_length=160)
    comment: str = Field(default="", max_length=4000)


class LLMConfigurationUpdate(BaseModel):
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")
    preset_id: str = Field(
        default="custom_openai",
        pattern=r"^[a-z][a-z0-9_-]{1,79}$",
    )
    api_mode: Literal["openai_chat", "anthropic_messages"] = "openai_chat"
    model: str = Field(min_length=1, max_length=200)
    fast_model: str | None = Field(default=None, min_length=1, max_length=200)
    reasoning_model: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str = Field(pattern=r"^https://", max_length=500)
    thinking_protocol: Literal["none", "deepseek"] = "none"
    api_key: str | None = Field(default=None, min_length=8, max_length=500)
    max_tokens: int | None = Field(default=None, ge=128, le=1_000_000)


class LLMConfigurationRead(BaseModel):
    provider: str
    preset_id: str
    api_mode: Literal["openai_chat", "anthropic_messages"]
    model: str
    fast_model: str
    reasoning_model: str
    base_url: str
    thinking_protocol: Literal["none", "deepseek"]
    api_key_configured: bool
    api_key_hint: str | None
    api_key_reentry_required: bool = False
    max_tokens: int | None
    source: str
    updated_at: datetime | None


class LLMConnectionTestResult(BaseModel):
    status: str
    provider: str
    model: str
    api_mode: Literal["openai_chat", "anthropic_messages"]
    latency_ms: int
    stages: list[str] = Field(default_factory=list)


class LLMProviderPresetRead(BaseModel):
    id: str
    name: str
    provider: str
    api_mode: Literal["openai_chat", "anthropic_messages"]
    base_url: str
    default_model: str
    fast_model: str
    reasoning_model: str
    supports_model_discovery: bool
    description: str
    billing_notice: str | None = None
    api_key_configured: bool = False
    api_key_hint: str | None = None
    api_key_reentry_required: bool = False


class LLMModelDiscoveryRequest(BaseModel):
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")
    api_mode: Literal["openai_chat", "anthropic_messages"] = "openai_chat"
    base_url: str = Field(pattern=r"^https://", max_length=500)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)


class LLMModelDiscoveryResult(BaseModel):
    status: str
    models: list[str]
    latency_ms: int


class MetricDefinitionCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,159}$")
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    fact_set_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.]{1,159}$",
    )
    fact_set_version: int | None = Field(default=None, ge=1)
    semantic_manifest_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.]{1,159}$",
    )
    semantic_manifest_version: int | None = Field(default=None, ge=1)
    record_type: str | None = Field(default=None, max_length=120)
    record_grain: str | None = Field(default=None, max_length=160)
    semantic_field_code: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,159}$")
    semantic_field_version: int = Field(ge=1)
    aggregation: Literal["count", "sum", "avg", "min", "max"]
    additivity: Literal["additive", "semi_additive", "non_additive"] = "additive"
    unit: str | None = Field(default=None, max_length=80)
    allowed_filter_fields: list[str] = Field(default_factory=list)
    allowed_group_fields: list[str] = Field(default_factory=list)
    forbidden_aggregation_dimensions: list[str] = Field(default_factory=list)
    identity_field_codes: list[str] = Field(default_factory=list)
    deduplication_policy: dict[str, Any] = Field(default_factory=dict)
    status_filters: list[dict[str, Any]] = Field(default_factory=list)
    time_policy: dict[str, Any] = Field(default_factory=dict)
    null_policy: Literal["exclude", "zero", "reject"] = "exclude"
    conflict_policy: Literal["reject", "exclude", "latest"] = "reject"
    evidence_policy: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)


class MetricDefinitionRead(MetricDefinitionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    enabled: bool
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SourceSupersessionCreate(BaseModel):
    superseded_item_id: uuid.UUID
    replacement_item_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=1000)


class SourceSupersessionRead(BaseModel):
    id: uuid.UUID
    administrative_unit_id: uuid.UUID
    superseded_item_id: uuid.UUID
    superseded_file_name: str
    replacement_item_id: uuid.UUID
    replacement_file_name: str
    reason: str
    declared_by_user_id: uuid.UUID
    created_at: datetime


class QueryFactSetDefinitionCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,159}$")
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    domain: str = Field(min_length=1, max_length=120)
    record_type: str = Field(min_length=1, max_length=120)
    record_grain: str = Field(min_length=1, max_length=160)
    provenance_rule: dict[str, Any]
    identity_field_codes: list[str] = Field(default_factory=list)
    dimension_field_codes: list[str] = Field(default_factory=list)
    measure_definitions: list[dict[str, Any]] = Field(default_factory=list)
    time_dimensions: list[dict[str, Any]] = Field(default_factory=list)
    status_dimensions: list[dict[str, Any]] = Field(default_factory=list)
    sensitive_field_policies: list[dict[str, Any]] = Field(default_factory=list)
    conflict_policy: dict[str, Any] = Field(default_factory=dict)
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class QueryFactSetDefinitionRead(QueryFactSetDefinitionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    definition_fingerprint: str
    published_at: datetime | None
    created_at: datetime


class SemanticManifestDefinitionCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,159}$")
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    fact_set_code: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,159}$")
    fact_set_version: int = Field(ge=1)
    root_entity: str = Field(min_length=1, max_length=160)
    entities: list[dict[str, Any]]
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    measures: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    allowed_join_paths: list[dict[str, Any]] = Field(default_factory=list)
    max_join_depth: int = Field(default=0, ge=0, le=3)
    deduplication_policy: dict[str, Any] = Field(default_factory=dict)
    default_time_policy: dict[str, Any] = Field(default_factory=dict)
    evidence_policy: dict[str, Any] = Field(default_factory=dict)
    catalog_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class SemanticManifestDefinitionRead(SemanticManifestDefinitionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    manifest_fingerprint: str
    published_at: datetime | None
    created_at: datetime


class QualityIssueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    approved_plan_id: uuid.UUID | None
    code: str
    severity: str
    message: str
    evidence: dict[str, object]
    created_at: datetime
