"""Store provider credentials independently from the active model route.

Revision ID: 20260729_0034
Revises: 20260729_0033
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0034"
down_revision: str | None = "20260729_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_provider_credentials",
        sa.Column("preset_id", sa.String(length=80), primary_key=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("api_mode", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        """
        INSERT INTO llm_provider_credentials (
            preset_id,
            provider,
            api_mode,
            base_url,
            encrypted_api_key,
            updated_at
        )
        SELECT
            preset_id,
            provider,
            api_mode,
            base_url,
            encrypted_api_key,
            updated_at
        FROM llm_configurations
        """
    )


def downgrade() -> None:
    op.drop_table("llm_provider_credentials")
