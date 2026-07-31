"""Add Hermes recognition cache, call metrics and proposal idempotency.

Revision ID: 20260728_0007
Revises: 20260728_0006
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0007"
down_revision: str | None = "20260728_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "template_proposals",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint(
        "uq_template_proposals_idempotency",
        "template_proposals",
        ["idempotency_key"],
    )
    op.create_table(
        "hermes_recognition_cache",
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("hermes_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("cache_key"),
    )
    op.create_table(
        "hermes_recognition_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("call_performed", sa.Boolean(), nullable=False),
        sa.Column("input_field_count", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["cache_key"],
            ["hermes_recognition_cache.cache_key"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["ingestion_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            name="uq_hermes_recognition_records_item",
        ),
    )
    op.create_index(
        "ix_hermes_recognition_records_item_id",
        "hermes_recognition_records",
        ["item_id"],
        unique=False,
    )
    op.create_index(
        "ix_hermes_recognition_records_cache_key",
        "hermes_recognition_records",
        ["cache_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hermes_recognition_records_cache_key",
        table_name="hermes_recognition_records",
    )
    op.drop_index(
        "ix_hermes_recognition_records_item_id",
        table_name="hermes_recognition_records",
    )
    op.drop_table("hermes_recognition_records")
    op.drop_table("hermes_recognition_cache")
    op.drop_constraint(
        "uq_template_proposals_idempotency",
        "template_proposals",
        type_="unique",
    )
    op.drop_column("template_proposals", "idempotency_key")
