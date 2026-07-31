"""Normalize a legacy semantic field variant source.

Revision ID: 20260730_0045
Revises: 20260730_0044
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_0045"
down_revision: str | None = "20260730_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE semantic_field_variants
            SET source = 'codex'
            WHERE source = 'codex_real_regression'
            """
        )
    )


def downgrade() -> None:
    # This is an irreversible data repair. Restoring the invalid legacy value
    # would make GET /api/fields fail response validation again.
    pass
