"""Configure the provider thinking parameter protocol.

Revision ID: 20260729_0028
Revises: 20260729_0027
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0028"
down_revision: str | None = "20260729_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_configurations",
        sa.Column(
            "thinking_protocol",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
    )
    op.execute(
        """
        UPDATE llm_configurations
        SET thinking_protocol = 'deepseek'
        WHERE provider = 'deepseek'
           OR lower(model) LIKE '%deepseek%'
           OR lower(fast_model) LIKE '%deepseek%'
           OR lower(reasoning_model) LIKE '%deepseek%'
        """
    )
    op.alter_column(
        "llm_configurations",
        "thinking_protocol",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("llm_configurations", "thinking_protocol")
