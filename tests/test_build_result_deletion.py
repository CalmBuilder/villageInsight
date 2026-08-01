import hashlib
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.build_result_deletion import (
    delete_build_result,
    request_build_result_deletion,
    validate_build_result_deletion_policy,
)
from village_insight.db.base import Base
from village_insight.db.models import (
    ApprovedImportPlan,
    DatasetRecord,
    DocumentProfile,
    DocumentSheetCatalog,
    DocumentTemplate,
    FieldMatch,
    GovernanceResolution,
    HermesRecognitionCache,
    HermesRecognitionRecord,
    ImportExecution,
    IngestionBatch,
    IngestionItem,
    Job,
    JobStatus,
    QualityIssue,
    RecordIndexValue,
    RecordValueLineage,
    RegionTemplateMatch,
    SheetCompositionMatch,
    TemplateMatch,
    TemplateProposal,
    TemplateStatus,
    TemplateVersion,
    WorkbookRouteMatch,
)
from village_insight.reimport import ReimportError, reset_item_for_reimport


def test_build_result_deletion_removes_all_exclusive_products_and_keeps_evidence(
    tmp_path,
) -> None:
    validate_build_result_deletion_policy()
    source_path = tmp_path / "source.xlsx"
    source_path.write_bytes(b"immutable-source")
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        batch = IngestionBatch(
            name="删除回归",
            total_files=1,
            completed_files=1,
            status="completed",
        )
        database.add(batch)
        database.flush()
        item = IngestionItem(
            batch_id=batch.id,
            original_name="新版台账.xlsx",
            source_path=str(source_path),
            source_sha256=source_sha256,
            size_bytes=128,
            status="imported",
            evidence_status="stored",
            formal_import_status="imported",
        )
        database.add(item)
        database.flush()
        profile = DocumentProfile(
            item_id=item.id,
            contract_version="profile/v1",
            source_sha256=item.source_sha256,
            parser_name="test",
            parser_version="1",
            profile={"sheets": []},
        )
        template = DocumentTemplate(code=f"delete.test.{item.id.hex}")
        version = TemplateVersion(
            version=1,
            name="临时模板",
            status=TemplateStatus.ADMIN_REVIEW,
            layout_fingerprint="b" * 64,
            definition={
                "domain": "test",
                "region_kind": "table",
                "record_type": "row",
                "record_grain": "one_row_per_record",
                "field_bindings": [],
            },
            source="hermes_provisional",
            source_metadata={"source_item_id": str(item.id)},
        )
        orphan_version = TemplateVersion(
            version=2,
            name="历史临时模板",
            status=TemplateStatus.ADMIN_REVIEW,
            layout_fingerprint="c" * 64,
            definition=version.definition,
            source="hermes_provisional",
            source_metadata={
                "source_item_id": str(item.id),
                "proposal_id": str(uuid.uuid4()),
            },
        )
        template.versions.extend([version, orphan_version])
        proposal = TemplateProposal(
            source="hermes",
            source_item_id=item.id,
            proposal={},
            status="pending",
        )
        database.add_all([profile, template, proposal])
        database.flush()
        version.source_metadata = {
            "source_item_id": str(item.id),
            "proposal_id": str(proposal.id),
        }
        plan = ApprovedImportPlan(
            item_id=item.id,
            source_sha256=item.source_sha256,
            profile_contract_version="profile/v1",
            layout_fingerprint="b" * 64,
            plan_source="hermes_provisional",
            proposal_id=proposal.id,
            template_id=template.id,
            template_version=version.version,
            layout_plan={},
            field_mappings=[],
            approved_by="test",
        )
        database.add(plan)
        database.flush()
        resolution = GovernanceResolution(
            proposal_id=proposal.id,
            item_id=item.id,
            domain="test",
            record_type="row",
            record_grain="one_row_per_record",
            status="committed",
            region_template_refs=[],
            approved_plan_id=plan.id,
            comment="已治理提案必须跨重导保留",
        )
        database.add(resolution)
        database.flush()
        record = DatasetRecord(
            ingestion_batch_id=batch.id,
            approved_plan_id=plan.id,
            item_id=item.id,
            template_id=template.id,
            template_version=1,
            record_type="row",
            sheet_id="sheet-1",
            region_id="region-1",
            source_row=2,
            raw_data={},
            semantic_data={},
        )
        database.add(record)
        database.flush()
        index_value = RecordIndexValue(
            record_id=record.id,
            semantic_field_code="test.value",
            semantic_field_version=1,
            data_type="text",
            text_value="value",
        )
        database.add(index_value)
        database.flush()
        cache = HermesRecognitionCache(
            cache_key="c" * 64,
            hermes_version="1",
            prompt_version="1",
            schema_version="1",
            provider="test",
            model="test",
            request_payload={},
            response_payload={},
        )
        database.add_all(
            [
                RecordValueLineage(
                    record_index_value_id=index_value.id,
                    source_sha256=item.source_sha256,
                    sheet_id="sheet-1",
                    source_cell_id="cell-1",
                    coordinate="A2",
                    raw_value="value",
                    display_value="value",
                    normalizer="text/v1",
                ),
                QualityIssue(
                    item_id=item.id,
                    approved_plan_id=plan.id,
                    code="TEST",
                    severity="warning",
                    message="derived",
                    evidence={},
                ),
                ImportExecution(
                    approved_plan_id=plan.id,
                    status="completed",
                    record_count=1,
                    value_count=1,
                ),
                DocumentSheetCatalog(
                    item_id=item.id,
                    sheet_id="sheet-1",
                    sheet_name="Sheet1",
                    sheet_order=0,
                    region_count=1,
                ),
                TemplateMatch(
                    item_id=item.id,
                    source_sha256=item.source_sha256,
                    profile_contract_version="profile/v1",
                    layout_fingerprint="b" * 64,
                    match_type="none",
                    score_basis_points=0,
                    differences={},
                    requires_hermes=True,
                    matcher_version="1",
                ),
                RegionTemplateMatch(
                    item_id=item.id,
                    sheet_id="sheet-1",
                    region_id="region-1",
                    header_id="header-1",
                    region_fingerprint="d" * 64,
                    match_type="none",
                    score_basis_points=0,
                    differences={},
                    requires_hermes=True,
                    matcher_version="1",
                ),
                FieldMatch(
                    item_id=item.id,
                    sheet_id="sheet-1",
                    region_id="region-1",
                    header_id="header-1",
                    source_column_id="column-1",
                    header_path=["字段"],
                    match_type="none",
                    score_basis_points=0,
                    context={},
                    differences={},
                    requires_hermes=True,
                    matcher_version="1",
                ),
                SheetCompositionMatch(
                    item_id=item.id,
                    sheet_id="sheet-1",
                    match_type="none",
                    score_basis_points=0,
                    total_slots=1,
                    matched_slots=0,
                    coverage_basis_points=0,
                    differences={},
                    matcher_version="1",
                ),
                WorkbookRouteMatch(
                    item_id=item.id,
                    match_type="none",
                    score_basis_points=0,
                    total_slots=1,
                    matched_slots=0,
                    coverage_basis_points=0,
                    differences={},
                    matcher_version="1",
                ),
                cache,
            ]
        )
        database.flush()
        database.add(
            HermesRecognitionRecord(
                item_id=item.id,
                cache_key=cache.cache_key,
                call_performed=True,
                input_field_count=1,
                provider="test",
                model="test",
            )
        )
        database.add(
            Job(
                item_id=item.id,
                batch_id=batch.id,
                tenant_id=item.tenant_id,
                administrative_unit_id=item.administrative_unit_id,
                requested_by_user_id=item.created_by_user_id,
                kind="MATERIALIZE_FILE",
                payload={"item_id": str(item.id), "plan_id": str(plan.id)},
                idempotency_key=f"materialize:{plan.id}:pending",
            )
        )
        database.commit()

        deletion = request_build_result_deletion(
            database,
            item_id=item.id,
            requested_by_user_id=uuid.uuid4(),
        )
        database.commit()
        old_job = database.query(Job).filter(Job.kind == "MATERIALIZE_FILE").one()
        assert old_job.status == JobStatus.CANCELLED

        completed = delete_build_result(
            database,
            item_id=item.id,
            deletion_id=deletion.id,
        )
        database.commit()

        assert completed.status == "completed"
        assert set(completed.deleted_counts) == {
            "record_value_lineage",
            "record_index_values",
            "dataset_records",
            "quality_issues",
            "import_executions",
            "hermes_recognition_records",
            "field_matches",
            "region_template_matches",
            "sheet_composition_matches",
            "workbook_route_matches",
            "template_matches",
            "document_sheet_catalog",
        }
        for model in (
            RecordValueLineage,
            RecordIndexValue,
            DatasetRecord,
            QualityIssue,
            ImportExecution,
            HermesRecognitionRecord,
            FieldMatch,
            RegionTemplateMatch,
            SheetCompositionMatch,
            WorkbookRouteMatch,
            TemplateMatch,
            DocumentSheetCatalog,
        ):
            assert database.query(model).count() == 0
        assert database.get(DocumentProfile, item.id) is not None
        assert database.get(HermesRecognitionCache, cache.cache_key) is not None
        assert database.get(TemplateProposal, proposal.id).build_result_retired_at is not None
        assert database.get(ApprovedImportPlan, plan.id).build_result_retired_at is not None
        assert database.get(TemplateVersion, version.id).build_result_retired_at is not None
        assert database.get(TemplateVersion, orphan_version.id).build_result_retired_at is not None
        database.refresh(item)
        assert item.status == "result_deleted"
        assert item.formal_import_status == "deleted"
        assert item.build_result_deletion_status == "deleted"
        database.refresh(batch)
        assert batch.deleted_files == 1

        with pytest.raises(ReimportError, match="必须显式选择恢复"):
            reset_item_for_reimport(database, item.id)
        restored_by = uuid.uuid4()
        reset_item_for_reimport(
            database,
            item.id,
            restore_deleted=True,
            restored_by_user_id=restored_by,
        )
        database.flush()
        database.refresh(item)
        database.refresh(completed)
        database.refresh(batch)
        assert item.status == "pending"
        assert item.formal_import_status == "pending"
        assert item.build_result_deletion_status == "active"
        assert item.build_result_deleted_at is None
        assert completed.status == "restored"
        assert completed.manifest["restoration"]["restored_by_user_id"] == str(restored_by)
        assert batch.deleted_files == 0
        database.refresh(resolution)
        assert database.get(TemplateProposal, proposal.id) is not None
        assert resolution.approved_plan_id is None
