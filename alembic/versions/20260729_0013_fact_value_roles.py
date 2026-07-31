"""Preserve semantic field roles in fact values.

Revision ID: 20260729_0013
Revises: 20260729_0012
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0013"
down_revision: str | None = "20260729_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_fact_value_record_field",
        "fact_values",
        type_="unique",
    )
    op.add_column(
        "fact_values",
        sa.Column("role", sa.String(length=80), nullable=False, server_default=""),
    )
    op.create_unique_constraint(
        "uq_fact_value_record_field",
        "fact_values",
        ["record_id", "semantic_field_code", "role"],
    )
    op.alter_column("fact_values", "role", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "uq_fact_value_record_field",
        "fact_values",
        type_="unique",
    )
    op.drop_column("fact_values", "role")
    op.create_unique_constraint(
        "uq_fact_value_record_field",
        "fact_values",
        ["record_id", "semantic_field_code"],
    )
