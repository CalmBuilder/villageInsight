"""Carry village scope through ingestion jobs and stable audit identities.

Revision ID: 20260729_0022
Revises: 20260729_0021
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0022"
down_revision: str | None = "20260729_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_columns(table_name: str) -> None:
    op.add_column(table_name, sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.add_column(
        table_name,
        sa.Column("administrative_unit_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        table_name,
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
    )


def upgrade() -> None:
    _scope_columns("ingestion_items")
    op.execute(
        """
        UPDATE ingestion_items AS item
        SET tenant_id = batch.tenant_id,
            administrative_unit_id = batch.administrative_unit_id,
            created_by_user_id = batch.created_by_user_id
        FROM ingestion_batches AS batch
        WHERE batch.id = item.batch_id
        """
    )
    for column in ("tenant_id", "administrative_unit_id", "created_by_user_id"):
        op.alter_column("ingestion_items", column, nullable=False)
    op.create_foreign_key(
        "fk_ingestion_items_tenant",
        "ingestion_items",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ingestion_items_administrative_unit",
        "ingestion_items",
        "administrative_units",
        ["administrative_unit_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ingestion_items_created_by_user",
        "ingestion_items",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_ingestion_items_tenant_id", "ingestion_items", ["tenant_id"])
    op.create_index(
        "ix_ingestion_items_administrative_unit_id",
        "ingestion_items",
        ["administrative_unit_id"],
    )
    op.create_index(
        "ix_ingestion_items_created_by_user_id",
        "ingestion_items",
        ["created_by_user_id"],
    )
    op.drop_constraint("uq_item_batch_sha256", "ingestion_items", type_="unique")
    op.create_unique_constraint(
        "uq_item_village_source_sha256",
        "ingestion_items",
        ["tenant_id", "administrative_unit_id", "source_sha256"],
    )

    op.add_column("jobs", sa.Column("tenant_id", sa.Uuid(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("administrative_unit_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
    )
    op.add_column("jobs", sa.Column("batch_id", sa.Uuid(), nullable=True))
    op.add_column("jobs", sa.Column("item_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE jobs AS job
        SET tenant_id = item.tenant_id,
            administrative_unit_id = item.administrative_unit_id,
            requested_by_user_id = item.created_by_user_id,
            batch_id = item.batch_id,
            item_id = item.id
        FROM ingestion_items AS item
        WHERE job.payload ->> 'item_id' = item.id::text
        """
    )
    for name, target, ondelete in (
        ("tenant_id", "tenants", "RESTRICT"),
        ("administrative_unit_id", "administrative_units", "RESTRICT"),
        ("requested_by_user_id", "users", "RESTRICT"),
        ("batch_id", "ingestion_batches", "CASCADE"),
        ("item_id", "ingestion_items", "CASCADE"),
    ):
        op.create_foreign_key(
            f"fk_jobs_{name.removesuffix('_id')}",
            "jobs",
            target,
            [name],
            ["id"],
            ondelete=ondelete,
        )
        op.create_index(f"ix_jobs_{name}", "jobs", [name])

    _scope_columns("template_proposals")
    op.add_column(
        "template_proposals",
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        """
        UPDATE template_proposals AS proposal
        SET tenant_id = item.tenant_id,
            administrative_unit_id = item.administrative_unit_id,
            created_by_user_id = item.created_by_user_id
        FROM ingestion_items AS item
        WHERE item.id = proposal.source_item_id
        """
    )
    for name, target, ondelete in (
        ("tenant_id", "tenants", "RESTRICT"),
        ("administrative_unit_id", "administrative_units", "RESTRICT"),
        ("created_by_user_id", "users", "SET NULL"),
        ("resolved_by_user_id", "users", "SET NULL"),
    ):
        op.create_foreign_key(
            f"fk_template_proposals_{name.removesuffix('_id')}",
            "template_proposals",
            target,
            [name],
            ["id"],
            ondelete=ondelete,
        )
        op.create_index(f"ix_template_proposals_{name}", "template_proposals", [name])

    op.add_column(
        "approved_import_plans",
        sa.Column(
            "approved_by_type",
            sa.String(length=32),
            nullable=False,
            server_default="user",
        ),
    )
    op.add_column(
        "approved_import_plans",
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        """
        UPDATE approved_import_plans
        SET approved_by_type = CASE
          WHEN approved_by = 'system:hermes' THEN 'hermes'
          WHEN approved_by LIKE 'system:%' THEN 'system'
          ELSE 'user'
        END
        """
    )
    op.execute(
        """
        UPDATE approved_import_plans AS plan
        SET approved_by_user_id = app_user.id
        FROM users AS app_user
        WHERE plan.approved_by_type = 'user'
          AND app_user.username = plan.approved_by
        """
    )
    op.alter_column("approved_import_plans", "approved_by_type", server_default=None)
    op.create_foreign_key(
        "fk_approved_import_plans_approved_by_user",
        "approved_import_plans",
        "users",
        ["approved_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_approved_import_plans_approved_by_user_id",
        "approved_import_plans",
        ["approved_by_user_id"],
    )

    for table_name in ("template_review_events", "semantic_field_review_events"):
        op.add_column(
            table_name,
            sa.Column(
                "actor_type",
                sa.String(length=32),
                nullable=False,
                server_default="user",
            ),
        )
        op.add_column(
            table_name,
            sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        )
        op.execute(
            f"""
            UPDATE {table_name}
            SET actor_type = CASE
              WHEN actor LIKE 'system:%' THEN 'system'
              ELSE 'user'
            END
            """
        )
        op.execute(
            f"""
            UPDATE {table_name} AS event
            SET actor_user_id = app_user.id
            FROM users AS app_user
            WHERE event.actor_type = 'user'
              AND app_user.username = event.actor
            """
        )
        op.alter_column(table_name, "actor_type", server_default=None)
        op.create_foreign_key(
            f"fk_{table_name}_actor_user",
            table_name,
            "users",
            ["actor_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            f"ix_{table_name}_actor_user_id",
            table_name,
            ["actor_user_id"],
        )


def downgrade() -> None:
    for table_name in ("semantic_field_review_events", "template_review_events"):
        op.drop_index(f"ix_{table_name}_actor_user_id", table_name=table_name)
        op.drop_constraint(
            f"fk_{table_name}_actor_user",
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "actor_user_id")
        op.drop_column(table_name, "actor_type")

    op.drop_index(
        "ix_approved_import_plans_approved_by_user_id",
        table_name="approved_import_plans",
    )
    op.drop_constraint(
        "fk_approved_import_plans_approved_by_user",
        "approved_import_plans",
        type_="foreignkey",
    )
    op.drop_column("approved_import_plans", "approved_by_user_id")
    op.drop_column("approved_import_plans", "approved_by_type")

    for name in (
        "resolved_by_user_id",
        "created_by_user_id",
        "administrative_unit_id",
        "tenant_id",
    ):
        op.drop_index(f"ix_template_proposals_{name}", table_name="template_proposals")
        op.drop_constraint(
            f"fk_template_proposals_{name.removesuffix('_id')}",
            "template_proposals",
            type_="foreignkey",
        )
        op.drop_column("template_proposals", name)

    for name in (
        "item_id",
        "batch_id",
        "requested_by_user_id",
        "administrative_unit_id",
        "tenant_id",
    ):
        op.drop_index(f"ix_jobs_{name}", table_name="jobs")
        op.drop_constraint(
            f"fk_jobs_{name.removesuffix('_id')}",
            "jobs",
            type_="foreignkey",
        )
        op.drop_column("jobs", name)

    op.drop_constraint(
        "uq_item_village_source_sha256",
        "ingestion_items",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_item_batch_sha256",
        "ingestion_items",
        ["batch_id", "source_sha256"],
    )
    for name in ("created_by_user_id", "administrative_unit_id", "tenant_id"):
        op.drop_index(f"ix_ingestion_items_{name}", table_name="ingestion_items")
        op.drop_constraint(
            f"fk_ingestion_items_{name.removesuffix('_id')}",
            "ingestion_items",
            type_="foreignkey",
        )
        op.drop_column("ingestion_items", name)
