"""Add auditable retry relation to question runs.

Revision ID: 20260729_0036
Revises: 20260729_0035
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0036"
down_revision: str | None = "20260729_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "question_runs",
        sa.Column("retry_of_run_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_question_runs_retry_of_run_id",
        "question_runs",
        "question_runs",
        ["retry_of_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_question_runs_retry_of_run_id",
        "question_runs",
        ["retry_of_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_question_runs_retry_of_run_id",
        table_name="question_runs",
    )
    op.drop_constraint(
        "fk_question_runs_retry_of_run_id",
        "question_runs",
        type_="foreignkey",
    )
    op.drop_column("question_runs", "retry_of_run_id")
