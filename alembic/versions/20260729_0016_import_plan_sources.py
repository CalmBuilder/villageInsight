"""Track template and user-confirmed Hermes import plan sources.

Revision ID: 20260729_0016
Revises: 20260729_0015
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0016"
down_revision: str | None = "20260729_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approved_import_plans",
        sa.Column(
            "plan_source",
            sa.String(length=32),
            nullable=False,
            server_default="template",
        ),
    )
    op.add_column(
        "approved_import_plans",
        sa.Column("proposal_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "approved_import_plans_proposal_id_fkey",
        "approved_import_plans",
        "template_proposals",
        ["proposal_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_approved_import_plans_proposal_id",
        "approved_import_plans",
        ["proposal_id"],
    )
    op.alter_column(
        "approved_import_plans",
        "plan_source",
        server_default=None,
    )

    op.add_column(
        "dataset_records",
        sa.Column(
            "plan_source",
            sa.String(length=32),
            nullable=False,
            server_default="template",
        ),
    )
    op.alter_column("dataset_records", "plan_source", server_default=None)


def downgrade() -> None:
    op.drop_column("dataset_records", "plan_source")
    op.drop_index(
        "ix_approved_import_plans_proposal_id",
        table_name="approved_import_plans",
    )
    op.drop_constraint(
        "approved_import_plans_proposal_id_fkey",
        "approved_import_plans",
        type_="foreignkey",
    )
    op.drop_column("approved_import_plans", "proposal_id")
    op.drop_column("approved_import_plans", "plan_source")
