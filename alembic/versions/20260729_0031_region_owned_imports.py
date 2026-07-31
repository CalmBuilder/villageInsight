"""Allow independent Region templates to own plans and records.

Revision ID: 20260729_0031
Revises: 20260729_0030
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0031"
down_revision: str | None = "20260729_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approved_import_plans",
        sa.Column("primary_region_template_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "approved_import_plans",
        sa.Column("primary_region_template_version", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_approved_import_plans_primary_region_template_id",
        "approved_import_plans",
        "region_templates",
        ["primary_region_template_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_approved_import_plans_primary_region_template_id",
        "approved_import_plans",
        ["primary_region_template_id"],
    )
    op.alter_column(
        "approved_import_plans",
        "template_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.alter_column(
        "approved_import_plans",
        "template_version",
        existing_type=sa.Integer(),
        nullable=True,
    )

    op.add_column(
        "dataset_records",
        sa.Column("region_template_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "dataset_records",
        sa.Column("region_template_version", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_dataset_records_region_template_id",
        "dataset_records",
        "region_templates",
        ["region_template_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_dataset_records_region_template_id",
        "dataset_records",
        ["region_template_id"],
    )
    op.alter_column(
        "dataset_records",
        "template_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.alter_column(
        "dataset_records",
        "template_version",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE approved_import_plans
        SET template_id = (
            SELECT id FROM document_templates ORDER BY created_at LIMIT 1
        ),
        template_version = COALESCE(template_version, 1)
        WHERE template_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE dataset_records
        SET template_id = (
            SELECT id FROM document_templates ORDER BY created_at LIMIT 1
        ),
        template_version = COALESCE(template_version, 1)
        WHERE template_id IS NULL
        """
    )
    op.alter_column(
        "dataset_records",
        "template_version",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "dataset_records",
        "template_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_index(
        "ix_dataset_records_region_template_id",
        table_name="dataset_records",
    )
    op.drop_constraint(
        "fk_dataset_records_region_template_id",
        "dataset_records",
        type_="foreignkey",
    )
    op.drop_column("dataset_records", "region_template_version")
    op.drop_column("dataset_records", "region_template_id")

    op.alter_column(
        "approved_import_plans",
        "template_version",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "approved_import_plans",
        "template_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_index(
        "ix_approved_import_plans_primary_region_template_id",
        table_name="approved_import_plans",
    )
    op.drop_constraint(
        "fk_approved_import_plans_primary_region_template_id",
        "approved_import_plans",
        type_="foreignkey",
    )
    op.drop_column(
        "approved_import_plans",
        "primary_region_template_version",
    )
    op.drop_column("approved_import_plans", "primary_region_template_id")
