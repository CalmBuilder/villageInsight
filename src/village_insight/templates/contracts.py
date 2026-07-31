from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TemplateFieldBinding(BaseModel):
    source_column_id: str = Field(min_length=1, max_length=500)
    header_path: list[str] = Field(min_length=1)
    semantic_field_code: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,159}$")
    semantic_field_version: int = Field(ge=1)
    source_selector: dict[str, object] | None = None
    role: str | None = Field(default=None, max_length=80)
    unit: str | None = Field(default=None, max_length=80)
    required: bool = False
    value_required: bool = False
    normalizer: str | None = Field(default=None, max_length=120)


class TemplateDefinition(BaseModel):
    contract_version: Literal["document-template/v1"] = "document-template/v1"
    domain: str = Field(min_length=1, max_length=80)
    region_kind: Literal["table", "form", "matrix"]
    record_type: str = Field(min_length=1, max_length=120)
    record_grain: str = Field(min_length=1, max_length=120)
    field_bindings: list[TemplateFieldBinding] = Field(default_factory=list)
    data_row_rules: list[dict[str, object]] = Field(default_factory=list)
    exclusion_rules: list[dict[str, object]] = Field(default_factory=list)
    metric_codes: list[str] = Field(default_factory=list)
    identity_field_codes: list[str] = Field(default_factory=list)


class RegionTemplateDefinition(BaseModel):
    contract_version: Literal["region-template/v1"] = "region-template/v1"
    domain: str = Field(min_length=1, max_length=80)
    region_kind: Literal["table", "form", "matrix"]
    record_type: str = Field(min_length=1, max_length=120)
    record_grain: str = Field(min_length=1, max_length=120)
    header_signature: list[list[str]] = Field(min_length=1)
    layout_rules: dict[str, object] = Field(default_factory=dict)
    field_bindings: list[TemplateFieldBinding] = Field(default_factory=list)
    identity_policy: dict[str, object] = Field(default_factory=dict)
    quality_rules: list[dict[str, object]] = Field(default_factory=list)
