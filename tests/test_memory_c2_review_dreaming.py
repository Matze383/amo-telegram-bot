from datetime import UTC, datetime

import pytest

from amo_bot.ai.memory_c2_service import (
    DreamStage,
    MemoryC2Service,
    MemoryScope,
    PermissionDeniedError,
    ReviewAction,
    ReviewActor,
)
from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import TopicAgentMemoryRepository


SECRET = "api_key=SECRET-123 raw prompt do not leak"


def _mk_repo(tmp_path) -> TopicAgentMemoryRepository:
    db_url = f"sqlite:///{tmp_path / 'memory_c2.sqlite'}"
    init_db(database_url=db_url)
    session_factory = create_session_factory(db_url)
    return TopicAgentMemoryRepository(session_factory())


def test_dream_stages_create_candidate_only_and_scope_isolated(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    svc = MemoryC2Service(repository=repo)

    p_scope = MemoryScope(scope_type="private_user", user_id=7001)
    t_scope = MemoryScope(scope_type="topic", chat_id=-7000, topic_id=7)
    g_scope = MemoryScope(scope_type="group_chat", chat_id=-7000)

    p, _ = svc.create_dream_candidate(scope=p_scope, stage=DreamStage.LIGHT, fact_text="p light")
    t, _ = svc.create_dream_candidate(scope=t_scope, stage=DreamStage.REM, fact_text="t rem")
    g, _ = svc.create_dream_candidate(scope=g_scope, stage=DreamStage.DEEP, fact_text="g deep")

    assert p.promotion_status == "candidate"
    assert t.promotion_status == "candidate"
    assert g.promotion_status == "candidate"

    assert [m.id for m in repo.list_long_memories(scope_type="private_user", user_id=7001, active_only=False)] == [p.id]
    assert [m.id for m in repo.list_long_memories(scope_type="topic", chat_id=-7000, topic_id=7, active_only=False)] == [t.id]
    assert [m.id for m in repo.list_long_memories(scope_type="group_chat", chat_id=-7000, active_only=False)] == [g.id]


def test_review_list_scope_bound_and_permission_gated(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    svc = MemoryC2Service(repository=repo)

    scope = MemoryScope(scope_type="private_user", user_id=7002)
    svc.create_dream_candidate(scope=scope, stage=DreamStage.LIGHT, fact_text="candidate")
    repo.create_long_memory(scope_type="private_user", user_id=7002, fact_text="approved")

    owner = ReviewActor(telegram_user_id=7002, role=Role.NORMAL)
    rows = svc.list_review_candidates(actor=owner, scope=scope)
    assert len(rows) == 1
    assert rows[0].promotion_status == "candidate"

    with pytest.raises(PermissionDeniedError):
        svc.list_review_candidates(actor=ReviewActor(telegram_user_id=7003, role=Role.NORMAL), scope=scope)


def test_approve_only_becomes_answer_effective(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    svc = MemoryC2Service(repository=repo)

    scope = MemoryScope(scope_type="private_user", user_id=7004)
    approved, _ = svc.create_dream_candidate(scope=scope, stage=DreamStage.LIGHT, fact_text="approved memory")
    rejected, _ = svc.create_dream_candidate(scope=scope, stage=DreamStage.LIGHT, fact_text="rejected memory")

    actor = ReviewActor(telegram_user_id=7004, role=Role.NORMAL)
    svc.apply_review_action(actor=actor, scope=scope, memory_id=approved.id, action=ReviewAction.APPROVE)
    svc.apply_review_action(actor=actor, scope=scope, memory_id=rejected.id, action=ReviewAction.REJECT)

    effective = repo.list_long_memories(scope_type="private_user", user_id=7004, answer_effective_only=True)
    assert [m.fact_text for m in effective] == ["approved memory"]


def test_non_approved_states_not_answer_effective(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    svc = MemoryC2Service(repository=repo)

    scope = MemoryScope(scope_type="private_user", user_id=7005)
    c, _ = svc.create_dream_candidate(scope=scope, stage=DreamStage.LIGHT, fact_text="candidate memory")
    r, _ = svc.create_dream_candidate(scope=scope, stage=DreamStage.REM, fact_text="reject memory")
    a, _ = svc.create_dream_candidate(scope=scope, stage=DreamStage.DEEP, fact_text="archive memory")
    d, _ = svc.create_dream_candidate(scope=scope, stage=DreamStage.DEEP, fact_text="deactivate memory")

    actor = ReviewActor(telegram_user_id=7005, role=Role.NORMAL)
    svc.apply_review_action(actor=actor, scope=scope, memory_id=r.id, action=ReviewAction.REJECT)
    svc.apply_review_action(actor=actor, scope=scope, memory_id=a.id, action=ReviewAction.ARCHIVE)
    svc.apply_review_action(actor=actor, scope=scope, memory_id=d.id, action=ReviewAction.DEACTIVATE)

    effective = repo.list_long_memories(scope_type="private_user", user_id=7005, answer_effective_only=True)
    assert effective == []
    assert c.id > 0


def test_permission_denied_wrong_scope_and_group_admin_rules(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    svc = MemoryC2Service(repository=repo)

    topic_scope = MemoryScope(scope_type="topic", chat_id=-7010, topic_id=1)
    row, _ = svc.create_dream_candidate(scope=topic_scope, stage=DreamStage.REM, fact_text="topic candidate")

    with pytest.raises(PermissionDeniedError):
        svc.list_review_candidates(actor=ReviewActor(telegram_user_id=1, role=Role.NORMAL), scope=topic_scope)

    admin = ReviewActor(telegram_user_id=2, role=Role.ADMIN)
    assert len(svc.list_review_candidates(actor=admin, scope=topic_scope)) == 1

    with pytest.raises(PermissionDeniedError):
        svc.apply_review_action(
            actor=ReviewActor(telegram_user_id=3, role=Role.NORMAL),
            scope=topic_scope,
            memory_id=row.id,
            action=ReviewAction.APPROVE,
        )


def test_lineage_redaction_payload_is_metadata_only(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    svc = MemoryC2Service(repository=repo)

    scope = MemoryScope(scope_type="private_user", user_id=7020)
    _, audit = svc.create_dream_candidate(
        scope=scope,
        stage=DreamStage.LIGHT,
        fact_text="safe fact",
        source_daily_memory_id=123,
        source_recent_message_id=456,
        source_context_ref=SECRET,
    )

    rendered = str(audit)
    assert "safe fact" not in rendered
    assert SECRET not in rendered
    assert "daily_ref" in rendered
    assert "recent_ref" in rendered
    assert "context_ref" in rendered


def test_c2_mini_e2e_matrix_private_topic_group_daily_recent_permission_nonretrieval(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    svc = MemoryC2Service(repository=repo)

    p_scope = MemoryScope(scope_type="private_user", user_id=7101)
    t_scope = MemoryScope(scope_type="topic", chat_id=-7100, topic_id=11)
    g_scope = MemoryScope(scope_type="group_chat", chat_id=-7100)

    p_row, p_audit = svc.create_dream_candidate(
        scope=p_scope,
        stage=DreamStage.LIGHT,
        fact_text="p matrix",
        source_daily_memory_id=11,
        source_recent_message_id=12,
        source_context_ref="recent-reply:abc",
    )
    t_row, _ = svc.create_dream_candidate(scope=t_scope, stage=DreamStage.REM, fact_text="t matrix")
    g_row, _ = svc.create_dream_candidate(scope=g_scope, stage=DreamStage.DEEP, fact_text="g matrix")

    svc.apply_review_action(
        actor=ReviewActor(telegram_user_id=7101, role=Role.NORMAL),
        scope=p_scope,
        memory_id=p_row.id,
        action=ReviewAction.APPROVE,
    )
    svc.apply_review_action(
        actor=ReviewActor(telegram_user_id=99, role=Role.ADMIN),
        scope=t_scope,
        memory_id=t_row.id,
        action=ReviewAction.ARCHIVE,
    )
    svc.apply_review_action(
        actor=ReviewActor(telegram_user_id=99, role=Role.ADMIN),
        scope=g_scope,
        memory_id=g_row.id,
        action=ReviewAction.REJECT,
    )

    private_effective = repo.list_long_memories(scope_type="private_user", user_id=7101, answer_effective_only=True)
    topic_effective = repo.list_long_memories(scope_type="topic", chat_id=-7100, topic_id=11, answer_effective_only=True)
    group_effective = repo.list_long_memories(scope_type="group_chat", chat_id=-7100, answer_effective_only=True)

    assert [m.fact_text for m in private_effective] == ["p matrix"]
    assert topic_effective == []
    assert group_effective == []

    assert "daily_ref" in p_audit.refs and "recent_ref" in p_audit.refs

    with pytest.raises(PermissionDeniedError):
        svc.list_review_candidates(
            actor=ReviewActor(telegram_user_id=7102, role=Role.NORMAL),
            scope=MemoryScope(scope_type="private_user", user_id=7101),
        )

    assert isinstance(datetime.now(UTC), datetime)
