"""Add file-level template matching and immutable approved import plans.

Revision ID: 20260728_0006
Revises: 20260728_0005
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0006"
down_revision: str | None = "20260728_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_items",
        sa.Column("relative_path", sa.String(length=1024), nullable=True),
    )
    op.create_table(
        "template_matches",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("profile_contract_version", sa.String(length=80), nullable=False),
        sa.Column("layout_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("match_type", sa.String(length=32), nullable=False),
        sa.Column("score_basis_points", sa.Integer(), nullable=False),
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
            ["template_id"],
            ["document_templates.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index(
        "ix_template_matches_source_sha256",
        "template_matches",
        ["source_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_template_matches_layout_fingerprint",
        "template_matches",
        ["layout_fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_template_matches_template_id",
        "template_matches",
        ["template_id"],
        unique=False,
    )
    op.create_table(
        "approved_import_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("profile_contract_version", sa.String(length=80), nullable=False),
        sa.Column("layout_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("layout_plan", sa.JSON(), nullable=False),
        sa.Column("field_mappings", sa.JSON(), nullable=False),
        sa.Column("approved_by", sa.String(length=160), nullable=False),
        sa.Column("approval_comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["ingestion_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["document_templates.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_approved_import_plans_item_id",
        "approved_import_plans",
        ["item_id"],
        unique=True,
    )
    op.create_index(
        "ix_approved_import_plans_source_sha256",
        "approved_import_plans",
        ["source_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_approved_import_plans_template_id",
        "approved_import_plans",
        ["template_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_approved_import_plans_template_id",
        table_name="approved_import_plans",
    )
    op.drop_index(
        "ix_approved_import_plans_source_sha256",
        table_name="approved_import_plans",
    )
    op.drop_index(
        "ix_approved_import_plans_item_id",
        table_name="approved_import_plans",
    )
    op.drop_table("approved_import_plans")
    op.drop_index("ix_template_matches_template_id", table_name="template_matches")
    op.drop_index(
        "ix_template_matches_layout_fingerprint",
        table_name="template_matches",
    )
    op.drop_index(
        "ix_template_matches_source_sha256",
        table_name="template_matches",
    )
    op.drop_table("template_matches")
    op.drop_column("ingestion_items", "relative_path")
