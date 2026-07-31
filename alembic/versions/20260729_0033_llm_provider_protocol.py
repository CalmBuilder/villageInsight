"""Add provider preset and wire protocol to LLM configuration.

Revision ID: 20260729_0033
Revises: 20260729_0032
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0033"
down_revision: str | None = "20260729_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_configurations",
        sa.Column(
            "preset_id",
            sa.String(length=80),
            nullable=False,
            server_default="custom_openai",
        ),
    )
    op.add_column(
        "llm_configurations",
        sa.Column(
            "api_mode",
            sa.String(length=32),
            nullable=False,
            server_default="openai_chat",
        ),
    )
    op.execute(
        """
        UPDATE llm_configurations
        SET preset_id = CASE
            WHEN provider = 'deepseek' THEN 'deepseek'
            WHEN provider = 'siliconflow' THEN 'siliconflow'
            ELSE 'custom_openai'
        END
        """
    )
    op.alter_column("llm_configurations", "preset_id", server_default=None)
    op.alter_column("llm_configurations", "api_mode", server_default=None)


def downgrade() -> None:
    op.drop_column("llm_configurations", "api_mode")
    op.drop_column("llm_configurations", "preset_id")
