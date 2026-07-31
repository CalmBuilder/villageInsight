"""Add explicit materialization policy to composition slots.

Revision ID: 20260729_0032
Revises: 20260729_0031
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0032"
down_revision: str | None = "20260729_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sheet_composition_region_slots",
        sa.Column(
            "materialize",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "workbook_route_sheet_slots",
        sa.Column(
            "materialize",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.alter_column(
        "sheet_composition_region_slots",
        "materialize",
        server_default=None,
    )
    op.alter_column(
        "workbook_route_sheet_slots",
        "materialize",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("workbook_route_sheet_slots", "materialize")
    op.drop_column("sheet_composition_region_slots", "materialize")
