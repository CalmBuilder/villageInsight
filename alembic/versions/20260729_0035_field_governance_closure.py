"""Add auditable field governance resolutions and contextual ignore rules.

Revision ID: 20260729_0035
Revises: 20260729_0034
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260729_0035"
down_revision: str | None = "20260729_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "governance_resolutions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "proposal_id",
            sa.Uuid(),
            sa.ForeignKey("template_proposals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("ingestion_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("contract_version", sa.String(80), nullable=False),
        sa.Column("domain", sa.String(80), nullable=False),
        sa.Column("record_type", sa.String(120), nullable=False),
        sa.Column("record_grain", sa.String(120), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "region_template_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "approved_plan_id",
            sa.Uuid(),
            sa.ForeignKey("approved_import_plans.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "proposal_id", name="uq_governance_resolutions_proposal"
        ),
    )
    op.create_index(
        "ix_governance_resolutions_proposal_id",
        "governance_resolutions",
        ["proposal_id"],
    )
    op.create_index(
        "ix_governance_resolutions_item_id",
        "governance_resolutions",
        ["item_id"],
    )
    op.create_index(
        "ix_governance_resolutions_approved_plan_id",
        "governance_resolutions",
        ["approved_plan_id"],
    )
    op.create_index(
        "ix_governance_resolutions_actor_user_id",
        "governance_resolutions",
        ["actor_user_id"],
    )

    op.create_table(
        "governance_field_resolutions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "governance_resolution_id",
            sa.Uuid(),
            sa.ForeignKey("governance_resolutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("ingestion_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_column_id", sa.String(500), nullable=False),
        sa.Column("sheet_id", sa.String(200), nullable=False),
        sa.Column("sheet_name", sa.String(200), nullable=False),
        sa.Column("column_index", sa.Integer(), nullable=False),
        sa.Column("column_coordinate", sa.String(16), nullable=False),
        sa.Column(
            "header_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("observed_data_type", sa.String(40)),
        sa.Column(
            "hermes_suggestion",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "resolution", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "semantic_field_code",
            sa.String(160),
            sa.ForeignKey("semantic_fields.code", ondelete="SET NULL"),
        ),
        sa.Column("semantic_field_version", sa.Integer()),
        sa.Column(
            "learned_variant_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "governance_resolution_id",
            "source_column_id",
            name="uq_governance_field_resolutions_source",
        ),
    )
    for name, columns in (
        ("ix_governance_field_resolutions_governance_resolution_id", ["governance_resolution_id"]),
        ("ix_governance_field_resolutions_item_id", ["item_id"]),
        ("ix_governance_field_resolutions_semantic_field_code", ["semantic_field_code"]),
        ("ix_governance_field_resolutions_actor_user_id", ["actor_user_id"]),
    ):
        op.create_index(name, "governance_field_resolutions", columns)

    op.create_table(
        "semantic_ignore_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rule_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "header_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "parent_path", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("domain", sa.String(80), nullable=False),
        sa.Column("record_type", sa.String(120), nullable=False),
        sa.Column("observed_data_type", sa.String(40)),
        sa.Column("reason", sa.String(240), nullable=False),
        sa.Column(
            "source_item_id",
            sa.Uuid(),
            sa.ForeignKey("ingestion_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_column_id", sa.String(500), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "rule_key", "version", name="uq_semantic_ignore_rules_version"
        ),
    )
    op.create_index(
        "ix_semantic_ignore_rules_active",
        "semantic_ignore_rules",
        ["rule_key", "status"],
    )
    op.create_index(
        "ix_semantic_ignore_rules_source_item_id",
        "semantic_ignore_rules",
        ["source_item_id"],
    )
    op.create_index(
        "ix_semantic_ignore_rules_actor_user_id",
        "semantic_ignore_rules",
        ["actor_user_id"],
    )


def downgrade() -> None:
    op.drop_table("semantic_ignore_rules")
    op.drop_table("governance_field_resolutions")
    op.drop_table("governance_resolutions")
