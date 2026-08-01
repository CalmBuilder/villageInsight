"""Add version-level source provenance to semantic fields.

Revision ID: 20260731_0046
Revises: 20260730_0045
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260731_0046"
down_revision: str | None = "20260730_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "semantic_field_versions",
        sa.Column(
            "source",
            sa.String(length=40),
            nullable=False,
            server_default="legacy",
        ),
    )
    op.add_column(
        "semantic_field_versions",
        sa.Column(
            "source_metadata",
            JSON_DOCUMENT,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE semantic_field_versions
            SET source_metadata = jsonb_build_object(
                'source_contract', 'four-layer-template-source/v1',
                'source', 'legacy'
            )
            WHERE source_metadata = '{}'::jsonb
            """
        )
    )


def downgrade() -> None:
    op.drop_column("semantic_field_versions", "source_metadata")
    op.drop_column("semantic_field_versions", "source")
