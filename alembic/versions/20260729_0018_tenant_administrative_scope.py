"""Add tenant, administrative-unit, membership, session, and data scopes.

Revision ID: 20260729_0018
Revises: 20260729_0017
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0018"
down_revision: str | None = "20260729_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_TENANT_ID = "00000000-0000-4000-8000-000000000001"
LEGACY_TOWNSHIP_ID = "00000000-0000-4000-8000-000000000002"
LEGACY_VILLAGE_ID = "00000000-0000-4000-8000-000000000003"
LEGACY_USER_ID = "00000000-0000-4000-8000-000000000004"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "administrative_units",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("unit_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("administrative_code", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "unit_type IN ('township', 'village')",
            name="ck_administrative_unit_type",
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["administrative_units.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "administrative_code",
            name="uq_administrative_unit_tenant_code",
        ),
    )
    op.create_index(
        "ix_administrative_units_tenant_parent",
        "administrative_units",
        ["tenant_id", "parent_id"],
    )
    op.create_index(
        "ix_administrative_units_tenant_id",
        "administrative_units",
        ["tenant_id"],
    )
    op.create_index(
        "ix_administrative_units_parent_id",
        "administrative_units",
        ["parent_id"],
    )
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('township_qa', 'village_operator', 'platform_governor')",
            name="ck_tenant_membership_role",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership_user"),
    )
    op.create_index("ix_tenant_memberships_tenant_id", "tenant_memberships", ["tenant_id"])
    op.create_index("ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"])
    op.create_table(
        "membership_scopes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("administrative_unit_id", sa.Uuid(), nullable=False),
        sa.Column("include_descendants", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["administrative_unit_id"],
            ["administrative_units.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["tenant_memberships.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "membership_id",
            "administrative_unit_id",
            name="uq_membership_scope_unit",
        ),
    )
    op.create_index(
        "ix_membership_scopes_administrative_unit_id",
        "membership_scopes",
        ["administrative_unit_id"],
    )
    op.create_index(
        "ix_membership_scopes_membership_id",
        "membership_scopes",
        ["membership_id"],
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["tenant_memberships.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_auth_sessions_active",
        "auth_sessions",
        ["token_hash", "expires_at"],
    )
    op.create_index("ix_auth_sessions_membership_id", "auth_sessions", ["membership_id"])
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.execute(
        f"""
        INSERT INTO tenants (id, name, status, created_at, updated_at)
        VALUES ('{LEGACY_TENANT_ID}', '历史数据租户', 'active', now(), now())
        """
    )
    op.execute(
        f"""
        INSERT INTO users
          (id, username, display_name, password_hash, status, created_at, updated_at)
        VALUES
          ('{LEGACY_USER_ID}', '__legacy__', '历史数据迁移用户', '!disabled!',
           'disabled', now(), now())
        """
    )
    op.execute(
        f"""
        INSERT INTO administrative_units
          (id, tenant_id, parent_id, unit_type, name, administrative_code,
           status, created_at, updated_at)
        VALUES
          ('{LEGACY_TOWNSHIP_ID}', '{LEGACY_TENANT_ID}', NULL, 'township',
           '历史数据乡镇', '__legacy_township__', 'active', now(), now()),
          ('{LEGACY_VILLAGE_ID}', '{LEGACY_TENANT_ID}', '{LEGACY_TOWNSHIP_ID}',
           'village', '历史数据村', '__legacy_village__', 'active', now(), now())
        """
    )

    for table_name in ("ingestion_batches", "dataset_records"):
        op.add_column(table_name, sa.Column("tenant_id", sa.Uuid(), nullable=True))
        op.add_column(
            table_name,
            sa.Column("administrative_unit_id", sa.Uuid(), nullable=True),
        )
    op.add_column(
        "ingestion_batches",
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "dataset_records",
        sa.Column("ingestion_batch_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        f"""
        UPDATE ingestion_batches
        SET tenant_id = '{LEGACY_TENANT_ID}',
            administrative_unit_id = '{LEGACY_VILLAGE_ID}',
            created_by_user_id = '{LEGACY_USER_ID}'
        """
    )
    op.execute(
        f"""
        UPDATE dataset_records AS record
        SET tenant_id = '{LEGACY_TENANT_ID}',
            administrative_unit_id = '{LEGACY_VILLAGE_ID}',
            ingestion_batch_id = item.batch_id
        FROM ingestion_items AS item
        WHERE item.id = record.item_id
        """
    )
    for table_name, column_name in (
        ("ingestion_batches", "tenant_id"),
        ("ingestion_batches", "administrative_unit_id"),
        ("ingestion_batches", "created_by_user_id"),
        ("dataset_records", "tenant_id"),
        ("dataset_records", "administrative_unit_id"),
        ("dataset_records", "ingestion_batch_id"),
    ):
        op.alter_column(table_name, column_name, nullable=False)

    op.create_foreign_key(
        "fk_ingestion_batches_tenant",
        "ingestion_batches",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ingestion_batches_administrative_unit",
        "ingestion_batches",
        "administrative_units",
        ["administrative_unit_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ingestion_batches_created_by",
        "ingestion_batches",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_dataset_records_tenant",
        "dataset_records",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_dataset_records_administrative_unit",
        "dataset_records",
        "administrative_units",
        ["administrative_unit_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_dataset_records_ingestion_batch",
        "dataset_records",
        "ingestion_batches",
        ["ingestion_batch_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for table_name, column_name in (
        ("ingestion_batches", "tenant_id"),
        ("ingestion_batches", "administrative_unit_id"),
        ("ingestion_batches", "created_by_user_id"),
        ("dataset_records", "tenant_id"),
        ("dataset_records", "administrative_unit_id"),
        ("dataset_records", "ingestion_batch_id"),
    ):
        op.create_index(f"ix_{table_name}_{column_name}", table_name, [column_name])

    op.create_table(
        "question_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("scope_unit_id", sa.Uuid(), nullable=False),
        sa.Column("include_descendants", sa.Boolean(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("validated_query_plan", sa.JSON(), nullable=False),
        sa.Column("answer", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_unit_id"],
            ["administrative_units.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_question_runs_tenant_created",
        "question_runs",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_question_runs_requested_by_user_id",
        "question_runs",
        ["requested_by_user_id"],
    )
    op.create_index("ix_question_runs_scope_unit_id", "question_runs", ["scope_unit_id"])
    op.create_index("ix_question_runs_tenant_id", "question_runs", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("question_runs")
    for table_name, constraints in (
        (
            "dataset_records",
            (
                "fk_dataset_records_ingestion_batch",
                "fk_dataset_records_administrative_unit",
                "fk_dataset_records_tenant",
            ),
        ),
        (
            "ingestion_batches",
            (
                "fk_ingestion_batches_created_by",
                "fk_ingestion_batches_administrative_unit",
                "fk_ingestion_batches_tenant",
            ),
        ),
    ):
        for constraint in constraints:
            op.drop_constraint(constraint, table_name, type_="foreignkey")
    op.drop_column("dataset_records", "ingestion_batch_id")
    op.drop_column("dataset_records", "administrative_unit_id")
    op.drop_column("dataset_records", "tenant_id")
    op.drop_column("ingestion_batches", "created_by_user_id")
    op.drop_column("ingestion_batches", "administrative_unit_id")
    op.drop_column("ingestion_batches", "tenant_id")
    op.drop_table("auth_sessions")
    op.drop_table("membership_scopes")
    op.drop_table("tenant_memberships")
    op.drop_table("administrative_units")
    op.drop_table("tenants")
    op.drop_table("users")
