"""Add typed fact materialization and cell lineage.

Revision ID: 20260728_0008
Revises: 20260728_0007
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0008"
down_revision: str | None = "20260728_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("approved_plan_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("value_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["approved_plan_id"],
            ["approved_import_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_import_executions_approved_plan_id",
        "import_executions",
        ["approved_plan_id"],
        unique=True,
    )
    op.create_table(
        "fact_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("approved_plan_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("record_type", sa.String(length=120), nullable=False),
        sa.Column("sheet_id", sa.String(length=200), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approved_plan_id"],
            ["approved_import_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["ingestion_items.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["document_templates.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "approved_plan_id",
            "sheet_id",
            "source_row",
            name="uq_fact_record_plan_source_row",
        ),
    )
    op.create_index("ix_fact_records_approved_plan_id", "fact_records", ["approved_plan_id"])
    op.create_index("ix_fact_records_item_id", "fact_records", ["item_id"])
    op.create_index("ix_fact_records_template_id", "fact_records", ["template_id"])
    op.create_table(
        "fact_values",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("semantic_field_code", sa.String(length=160), nullable=False),
        sa.Column("semantic_field_version", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.String(length=40), nullable=False),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("integer_value", sa.Integer(), nullable=True),
        sa.Column("decimal_value", sa.Numeric(precision=38, scale=10), nullable=True),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        sa.Column("date_value", sa.Date(), nullable=True),
        sa.Column("datetime_value", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["fact_records.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "record_id",
            "semantic_field_code",
            name="uq_fact_value_record_field",
        ),
    )
    op.create_index("ix_fact_values_record_id", "fact_values", ["record_id"])
    op.create_index(
        "ix_fact_values_semantic_field_code",
        "fact_values",
        ["semantic_field_code"],
    )
    op.create_table(
        "fact_value_lineage",
        sa.Column("fact_value_id", sa.Uuid(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("sheet_id", sa.String(length=200), nullable=False),
        sa.Column("source_cell_id", sa.String(length=240), nullable=False),
        sa.Column("coordinate", sa.String(length=32), nullable=False),
        sa.Column("raw_value", sa.JSON(), nullable=False),
        sa.Column("display_value", sa.JSON(), nullable=False),
        sa.Column("normalizer", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(
            ["fact_value_id"],
            ["fact_values.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("fact_value_id"),
    )
    op.create_index(
        "ix_fact_value_lineage_source_sha256",
        "fact_value_lineage",
        ["source_sha256"],
    )
    op.create_index(
        "ix_fact_value_lineage_source_cell_id",
        "fact_value_lineage",
        ["source_cell_id"],
    )


def downgrade() -> None:
    op.drop_table("fact_value_lineage")
    op.drop_table("fact_values")
    op.drop_table("fact_records")
    op.drop_table("import_executions")
