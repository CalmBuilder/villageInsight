"""Move workbook profiles into their own evidence table.

Revision ID: 20260728_0004
Revises: 20260728_0003
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0004"
down_revision: str | None = "20260728_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_profiles",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version", sa.String(length=80), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("parser_name", sa.String(length=80), nullable=False),
        sa.Column("parser_version", sa.String(length=80), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["ingestion_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index(
        "ix_document_profiles_source_sha256",
        "document_profiles",
        ["source_sha256"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO document_profiles (
            item_id,
            contract_version,
            source_sha256,
            parser_name,
            parser_version,
            profile,
            created_at,
            updated_at
        )
        SELECT
            id,
            COALESCE(profile->>'contract_version', 'workbook-profile/v1'),
            source_sha256,
            COALESCE(parser_name, 'unknown'),
            COALESCE(profile->>'parser_version', 'unknown'),
            profile,
            updated_at,
            updated_at
        FROM ingestion_items
        WHERE profile IS NOT NULL
        """
    )
    op.drop_column("ingestion_items", "profile")


def downgrade() -> None:
    op.add_column(
        "ingestion_items",
        sa.Column("profile", sa.JSON(), nullable=True),
    )
    op.execute(
        """
        UPDATE ingestion_items AS item
        SET profile = evidence.profile
        FROM document_profiles AS evidence
        WHERE evidence.item_id = item.id
        """
    )
    op.drop_index(
        "ix_document_profiles_source_sha256",
        table_name="document_profiles",
    )
    op.drop_table("document_profiles")
