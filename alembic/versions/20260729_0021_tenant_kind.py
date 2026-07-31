"""Distinguish platform and business tenants.

Revision ID: 20260729_0021
Revises: 20260729_0020
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0021"
down_revision: str | None = "20260729_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            server_default="business",
        ),
    )
    op.create_check_constraint(
        "ck_tenant_kind",
        "tenants",
        "kind IN ('business', 'platform')",
    )
    op.alter_column("tenants", "kind", server_default=None)
    op.create_unique_constraint("uq_tenants_name", "tenants", ["name"])
    op.drop_constraint(
        "ck_tenant_membership_role",
        "tenant_memberships",
        type_="check",
    )
    op.execute(
        """
        UPDATE tenant_memberships
        SET role = CASE
          WHEN role = 'township_qa' THEN 'tenant_admin'
          WHEN role IN ('system_admin', 'platform_governor') THEN 'platform_admin'
          ELSE role
        END
        """
    )
    op.create_check_constraint(
        "ck_tenant_membership_role",
        "tenant_memberships",
        "role IN ('tenant_admin', 'village_operator', 'platform_admin')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_tenant_membership_role",
        "tenant_memberships",
        type_="check",
    )
    op.execute(
        """
        UPDATE tenant_memberships
        SET role = CASE
          WHEN role = 'tenant_admin' THEN 'township_qa'
          WHEN role = 'platform_admin' THEN 'platform_governor'
          ELSE role
        END
        """
    )
    op.create_check_constraint(
        "ck_tenant_membership_role",
        "tenant_memberships",
        "role IN "
        "('township_qa', 'village_operator', 'system_admin', 'platform_governor')",
    )
    op.drop_constraint("uq_tenants_name", "tenants", type_="unique")
    op.drop_constraint("ck_tenant_kind", "tenants", type_="check")
    op.drop_column("tenants", "kind")
