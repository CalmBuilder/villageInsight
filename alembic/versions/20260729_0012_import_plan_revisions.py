"""Allow immutable import plan revisions.

Revision ID: 20260729_0012
Revises: 20260729_0011
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0012"
down_revision: str | None = "20260729_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_approved_import_plans_item_id",
        table_name="approved_import_plans",
    )
    op.add_column(
        "approved_import_plans",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "approved_import_plans",
        sa.Column("supersedes_plan_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_approved_import_plans_supersedes",
        "approved_import_plans",
        "approved_import_plans",
        ["supersedes_plan_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_approved_import_plans_item_id",
        "approved_import_plans",
        ["item_id"],
        unique=False,
    )
    op.create_index(
        "ix_approved_import_plans_supersedes_plan_id",
        "approved_import_plans",
        ["supersedes_plan_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_approved_import_plans_item_revision",
        "approved_import_plans",
        ["item_id", "revision"],
    )
    op.alter_column(
        "approved_import_plans",
        "revision",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_approved_import_plans_item_revision",
        "approved_import_plans",
        type_="unique",
    )
    op.drop_index(
        "ix_approved_import_plans_supersedes_plan_id",
        table_name="approved_import_plans",
    )
    op.drop_index(
        "ix_approved_import_plans_item_id",
        table_name="approved_import_plans",
    )
    op.drop_constraint(
        "fk_approved_import_plans_supersedes",
        "approved_import_plans",
        type_="foreignkey",
    )
    op.drop_column("approved_import_plans", "supersedes_plan_id")
    op.drop_column("approved_import_plans", "revision")
    op.create_index(
        "ix_approved_import_plans_item_id",
        "approved_import_plans",
        ["item_id"],
        unique=True,
    )
