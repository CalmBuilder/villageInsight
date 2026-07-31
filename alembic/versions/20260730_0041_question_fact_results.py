"""Separate deterministic fact results from model-written answers.

Revision ID: 20260730_0041
Revises: 20260730_0040
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260730_0041"
down_revision: str | None = "20260730_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "question_fact_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question_run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=80), nullable=False),
        sa.Column("result_grade", sa.String(length=32), nullable=False),
        sa.Column("contract_version", sa.String(length=80), nullable=False),
        sa.Column("fact_set_code", sa.String(length=160), nullable=True),
        sa.Column("fact_set_version", sa.Integer(), nullable=True),
        sa.Column("semantic_manifest_code", sa.String(length=160), nullable=True),
        sa.Column("semantic_manifest_version", sa.Integer(), nullable=True),
        sa.Column("metric_code", sa.String(length=160), nullable=True),
        sa.Column("metric_version", sa.Integer(), nullable=True),
        sa.Column("safe_query_plan", JSONB, nullable=False),
        sa.Column("semantic_query_plan", JSONB, nullable=False),
        sa.Column("semantic_plan_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("execution_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("structured_result", JSONB, nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("source_file_count", sa.Integer(), nullable=False),
        sa.Column("data_village_count", sa.Integer(), nullable=True),
        sa.Column("dataset_snapshot", JSONB, nullable=False),
        sa.Column(
            "eligible_source_item_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["question_run_id"],
            ["question_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_question_fact_results_question_run_id",
        "question_fact_results",
        ["question_run_id"],
    )
    op.create_index(
        "ix_question_fact_results_run_created",
        "question_fact_results",
        ["question_run_id", "created_at"],
    )
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


def downgrade() -> None:
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
    op.drop_table("question_fact_results")
