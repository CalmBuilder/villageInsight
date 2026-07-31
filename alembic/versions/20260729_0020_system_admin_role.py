"""Allow the tenant-scoped system administrator role.

Revision ID: 20260729_0020
Revises: 20260729_0019
Create Date: 2026-07-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260729_0020"
down_revision: str | None = "20260729_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_tenant_membership_role",
        "tenant_memberships",
        type_="check",
    )
    op.create_check_constraint(
        "ck_tenant_membership_role",
        "tenant_memberships",
        "role IN "
        "('township_qa', 'village_operator', 'system_admin', 'platform_governor')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_tenant_membership_role",
        "tenant_memberships",
        type_="check",
    )
    op.create_check_constraint(
        "ck_tenant_membership_role",
        "tenant_memberships",
        "role IN ('township_qa', 'village_operator', 'platform_governor')",
    )
