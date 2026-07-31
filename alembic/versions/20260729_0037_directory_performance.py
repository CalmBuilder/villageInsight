"""Add directory projections and list-query indexes.

Revision ID: 20260729_0037
Revises: 20260729_0036
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0037"
down_revision: str | None = "20260729_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_sheet_catalog",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.String(length=200), nullable=False),
        sa.Column("sheet_name", sa.String(length=512), nullable=False),
        sa.Column("sheet_order", sa.Integer(), nullable=False),
        sa.Column("region_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["ingestion_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "sheet_id",
            name="uq_document_sheet_catalog_item_sheet",
        ),
    )
    op.create_index(
        "ix_document_sheet_catalog_item_id",
        "document_sheet_catalog",
        ["item_id"],
    )
    op.create_index(
        "ix_document_sheet_catalog_item_order",
        "document_sheet_catalog",
        ["item_id", "sheet_order"],
    )
    op.execute(
        """
        INSERT INTO document_sheet_catalog
            (id, item_id, sheet_id, sheet_name, sheet_order, region_count)
        SELECT
            gen_random_uuid(),
            profiles.item_id,
            sheet.value->>'id',
            COALESCE(NULLIF(sheet.value->>'name', ''), sheet.value->>'id'),
            (sheet.ordinality - 1)::integer,
            jsonb_array_length(
                COALESCE(sheet.value->'region_candidates', '[]'::jsonb)
            )
        FROM document_profiles AS profiles
        CROSS JOIN LATERAL jsonb_array_elements(
            COALESCE(profiles.profile->'sheets', '[]'::jsonb)
        ) WITH ORDINALITY AS sheet(value, ordinality)
        WHERE COALESCE(sheet.value->>'id', '') <> ''
        """
    )

    op.create_index(
        "ix_ingestion_items_created_page",
        "ingestion_items",
        ["created_at", "id"],
    )
    op.create_index(
        "ix_ingestion_items_scope_created_page",
        "ingestion_items",
        ["tenant_id", "administrative_unit_id", "created_at", "id"],
    )
    op.create_index(
        "ix_template_proposals_pending_created",
        "template_proposals",
        ["created_at", "id"],
        postgresql_where=sa.text("status = 'pending' AND source_item_id IS NOT NULL"),
    )
    op.create_index(
        "ix_template_proposals_pending_scope_created",
        "template_proposals",
        ["tenant_id", "administrative_unit_id", "created_at", "id"],
        postgresql_where=sa.text("status = 'pending' AND source_item_id IS NOT NULL"),
    )
    op.create_index(
        "ix_quality_issues_item_code",
        "quality_issues",
        ["item_id", "code"],
    )
    op.create_index(
        "ix_dataset_records_item_group_source",
        "dataset_records",
        ["item_id", "sheet_id", "region_id", "record_type", "source_row"],
    )
    op.create_index(
        "ix_dataset_records_scope_created_page",
        "dataset_records",
        ["tenant_id", "administrative_unit_id", "created_at", "id"],
    )
    op.create_index(
        "ix_semantic_field_versions_directory",
        "semantic_field_versions",
        ["status", "layer", "data_type", "field_id", "version"],
    )
    op.create_index(
        "ix_region_template_versions_directory",
        "region_template_versions",
        ["status", "domain", "record_type", "region_template_id", "version"],
    )
    op.create_index(
        "ix_template_versions_directory",
        "template_versions",
        ["status", "template_id", "version"],
    )
    op.create_index(
        "ix_sheet_composition_versions_directory",
        "sheet_composition_versions",
        ["status", "sheet_composition_id", "version"],
    )
    op.create_index(
        "ix_workbook_route_versions_directory",
        "workbook_route_versions",
        ["status", "workbook_route_id", "version"],
    )

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_tenants_name_trgm ON tenants "
        "USING gin (lower(name) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_users_display_name_trgm ON users "
        "USING gin (lower(display_name) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_administrative_units_name_trgm ON administrative_units "
        "USING gin (lower(name) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_semantic_field_versions_name_trgm ON semantic_field_versions "
        "USING gin (lower(name) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_region_template_versions_name_trgm ON region_template_versions "
        "USING gin (lower(name) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_template_versions_name_trgm ON template_versions "
        "USING gin (lower(name) gin_trgm_ops)"
    )


def downgrade() -> None:
    for index_name, table_name in (
        ("ix_template_versions_name_trgm", "template_versions"),
        ("ix_region_template_versions_name_trgm", "region_template_versions"),
        ("ix_semantic_field_versions_name_trgm", "semantic_field_versions"),
        ("ix_administrative_units_name_trgm", "administrative_units"),
        ("ix_users_display_name_trgm", "users"),
        ("ix_tenants_name_trgm", "tenants"),
        ("ix_workbook_route_versions_directory", "workbook_route_versions"),
        ("ix_sheet_composition_versions_directory", "sheet_composition_versions"),
        ("ix_template_versions_directory", "template_versions"),
        ("ix_region_template_versions_directory", "region_template_versions"),
        ("ix_semantic_field_versions_directory", "semantic_field_versions"),
        ("ix_dataset_records_scope_created_page", "dataset_records"),
        ("ix_dataset_records_item_group_source", "dataset_records"),
        ("ix_quality_issues_item_code", "quality_issues"),
        ("ix_template_proposals_pending_scope_created", "template_proposals"),
        ("ix_template_proposals_pending_created", "template_proposals"),
        ("ix_ingestion_items_scope_created_page", "ingestion_items"),
        ("ix_ingestion_items_created_page", "ingestion_items"),
    ):
        op.drop_index(index_name, table_name=table_name)
    op.drop_index(
        "ix_document_sheet_catalog_item_order",
        table_name="document_sheet_catalog",
    )
    op.drop_index(
        "ix_document_sheet_catalog_item_id",
        table_name="document_sheet_catalog",
    )
    op.drop_table("document_sheet_catalog")
