"""store large document profiles as compressed binary payloads

Revision ID: 20260729_0038
Revises: 20260729_0037
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0038"
down_revision: str | None = "20260729_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_profiles",
        sa.Column("profile_payload", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "document_profiles",
        sa.Column("profile_encoding", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_profiles", "profile_encoding")
    op.drop_column("document_profiles", "profile_payload")
