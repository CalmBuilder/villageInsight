"""Add auditable materialization quality issues.

Revision ID: 20260728_0010
Revises: 20260728_0009
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0010"
down_revision: str | None = "20260728_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("approved_plan_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approved_plan_id"],
            ["approved_import_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["ingestion_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quality_issues_item_id", "quality_issues", ["item_id"])
    op.create_index(
        "ix_quality_issues_approved_plan_id",
        "quality_issues",
        ["approved_plan_id"],
    )
    op.create_index("ix_quality_issues_code", "quality_issues", ["code"])


def downgrade() -> None:
    op.drop_table("quality_issues")
