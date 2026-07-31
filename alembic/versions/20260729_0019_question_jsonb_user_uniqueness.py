"""Use JSONB for question audits and remove the redundant username index.

Revision ID: 20260729_0019
Revises: 20260729_0018
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260729_0019"
down_revision: str | None = "20260729_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "question_runs",
        "validated_query_plan",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="validated_query_plan::jsonb",
    )
    op.alter_column(
        "question_runs",
        "answer",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="answer::jsonb",
    )
    op.drop_index("ix_users_username", table_name="users")


def downgrade() -> None:
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.alter_column(
        "question_runs",
        "answer",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.JSON(),
        postgresql_using="answer::json",
    )
    op.alter_column(
        "question_runs",
        "validated_query_plan",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.JSON(),
        postgresql_using="validated_query_plan::json",
    )
