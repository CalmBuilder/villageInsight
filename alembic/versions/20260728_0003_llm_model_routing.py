"""Add fast and reasoning model routes.

Revision ID: 20260728_0003
Revises: 20260728_0002
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0003"
down_revision: str | None = "20260728_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_configurations",
        sa.Column("fast_model", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "llm_configurations",
        sa.Column("reasoning_model", sa.String(length=200), nullable=True),
    )
    op.execute(
        """
        UPDATE llm_configurations
        SET fast_model = model, reasoning_model = model
        """
    )
    op.alter_column("llm_configurations", "fast_model", nullable=False)
    op.alter_column("llm_configurations", "reasoning_model", nullable=False)


def downgrade() -> None:
    op.drop_column("llm_configurations", "reasoning_model")
    op.drop_column("llm_configurations", "fast_model")
