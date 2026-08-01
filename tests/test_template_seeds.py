import hashlib

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    DocumentTemplate,
    SemanticField,
    SemanticFieldVersion,
    TemplateProposal,
    TemplateVersion,
)
from village_insight.templates.seeds import (
    generate_seed_manifest,
    import_pending_seed_manifest,
)


def _cluster(fingerprint: str, header: str) -> dict[str, object]:
    return {
        "layout_fingerprint": fingerprint,
        "source_file_count": 1,
        "unique_content_count": 1,
        "representative_path": f"/evidence/{header}.xlsx",
        "representative_evidence": {
            "source_sha256": hashlib.sha256(header.encode()).hexdigest(),
            "parser_name": "test",
            "parser_version": "test",
            "header_columns": [
                {
                    "source_column_id": f"column:{header}",
                    "header_path": [header],
                    "evidence_cell_ids": [f"cell:{header}"],
                }
            ],
            "layout_candidates": [],
        },
        "members": [],
    }


def test_seed_manifest_reuses_catalog_and_preserves_review_boundary() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    exact_fingerprint = hashlib.sha256(b"exact").hexdigest()
    new_fingerprint = hashlib.sha256(b"new").hexdigest()
    with Session(engine) as database:
        name = SemanticField(code="person.name", published_version=1)
        name.versions.append(
            SemanticFieldVersion(
                version=1,
                name="姓名",
                layer="base",
                data_type="text",
                status="published",
            )
        )
        template = DocumentTemplate(code="people", published_version=1)
        template.versions.append(
            TemplateVersion(
                version=1,
                name="人员",
                status="published",
                layout_fingerprint=exact_fingerprint,
                definition={"field_bindings": []},
            )
        )
        database.add_all([name, template])
        database.flush()
        report = {
            "summary": {"source_file_count": 2},
            "clusters": [
                _cluster(exact_fingerprint, "姓名"),
                _cluster(new_fingerprint, "未知字段"),
            ],
        }

        manifest = generate_seed_manifest(database, report)
        first_import = import_pending_seed_manifest(database, manifest)
        second_import = import_pending_seed_manifest(database, manifest)
        proposals = list(database.query(TemplateProposal))

    assert manifest["summary"]["published_match_cluster_count"] == 1
    assert manifest["summary"]["pending_review_cluster_count"] == 1
    exact, pending = manifest["template_seeds"]
    assert exact["review_status"] == "published_match"
    assert exact["field_decisions"][0]["suggested_semantic_field_code"] == "person.name"
    assert pending["review_status"] == "pending"
    assert pending["field_decisions"][0]["action"] == "review_required"
    assert first_import == {
        "created": 1,
        "existing": 0,
        "skipped_published": 1,
        "superseded": 0,
    }
    assert second_import == {
        "created": 0,
        "existing": 1,
        "skipped_published": 1,
        "superseded": 0,
    }
    assert len(proposals) == 1
