"""Index text semantic values used by generated read-only queries.

Revision ID: 20260730_0044
Revises: 20260730_0043
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_0044"
down_revision: str | None = "20260730_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_record_index_values_field_text_record",
        "record_index_values",
        ["semantic_field_code", "text_value", "record_id"],
        postgresql_where=sa.text("role = ''"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_record_index_values_field_text_record",
        table_name="record_index_values",
    )
