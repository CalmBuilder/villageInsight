import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from village_insight.api.routes.questions import (
    ConversationRename,
    _history_for,
    _resolve_question_scope,
    _resolve_retry_run,
    _store_fact_results,
    delete_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
)
from village_insight.db.base import Base
from village_insight.db.models import (
    AdministrativeUnit,
    AdministrativeUnitType,
    MembershipRole,
    QuestionConversation,
    QuestionFactResult,
    QuestionRun,
    Tenant,
    TenantMembership,
    User,
)
from village_insight.identity import ROLE_PERMISSIONS, Principal


def _seed_scope() -> tuple[
    Session,
    Tenant,
    User,
    TenantMembership,
    AdministrativeUnit,
    AdministrativeUnit,
    AdministrativeUnit,
]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    database = Session(engine)
    tenant = Tenant(name="范围测试租户")
    user = User(
        username=f"scope-{uuid.uuid4()}",
        display_name="范围测试用户",
        password_hash="not-used",
    )
    database.add_all([tenant, user])
    database.flush()
    township = AdministrativeUnit(
        tenant_id=tenant.id,
        unit_type=AdministrativeUnitType.TOWNSHIP,
        name="测试乡镇",
    )
    database.add(township)
    database.flush()
    first = AdministrativeUnit(
        tenant_id=tenant.id,
        parent_id=township.id,
        unit_type=AdministrativeUnitType.VILLAGE,
        name="甲村",
    )
    second = AdministrativeUnit(
        tenant_id=tenant.id,
        parent_id=township.id,
        unit_type=AdministrativeUnitType.VILLAGE,
        name="乙村",
    )
    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        role=MembershipRole.TENANT_ADMIN,
    )
    database.add_all([first, second, membership])
    database.commit()
    return database, tenant, user, membership, township, first, second


def _tenant_principal(
    tenant: Tenant,
    user: User,
    membership: TenantMembership,
    township: AdministrativeUnit,
    *villages: AdministrativeUnit,
) -> Principal:
    return Principal(
        user=user,
        tenant=tenant,
        membership=membership,
        scope_unit=township,
        include_descendants=True,
        permissions=ROLE_PERMISSIONS[MembershipRole.TENANT_ADMIN],
        allowed_unit_ids=frozenset((township.id, *(village.id for village in villages))),
    )


def test_tenant_admin_can_select_all_villages_or_one_village() -> None:
    database, tenant, user, membership, township, first, second = _seed_scope()
    principal = Principal(
        user=user,
        tenant=tenant,
        membership=membership,
        scope_unit=township,
        include_descendants=True,
        permissions=ROLE_PERMISSIONS[MembershipRole.TENANT_ADMIN],
        allowed_unit_ids=frozenset((township.id, first.id, second.id)),
    )

    all_villages = _resolve_question_scope(database, principal, township.id)
    one_village = _resolve_question_scope(database, principal, second.id)

    assert all_villages.mode == "all_villages"
    assert set(all_villages.unit_ids) == {first.id, second.id}
    assert township.id not in all_villages.unit_ids
    assert one_village.mode == "village"
    assert one_village.unit_ids == (second.id,)


def test_question_scope_rejects_village_outside_tenant_admin_grant() -> None:
    database, tenant, user, membership, township, first, second = _seed_scope()
    principal = Principal(
        user=user,
        tenant=tenant,
        membership=membership,
        scope_unit=township,
        include_descendants=True,
        permissions=ROLE_PERMISSIONS[MembershipRole.TENANT_ADMIN],
        allowed_unit_ids=frozenset((township.id, first.id)),
    )

    with pytest.raises(HTTPException) as exc_info:
        _resolve_question_scope(database, principal, second.id)

    assert exc_info.value.status_code == 403


def test_village_operator_scope_is_fixed() -> None:
    database, tenant, user, membership, _township, first, second = _seed_scope()
    membership.role = MembershipRole.VILLAGE_OPERATOR
    database.commit()
    principal = Principal(
        user=user,
        tenant=tenant,
        membership=membership,
        scope_unit=first,
        include_descendants=False,
        permissions=ROLE_PERMISSIONS[MembershipRole.VILLAGE_OPERATOR],
        allowed_unit_ids=frozenset((first.id,)),
    )

    own_scope = _resolve_question_scope(database, principal, first.id)
    assert own_scope.unit_ids == (first.id,)

    with pytest.raises(HTTPException) as exc_info:
        _resolve_question_scope(database, principal, second.id)
    assert exc_info.value.status_code == 403


def test_fact_results_are_persisted_without_selecting_one_primary_result() -> None:
    database, tenant, user, _, township, _, _ = _seed_scope()
    run = QuestionRun(
        tenant_id=tenant.id,
        requested_by_user_id=user.id,
        scope_unit_id=township.id,
        include_descendants=True,
        question="统计测试",
        status="running",
        scope_snapshot={
            "source_item_fingerprint": "a" * 64,
            "source_item_ids": [],
        },
    )
    database.add(run)
    database.flush()
    result = {
        "status": "success",
        "tool": "execute_safe_query",
        "contract_version": "safe-query-answer/v1",
        "result_grade": "contract_query",
        "safe_query_plan": {"contract_version": "safe-query/v1"},
        "semantic_plan": {
            "fact_set_code": "population.registry",
            "fact_set_version": 1,
            "manifest_code": "population.registry",
            "manifest_version": 1,
            "semantic_plan_fingerprint": "b" * 64,
        },
        "rows": [],
        "evidence_summary": {
            "record_count": 2,
            "source_file_count": 1,
            "data_village_count": 1,
        },
    }

    candidate = {
        **result,
        "tool": "query_postgres",
        "acceptance_status": "candidate_only",
    }
    stored = _store_fact_results(
        database,
        run=run,
        tool_results=[result, candidate],
    )

    assert len(stored) == 2
    assert stored[0].accepted is True
    assert stored[1].accepted is False
    assert stored[0].result_grade == "contract_query"
    assert stored[0].fact_set_code == "population.registry"
    assert database.scalar(
        select(func.count()).select_from(QuestionFactResult)
    ) == 2


def test_retry_accepts_only_latest_owned_run_and_rebuilds_prior_history() -> None:
    database, tenant, user, membership, township, first, second = _seed_scope()
    principal = _tenant_principal(
        tenant,
        user,
        membership,
        township,
        first,
        second,
    )
    conversation = QuestionConversation(
        tenant_id=tenant.id,
        requested_by_user_id=user.id,
        scope_unit_id=township.id,
        source_item_id=None,
        include_descendants=True,
        title="人口查询",
    )
    database.add(conversation)
    database.flush()
    now = datetime.now(UTC)
    first_run = QuestionRun(
        conversation_id=conversation.id,
        tenant_id=tenant.id,
        requested_by_user_id=user.id,
        scope_unit_id=township.id,
        source_item_id=None,
        include_descendants=True,
        question="全村总人数是多少？",
        answer_text="原始回答",
        status="succeeded",
        created_at=now,
    )
    latest_run = QuestionRun(
        conversation_id=conversation.id,
        tenant_id=tenant.id,
        requested_by_user_id=user.id,
        scope_unit_id=township.id,
        source_item_id=None,
        include_descendants=True,
        question="其中女性有多少？",
        answer_text="最新回答",
        status="succeeded",
        created_at=now + timedelta(seconds=1),
    )
    database.add_all([first_run, latest_run])
    database.commit()

    with pytest.raises(HTTPException) as exc_info:
        _resolve_retry_run(
            database,
            principal,
            conversation,
            first_run.id,
        )
    assert exc_info.value.status_code == 409

    resolved = _resolve_retry_run(
        database,
        principal,
        conversation,
        latest_run.id,
    )
    history = _history_for(
        database,
        conversation.id,
        exclude_run_ids=(latest_run.id,),
    )

    assert resolved is latest_run
    assert history == [
        {"role": "user", "content": first_run.question},
        {"role": "assistant", "content": first_run.answer_text},
    ]


def test_conversation_list_and_history_are_paginated_without_losing_counts() -> None:
    database, tenant, user, membership, township, first, second = _seed_scope()
    principal = _tenant_principal(
        tenant,
        user,
        membership,
        township,
        first,
        second,
    )
    now = datetime.now(UTC)
    conversations = [
        QuestionConversation(
            tenant_id=tenant.id,
            requested_by_user_id=user.id,
            scope_unit_id=township.id,
            source_item_id=None,
            include_descendants=True,
            title=f"人口查询 {index}",
            updated_at=now + timedelta(minutes=index),
        )
        for index in range(3)
    ]
    database.add_all(conversations)
    database.flush()
    database.add_all(
        [
            QuestionRun(
                conversation_id=conversations[2].id,
                tenant_id=tenant.id,
                requested_by_user_id=user.id,
                scope_unit_id=township.id,
                source_item_id=None,
                include_descendants=True,
                question=f"第 {index} 轮",
                status="succeeded",
                completed_at=now + timedelta(seconds=index),
                created_at=now + timedelta(seconds=index),
            )
            for index in range(25)
        ]
    )
    database.commit()

    page = list_conversations(
        database=database,
        principal=principal,
        scope_unit_id=township.id,
        source_item_id=None,
        page=1,
        page_size=2,
        search="人口",
    )
    detail = get_conversation(
        conversation_id=conversations[2].id,
        database=database,
        principal=principal,
        run_offset=0,
        run_limit=20,
    )
    older = get_conversation(
        conversation_id=conversations[2].id,
        database=database,
        principal=principal,
        run_offset=20,
        run_limit=20,
    )

    assert page.total == 3
    assert page.total_pages == 2
    assert [item.title for item in page.items] == ["人口查询 2", "人口查询 1"]
    assert page.items[0].run_count == 25
    assert detail.run_total == 25
    assert detail.conversation.run_count == 25
    assert detail.has_more_before is True
    assert len(detail.runs) == 20
    assert older.has_more_before is False
    assert len(older.runs) == 5
    assert older.runs[-1].question == "第 4 轮"


def test_soft_delete_hides_conversation_but_keeps_run_evidence() -> None:
    database, tenant, user, membership, township, first, second = _seed_scope()
    principal = _tenant_principal(
        tenant,
        user,
        membership,
        township,
        first,
        second,
    )
    conversation = QuestionConversation(
        tenant_id=tenant.id,
        requested_by_user_id=user.id,
        scope_unit_id=township.id,
        source_item_id=None,
        include_descendants=True,
        title="需要清理的会话",
    )
    database.add(conversation)
    database.flush()
    run = QuestionRun(
        conversation_id=conversation.id,
        tenant_id=tenant.id,
        requested_by_user_id=user.id,
        scope_unit_id=township.id,
        source_item_id=None,
        include_descendants=True,
        question="全村总人数",
        status="succeeded",
        evidence=[{"record_count": 10}],
        completed_at=datetime.now(UTC),
    )
    database.add(run)
    database.commit()

    result = delete_conversation(
        conversation_id=conversation.id,
        database=database,
        principal=principal,
    )

    database.refresh(conversation)
    assert result.deleted == 1
    assert conversation.status == "deleted"
    assert database.scalar(
        select(func.count(QuestionRun.id)).where(
            QuestionRun.conversation_id == conversation.id
        )
    ) == 1


def test_running_conversation_cannot_be_deleted() -> None:
    database, tenant, user, membership, township, first, second = _seed_scope()
    principal = _tenant_principal(
        tenant,
        user,
        membership,
        township,
        first,
        second,
    )
    conversation = QuestionConversation(
        tenant_id=tenant.id,
        requested_by_user_id=user.id,
        scope_unit_id=township.id,
        source_item_id=None,
        include_descendants=True,
        title="正在运行",
    )
    database.add(conversation)
    database.flush()
    database.add(
        QuestionRun(
            conversation_id=conversation.id,
            tenant_id=tenant.id,
            requested_by_user_id=user.id,
            scope_unit_id=township.id,
            source_item_id=None,
            include_descendants=True,
            question="正在查询",
            status="running",
            completed_at=None,
        )
    )
    database.commit()

    with pytest.raises(HTTPException) as exc_info:
        delete_conversation(
            conversation_id=conversation.id,
            database=database,
            principal=principal,
        )

    assert exc_info.value.status_code == 409


def test_rename_conversation_changes_only_the_title() -> None:
    database, tenant, user, membership, township, first, second = _seed_scope()
    principal = _tenant_principal(
        tenant,
        user,
        membership,
        township,
        first,
        second,
    )
    conversation = QuestionConversation(
        tenant_id=tenant.id,
        requested_by_user_id=user.id,
        scope_unit_id=township.id,
        source_item_id=None,
        include_descendants=True,
        title="旧标题",
    )
    database.add(conversation)
    database.commit()

    result = rename_conversation(
        conversation_id=conversation.id,
        payload=ConversationRename(title="  新标题  "),
        database=database,
        principal=principal,
    )

    assert result.title == "新标题"
    assert result.scope_unit_id == township.id
    assert result.source_item_id is None
    assert result.scope_mode == "all_villages"
