"""Add governed metric catalog.

Revision ID: 20260728_0009
Revises: 20260728_0008
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0009"
down_revision: str | None = "20260728_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metric_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("semantic_field_code", sa.String(length=160), nullable=False),
        sa.Column("semantic_field_version", sa.Integer(), nullable=False),
        sa.Column("aggregation", sa.String(length=32), nullable=False),
        sa.Column("unit", sa.String(length=80), nullable=True),
        sa.Column("allowed_filter_fields", sa.JSON(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_metric_definitions_code"),
    )
    op.create_index(
        "ix_metric_definitions_semantic_field_code",
        "metric_definitions",
        ["semantic_field_code"],
    )


def downgrade() -> None:
    op.drop_table("metric_definitions")
