"""Add auditable per-item build-result deletion lifecycle.

Revision ID: 20260801_0047
Revises: 20260731_0046
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260801_0047"
down_revision: str | None = "20260731_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "ingestion_batches",
        sa.Column("deleted_files", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ingestion_items",
        sa.Column(
            "build_result_deletion_status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "ingestion_items",
        sa.Column("build_result_deleted_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "ingestion_items",
        sa.Column("build_result_deleted_by_user_id", sa.Uuid()),
    )
    op.create_foreign_key(
        "fk_ingestion_items_build_result_deleted_by_user",
        "ingestion_items",
        "users",
        ["build_result_deleted_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ingestion_items_build_result_deleted_by_user_id",
        "ingestion_items",
        ["build_result_deleted_by_user_id"],
    )

    op.create_table(
        "ingestion_build_result_deletions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("administrative_unit_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid()),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("manifest", JSON_DOCUMENT, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "deleted_counts", JSON_DOCUMENT, nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "retired_counts", JSON_DOCUMENT, nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["item_id"], ["ingestion_items.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["administrative_unit_id"],
            ["administrative_units.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["ingestion_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("item_id", name="uq_build_result_deletion_item"),
    )
    for column in (
        "item_id",
        "tenant_id",
        "administrative_unit_id",
        "batch_id",
        "requested_by_user_id",
    ):
        op.create_index(
            f"ix_ingestion_build_result_deletions_{column}",
            "ingestion_build_result_deletions",
            [column],
        )

    for table in ("template_proposals", "approved_import_plans", "template_versions"):
        op.add_column(
            table,
            sa.Column("build_result_retired_at", sa.DateTime(timezone=True)),
        )
        op.add_column(
            table,
            sa.Column("build_result_retired_by_deletion_id", sa.Uuid()),
        )
        op.create_foreign_key(
            f"fk_{table}_build_result_retirement",
            table,
            "ingestion_build_result_deletions",
            ["build_result_retired_by_deletion_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(
            f"ix_{table}_build_result_retired_by_deletion_id",
            table,
            ["build_result_retired_by_deletion_id"],
        )


def downgrade() -> None:
    for table in ("template_versions", "approved_import_plans", "template_proposals"):
        op.drop_index(
            f"ix_{table}_build_result_retired_by_deletion_id",
            table_name=table,
        )
        op.drop_constraint(
            f"fk_{table}_build_result_retirement",
            table,
            type_="foreignkey",
        )
        op.drop_column(table, "build_result_retired_by_deletion_id")
        op.drop_column(table, "build_result_retired_at")

    op.drop_table("ingestion_build_result_deletions")
    op.drop_index(
        "ix_ingestion_items_build_result_deleted_by_user_id",
        table_name="ingestion_items",
    )
    op.drop_constraint(
        "fk_ingestion_items_build_result_deleted_by_user",
        "ingestion_items",
        type_="foreignkey",
    )
    op.drop_column("ingestion_items", "build_result_deleted_by_user_id")
    op.drop_column("ingestion_items", "build_result_deleted_at")
    op.drop_column("ingestion_items", "build_result_deletion_status")
    op.drop_column("ingestion_batches", "deleted_files")
