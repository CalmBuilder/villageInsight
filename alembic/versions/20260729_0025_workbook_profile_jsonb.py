"""Store the complete workbook evidence profile as JSONB.

Revision ID: 20260729_0025
Revises: 20260729_0024
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260729_0025"
down_revision: str | None = "20260729_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "document_profiles",
        "profile",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(),
        postgresql_using="profile::jsonb",
        existing_nullable=False,
    )
    op.create_index(
        "ix_document_profiles_profile_gin",
        "document_profiles",
        ["profile"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"profile": "jsonb_path_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_profiles_profile_gin",
        table_name="document_profiles",
    )
    op.alter_column(
        "document_profiles",
        "profile",
        existing_type=postgresql.JSONB(),
        type_=sa.JSON(),
        postgresql_using="profile::json",
        existing_nullable=False,
    )
