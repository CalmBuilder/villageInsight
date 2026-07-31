"""Add immutable file scope to question conversations and runs.

Revision ID: 20260729_0027
Revises: 20260729_0026
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0027"
down_revision: str | None = "20260729_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in ("question_conversations", "question_runs"):
        op.add_column(
            table_name,
            sa.Column("source_item_id", sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table_name}_source_item_id",
            table_name,
            "ingestion_items",
            ["source_item_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(
            f"ix_{table_name}_source_item_id",
            table_name,
            ["source_item_id"],
        )


def downgrade() -> None:
    for table_name in ("question_runs", "question_conversations"):
        op.drop_index(
            f"ix_{table_name}_source_item_id",
            table_name=table_name,
        )
        op.drop_constraint(
            f"fk_{table_name}_source_item_id",
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "source_item_id")
