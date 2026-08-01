import hashlib
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from village_insight.db.base import Base
from village_insight.db.models import (
    ProposalStatus,
    SemanticField,
    SemanticFieldVersion,
    TemplateProposal,
    TemplateStatus,
)
from village_insight.templates.field_semantics import (
    analyze_header_path,
    equivalent_semantic_labels,
    looks_like_observed_value_header,
    normalize_role_code,
    semantic_candidate_is_compatible,
    semantic_identity,
)
from village_insight.templates.four_layer_seeds import (
    _looks_like_observed_value,
    build_four_layer_seed_package,
    import_review_packages,
    validate_package,
)


def _cluster(
    *,
    root: Path,
    village: str,
    title: str,
    leaf: str,
    source_column_id: str,
) -> dict[str, object]:
    source_path = str(root / village / f"{title}.xlsx")
    fingerprint = hashlib.sha256(source_path.encode()).hexdigest()
    region_id = f"{fingerprint}:sheet:0:region:1"
    header_id = f"{fingerprint}:sheet:0:header:1"
    return {
        "layout_fingerprint": fingerprint,
        "source_file_count": 1,
        "unique_content_count": 1,
        "representative_path": source_path,
        "source_paths": [source_path],
        "header_variants": [[title, leaf]],
        "members": [],
        "representative_evidence": {
            "source_sha256": fingerprint,
            "header_columns": [
                {
                    "region_id": region_id,
                    "header_id": header_id,
                    "source_column_id": source_column_id,
                    "header_path": [title, leaf],
                    "header_rows": [1, 2],
                    "column": 1,
                }
            ],
            "layout_candidates": [
                {
                    "region_id": region_id,
                    "header_id": header_id,
                    "data_start_row": 3,
                    "confidence": 1.0,
                }
            ],
        },
    }


def test_header_semantics_separates_base_field_from_role_and_title() -> None:
    head = analyze_header_path(["2026年低保发放清册", "户主身份证号码"])
    applicant = analyze_header_path(["申请信息", "申请人身份证号"])

    assert head.base_label == "身份证号"
    assert head.concept_key == "person.identity_number"
    assert head.role == "household_head"
    assert applicant.base_label == "身份证号"
    assert applicant.role == "applicant"
    assert semantic_identity(
        header_path=["2026年低保发放清册", "户主身份证号码"],
        domain="social_security",
    ) == semantic_identity(
        header_path=["矛盾调解申请", "申请人身份证号"],
        domain="governance",
    )


def test_ambiguous_measure_keeps_business_context() -> None:
    payment = semantic_identity(
        header_path=["补贴发放", "金额"],
        domain="finance",
    )
    arrears = semantic_identity(
        header_path=["欠费信息", "金额"],
        domain="finance",
    )

    assert payment != arrears
    assert payment["qualifier"] == "补贴发放"
    assert arrears["qualifier"] == "欠费信息"


def test_relationship_to_household_head_is_a_field_not_a_role_variant() -> None:
    relationship = analyze_header_path(["与户主关系"])
    compared_relationship = analyze_header_path(["与户主关系（与派出所人口比对）"])

    assert relationship.role is None
    assert compared_relationship.role is None
    assert relationship.base_label == "与户主关系"


def test_known_concept_expands_cross_file_aliases() -> None:
    assert {
        "联系电话",
        "电话号码",
        "手机号",
        "手机号码",
    } <= equivalent_semantic_labels("电话号码")
    assert analyze_header_path(["农户姓名"]).concept_key == "person.name"


def test_metadata_parent_does_not_assign_contact_role_to_unrelated_columns() -> None:
    unrelated = analyze_header_path(["联系人：张三 联系电话：123", "项目名称"])
    contact_name = analyze_header_path(["联系人", "姓名"])

    assert unrelated.role is None
    assert contact_name.role == "contact"


def test_household_and_person_variants_reuse_global_fields_with_roles() -> None:
    actual_head = analyze_header_path(["实际户主"])
    mother = analyze_header_path(["未成年人母亲"])
    guardian = analyze_header_path(["监护照料人"])

    assert (actual_head.concept_key, actual_head.role) == (
        "person.name",
        "household_head",
    )
    assert (mother.concept_key, mother.role) == ("person.name", "mother")
    assert (guardian.concept_key, guardian.role) == ("person.name", "guardian")
    assert semantic_identity(
        header_path=["家庭人口"],
        domain="population",
    ) == semantic_identity(
        header_path=["家庭成员数"],
        domain="assistance",
    )


def test_gendered_parties_and_guardians_get_stable_semantic_roles() -> None:
    female = analyze_header_path(["婚姻登记", "女方", "姓名"])
    male = analyze_header_path(["男方身份证号码"])
    guardian = analyze_header_path(["监护人身份证号码"])

    assert (female.concept_key, female.role) == ("person.name", "female_party")
    assert (male.concept_key, male.role) == (
        "person.identity_number",
        "male_party",
    )
    assert (guardian.concept_key, guardian.role) == (
        "person.identity_number",
        "guardian",
    )


def test_semantic_candidate_compatibility_rejects_substring_only_reuse() -> None:
    assert (
        semantic_candidate_is_compatible(
            header_path=["土地信息", "确权面积"],
            candidate_labels=["面积"],
            reasons=["semantic_label_overlap", "data_type"],
        )
        is False
    )
    assert (
        semantic_candidate_is_compatible(
            header_path=["家庭成员", "监护人身份证号码"],
            candidate_labels=["身份证号"],
            reasons=["normalized_base_alias", "data_type"],
        )
        is True
    )
    assert (
        semantic_candidate_is_compatible(
            header_path=["土地信息", "确权面积"],
            candidate_labels=["面积"],
            reasons=["full_header_path"],
        )
        is True
    )


def test_role_code_normalization_rejects_semantic_labels_and_keeps_structure() -> None:
    assert normalize_role_code("recordidentifier") is None
    assert normalize_role_code("primaryContact") == "contact"
    assert normalize_role_code("duplicate_2") == "duplicate_2"
    assert normalize_role_code("户主") == "household_head"


def test_seed_header_quality_gate_separates_labels_from_observed_values() -> None:
    assert _looks_like_observed_value(["就业单位名称"]) is False
    assert _looks_like_observed_value(["联系电话"]) is False
    assert _looks_like_observed_value(["行政村"]) is False
    assert _looks_like_observed_value(["河西街道办事处环卫站"]) is True
    assert _looks_like_observed_value(["美高酒店餐饮管理有限公司"]) is True
    assert _looks_like_observed_value(["52222119850414321X"]) is True
    assert _looks_like_observed_value(["2026年7月29日"]) is True
    assert _looks_like_observed_value(["理化乡矛盾纠纷管理台账"]) is True
    assert looks_like_observed_value_header(["行政村"]) is False
    assert looks_like_observed_value_header(["8月"]) is False
    assert looks_like_observed_value_header(["123456789012345678"]) is True


def test_seed_generator_reuses_published_base_field_with_normalized_role(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        field = SemanticField(code="person.name", published_version=1)
        field.versions.append(
            SemanticFieldVersion(
                version=1,
                name="姓名",
                aliases=["人员姓名"],
                layer="base",
                data_type="text",
                status=TemplateStatus.PUBLISHED,
            )
        )
        database.add(field)
        database.flush()
        report = {
            "root": str(tmp_path),
            "clusters": [
                _cluster(
                    root=tmp_path,
                    village="甲村",
                    title="2026年低保发放清册",
                    leaf="户主姓名",
                    source_column_id="column:head",
                ),
                _cluster(
                    root=tmp_path,
                    village="乙村",
                    title="矛盾纠纷申请表",
                    leaf="申请人姓名",
                    source_column_id="column:applicant",
                ),
            ],
            "failures": [],
        }

        package = build_four_layer_seed_package(database, report)

    bindings = [
        binding for region in package["region_templates"] for binding in region["field_bindings"]
    ]
    assert {binding["semantic_field_code"] for binding in bindings} == {"person.name"}
    assert {binding["role"] for binding in bindings} == {
        "household_head",
        "applicant",
    }
    assert package["summary"]["new_field_review_count"] == 0


def test_seed_generator_groups_unknown_person_field_across_titles_and_roles(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        report = {
            "root": str(tmp_path),
            "clusters": [
                _cluster(
                    root=tmp_path,
                    village="甲村",
                    title="2026年低保发放清册",
                    leaf="户主身份证号码",
                    source_column_id="column:head",
                ),
                _cluster(
                    root=tmp_path,
                    village="乙村",
                    title="矛盾纠纷申请表",
                    leaf="申请人身份证号",
                    source_column_id="column:applicant",
                ),
            ],
            "failures": [],
        }

        package = build_four_layer_seed_package(database, report)

    fields = package["semantic_fields"]
    assert len(fields) == 1
    assert fields[0]["name"] == "身份证号"
    assert set(fields[0]["roles"]) == {"household_head", "applicant"}
    assert len(fields[0]["header_paths"]) == 2


def test_seed_generator_routes_value_heavy_region_to_structure_review(
    tmp_path: Path,
) -> None:
    cluster = _cluster(
        root=tmp_path,
        village="甲村",
        title="就业补贴申请表",
        leaf="就业单位名称",
        source_column_id="column:label",
    )
    region_id = str(
        cluster["representative_evidence"]["header_columns"][0]["region_id"]  # type: ignore[index]
    )
    cluster["representative_evidence"]["header_columns"].extend(  # type: ignore[index]
        [
            {
                "region_id": region_id,
                "source_column_id": "column:value-1",
                "header_path": ["河西街道办事处环卫站"],
                "header_rows": [2],
                "column": 2,
            },
            {
                "region_id": region_id,
                "source_column_id": "column:value-2",
                "header_path": ["52222119850414321X"],
                "header_rows": [2],
                "column": 3,
            },
        ]
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        package = build_four_layer_seed_package(
            database,
            {
                "root": str(tmp_path),
                "clusters": [cluster],
                "failures": [],
            },
        )

    assert package["semantic_fields"] == []
    assert package["region_templates"] == []
    assert package["workbook_routes"][0]["status"] == "admin_review"
    assert package["workbook_routes"][0]["unresolved_regions"][0]["requires_hermes"]
    assert package["summary"]["unresolved_region_count"] == 1
    validation = validate_package(package)
    assert validation["safe_to_import_pending"] is True
    assert validation["safe_to_publish"] is False
    assert validation["reference_integrity"] == {
        "missing_fields": [],
        "missing_regions": [],
        "missing_sheets": [],
    }


def test_v3_review_import_supersedes_older_four_layer_pending_seed(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        database.add(
            TemplateProposal(
                idempotency_key="four-layer-v2:old",
                source="bootstrap",
                model_name="codex",
                prompt_version="codex-four-layer-bootstrap/v1",
                proposal={
                    "contract_version": "four-layer-template-seed/v2",
                    "generation_sha256": "old",
                },
                status=ProposalStatus.PENDING,
            )
        )
        package = build_four_layer_seed_package(
            database,
            {
                "root": str(tmp_path),
                "clusters": [
                    _cluster(
                        root=tmp_path,
                        village="甲村",
                        title="人员清册",
                        leaf="姓名",
                        source_column_id="column:name",
                    )
                ],
                "failures": [],
            },
        )
        first = import_review_packages(database, package)
        second = import_review_packages(database, package)
        proposals = list(database.query(TemplateProposal))

    assert first == {"created": 1, "existing": 0, "superseded": 1}
    assert second == {"created": 0, "existing": 1, "superseded": 0}
    assert sorted(proposal.status for proposal in proposals) == [
        ProposalStatus.PENDING,
        ProposalStatus.REJECTED,
    ]
