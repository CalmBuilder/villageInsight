"""Add durable question conversations and Hermes tool-run audit.

Revision ID: 20260729_0024
Revises: 20260729_0023
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0024"
down_revision: str | None = "20260729_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "question_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("scope_unit_id", sa.Uuid(), nullable=False),
        sa.Column("include_descendants", sa.Boolean(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_unit_id"],
            ["administrative_units.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_question_conversations_tenant_id",
        "question_conversations",
        ["tenant_id"],
    )
    op.create_index(
        "ix_question_conversations_requested_by_user_id",
        "question_conversations",
        ["requested_by_user_id"],
    )
    op.create_index(
        "ix_question_conversations_scope_unit_id",
        "question_conversations",
        ["scope_unit_id"],
    )
    op.create_index(
        "ix_question_conversations_owner_updated",
        "question_conversations",
        ["tenant_id", "requested_by_user_id", "updated_at"],
    )

    op.add_column(
        "question_runs",
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "question_runs",
        sa.Column("route", sa.String(length=32), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "question_runs",
        sa.Column("tool_trace", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "question_runs",
        sa.Column("answer_text", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "question_runs",
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "question_runs",
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "question_runs",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_question_runs_conversation_id",
        "question_runs",
        "question_conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_question_runs_conversation_id",
        "question_runs",
        ["conversation_id"],
    )
    for name in ("route", "tool_trace", "answer_text", "evidence", "started_at"):
        op.alter_column("question_runs", name, server_default=None)


def downgrade() -> None:
    op.drop_index("ix_question_runs_conversation_id", table_name="question_runs")
    op.drop_constraint(
        "fk_question_runs_conversation_id",
        "question_runs",
        type_="foreignkey",
    )
    for name in (
        "completed_at",
        "started_at",
        "evidence",
        "answer_text",
        "tool_trace",
        "route",
        "conversation_id",
    ):
        op.drop_column("question_runs", name)

    op.drop_index(
        "ix_question_conversations_owner_updated",
        table_name="question_conversations",
    )
    op.drop_index(
        "ix_question_conversations_scope_unit_id",
        table_name="question_conversations",
    )
    op.drop_index(
        "ix_question_conversations_requested_by_user_id",
        table_name="question_conversations",
    )
    op.drop_index(
        "ix_question_conversations_tenant_id",
        table_name="question_conversations",
    )
    op.drop_table("question_conversations")
