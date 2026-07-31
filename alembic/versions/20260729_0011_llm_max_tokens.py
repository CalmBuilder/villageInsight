"""Add optional LLM output token cap.

Revision ID: 20260729_0011
Revises: 20260728_0010
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0011"
down_revision: str | None = "20260728_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_configurations",
        sa.Column("max_tokens", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_configurations", "max_tokens")
