from sqlalchemy.orm import Session

from amo_bot.ai.router import AIRouter, AIRouterDecision, AIRouterReasonCode
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import TopicAgentMemoryRepository


def _mk_repo(tmp_path) -> TopicAgentMemoryRepository:
    db_url = f"sqlite:///{tmp_path / 'ai_router.sqlite'}"
    init_db(database_url=db_url)
    session_factory = create_session_factory(db_url)
    return TopicAgentMemoryRepository(session_factory())


def test_default_decision_is_passthrough_noop() -> None:
    decision = AIRouter().decide(prompt="hello")
    assert decision == AIRouterDecision(
        passthrough=True,
        eligible=False,
        reason_code=AIRouterReasonCode.DEFAULT_NOOP,
    )


def test_default_decision_is_deterministic() -> None:
    router = AIRouter()
    first = router.decide(prompt="one")
    second = router.decide(prompt="two")
    assert first == second


def test_every_decision_has_exactly_one_reason_code() -> None:
    decision = AIRouter().decide(prompt="hello")
    assert isinstance(decision.reason_code, AIRouterReasonCode)
    assert decision.reason_code.value == "default_noop"


def test_scope_matrix_active_and_inactive(tmp_path) -> None:
    repo = _mk_repo(tmp_path)

    repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=11, ai_enabled=True)
    repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=22, ai_enabled=False)
    repo.upsert_config(scope_type="private_user", user_id=77, ai_enabled=True)
    repo.upsert_config(scope_type="private_user", user_id=88, ai_enabled=False)

    router = AIRouter(topic_agent_memory_repository=repo)

    active_topic = router.decide(prompt="x", chat_id=-1001, topic_id=11, user_id=500)
    assert active_topic.eligible is True
    assert active_topic.reason_code is AIRouterReasonCode.SCOPE_ENABLED

    active_topic_mention = router.decide(
        prompt="hello @amo_bot",
        chat_id=-1001,
        topic_id=11,
        user_id=500,
        bot_username="amo_bot",
    )
    assert active_topic_mention.eligible is True
    assert active_topic_mention.reason_code is AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE

    active_topic_reply_to_bot = router.decide(
        prompt="hello",
        chat_id=-1001,
        topic_id=11,
        user_id=500,
        reply_to_is_bot=True,
    )
    assert active_topic_reply_to_bot.eligible is True
    assert active_topic_reply_to_bot.reason_code is AIRouterReasonCode.REPLY_TO_BOT_IN_ACTIVE_SCOPE

    inactive_topic = router.decide(prompt="x", chat_id=-1001, topic_id=22, user_id=500)
    assert inactive_topic.eligible is False
    assert inactive_topic.reason_code is AIRouterReasonCode.DEFAULT_NOOP

    active_private = router.decide(prompt="x", chat_id=77, user_id=77)
    assert active_private.eligible is True
    assert active_private.reason_code is AIRouterReasonCode.SCOPE_ENABLED

    inactive_private = router.decide(prompt="x", chat_id=88, user_id=88)
    assert inactive_private.eligible is False
    assert inactive_private.reason_code is AIRouterReasonCode.DEFAULT_NOOP


def test_missing_config_defaults_to_disabled(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    router = AIRouter(topic_agent_memory_repository=repo)

    topic_missing = router.decide(prompt="x", chat_id=-1009, topic_id=901, user_id=7)
    private_missing = router.decide(prompt="x", chat_id=901, user_id=901)

    assert topic_missing.eligible is False
    assert topic_missing.reason_code is AIRouterReasonCode.DEFAULT_NOOP
    assert private_missing.eligible is False
    assert private_missing.reason_code is AIRouterReasonCode.DEFAULT_NOOP


def test_mention_in_inactive_scope_is_noop(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="topic", chat_id=-1001, topic_id=22, ai_enabled=False)
    router = AIRouter(topic_agent_memory_repository=repo)

    decision = router.decide(
        prompt="@amo_bot please help",
        chat_id=-1001,
        topic_id=22,
        user_id=500,
        bot_username="amo_bot",
    )

    assert decision.eligible is False
    assert decision.reason_code is AIRouterReasonCode.DEFAULT_NOOP


def test_reply_to_other_in_active_scope_remains_scope_enabled(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=77, ai_enabled=True)
    router = AIRouter(topic_agent_memory_repository=repo)

    decision = router.decide(
        prompt="hello there",
        chat_id=77,
        user_id=77,
        reply_to_is_bot=False,
    )

    assert decision.eligible is True
    assert decision.reason_code is AIRouterReasonCode.SCOPE_ENABLED


def test_without_mention_in_active_scope_remains_scope_enabled(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=77, ai_enabled=True)
    router = AIRouter(topic_agent_memory_repository=repo)

    decision = router.decide(
        prompt="hello there",
        chat_id=77,
        user_id=77,
        bot_username="amo_bot",
    )

    assert decision.eligible is True
    assert decision.reason_code is AIRouterReasonCode.SCOPE_ENABLED


def test_bot_username_parsing_edge_cases(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=77, ai_enabled=True)
    router = AIRouter(topic_agent_memory_repository=repo)

    valid = router.decide(
        prompt="Hi @Amo_Bot!",
        chat_id=77,
        user_id=77,
        bot_username="@amo_bot",
    )
    assert valid.reason_code is AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE

    suffix_invalid = router.decide(
        prompt="Hi @amo_bot123",
        chat_id=77,
        user_id=77,
        bot_username="amo_bot",
    )
    assert suffix_invalid.reason_code is AIRouterReasonCode.SCOPE_ENABLED

    empty_username = router.decide(
        prompt="Hi @amo_bot",
        chat_id=77,
        user_id=77,
        bot_username="   ",
    )
    assert empty_username.reason_code is AIRouterReasonCode.SCOPE_ENABLED
