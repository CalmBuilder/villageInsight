"""Persist immutable scope and catalog snapshots for question runs.

Revision ID: 20260730_0039
Revises: 20260729_0038
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260730_0039"
down_revision: str | None = "20260729_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "question_runs",
        sa.Column(
            "scope_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "question_runs",
        sa.Column(
            "catalog_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("question_runs", "scope_snapshot", server_default=None)
    op.alter_column("question_runs", "catalog_snapshot", server_default=None)


def downgrade() -> None:
    op.drop_column("question_runs", "catalog_snapshot")
    op.drop_column("question_runs", "scope_snapshot")
