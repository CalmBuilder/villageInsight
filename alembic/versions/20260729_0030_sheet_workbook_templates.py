"""Add Sheet compositions, Workbook routes, and layered match ledgers.

Revision ID: 20260729_0030
Revises: 20260729_0029
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0030"
down_revision: str | None = "20260729_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _review_columns(version_column: str) -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(version_column, sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=160), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "sheet_compositions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=160), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_sheet_compositions_code"),
    )
    op.create_table(
        "sheet_composition_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sheet_composition_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("composition_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("matching_rules", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["sheet_composition_id"],
            ["sheet_compositions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sheet_composition_id",
            "version",
            name="uq_sheet_composition_versions_version",
        ),
    )
    op.create_index(
        "ix_sheet_composition_versions_sheet_composition_id",
        "sheet_composition_versions",
        ["sheet_composition_id"],
    )
    op.create_index(
        "ix_sheet_composition_versions_fingerprint",
        "sheet_composition_versions",
        ["composition_fingerprint"],
    )
    op.create_table(
        "sheet_composition_region_slots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sheet_composition_version_id", sa.Uuid(), nullable=False),
        sa.Column("slot_key", sa.String(length=120), nullable=False),
        sa.Column("region_template_id", sa.Uuid(), nullable=False),
        sa.Column("region_template_version", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("cardinality", sa.String(length=20), nullable=False),
        sa.Column("match_hints", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sheet_composition_version_id"],
            ["sheet_composition_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["region_template_id"],
            ["region_templates.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sheet_composition_version_id",
            "slot_key",
            name="uq_sheet_composition_region_slots_key",
        ),
    )
    op.create_index(
        "ix_sheet_composition_region_slots_sheet_composition_version_id",
        "sheet_composition_region_slots",
        ["sheet_composition_version_id"],
    )
    op.create_index(
        "ix_sheet_composition_region_slots_region_template_id",
        "sheet_composition_region_slots",
        ["region_template_id"],
    )
    op.create_table(
        "sheet_composition_review_events",
        *_review_columns("sheet_composition_version_id"),
        sa.ForeignKeyConstraint(
            ["sheet_composition_version_id"],
            ["sheet_composition_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sheet_composition_review_events_sheet_composition_version_id",
        "sheet_composition_review_events",
        ["sheet_composition_version_id"],
    )
    op.create_index(
        "ix_sheet_composition_review_events_actor_user_id",
        "sheet_composition_review_events",
        ["actor_user_id"],
    )

    op.create_table(
        "workbook_routes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=160), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_workbook_routes_code"),
    )
    op.create_table(
        "workbook_route_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workbook_route_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("route_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("matching_rules", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workbook_route_id"],
            ["workbook_routes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workbook_route_id",
            "version",
            name="uq_workbook_route_versions_version",
        ),
    )
    op.create_index(
        "ix_workbook_route_versions_workbook_route_id",
        "workbook_route_versions",
        ["workbook_route_id"],
    )
    op.create_index(
        "ix_workbook_route_versions_fingerprint",
        "workbook_route_versions",
        ["route_fingerprint"],
    )
    op.create_table(
        "workbook_route_sheet_slots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workbook_route_version_id", sa.Uuid(), nullable=False),
        sa.Column("slot_key", sa.String(length=120), nullable=False),
        sa.Column("sheet_composition_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_composition_version", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("cardinality", sa.String(length=20), nullable=False),
        sa.Column("match_hints", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workbook_route_version_id"],
            ["workbook_route_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sheet_composition_id"],
            ["sheet_compositions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workbook_route_version_id",
            "slot_key",
            name="uq_workbook_route_sheet_slots_key",
        ),
    )
    op.create_index(
        "ix_workbook_route_sheet_slots_workbook_route_version_id",
        "workbook_route_sheet_slots",
        ["workbook_route_version_id"],
    )
    op.create_index(
        "ix_workbook_route_sheet_slots_sheet_composition_id",
        "workbook_route_sheet_slots",
        ["sheet_composition_id"],
    )
    op.create_table(
        "workbook_route_review_events",
        *_review_columns("workbook_route_version_id"),
        sa.ForeignKeyConstraint(
            ["workbook_route_version_id"],
            ["workbook_route_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workbook_route_review_events_workbook_route_version_id",
        "workbook_route_review_events",
        ["workbook_route_version_id"],
    )
    op.create_index(
        "ix_workbook_route_review_events_actor_user_id",
        "workbook_route_review_events",
        ["actor_user_id"],
    )

    op.create_table(
        "sheet_composition_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("sheet_id", sa.String(length=200), nullable=False),
        sa.Column("sheet_composition_id", sa.Uuid(), nullable=True),
        sa.Column("sheet_composition_version", sa.Integer(), nullable=True),
        sa.Column("match_type", sa.String(length=32), nullable=False),
        sa.Column("score_basis_points", sa.Integer(), nullable=False),
        sa.Column("total_slots", sa.Integer(), nullable=False),
        sa.Column("matched_slots", sa.Integer(), nullable=False),
        sa.Column("coverage_basis_points", sa.Integer(), nullable=False),
        sa.Column("differences", sa.JSON(), nullable=False),
        sa.Column("matcher_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["ingestion_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sheet_composition_id"],
            ["sheet_compositions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "sheet_id",
            name="uq_sheet_composition_matches_source",
        ),
    )
    op.create_index(
        "ix_sheet_composition_matches_item_id",
        "sheet_composition_matches",
        ["item_id"],
    )
    op.create_index(
        "ix_sheet_composition_matches_sheet_composition_id",
        "sheet_composition_matches",
        ["sheet_composition_id"],
    )
    op.create_table(
        "workbook_route_matches",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("workbook_route_id", sa.Uuid(), nullable=True),
        sa.Column("workbook_route_version", sa.Integer(), nullable=True),
        sa.Column("match_type", sa.String(length=32), nullable=False),
        sa.Column("score_basis_points", sa.Integer(), nullable=False),
        sa.Column("total_slots", sa.Integer(), nullable=False),
        sa.Column("matched_slots", sa.Integer(), nullable=False),
        sa.Column("coverage_basis_points", sa.Integer(), nullable=False),
        sa.Column("differences", sa.JSON(), nullable=False),
        sa.Column("matcher_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["ingestion_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workbook_route_id"],
            ["workbook_routes.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.create_index(
        "ix_workbook_route_matches_workbook_route_id",
        "workbook_route_matches",
        ["workbook_route_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workbook_route_matches_workbook_route_id",
        table_name="workbook_route_matches",
    )
    op.drop_table("workbook_route_matches")
    op.drop_index(
        "ix_sheet_composition_matches_sheet_composition_id",
        table_name="sheet_composition_matches",
    )
    op.drop_index(
        "ix_sheet_composition_matches_item_id",
        table_name="sheet_composition_matches",
    )
    op.drop_table("sheet_composition_matches")
    op.drop_index(
        "ix_workbook_route_review_events_actor_user_id",
        table_name="workbook_route_review_events",
    )
    op.drop_index(
        "ix_workbook_route_review_events_workbook_route_version_id",
        table_name="workbook_route_review_events",
    )
    op.drop_table("workbook_route_review_events")
    op.drop_index(
        "ix_workbook_route_sheet_slots_sheet_composition_id",
        table_name="workbook_route_sheet_slots",
    )
    op.drop_index(
        "ix_workbook_route_sheet_slots_workbook_route_version_id",
        table_name="workbook_route_sheet_slots",
    )
    op.drop_table("workbook_route_sheet_slots")
    op.drop_index(
        "ix_workbook_route_versions_fingerprint",
        table_name="workbook_route_versions",
    )
    op.drop_index(
        "ix_workbook_route_versions_workbook_route_id",
        table_name="workbook_route_versions",
    )
    op.drop_table("workbook_route_versions")
    op.drop_table("workbook_routes")
    op.drop_index(
        "ix_sheet_composition_review_events_actor_user_id",
        table_name="sheet_composition_review_events",
    )
    op.drop_index(
        "ix_sheet_composition_review_events_sheet_composition_version_id",
        table_name="sheet_composition_review_events",
    )
    op.drop_table("sheet_composition_review_events")
    op.drop_index(
        "ix_sheet_composition_region_slots_region_template_id",
        table_name="sheet_composition_region_slots",
    )
    op.drop_index(
        "ix_sheet_composition_region_slots_sheet_composition_version_id",
        table_name="sheet_composition_region_slots",
    )
    op.drop_table("sheet_composition_region_slots")
    op.drop_index(
        "ix_sheet_composition_versions_fingerprint",
        table_name="sheet_composition_versions",
    )
    op.drop_index(
        "ix_sheet_composition_versions_sheet_composition_id",
        table_name="sheet_composition_versions",
    )
    op.drop_table("sheet_composition_versions")
    op.drop_table("sheet_compositions")
