"""Add Region-level template components and auditable matches.

Revision ID: 20260729_0023
Revises: 20260729_0022
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0023"
down_revision: str | None = "20260729_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "template_region_components",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("component_key", sa.String(length=120), nullable=False),
        sa.Column("region_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.JSON(), nullable=False),
        sa.Column("source_decision_index", sa.Integer(), nullable=False),
        sa.Column("field_binding_indexes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["template_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "template_version_id",
            "component_key",
            name="uq_template_region_component_key",
        ),
    )
    op.create_index(
        "ix_template_region_components_template_version_id",
        "template_region_components",
        ["template_version_id"],
    )
    op.create_index(
        "ix_template_region_components_fingerprint",
        "template_region_components",
        ["region_fingerprint"],
    )

    op.create_table(
        "region_template_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.String(length=200), nullable=False),
        sa.Column("region_id", sa.String(length=200), nullable=False),
        sa.Column("header_id", sa.String(length=200), nullable=False),
        sa.Column("region_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("match_type", sa.String(length=32), nullable=False),
        sa.Column("score_basis_points", sa.Integer(), nullable=False),
        sa.Column("template_region_component_id", sa.Uuid(), nullable=True),
        sa.Column("template_id", sa.Uuid(), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("differences", sa.JSON(), nullable=False),
        sa.Column("requires_hermes", sa.Boolean(), nullable=False),
        sa.Column("matcher_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["ingestion_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["template_region_component_id"],
            ["template_region_components.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["document_templates.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "sheet_id",
            "region_id",
            "header_id",
            name="uq_region_template_matches_source",
        ),
    )
    op.create_index(
        "ix_region_template_matches_item_id",
        "region_template_matches",
        ["item_id"],
    )
    op.create_index(
        "ix_region_template_matches_region_fingerprint",
        "region_template_matches",
        ["region_fingerprint"],
    )
    op.create_index(
        "ix_region_template_matches_template_region_component_id",
        "region_template_matches",
        ["template_region_component_id"],
    )
    op.create_index(
        "ix_region_template_matches_template_id",
        "region_template_matches",
        ["template_id"],
    )
    op.create_index(
        "ix_region_template_matches_item_requires_hermes",
        "region_template_matches",
        ["item_id", "requires_hermes"],
    )

    for name in ("total_regions", "matched_regions", "coverage_basis_points"):
        op.add_column(
            "template_matches",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )
        op.alter_column("template_matches", name, server_default=None)


def downgrade() -> None:
    for name in ("coverage_basis_points", "matched_regions", "total_regions"):
        op.drop_column("template_matches", name)
    op.drop_index(
        "ix_region_template_matches_item_requires_hermes",
        table_name="region_template_matches",
    )
    op.drop_index(
        "ix_region_template_matches_template_id",
        table_name="region_template_matches",
    )
    op.drop_index(
        "ix_region_template_matches_template_region_component_id",
        table_name="region_template_matches",
    )
    op.drop_index(
        "ix_region_template_matches_region_fingerprint",
        table_name="region_template_matches",
    )
    op.drop_index(
        "ix_region_template_matches_item_id",
        table_name="region_template_matches",
    )
    op.drop_table("region_template_matches")
    op.drop_index(
        "ix_template_region_components_fingerprint",
        table_name="template_region_components",
    )
    op.drop_index(
        "ix_template_region_components_template_version_id",
        table_name="template_region_components",
    )
    op.drop_table("template_region_components")
