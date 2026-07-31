"""Separate physical evidence storage from formal business import status.

Revision ID: 20260729_0017
Revises: 20260729_0016
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0017"
down_revision: str | None = "20260729_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_items",
        sa.Column(
            "evidence_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "ingestion_items",
        sa.Column(
            "formal_import_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.execute(
        """
        UPDATE ingestion_items AS item
        SET evidence_status = 'stored'
        WHERE EXISTS (
          SELECT 1 FROM document_profiles AS profile
          WHERE profile.item_id = item.id
        )
        """
    )
    op.execute(
        """
        UPDATE ingestion_items
        SET evidence_status = 'failed'
        WHERE status = 'failed' AND evidence_status = 'pending'
        """
    )
    op.execute(
        """
        UPDATE ingestion_items AS item
        SET formal_import_status = execution.status
        FROM approved_import_plans AS plan
        JOIN import_executions AS execution
          ON execution.approved_plan_id = plan.id
        WHERE plan.item_id = item.id
          AND execution.status IN ('completed', 'partial')
        """
    )
    op.execute(
        """
        UPDATE ingestion_items
        SET formal_import_status = 'imported'
        WHERE formal_import_status = 'completed'
        """
    )
    op.execute(
        """
        UPDATE ingestion_items AS item
        SET formal_import_status = 'pending_rebuild'
        WHERE EXISTS (
          SELECT 1
          FROM dataset_records AS record
          WHERE record.item_id = item.id
            AND record.mapping_status = 'pending_rebuild'
        )
        """
    )
    op.execute(
        """
        UPDATE ingestion_items
        SET formal_import_status = CASE
          WHEN status = 'failed' THEN 'failed'
          WHEN status = 'materializing' THEN 'materializing'
          WHEN status IN ('needs_review', 'recognizing', 'ready') THEN 'needs_review'
          ELSE formal_import_status
        END
        WHERE formal_import_status = 'pending'
        """
    )
    op.alter_column("ingestion_items", "evidence_status", server_default=None)
    op.alter_column(
        "ingestion_items",
        "formal_import_status",
        server_default=None,
    )

    op.execute(
        """
        WITH counts AS (
          SELECT
            batch_id,
            count(*) FILTER (
              WHERE formal_import_status = 'imported'
            ) AS imported,
            count(*) FILTER (
              WHERE formal_import_status IN ('partial', 'pending_rebuild')
            ) AS partial,
            count(*) FILTER (
              WHERE formal_import_status = 'failed'
            ) AS failed
          FROM ingestion_items
          GROUP BY batch_id
        )
        UPDATE ingestion_batches AS batch
        SET
          completed_files = counts.imported,
          failed_files = counts.failed,
          status = CASE
            WHEN counts.imported + counts.partial + counts.failed = 0
              THEN 'pending'
            WHEN counts.imported + counts.partial + counts.failed < batch.total_files
              THEN 'running'
            WHEN counts.failed = batch.total_files
              THEN 'failed'
            WHEN counts.partial > 0 OR counts.failed > 0
              THEN 'partial'
            ELSE 'completed'
          END
        FROM counts
        WHERE counts.batch_id = batch.id
        """
    )


def downgrade() -> None:
    op.drop_column("ingestion_items", "formal_import_status")
    op.drop_column("ingestion_items", "evidence_status")
