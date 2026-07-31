"""Replace EAV-only facts with authoritative JSONB dataset records.

Revision ID: 20260729_0015
Revises: 20260729_0014
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260729_0015"
down_revision: str | None = "20260729_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("fact_records", "dataset_records")
    op.rename_table("fact_values", "record_index_values")
    op.rename_table("fact_value_lineage", "record_value_lineage")

    op.execute(
        "ALTER TABLE dataset_records RENAME CONSTRAINT fact_records_pkey TO dataset_records_pkey"
    )
    op.execute(
        "ALTER TABLE dataset_records "
        "RENAME CONSTRAINT fact_records_approved_plan_id_fkey "
        "TO dataset_records_approved_plan_id_fkey"
    )
    op.execute(
        "ALTER TABLE dataset_records RENAME CONSTRAINT fact_records_item_id_fkey "
        "TO dataset_records_item_id_fkey"
    )
    op.execute(
        "ALTER TABLE dataset_records "
        "RENAME CONSTRAINT fact_records_template_id_fkey "
        "TO dataset_records_template_id_fkey"
    )
    op.execute(
        "ALTER TABLE record_index_values RENAME CONSTRAINT fact_values_pkey "
        "TO record_index_values_pkey"
    )
    op.execute(
        "ALTER TABLE record_index_values "
        "RENAME CONSTRAINT fact_values_record_id_fkey "
        "TO record_index_values_record_id_fkey"
    )
    op.execute(
        "ALTER TABLE record_value_lineage "
        "RENAME CONSTRAINT fact_value_lineage_pkey "
        "TO record_value_lineage_pkey"
    )
    op.execute(
        "ALTER TABLE record_value_lineage "
        "RENAME CONSTRAINT fact_value_lineage_fact_value_id_fkey "
        "TO record_value_lineage_record_index_value_id_fkey"
    )

    op.execute(
        "ALTER INDEX ix_fact_records_approved_plan_id RENAME TO ix_dataset_records_approved_plan_id"
    )
    op.execute("ALTER INDEX ix_fact_records_item_id RENAME TO ix_dataset_records_item_id")
    op.execute("ALTER INDEX ix_fact_records_template_id RENAME TO ix_dataset_records_template_id")
    op.execute("ALTER INDEX ix_fact_values_record_id RENAME TO ix_record_index_values_record_id")
    op.execute(
        "ALTER INDEX ix_fact_values_semantic_field_code "
        "RENAME TO ix_record_index_values_semantic_field_code"
    )
    op.execute(
        "ALTER INDEX ix_fact_value_lineage_source_sha256 "
        "RENAME TO ix_record_value_lineage_source_sha256"
    )
    op.execute(
        "ALTER INDEX ix_fact_value_lineage_source_cell_id "
        "RENAME TO ix_record_value_lineage_source_cell_id"
    )

    op.drop_constraint(
        "uq_fact_record_plan_source_row",
        "dataset_records",
        type_="unique",
    )
    op.add_column(
        "dataset_records",
        sa.Column(
            "region_id",
            sa.String(length=200),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "dataset_records",
        sa.Column(
            "raw_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "dataset_records",
        sa.Column(
            "semantic_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "dataset_records",
        sa.Column(
            "mapping_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending_rebuild",
        ),
    )
    op.add_column(
        "dataset_records",
        sa.Column(
            "quality_status",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_unique_constraint(
        "uq_dataset_record_plan_source_row",
        "dataset_records",
        ["approved_plan_id", "sheet_id", "region_id", "source_row"],
    )
    op.create_index(
        "ix_dataset_records_mapping_status",
        "dataset_records",
        ["mapping_status"],
    )
    op.create_index(
        "ix_dataset_records_quality_status",
        "dataset_records",
        ["quality_status"],
    )
    op.execute(
        "CREATE INDEX ix_dataset_records_raw_data_gin "
        "ON dataset_records USING gin (raw_data jsonb_path_ops)"
    )
    op.execute(
        "CREATE INDEX ix_dataset_records_semantic_data_gin "
        "ON dataset_records USING gin (semantic_data jsonb_path_ops)"
    )
    op.alter_column(
        "dataset_records",
        "region_id",
        server_default=None,
    )
    op.alter_column(
        "dataset_records",
        "raw_data",
        server_default=None,
    )
    op.alter_column(
        "dataset_records",
        "semantic_data",
        server_default=None,
    )
    op.alter_column(
        "dataset_records",
        "mapping_status",
        server_default=None,
    )
    op.alter_column(
        "dataset_records",
        "quality_status",
        server_default=None,
    )

    op.execute(
        "ALTER TABLE record_index_values "
        "RENAME CONSTRAINT uq_fact_value_record_field "
        "TO uq_record_index_value_record_field"
    )
    op.alter_column(
        "record_value_lineage",
        "fact_value_id",
        new_column_name="record_index_value_id",
    )


def downgrade() -> None:
    op.alter_column(
        "record_value_lineage",
        "record_index_value_id",
        new_column_name="fact_value_id",
    )
    op.execute(
        "ALTER TABLE record_index_values "
        "RENAME CONSTRAINT uq_record_index_value_record_field "
        "TO uq_fact_value_record_field"
    )
    op.execute(
        "ALTER TABLE record_value_lineage "
        "RENAME CONSTRAINT record_value_lineage_record_index_value_id_fkey "
        "TO fact_value_lineage_fact_value_id_fkey"
    )
    op.execute(
        "ALTER TABLE record_value_lineage "
        "RENAME CONSTRAINT record_value_lineage_pkey "
        "TO fact_value_lineage_pkey"
    )
    op.execute(
        "ALTER TABLE record_index_values "
        "RENAME CONSTRAINT record_index_values_record_id_fkey "
        "TO fact_values_record_id_fkey"
    )
    op.execute(
        "ALTER TABLE record_index_values "
        "RENAME CONSTRAINT record_index_values_pkey TO fact_values_pkey"
    )
    op.execute(
        "ALTER TABLE dataset_records "
        "RENAME CONSTRAINT dataset_records_template_id_fkey "
        "TO fact_records_template_id_fkey"
    )
    op.execute(
        "ALTER TABLE dataset_records "
        "RENAME CONSTRAINT dataset_records_item_id_fkey "
        "TO fact_records_item_id_fkey"
    )
    op.execute(
        "ALTER TABLE dataset_records "
        "RENAME CONSTRAINT dataset_records_approved_plan_id_fkey "
        "TO fact_records_approved_plan_id_fkey"
    )
    op.execute(
        "ALTER TABLE dataset_records RENAME CONSTRAINT dataset_records_pkey TO fact_records_pkey"
    )
    op.drop_index(
        "ix_dataset_records_semantic_data_gin",
        table_name="dataset_records",
    )
    op.drop_index("ix_dataset_records_raw_data_gin", table_name="dataset_records")
    op.drop_index(
        "ix_dataset_records_quality_status",
        table_name="dataset_records",
    )
    op.drop_index(
        "ix_dataset_records_mapping_status",
        table_name="dataset_records",
    )
    op.drop_constraint(
        "uq_dataset_record_plan_source_row",
        "dataset_records",
        type_="unique",
    )
    op.drop_column("dataset_records", "quality_status")
    op.drop_column("dataset_records", "mapping_status")
    op.drop_column("dataset_records", "semantic_data")
    op.drop_column("dataset_records", "raw_data")
    op.drop_column("dataset_records", "region_id")
    op.create_unique_constraint(
        "uq_fact_record_plan_source_row",
        "dataset_records",
        ["approved_plan_id", "sheet_id", "source_row"],
    )

    op.execute(
        "ALTER INDEX ix_record_value_lineage_source_cell_id "
        "RENAME TO ix_fact_value_lineage_source_cell_id"
    )
    op.execute(
        "ALTER INDEX ix_record_value_lineage_source_sha256 "
        "RENAME TO ix_fact_value_lineage_source_sha256"
    )
    op.execute(
        "ALTER INDEX ix_record_index_values_semantic_field_code "
        "RENAME TO ix_fact_values_semantic_field_code"
    )
    op.execute("ALTER INDEX ix_record_index_values_record_id RENAME TO ix_fact_values_record_id")
    op.execute("ALTER INDEX ix_dataset_records_template_id RENAME TO ix_fact_records_template_id")
    op.execute("ALTER INDEX ix_dataset_records_item_id RENAME TO ix_fact_records_item_id")
    op.execute(
        "ALTER INDEX ix_dataset_records_approved_plan_id RENAME TO ix_fact_records_approved_plan_id"
    )
    op.rename_table("record_value_lineage", "fact_value_lineage")
    op.rename_table("record_index_values", "fact_values")
    op.rename_table("dataset_records", "fact_records")
