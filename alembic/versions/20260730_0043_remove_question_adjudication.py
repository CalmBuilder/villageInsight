"""Remove the single-result question adjudication state.

Revision ID: 20260730_0043
Revises: 20260730_0042
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_0043"
down_revision: str | None = "20260730_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_question_runs_accepted_fact_result_id",
        table_name="question_runs",
    )
    op.drop_constraint(
        "fk_question_runs_accepted_fact_result_id",
        "question_runs",
        type_="foreignkey",
    )
    op.drop_column("question_runs", "accepted_fact_result_id")
    op.drop_column("question_runs", "answer_validation_status")
    op.drop_column("question_runs", "result_grade")


def downgrade() -> None:
    op.add_column(
        "question_runs",
        sa.Column("result_grade", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "question_runs",
        sa.Column(
            "answer_validation_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "question_runs",
        sa.Column("accepted_fact_result_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_question_runs_accepted_fact_result_id",
        "question_runs",
        "question_fact_results",
        ["accepted_fact_result_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_question_runs_accepted_fact_result_id",
        "question_runs",
        ["accepted_fact_result_id"],
    )
