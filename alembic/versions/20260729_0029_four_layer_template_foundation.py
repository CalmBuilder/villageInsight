"""Add field variants and independent Region templates.

Revision ID: 20260729_0029
Revises: 20260729_0028
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0029"
down_revision: str | None = "20260729_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "semantic_field_variants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("field_version_id", sa.Uuid(), nullable=False),
        sa.Column("variant_key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("normalized_value", sa.String(length=500), nullable=False),
        sa.Column("alias", sa.String(length=500), nullable=True),
        sa.Column("header_path", sa.JSON(), nullable=False),
        sa.Column("parent_path", sa.JSON(), nullable=False),
        sa.Column("role", sa.String(length=120), nullable=True),
        sa.Column("domain", sa.String(length=80), nullable=True),
        sa.Column("record_type", sa.String(length=120), nullable=True),
        sa.Column("observed_data_type", sa.String(length=40), nullable=True),
        sa.Column("unit_dimension", sa.String(length=80), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("confidence_basis_points", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["field_version_id"],
            ["semantic_field_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "field_version_id",
            "variant_key",
            name="uq_semantic_field_variants_key",
        ),
    )
    op.create_index(
        "ix_semantic_field_variants_field_version_id",
        "semantic_field_variants",
        ["field_version_id"],
    )
    op.create_index(
        "ix_semantic_field_variants_normalized",
        "semantic_field_variants",
        ["normalized_value"],
    )

    op.create_table(
        "region_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=160), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_region_templates_code"),
    )
    op.create_table(
        "region_template_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("region_template_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("domain", sa.String(length=80), nullable=False),
        sa.Column("record_type", sa.String(length=120), nullable=False),
        sa.Column("record_grain", sa.String(length=120), nullable=False),
        sa.Column("region_kind", sa.String(length=40), nullable=False),
        sa.Column("region_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("header_signature", sa.JSON(), nullable=False),
        sa.Column("layout_rules", sa.JSON(), nullable=False),
        sa.Column("field_bindings", sa.JSON(), nullable=False),
        sa.Column("identity_policy", sa.JSON(), nullable=False),
        sa.Column("quality_rules", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["region_template_id"],
            ["region_templates.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "region_template_id",
            "version",
            name="uq_region_template_versions_version",
        ),
    )
    op.create_index(
        "ix_region_template_versions_region_template_id",
        "region_template_versions",
        ["region_template_id"],
    )
    op.create_index(
        "ix_region_template_versions_fingerprint",
        "region_template_versions",
        ["region_fingerprint"],
    )
    op.create_table(
        "region_template_review_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("region_template_version_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=160), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["region_template_version_id"],
            ["region_template_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_region_template_review_events_region_template_version_id",
        "region_template_review_events",
        ["region_template_version_id"],
    )
    op.create_index(
        "ix_region_template_review_events_actor_user_id",
        "region_template_review_events",
        ["actor_user_id"],
    )
    op.add_column(
        "region_template_matches",
        sa.Column("region_template_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "region_template_matches",
        sa.Column("region_template_version", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_region_template_matches_region_template_id",
        "region_template_matches",
        "region_templates",
        ["region_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_region_template_matches_region_template_id",
        "region_template_matches",
        ["region_template_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_region_template_matches_region_template_id",
        table_name="region_template_matches",
    )
    op.drop_constraint(
        "fk_region_template_matches_region_template_id",
        "region_template_matches",
        type_="foreignkey",
    )
    op.drop_column("region_template_matches", "region_template_version")
    op.drop_column("region_template_matches", "region_template_id")
    op.drop_index(
        "ix_region_template_review_events_actor_user_id",
        table_name="region_template_review_events",
    )
    op.drop_index(
        "ix_region_template_review_events_region_template_version_id",
        table_name="region_template_review_events",
    )
    op.drop_table("region_template_review_events")
    op.drop_index(
        "ix_region_template_versions_fingerprint",
        table_name="region_template_versions",
    )
    op.drop_index(
        "ix_region_template_versions_region_template_id",
        table_name="region_template_versions",
    )
    op.drop_table("region_template_versions")
    op.drop_table("region_templates")
    op.drop_index(
        "ix_semantic_field_variants_normalized",
        table_name="semantic_field_variants",
    )
    op.drop_index(
        "ix_semantic_field_variants_field_version_id",
        table_name="semantic_field_variants",
    )
    op.drop_table("semantic_field_variants")
