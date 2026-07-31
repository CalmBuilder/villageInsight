"""Add auditable field-level template matches.

Revision ID: 20260729_0026
Revises: 20260729_0025
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0026"
down_revision: str | None = "20260729_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.String(length=200), nullable=False),
        sa.Column("region_id", sa.String(length=200), nullable=False),
        sa.Column("header_id", sa.String(length=200), nullable=False),
        sa.Column("source_column_id", sa.String(length=500), nullable=False),
        sa.Column("header_path", sa.JSON(), nullable=False),
        sa.Column("observed_data_type", sa.String(length=40), nullable=True),
        sa.Column("semantic_field_code", sa.String(length=160), nullable=True),
        sa.Column("semantic_field_version", sa.Integer(), nullable=True),
        sa.Column("match_type", sa.String(length=32), nullable=False),
        sa.Column("score_basis_points", sa.Integer(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
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
            ["semantic_field_code"],
            ["semantic_fields.code"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "sheet_id",
            "region_id",
            "header_id",
            "source_column_id",
            name="uq_field_matches_source",
        ),
    )
    op.create_index("ix_field_matches_item_id", "field_matches", ["item_id"])
    op.create_index(
        "ix_field_matches_semantic_field_code",
        "field_matches",
        ["semantic_field_code"],
    )
    op.create_index(
        "ix_field_matches_item_requires_hermes",
        "field_matches",
        ["item_id", "requires_hermes"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_field_matches_item_requires_hermes",
        table_name="field_matches",
    )
    op.drop_index(
        "ix_field_matches_semantic_field_code",
        table_name="field_matches",
    )
    op.drop_index("ix_field_matches_item_id", table_name="field_matches")
    op.drop_table("field_matches")
