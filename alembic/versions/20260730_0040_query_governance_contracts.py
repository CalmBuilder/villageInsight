"""Add versioned fact-set, semantic-manifest, and metric contracts.

Revision ID: 20260730_0040
Revises: 20260730_0039
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260730_0040"
down_revision: str | None = "20260730_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "query_fact_set_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("aliases", JSONB, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("domain", sa.String(length=120), nullable=False),
        sa.Column("record_type", sa.String(length=120), nullable=False),
        sa.Column("record_grain", sa.String(length=160), nullable=False),
        sa.Column("provenance_rule", JSONB, nullable=False),
        sa.Column("identity_field_codes", JSONB, nullable=False),
        sa.Column("dimension_field_codes", JSONB, nullable=False),
        sa.Column("measure_definitions", JSONB, nullable=False),
        sa.Column("time_dimensions", JSONB, nullable=False),
        sa.Column("status_dimensions", JSONB, nullable=False),
        sa.Column("sensitive_field_policies", JSONB, nullable=False),
        sa.Column("conflict_policy", JSONB, nullable=False),
        sa.Column("catalog_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("definition_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "code",
            "version",
            name="uq_query_fact_set_definitions_code_version",
        ),
        sa.UniqueConstraint(
            "definition_fingerprint",
            name="uq_query_fact_set_definitions_definition_fingerprint",
        ),
    )
    op.create_index(
        "ix_query_fact_set_definitions_status",
        "query_fact_set_definitions",
        ["status"],
    )
    op.create_index(
        "ix_query_fact_set_definitions_record_type",
        "query_fact_set_definitions",
        ["record_type"],
    )
    op.create_table(
        "semantic_manifest_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("fact_set_code", sa.String(length=160), nullable=False),
        sa.Column("fact_set_version", sa.Integer(), nullable=False),
        sa.Column("root_entity", sa.String(length=160), nullable=False),
        sa.Column("entities", JSONB, nullable=False),
        sa.Column("dimensions", JSONB, nullable=False),
        sa.Column("measures", JSONB, nullable=False),
        sa.Column("relationships", JSONB, nullable=False),
        sa.Column("allowed_join_paths", JSONB, nullable=False),
        sa.Column("max_join_depth", sa.Integer(), nullable=False),
        sa.Column("deduplication_policy", JSONB, nullable=False),
        sa.Column("default_time_policy", JSONB, nullable=False),
        sa.Column("evidence_policy", JSONB, nullable=False),
        sa.Column("catalog_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("manifest_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "code",
            "version",
            name="uq_semantic_manifest_definitions_code_version",
        ),
        sa.UniqueConstraint(
            "manifest_fingerprint",
            name="uq_semantic_manifest_definitions_manifest_fingerprint",
        ),
    )
    op.create_index(
        "ix_semantic_manifest_definitions_status",
        "semantic_manifest_definitions",
        ["status"],
    )
    op.create_index(
        "ix_semantic_manifest_definitions_fact_set_code",
        "semantic_manifest_definitions",
        ["fact_set_code"],
    )

    op.drop_constraint(
        "uq_metric_definitions_code",
        "metric_definitions",
        type_="unique",
    )
    op.add_column(
        "metric_definitions",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "metric_definitions",
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="published",
        ),
    )
    for name, type_ in (
        ("fact_set_code", sa.String(length=160)),
        ("fact_set_version", sa.Integer()),
        ("semantic_manifest_code", sa.String(length=160)),
        ("semantic_manifest_version", sa.Integer()),
        ("record_type", sa.String(length=120)),
        ("record_grain", sa.String(length=160)),
    ):
        op.add_column(
            "metric_definitions",
            sa.Column(name, type_, nullable=True),
        )
    op.add_column(
        "metric_definitions",
        sa.Column(
            "additivity",
            sa.String(length=32),
            nullable=False,
            server_default="additive",
        ),
    )
    for name, default in (
        ("allowed_group_fields", "[]"),
        ("forbidden_aggregation_dimensions", "[]"),
        ("identity_field_codes", "[]"),
        ("deduplication_policy", "{}"),
        ("status_filters", "[]"),
        ("time_policy", "{}"),
        ("evidence_policy", "{}"),
    ):
        op.add_column(
            "metric_definitions",
            sa.Column(
                name,
                JSONB,
                nullable=False,
                server_default=sa.text(f"'{default}'::jsonb"),
            ),
        )
    op.add_column(
        "metric_definitions",
        sa.Column(
            "null_policy",
            sa.String(length=32),
            nullable=False,
            server_default="exclude",
        ),
    )
    op.add_column(
        "metric_definitions",
        sa.Column(
            "conflict_policy",
            sa.String(length=32),
            nullable=False,
            server_default="reject",
        ),
    )
    op.add_column(
        "metric_definitions",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_metric_definitions_code_version",
        "metric_definitions",
        ["code", "version"],
    )
    op.create_index(
        "ix_metric_definitions_status",
        "metric_definitions",
        ["status"],
    )
    op.create_index(
        "ix_metric_definitions_fact_set_code",
        "metric_definitions",
        ["fact_set_code"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metric_definitions_fact_set_code",
        table_name="metric_definitions",
    )
    op.drop_index("ix_metric_definitions_status", table_name="metric_definitions")
    op.drop_constraint(
        "uq_metric_definitions_code_version",
        "metric_definitions",
        type_="unique",
    )
    for name in (
        "published_at",
        "evidence_policy",
        "conflict_policy",
        "null_policy",
        "time_policy",
        "status_filters",
        "deduplication_policy",
        "identity_field_codes",
        "forbidden_aggregation_dimensions",
        "allowed_group_fields",
        "additivity",
        "record_grain",
        "record_type",
        "semantic_manifest_version",
        "semantic_manifest_code",
        "fact_set_version",
        "fact_set_code",
        "status",
        "version",
    ):
        op.drop_column("metric_definitions", name)
    op.create_unique_constraint(
        "uq_metric_definitions_code",
        "metric_definitions",
        ["code"],
    )
    op.drop_table("semantic_manifest_definitions")
    op.drop_table("query_fact_set_definitions")
