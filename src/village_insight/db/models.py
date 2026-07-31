from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from village_insight.db.base import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

LEGACY_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
LEGACY_TOWNSHIP_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
LEGACY_VILLAGE_ID = uuid.UUID("00000000-0000-4000-8000-000000000003")
LEGACY_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000004")


def utcnow() -> datetime:
    return datetime.now(UTC)


class BatchStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ItemStatus(StrEnum):
    PENDING = "pending"
    PROFILING = "profiling"
    MATCHING = "matching"
    RECOGNIZING = "recognizing"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    MATERIALIZING = "materializing"
    IMPORTED = "imported"
    FAILED = "failed"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EvidenceStatus(StrEnum):
    PENDING = "pending"
    STORED = "stored"
    FAILED = "failed"


class FormalImportStatus(StrEnum):
    PENDING = "pending"
    NEEDS_REVIEW = "needs_review"
    MATERIALIZING = "materializing"
    IMPORTED = "imported"
    PARTIAL = "partial"
    PENDING_REBUILD = "pending_rebuild"
    FAILED = "failed"


class TemplateStatus(StrEnum):
    DRAFT = "draft"
    USER_CONFIRMED = "user_confirmed"
    ADMIN_REVIEW = "admin_review"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class MatchType(StrEnum):
    EXACT = "exact"
    PARTIAL = "partial"
    NONE = "none"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class TenantKind(StrEnum):
    BUSINESS = "business"
    PLATFORM = "platform"


class AdministrativeUnitType(StrEnum):
    TOWNSHIP = "township"
    VILLAGE = "village"


class MembershipRole(StrEnum):
    TENANT_ADMIN = "tenant_admin"
    VILLAGE_OPERATOR = "village_operator"
    PLATFORM_ADMIN = "platform_admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(160), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default=UserStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("name", name="uq_tenants_name"),
        CheckConstraint(
            "kind IN ('business', 'platform')",
            name="ck_tenant_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32), default=TenantKind.BUSINESS)
    status: Mapped[str] = mapped_column(String(32), default=UserStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AdministrativeUnit(Base):
    __tablename__ = "administrative_units"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "administrative_code",
            name="uq_administrative_unit_tenant_code",
        ),
        CheckConstraint(
            "unit_type IN ('township', 'village')",
            name="ck_administrative_unit_type",
        ),
        Index("ix_administrative_units_tenant_parent", "tenant_id", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("administrative_units.id", ondelete="RESTRICT"), index=True
    )
    unit_type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    administrative_code: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), default=UserStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership_user"),
        CheckConstraint(
            "role IN ('tenant_admin', 'village_operator', 'platform_admin')",
            name="ck_tenant_membership_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32), default=UserStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MembershipScope(Base):
    __tablename__ = "membership_scopes"
    __table_args__ = (
        UniqueConstraint(
            "membership_id",
            "administrative_unit_id",
            name="uq_membership_scope_unit",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant_memberships.id", ondelete="CASCADE"), index=True
    )
    administrative_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("administrative_units.id", ondelete="RESTRICT"), index=True
    )
    include_descendants: Mapped[bool] = mapped_column(Boolean, default=False)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_active", "token_hash", "expires_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant_memberships.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestionBatch(Base):
    __tablename__ = "ingestion_batches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        default=lambda: LEGACY_TENANT_ID,
        index=True,
    )
    administrative_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("administrative_units.id", ondelete="RESTRICT"),
        default=lambda: LEGACY_VILLAGE_ID,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        default=lambda: LEGACY_USER_ID,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200))
    source_kind: Mapped[str] = mapped_column(String(32), default="upload")
    status: Mapped[str] = mapped_column(String(32), default=BatchStatus.PENDING)
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    completed_files: Mapped[int] = mapped_column(Integer, default=0)
    failed_files: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    items: Mapped[list[IngestionItem]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class IngestionItem(Base):
    __tablename__ = "ingestion_items"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "administrative_unit_id",
            "source_sha256",
            name="uq_item_village_source_sha256",
        ),
        Index("ix_ingestion_items_batch_status", "batch_id", "status"),
        Index("ix_ingestion_items_created_page", "created_at", "id"),
        Index(
            "ix_ingestion_items_scope_created_page",
            "tenant_id",
            "administrative_unit_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        default=lambda: LEGACY_TENANT_ID,
        index=True,
    )
    administrative_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("administrative_units.id", ondelete="RESTRICT"),
        default=lambda: LEGACY_VILLAGE_ID,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        default=lambda: LEGACY_USER_ID,
        index=True,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="CASCADE"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(512))
    relative_path: Mapped[str | None] = mapped_column(String(1024))
    source_path: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default=ItemStatus.PENDING)
    evidence_status: Mapped[str] = mapped_column(
        String(32),
        default=EvidenceStatus.PENDING,
    )
    formal_import_status: Mapped[str] = mapped_column(
        String(32),
        default=FormalImportStatus.PENDING,
    )
    parser_name: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    batch: Mapped[IngestionBatch] = relationship(back_populates="items")
    profile_record: Mapped[DocumentProfile | None] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        uselist=False,
    )
    sheet_catalog: Mapped[list[DocumentSheetCatalog]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
    )
    template_match: Mapped[TemplateMatch | None] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        uselist=False,
    )
    approved_import_plans: Mapped[list[ApprovedImportPlan]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
    )


class DocumentProfile(Base):
    __tablename__ = "document_profiles"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    contract_version: Mapped[str] = mapped_column(String(80))
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    parser_name: Mapped[str] = mapped_column(String(80))
    parser_version: Mapped[str] = mapped_column(String(80))
    profile: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    profile_payload: Mapped[bytes | None] = mapped_column(LargeBinary)
    profile_encoding: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    item: Mapped[IngestionItem] = relationship(back_populates="profile_record")


class DocumentSheetCatalog(Base):
    """Rebuildable query projection of Sheet identity from immutable profiles."""

    __tablename__ = "document_sheet_catalog"
    __table_args__ = (
        UniqueConstraint("item_id", "sheet_id", name="uq_document_sheet_catalog_item_sheet"),
        Index("ix_document_sheet_catalog_item_order", "item_id", "sheet_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="CASCADE"),
        index=True,
    )
    sheet_id: Mapped[str] = mapped_column(String(200))
    sheet_name: Mapped[str] = mapped_column(String(512))
    sheet_order: Mapped[int] = mapped_column(Integer)
    region_count: Mapped[int] = mapped_column(Integer, default=0)

    item: Mapped[IngestionItem] = relationship(back_populates="sheet_catalog")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        Index("ix_jobs_claim", "status", "available_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    administrative_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("administrative_units.id", ondelete="RESTRICT"), index=True
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.PENDING)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SemanticField(Base):
    __tablename__ = "semantic_fields"
    __table_args__ = (UniqueConstraint("code", name="uq_semantic_fields_code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(160))
    published_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    versions: Mapped[list[SemanticFieldVersion]] = relationship(
        back_populates="field",
        cascade="all, delete-orphan",
    )


class SemanticFieldVersion(Base):
    __tablename__ = "semantic_field_versions"
    __table_args__ = (
        UniqueConstraint("field_id", "version", name="uq_semantic_field_versions_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_fields.id", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    layer: Mapped[str] = mapped_column(String(40))
    data_type: Mapped[str] = mapped_column(String(40))
    unit_dimension: Mapped[str | None] = mapped_column(String(80))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    validators: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default=TemplateStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    field: Mapped[SemanticField] = relationship(back_populates="versions")
    review_events: Mapped[list[SemanticFieldReviewEvent]] = relationship(
        back_populates="field_version",
        cascade="all, delete-orphan",
    )
    variants: Mapped[list[SemanticFieldVariant]] = relationship(
        back_populates="field_version",
        cascade="all, delete-orphan",
    )


class SemanticFieldVariant(Base):
    __tablename__ = "semantic_field_variants"
    __table_args__ = (
        UniqueConstraint(
            "field_version_id",
            "variant_key",
            name="uq_semantic_field_variants_key",
        ),
        Index(
            "ix_semantic_field_variants_normalized",
            "normalized_value",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_field_versions.id", ondelete="CASCADE"),
        index=True,
    )
    variant_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(40))
    normalized_value: Mapped[str] = mapped_column(String(500))
    alias: Mapped[str | None] = mapped_column(String(500))
    header_path: Mapped[list[str]] = mapped_column(JSON, default=list)
    parent_path: Mapped[list[str]] = mapped_column(JSON, default=list)
    role: Mapped[str | None] = mapped_column(String(120))
    domain: Mapped[str | None] = mapped_column(String(80))
    record_type: Mapped[str | None] = mapped_column(String(120))
    observed_data_type: Mapped[str | None] = mapped_column(String(40))
    unit_dimension: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(40))
    confidence_basis_points: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    field_version: Mapped[SemanticFieldVersion] = relationship(back_populates="variants")


class SemanticFieldReviewEvent(Base):
    __tablename__ = "semantic_field_review_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    field_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_field_versions.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(160))
    actor_type: Mapped[str] = mapped_column(String(32), default="user")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    field_version: Mapped[SemanticFieldVersion] = relationship(back_populates="review_events")


class DocumentTemplate(Base):
    __tablename__ = "document_templates"
    __table_args__ = (UniqueConstraint("code", name="uq_document_templates_code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(160))
    published_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    versions: Mapped[list[TemplateVersion]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_template_versions_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_templates.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default=TemplateStatus.DRAFT)
    layout_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    template: Mapped[DocumentTemplate] = relationship(back_populates="versions")
    review_events: Mapped[list[TemplateReviewEvent]] = relationship(
        back_populates="template_version",
        cascade="all, delete-orphan",
    )


class TemplateRegionComponent(Base):
    __tablename__ = "template_region_components"
    __table_args__ = (
        UniqueConstraint(
            "template_version_id",
            "component_key",
            name="uq_template_region_component_key",
        ),
        Index(
            "ix_template_region_components_fingerprint",
            "region_fingerprint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("template_versions.id", ondelete="CASCADE"),
        index=True,
    )
    component_key: Mapped[str] = mapped_column(String(120))
    region_fingerprint: Mapped[str] = mapped_column(String(64))
    signature: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_decision_index: Mapped[int] = mapped_column(Integer)
    field_binding_indexes: Mapped[list[int]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RegionTemplate(Base):
    __tablename__ = "region_templates"
    __table_args__ = (UniqueConstraint("code", name="uq_region_templates_code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(160))
    published_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    versions: Mapped[list[RegionTemplateVersion]] = relationship(
        back_populates="region_template",
        cascade="all, delete-orphan",
    )


class RegionTemplateVersion(Base):
    __tablename__ = "region_template_versions"
    __table_args__ = (
        UniqueConstraint(
            "region_template_id",
            "version",
            name="uq_region_template_versions_version",
        ),
        Index(
            "ix_region_template_versions_fingerprint",
            "region_fingerprint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    region_template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("region_templates.id", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default=TemplateStatus.DRAFT)
    domain: Mapped[str] = mapped_column(String(80))
    record_type: Mapped[str] = mapped_column(String(120))
    record_grain: Mapped[str] = mapped_column(String(120))
    region_kind: Mapped[str] = mapped_column(String(40))
    region_fingerprint: Mapped[str] = mapped_column(String(64))
    header_signature: Mapped[list[list[str]]] = mapped_column(JSON, default=list)
    layout_rules: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    field_bindings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    identity_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    quality_rules: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    source: Mapped[str] = mapped_column(String(40), default="manual")
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    region_template: Mapped[RegionTemplate] = relationship(back_populates="versions")
    review_events: Mapped[list[RegionTemplateReviewEvent]] = relationship(
        back_populates="region_template_version",
        cascade="all, delete-orphan",
    )


class RegionTemplateReviewEvent(Base):
    __tablename__ = "region_template_review_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    region_template_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("region_template_versions.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(160))
    actor_type: Mapped[str] = mapped_column(String(32), default="user")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    region_template_version: Mapped[RegionTemplateVersion] = relationship(
        back_populates="review_events"
    )


class SheetComposition(Base):
    __tablename__ = "sheet_compositions"
    __table_args__ = (UniqueConstraint("code", name="uq_sheet_compositions_code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(160))
    published_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    versions: Mapped[list[SheetCompositionVersion]] = relationship(
        back_populates="sheet_composition",
        cascade="all, delete-orphan",
    )


class SheetCompositionVersion(Base):
    __tablename__ = "sheet_composition_versions"
    __table_args__ = (
        UniqueConstraint(
            "sheet_composition_id",
            "version",
            name="uq_sheet_composition_versions_version",
        ),
        Index(
            "ix_sheet_composition_versions_fingerprint",
            "composition_fingerprint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sheet_composition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sheet_compositions.id", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default=TemplateStatus.DRAFT)
    composition_fingerprint: Mapped[str] = mapped_column(String(64))
    matching_rules: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    source: Mapped[str] = mapped_column(String(40), default="manual")
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sheet_composition: Mapped[SheetComposition] = relationship(back_populates="versions")
    region_slots: Mapped[list[SheetCompositionRegionSlot]] = relationship(
        back_populates="sheet_composition_version",
        cascade="all, delete-orphan",
        order_by="SheetCompositionRegionSlot.ordinal",
    )
    review_events: Mapped[list[SheetCompositionReviewEvent]] = relationship(
        back_populates="sheet_composition_version",
        cascade="all, delete-orphan",
    )


class SheetCompositionRegionSlot(Base):
    __tablename__ = "sheet_composition_region_slots"
    __table_args__ = (
        UniqueConstraint(
            "sheet_composition_version_id",
            "slot_key",
            name="uq_sheet_composition_region_slots_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sheet_composition_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sheet_composition_versions.id", ondelete="CASCADE"),
        index=True,
    )
    slot_key: Mapped[str] = mapped_column(String(120))
    region_template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("region_templates.id", ondelete="RESTRICT"),
        index=True,
    )
    region_template_version: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    cardinality: Mapped[str] = mapped_column(String(20), default="one")
    materialize: Mapped[bool] = mapped_column(Boolean, default=True)
    match_hints: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)

    sheet_composition_version: Mapped[SheetCompositionVersion] = relationship(
        back_populates="region_slots"
    )


class SheetCompositionReviewEvent(Base):
    __tablename__ = "sheet_composition_review_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sheet_composition_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sheet_composition_versions.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(160))
    actor_type: Mapped[str] = mapped_column(String(32), default="user")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sheet_composition_version: Mapped[SheetCompositionVersion] = relationship(
        back_populates="review_events"
    )


class WorkbookRoute(Base):
    __tablename__ = "workbook_routes"
    __table_args__ = (UniqueConstraint("code", name="uq_workbook_routes_code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(160))
    published_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    versions: Mapped[list[WorkbookRouteVersion]] = relationship(
        back_populates="workbook_route",
        cascade="all, delete-orphan",
    )


class WorkbookRouteVersion(Base):
    __tablename__ = "workbook_route_versions"
    __table_args__ = (
        UniqueConstraint(
            "workbook_route_id",
            "version",
            name="uq_workbook_route_versions_version",
        ),
        Index("ix_workbook_route_versions_fingerprint", "route_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workbook_route_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workbook_routes.id", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default=TemplateStatus.DRAFT)
    route_fingerprint: Mapped[str] = mapped_column(String(64))
    matching_rules: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    source: Mapped[str] = mapped_column(String(40), default="manual")
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workbook_route: Mapped[WorkbookRoute] = relationship(back_populates="versions")
    sheet_slots: Mapped[list[WorkbookRouteSheetSlot]] = relationship(
        back_populates="workbook_route_version",
        cascade="all, delete-orphan",
        order_by="WorkbookRouteSheetSlot.ordinal",
    )
    review_events: Mapped[list[WorkbookRouteReviewEvent]] = relationship(
        back_populates="workbook_route_version",
        cascade="all, delete-orphan",
    )


class WorkbookRouteSheetSlot(Base):
    __tablename__ = "workbook_route_sheet_slots"
    __table_args__ = (
        UniqueConstraint(
            "workbook_route_version_id",
            "slot_key",
            name="uq_workbook_route_sheet_slots_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workbook_route_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workbook_route_versions.id", ondelete="CASCADE"),
        index=True,
    )
    slot_key: Mapped[str] = mapped_column(String(120))
    sheet_composition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sheet_compositions.id", ondelete="RESTRICT"),
        index=True,
    )
    sheet_composition_version: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    cardinality: Mapped[str] = mapped_column(String(20), default="one")
    materialize: Mapped[bool] = mapped_column(Boolean, default=True)
    match_hints: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)

    workbook_route_version: Mapped[WorkbookRouteVersion] = relationship(
        back_populates="sheet_slots"
    )


class WorkbookRouteReviewEvent(Base):
    __tablename__ = "workbook_route_review_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workbook_route_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workbook_route_versions.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(160))
    actor_type: Mapped[str] = mapped_column(String(32), default="user")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workbook_route_version: Mapped[WorkbookRouteVersion] = relationship(
        back_populates="review_events"
    )


class TemplateReviewEvent(Base):
    __tablename__ = "template_review_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("template_versions.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(40))
    from_status: Mapped[str] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(160))
    actor_type: Mapped[str] = mapped_column(String(32), default="user")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    template_version: Mapped[TemplateVersion] = relationship(back_populates="review_events")


class TemplateProposal(Base):
    __tablename__ = "template_proposals"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_template_proposals_idempotency"),
        Index(
            "ix_template_proposals_pending_created",
            "created_at",
            "id",
            postgresql_where=text("status = 'pending' AND source_item_id IS NOT NULL"),
        ),
        Index(
            "ix_template_proposals_pending_scope_created",
            "tenant_id",
            "administrative_unit_id",
            "created_at",
            "id",
            postgresql_where=text("status = 'pending' AND source_item_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    administrative_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("administrative_units.id", ondelete="RESTRICT"), index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(32))
    source_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="SET NULL"),
        index=True,
    )
    model_name: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(80))
    confidence: Mapped[float | None] = mapped_column()
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default=ProposalStatus.PENDING)
    resolution_comment: Mapped[str] = mapped_column(Text, default="")
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GovernanceResolution(Base):
    __tablename__ = "governance_resolutions"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_governance_resolutions_proposal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("template_proposals.id", ondelete="RESTRICT"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="RESTRICT"), index=True
    )
    contract_version: Mapped[str] = mapped_column(
        String(80), default="field-governance-resolution/v1"
    )
    domain: Mapped[str] = mapped_column(String(80))
    record_type: Mapped[str] = mapped_column(String(120))
    record_grain: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="committed")
    region_template_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT, default=list
    )
    approved_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approved_import_plans.id", ondelete="SET NULL"), index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GovernanceFieldResolution(Base):
    __tablename__ = "governance_field_resolutions"
    __table_args__ = (
        UniqueConstraint(
            "governance_resolution_id",
            "source_column_id",
            name="uq_governance_field_resolutions_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    governance_resolution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("governance_resolutions.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="RESTRICT"), index=True
    )
    source_column_id: Mapped[str] = mapped_column(String(500))
    sheet_id: Mapped[str] = mapped_column(String(200))
    sheet_name: Mapped[str] = mapped_column(String(200))
    column_index: Mapped[int] = mapped_column(Integer)
    column_coordinate: Mapped[str] = mapped_column(String(16))
    header_path: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    observed_data_type: Mapped[str | None] = mapped_column(String(40))
    hermes_suggestion: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    resolution: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    semantic_field_code: Mapped[str | None] = mapped_column(
        ForeignKey("semantic_fields.code", ondelete="SET NULL"), index=True
    )
    semantic_field_version: Mapped[int | None] = mapped_column(Integer)
    learned_variant_keys: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SemanticIgnoreRule(Base):
    __tablename__ = "semantic_ignore_rules"
    __table_args__ = (
        UniqueConstraint("rule_key", "version", name="uq_semantic_ignore_rules_version"),
        Index("ix_semantic_ignore_rules_active", "rule_key", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    rule_key: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default=TemplateStatus.PUBLISHED)
    header_path: Mapped[list[str]] = mapped_column(JSON_DOCUMENT)
    parent_path: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    domain: Mapped[str] = mapped_column(String(80))
    record_type: Mapped[str] = mapped_column(String(120))
    observed_data_type: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(240))
    source_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="RESTRICT"), index=True
    )
    source_column_id: Mapped[str] = mapped_column(String(500))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HermesRecognitionCache(Base):
    __tablename__ = "hermes_recognition_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    hermes_version: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(200))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HermesRecognitionRecord(Base):
    __tablename__ = "hermes_recognition_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="CASCADE"),
        index=True,
    )
    cache_key: Mapped[str] = mapped_column(
        ForeignKey("hermes_recognition_cache.cache_key", ondelete="RESTRICT"),
        index=True,
    )
    call_performed: Mapped[bool] = mapped_column()
    input_field_count: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TemplateMatch(Base):
    __tablename__ = "template_matches"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    profile_contract_version: Mapped[str] = mapped_column(String(80))
    layout_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    match_type: Mapped[str] = mapped_column(String(32))
    score_basis_points: Mapped[int] = mapped_column(Integer)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_templates.id", ondelete="SET NULL"),
        index=True,
    )
    template_version: Mapped[int | None] = mapped_column(Integer)
    differences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requires_hermes: Mapped[bool] = mapped_column(default=True)
    matcher_version: Mapped[str] = mapped_column(String(80))
    total_regions: Mapped[int] = mapped_column(Integer, default=0)
    matched_regions: Mapped[int] = mapped_column(Integer, default=0)
    coverage_basis_points: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    item: Mapped[IngestionItem] = relationship(back_populates="template_match")


class RegionTemplateMatch(Base):
    __tablename__ = "region_template_matches"
    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "sheet_id",
            "region_id",
            "header_id",
            name="uq_region_template_matches_source",
        ),
        Index(
            "ix_region_template_matches_item_requires_hermes",
            "item_id",
            "requires_hermes",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="CASCADE"),
        index=True,
    )
    sheet_id: Mapped[str] = mapped_column(String(200))
    region_id: Mapped[str] = mapped_column(String(200))
    header_id: Mapped[str] = mapped_column(String(200))
    region_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    match_type: Mapped[str] = mapped_column(String(32))
    score_basis_points: Mapped[int] = mapped_column(Integer)
    template_region_component_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("template_region_components.id", ondelete="SET NULL"),
        index=True,
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_templates.id", ondelete="SET NULL"),
        index=True,
    )
    template_version: Mapped[int | None] = mapped_column(Integer)
    region_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("region_templates.id", ondelete="SET NULL"),
        index=True,
    )
    region_template_version: Mapped[int | None] = mapped_column(Integer)
    differences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requires_hermes: Mapped[bool] = mapped_column(default=True)
    matcher_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FieldMatch(Base):
    __tablename__ = "field_matches"
    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "sheet_id",
            "region_id",
            "header_id",
            "source_column_id",
            name="uq_field_matches_source",
        ),
        Index(
            "ix_field_matches_item_requires_hermes",
            "item_id",
            "requires_hermes",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="CASCADE"),
        index=True,
    )
    sheet_id: Mapped[str] = mapped_column(String(200))
    region_id: Mapped[str] = mapped_column(String(200))
    header_id: Mapped[str] = mapped_column(String(200))
    source_column_id: Mapped[str] = mapped_column(String(500))
    header_path: Mapped[list[str]] = mapped_column(JSON)
    observed_data_type: Mapped[str | None] = mapped_column(String(40))
    semantic_field_code: Mapped[str | None] = mapped_column(
        ForeignKey("semantic_fields.code", ondelete="SET NULL"),
        index=True,
    )
    semantic_field_version: Mapped[int | None] = mapped_column(Integer)
    match_type: Mapped[str] = mapped_column(String(32))
    score_basis_points: Mapped[int] = mapped_column(Integer)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    differences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requires_hermes: Mapped[bool] = mapped_column(default=True)
    matcher_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SheetCompositionMatch(Base):
    __tablename__ = "sheet_composition_matches"
    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "sheet_id",
            name="uq_sheet_composition_matches_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="CASCADE"),
        index=True,
    )
    sheet_id: Mapped[str] = mapped_column(String(200))
    sheet_composition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sheet_compositions.id", ondelete="SET NULL"),
        index=True,
    )
    sheet_composition_version: Mapped[int | None] = mapped_column(Integer)
    match_type: Mapped[str] = mapped_column(String(32))
    score_basis_points: Mapped[int] = mapped_column(Integer)
    total_slots: Mapped[int] = mapped_column(Integer)
    matched_slots: Mapped[int] = mapped_column(Integer)
    coverage_basis_points: Mapped[int] = mapped_column(Integer)
    differences: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    matcher_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkbookRouteMatch(Base):
    __tablename__ = "workbook_route_matches"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    workbook_route_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workbook_routes.id", ondelete="SET NULL"),
        index=True,
    )
    workbook_route_version: Mapped[int | None] = mapped_column(Integer)
    match_type: Mapped[str] = mapped_column(String(32))
    score_basis_points: Mapped[int] = mapped_column(Integer)
    total_slots: Mapped[int] = mapped_column(Integer)
    matched_slots: Mapped[int] = mapped_column(Integer)
    coverage_basis_points: Mapped[int] = mapped_column(Integer)
    differences: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    matcher_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApprovedImportPlan(Base):
    __tablename__ = "approved_import_plans"
    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "revision",
            name="uq_approved_import_plans_item_revision",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="CASCADE"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approved_import_plans.id", ondelete="RESTRICT"),
        index=True,
    )
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    profile_contract_version: Mapped[str] = mapped_column(String(80))
    layout_fingerprint: Mapped[str] = mapped_column(String(64))
    plan_source: Mapped[str] = mapped_column(String(32), default="template")
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("template_proposals.id", ondelete="RESTRICT"),
        index=True,
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_templates.id", ondelete="RESTRICT"),
        index=True,
    )
    template_version: Mapped[int | None] = mapped_column(Integer)
    primary_region_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("region_templates.id", ondelete="RESTRICT"),
        index=True,
    )
    primary_region_template_version: Mapped[int | None] = mapped_column(Integer)
    layout_plan: Mapped[dict[str, Any]] = mapped_column(JSON)
    field_mappings: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    approved_by: Mapped[str] = mapped_column(String(160))
    approved_by_type: Mapped[str] = mapped_column(String(32), default="user")
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    approval_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[IngestionItem] = relationship(back_populates="approved_import_plans")


class ImportExecution(Base):
    __tablename__ = "import_executions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    approved_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approved_import_plans.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="running")
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    value_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetRecord(Base):
    __tablename__ = "dataset_records"
    __table_args__ = (
        UniqueConstraint(
            "approved_plan_id",
            "sheet_id",
            "region_id",
            "source_row",
            name="uq_dataset_record_plan_source_row",
        ),
        Index("ix_dataset_records_mapping_status", "mapping_status"),
        Index("ix_dataset_records_quality_status", "quality_status"),
        Index(
            "ix_dataset_records_item_group_source",
            "item_id",
            "sheet_id",
            "region_id",
            "record_type",
            "source_row",
        ),
        Index(
            "ix_dataset_records_scope_created_page",
            "tenant_id",
            "administrative_unit_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_dataset_records_raw_data_gin",
            "raw_data",
            postgresql_using="gin",
            postgresql_ops={"raw_data": "jsonb_path_ops"},
        ),
        Index(
            "ix_dataset_records_semantic_data_gin",
            "semantic_data",
            postgresql_using="gin",
            postgresql_ops={"semantic_data": "jsonb_path_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        default=lambda: LEGACY_TENANT_ID,
        index=True,
    )
    administrative_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("administrative_units.id", ondelete="RESTRICT"),
        default=lambda: LEGACY_VILLAGE_ID,
        index=True,
    )
    ingestion_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_batches.id", ondelete="RESTRICT"),
        index=True,
    )
    approved_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approved_import_plans.id", ondelete="RESTRICT"),
        index=True,
    )
    plan_source: Mapped[str] = mapped_column(String(32), default="template")
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="RESTRICT"),
        index=True,
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_templates.id", ondelete="RESTRICT"),
        index=True,
    )
    template_version: Mapped[int | None] = mapped_column(Integer)
    region_template_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("region_templates.id", ondelete="RESTRICT"),
        index=True,
    )
    region_template_version: Mapped[int | None] = mapped_column(Integer)
    record_type: Mapped[str] = mapped_column(String(120))
    sheet_id: Mapped[str] = mapped_column(String(200))
    region_id: Mapped[str] = mapped_column(String(200))
    source_row: Mapped[int] = mapped_column(Integer)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    semantic_data: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    mapping_status: Mapped[str] = mapped_column(String(32), default="complete")
    quality_status: Mapped[str] = mapped_column(String(32), default="passed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecordIndexValue(Base):
    __tablename__ = "record_index_values"
    __table_args__ = (
        UniqueConstraint(
            "record_id",
            "semantic_field_code",
            "role",
            name="uq_record_index_value_record_field",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_records.id", ondelete="CASCADE"),
        index=True,
    )
    semantic_field_code: Mapped[str] = mapped_column(String(160), index=True)
    semantic_field_version: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(80), default="")
    data_type: Mapped[str] = mapped_column(String(40))
    text_value: Mapped[str | None] = mapped_column(Text)
    integer_value: Mapped[int | None] = mapped_column(Integer)
    decimal_value: Mapped[Any | None] = mapped_column(Numeric(38, 10))
    boolean_value: Mapped[bool | None] = mapped_column(Boolean)
    date_value: Mapped[Any | None] = mapped_column(Date)
    datetime_value: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecordValueLineage(Base):
    __tablename__ = "record_value_lineage"

    record_index_value_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("record_index_values.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    sheet_id: Mapped[str] = mapped_column(String(200))
    source_cell_id: Mapped[str] = mapped_column(String(240), index=True)
    coordinate: Mapped[str] = mapped_column(String(32))
    raw_value: Mapped[Any] = mapped_column(JSON)
    display_value: Mapped[Any] = mapped_column(JSON)
    normalizer: Mapped[str] = mapped_column(String(120))


class QuestionConversation(Base):
    __tablename__ = "question_conversations"
    __table_args__ = (
        Index(
            "ix_question_conversations_owner_updated",
            "tenant_id",
            "requested_by_user_id",
            "updated_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    scope_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("administrative_units.id", ondelete="RESTRICT"), index=True
    )
    source_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="RESTRICT"), index=True
    )
    include_descendants: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str] = mapped_column(String(240), default="新的问数")
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class IngestionItemSupersession(Base):
    """Immutable declaration that a newer source replaces an older source."""

    __tablename__ = "ingestion_item_supersessions"
    __table_args__ = (
        UniqueConstraint(
            "superseded_item_id",
            name="uq_ingestion_item_supersessions_superseded_item",
        ),
        CheckConstraint(
            "superseded_item_id <> replacement_item_id",
            name="ck_ingestion_item_supersessions_distinct_items",
        ),
        Index(
            "ix_ingestion_item_supersessions_scope_created",
            "tenant_id",
            "administrative_unit_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        index=True,
    )
    administrative_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("administrative_units.id", ondelete="RESTRICT"),
        index=True,
    )
    superseded_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="RESTRICT"),
        index=True,
    )
    replacement_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="RESTRICT"),
        index=True,
    )
    reason: Mapped[str] = mapped_column(Text, default="")
    declared_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QuestionRun(Base):
    __tablename__ = "question_runs"
    __table_args__ = (Index("ix_question_runs_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("question_conversations.id", ondelete="CASCADE"),
        index=True,
    )
    retry_of_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("question_runs.id", ondelete="SET NULL"),
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    scope_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("administrative_units.id", ondelete="RESTRICT"), index=True
    )
    source_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="RESTRICT"), index=True
    )
    include_descendants: Mapped[bool] = mapped_column(Boolean, default=False)
    question: Mapped[str] = mapped_column(Text)
    scope_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    catalog_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    validated_query_plan: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    answer: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    status: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(80))
    route: Mapped[str] = mapped_column(String(32), default="legacy")
    tool_trace: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, default=list)
    answer_text: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DOCUMENT, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QuestionFactResult(Base):
    __tablename__ = "question_fact_results"
    __table_args__ = (
        Index(
            "ix_question_fact_results_run_created",
            "question_run_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    question_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_runs.id", ondelete="CASCADE"),
        index=True,
    )
    tool_name: Mapped[str] = mapped_column(String(80))
    result_grade: Mapped[str] = mapped_column(String(32))
    contract_version: Mapped[str] = mapped_column(String(80))
    fact_set_code: Mapped[str | None] = mapped_column(String(160))
    fact_set_version: Mapped[int | None] = mapped_column(Integer)
    semantic_manifest_code: Mapped[str | None] = mapped_column(String(160))
    semantic_manifest_version: Mapped[int | None] = mapped_column(Integer)
    metric_code: Mapped[str | None] = mapped_column(String(160))
    metric_version: Mapped[int | None] = mapped_column(Integer)
    safe_query_plan: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    semantic_query_plan: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    semantic_plan_fingerprint: Mapped[str | None] = mapped_column(String(64))
    execution_fingerprint: Mapped[str] = mapped_column(String(64))
    structured_result: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    source_file_count: Mapped[int] = mapped_column(Integer, default=0)
    data_village_count: Mapped[int | None] = mapped_column(Integer)
    dataset_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    eligible_source_item_fingerprint: Mapped[str] = mapped_column(String(64))
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QueryFactSetDefinition(Base):
    __tablename__ = "query_fact_set_definitions"
    __table_args__ = (
        UniqueConstraint(
            "code",
            "version",
            name="uq_query_fact_set_definitions_code_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    aliases: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    domain: Mapped[str] = mapped_column(String(120), default="")
    record_type: Mapped[str] = mapped_column(String(120), index=True)
    record_grain: Mapped[str] = mapped_column(String(160))
    provenance_rule: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    identity_field_codes: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    dimension_field_codes: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    measure_definitions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    time_dimensions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    status_dimensions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    sensitive_field_policies: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    conflict_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    catalog_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    definition_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SemanticManifestDefinition(Base):
    __tablename__ = "semantic_manifest_definitions"
    __table_args__ = (
        UniqueConstraint(
            "code",
            "version",
            name="uq_semantic_manifest_definitions_code_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    fact_set_code: Mapped[str] = mapped_column(String(160), index=True)
    fact_set_version: Mapped[int] = mapped_column(Integer)
    root_entity: Mapped[str] = mapped_column(String(160))
    entities: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    dimensions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    measures: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    relationships: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    allowed_join_paths: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    max_join_depth: Mapped[int] = mapped_column(Integer, default=0)
    deduplication_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    default_time_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    evidence_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    catalog_fingerprint: Mapped[str] = mapped_column(String(64))
    manifest_fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MetricDefinition(Base):
    __tablename__ = "metric_definitions"
    __table_args__ = (
        UniqueConstraint(
            "code",
            "version",
            name="uq_metric_definitions_code_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="published", index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    fact_set_code: Mapped[str | None] = mapped_column(String(160), index=True)
    fact_set_version: Mapped[int | None] = mapped_column(Integer)
    semantic_manifest_code: Mapped[str | None] = mapped_column(String(160))
    semantic_manifest_version: Mapped[int | None] = mapped_column(Integer)
    record_type: Mapped[str | None] = mapped_column(String(120))
    record_grain: Mapped[str | None] = mapped_column(String(160))
    semantic_field_code: Mapped[str] = mapped_column(String(160), index=True)
    semantic_field_version: Mapped[int] = mapped_column(Integer)
    aggregation: Mapped[str] = mapped_column(String(32))
    additivity: Mapped[str] = mapped_column(String(32), default="additive")
    unit: Mapped[str | None] = mapped_column(String(80))
    allowed_filter_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_group_fields: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    forbidden_aggregation_dimensions: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    identity_field_codes: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    deduplication_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    status_filters: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT,
        default=list,
    )
    time_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    null_policy: Mapped[str] = mapped_column(String(32), default="exclude")
    conflict_policy: Mapped[str] = mapped_column(String(32), default="reject")
    evidence_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        default=dict,
    )
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class QualityIssue(Base):
    __tablename__ = "quality_issues"
    __table_args__ = (Index("ix_quality_issues_item_code", "item_id", "code"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_items.id", ondelete="CASCADE"),
        index=True,
    )
    approved_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approved_import_plans.id", ondelete="CASCADE"),
        index=True,
    )
    code: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMConfiguration(Base):
    __tablename__ = "llm_configurations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="default")
    provider: Mapped[str] = mapped_column(String(80))
    preset_id: Mapped[str] = mapped_column(String(80), default="custom_openai")
    api_mode: Mapped[str] = mapped_column(String(32), default="openai_chat")
    model: Mapped[str] = mapped_column(String(200))
    fast_model: Mapped[str] = mapped_column(String(200))
    reasoning_model: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(500))
    thinking_protocol: Mapped[str] = mapped_column(String(32), default="none")
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class LLMProviderCredential(Base):
    __tablename__ = "llm_provider_credentials"

    preset_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(80))
    api_mode: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(500))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
