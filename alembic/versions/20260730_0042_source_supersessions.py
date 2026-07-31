"""Add immutable source-file supersession declarations.

Revision ID: 20260730_0042
Revises: 20260730_0041
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_0042"
down_revision: str | None = "20260730_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_item_supersessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("administrative_unit_id", sa.Uuid(), nullable=False),
        sa.Column("superseded_item_id", sa.Uuid(), nullable=False),
        sa.Column("replacement_item_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("declared_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "superseded_item_id <> replacement_item_id",
            name="ck_ingestion_item_supersessions_distinct_items",
        ),
        sa.ForeignKeyConstraint(
            ["administrative_unit_id"],
            ["administrative_units.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["declared_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_item_id"],
            ["ingestion_items.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_item_id"],
            ["ingestion_items.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "superseded_item_id",
            name="uq_ingestion_item_supersessions_superseded_item",
        ),
    )
    op.create_index(
        "ix_ingestion_item_supersessions_tenant_id",
        "ingestion_item_supersessions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_ingestion_item_supersessions_administrative_unit_id",
        "ingestion_item_supersessions",
        ["administrative_unit_id"],
    )
    op.create_index(
        "ix_ingestion_item_supersessions_superseded_item_id",
        "ingestion_item_supersessions",
        ["superseded_item_id"],
    )
    op.create_index(
        "ix_ingestion_item_supersessions_replacement_item_id",
        "ingestion_item_supersessions",
        ["replacement_item_id"],
    )
    op.create_index(
        "ix_ingestion_item_supersessions_declared_by_user_id",
        "ingestion_item_supersessions",
        ["declared_by_user_id"],
    )
    op.create_index(
        "ix_ingestion_item_supersessions_scope_created",
        "ingestion_item_supersessions",
        ["tenant_id", "administrative_unit_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("ingestion_item_supersessions")
