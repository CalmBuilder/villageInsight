"""Add versioned fields, template lifecycle and proposal separation.

Revision ID: 20260728_0005
Revises: 20260728_0004
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0005"
down_revision: str | None = "20260728_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "semantic_fields",
        sa.Column("published_version", sa.Integer(), nullable=True),
    )
    op.create_table(
        "semantic_field_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("field_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("layer", sa.String(length=40), nullable=False),
        sa.Column("data_type", sa.String(length=40), nullable=False),
        sa.Column("unit_dimension", sa.String(length=80), nullable=True),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("validators", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["semantic_fields.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "field_id",
            "version",
            name="uq_semantic_field_versions_version",
        ),
    )
    op.create_index(
        "ix_semantic_field_versions_field_id",
        "semantic_field_versions",
        ["field_id"],
        unique=False,
    )
    op.execute(
        """
        INSERT INTO semantic_field_versions (
            id, field_id, version, name, description, layer, data_type,
            unit_dimension, aliases, validators, status, created_at
        )
        SELECT
            id, id, 1, name, description, 'domain', data_type,
            unit, aliases, '[]'::json,
            CASE WHEN enabled THEN 'published' ELSE 'deprecated' END,
            created_at
        FROM semantic_fields
        """
    )
    op.execute(
        "UPDATE semantic_fields SET published_version = 1 WHERE enabled = true"
    )
    for column in ("name", "description", "data_type", "unit", "aliases", "enabled"):
        op.drop_column("semantic_fields", column)

    op.create_table(
        "semantic_field_review_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("field_version_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=160), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["field_version_id"],
            ["semantic_field_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_semantic_field_review_events_field_version_id",
        "semantic_field_review_events",
        ["field_version_id"],
        unique=False,
    )

    op.alter_column(
        "document_templates",
        "active_version",
        new_column_name="published_version",
    )
    op.add_column(
        "template_versions",
        sa.Column("name", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "template_versions",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "template_versions",
        sa.Column("status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "template_versions",
        sa.Column("layout_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "template_versions",
        sa.Column("source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "template_versions",
        sa.Column("source_metadata", sa.JSON(), nullable=True),
    )
    op.execute(
        """
        UPDATE template_versions AS version
        SET
            name = template.name,
            description = template.description,
            status = CASE template.status
                WHEN 'active' THEN 'published'
                WHEN 'archived' THEN 'deprecated'
                ELSE 'draft'
            END,
            layout_fingerprint = repeat(md5(version.definition::text), 2),
            source = 'manual',
            source_metadata = '{}'::json
        FROM document_templates AS template
        WHERE template.id = version.template_id
        """
    )
    op.execute(
        """
        INSERT INTO template_versions (
            id, template_id, version, name, description, status,
            layout_fingerprint, definition, source, source_metadata, created_at
        )
        SELECT
            template.id,
            template.id,
            1,
            template.name,
            template.description,
            CASE template.status
                WHEN 'active' THEN 'published'
                WHEN 'archived' THEN 'deprecated'
                ELSE 'draft'
            END,
            repeat(md5(template.code), 2),
            json_build_object(
                'contract_version', 'document-template/v1',
                'domain', 'unknown',
                'region_kind', 'table',
                'record_type', 'unknown',
                'record_grain', 'unknown',
                'field_bindings', json_build_array(),
                'data_row_rules', json_build_array(),
                'exclusion_rules', json_build_array(),
                'metric_codes', json_build_array()
            ),
            'manual',
            '{}'::json,
            template.created_at
        FROM document_templates AS template
        WHERE NOT EXISTS (
            SELECT 1
            FROM template_versions AS version
            WHERE version.template_id = template.id
        )
        """
    )
    for column in (
        "name",
        "description",
        "status",
        "layout_fingerprint",
        "source",
        "source_metadata",
    ):
        op.alter_column("template_versions", column, nullable=False)
    op.create_index(
        "ix_template_versions_layout_fingerprint",
        "template_versions",
        ["layout_fingerprint"],
        unique=False,
    )
    op.drop_column("template_versions", "review_note")
    op.drop_column("template_versions", "reviewed")
    for column in ("name", "description", "status"):
        op.drop_column("document_templates", column)

    op.create_table(
        "template_review_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=160), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["template_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_template_review_events_template_version_id",
        "template_review_events",
        ["template_version_id"],
        unique=False,
    )
    op.create_table(
        "template_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_item_id", sa.Uuid(), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("prompt_version", sa.String(length=80), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("proposal", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resolution_comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_item_id"],
            ["ingestion_items.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_template_proposals_source_item_id",
        "template_proposals",
        ["source_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_template_proposals_source_item_id",
        table_name="template_proposals",
    )
    op.drop_table("template_proposals")
    op.drop_index(
        "ix_template_review_events_template_version_id",
        table_name="template_review_events",
    )
    op.drop_table("template_review_events")

    op.add_column(
        "document_templates",
        sa.Column("status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "document_templates",
        sa.Column("name", sa.String(length=200), nullable=True),
    )
    op.execute(
        """
        UPDATE document_templates AS template
        SET
            name = version.name,
            description = version.description,
            status = CASE version.status
                WHEN 'published' THEN 'active'
                WHEN 'deprecated' THEN 'archived'
                ELSE 'draft'
            END
        FROM template_versions AS version
        WHERE version.template_id = template.id
          AND version.version = (
              SELECT max(latest.version)
              FROM template_versions AS latest
              WHERE latest.template_id = template.id
          )
        """
    )
    for column in ("name", "description", "status"):
        op.alter_column("document_templates", column, nullable=False)
    op.add_column(
        "template_versions",
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "template_versions",
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
    )
    op.drop_index(
        "ix_template_versions_layout_fingerprint",
        table_name="template_versions",
    )
    for column in (
        "source_metadata",
        "source",
        "layout_fingerprint",
        "status",
        "description",
        "name",
    ):
        op.drop_column("template_versions", column)
    op.alter_column(
        "document_templates",
        "published_version",
        new_column_name="active_version",
    )

    op.drop_index(
        "ix_semantic_field_review_events_field_version_id",
        table_name="semantic_field_review_events",
    )
    op.drop_table("semantic_field_review_events")
    op.add_column(
        "semantic_fields",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "semantic_fields",
        sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "semantic_fields",
        sa.Column("unit", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "semantic_fields",
        sa.Column("data_type", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "semantic_fields",
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "semantic_fields",
        sa.Column("name", sa.String(length=200), nullable=True),
    )
    op.execute(
        """
        UPDATE semantic_fields AS field
        SET
            name = version.name,
            description = version.description,
            data_type = version.data_type,
            unit = version.unit_dimension,
            aliases = version.aliases,
            enabled = version.status = 'published'
        FROM semantic_field_versions AS version
        WHERE version.field_id = field.id
          AND version.version = 1
        """
    )
    op.alter_column("semantic_fields", "name", nullable=False)
    op.alter_column("semantic_fields", "data_type", nullable=False)
    op.drop_index(
        "ix_semantic_field_versions_field_id",
        table_name="semantic_field_versions",
    )
    op.drop_table("semantic_field_versions")
    op.drop_column("semantic_fields", "published_version")
