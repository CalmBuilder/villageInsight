"""record every Hermes recognition attempt

Revision ID: 20260729_0014
Revises: 20260729_0013
Create Date: 2026-07-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260729_0014"
down_revision: str | None = "20260729_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_hermes_recognition_records_item",
        "hermes_recognition_records",
        type_="unique",
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_hermes_recognition_records_item",
        "hermes_recognition_records",
        ["item_id"],
    )
