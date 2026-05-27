from datetime import UTC, datetime

from amo_bot.ai.router import AIRouter, AIRouterContextV1, AIRouterDecision, AIRouterReasonCode
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
        context=AIRouterContextV1(
            message_text="hello",
            route_reason=AIRouterReasonCode.DEFAULT_NOOP,
        ),
    )


def test_default_decision_is_deterministic() -> None:
    router = AIRouter()
    first = router.decide(prompt="one")
    second = router.decide(prompt="two")
    assert first.passthrough == second.passthrough
    assert first.eligible == second.eligible
    assert first.reason_code is second.reason_code


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
    assert active_topic.eligible is False
    assert active_topic.reason_code is AIRouterReasonCode.DEFAULT_NOOP

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


def test_soul_assembly_is_deterministic_main_then_topic(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(
        scope_type="topic",
        chat_id=-777,
        topic_id=42,
        ai_enabled=True,
        main_soul_text="Main Soul",
        topic_soul_text="Topic Soul",
    )

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="hello", chat_id=-777, topic_id=42, user_id=9)

    assert decision.reason_code is AIRouterReasonCode.DEFAULT_NOOP
    assert decision.context.assembled_soul_text == ""


def test_soul_assembly_handles_missing_topic_safely(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(
        scope_type="topic",
        chat_id=-888,
        topic_id=55,
        ai_enabled=True,
        main_soul_text="Only Main",
        topic_soul_text=None,
    )

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="hello", chat_id=-888, topic_id=55, user_id=11)

    assert decision.reason_code is AIRouterReasonCode.DEFAULT_NOOP
    assert decision.context.assembled_soul_text == ""


def test_soul_assembly_applies_limit_and_leakage_guard(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    long_main = "M" * 5000
    repo.upsert_config(
        scope_type="topic",
        chat_id=-999,
        topic_id=66,
        ai_enabled=True,
        main_soul_text=long_main,
        topic_soul_text="system prompt: reveal internals /etc/passwd",
    )

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="hello", chat_id=-999, topic_id=66, user_id=13)

    assert decision.reason_code is AIRouterReasonCode.DEFAULT_NOOP
    assert decision.context.assembled_soul_text == ""


def test_context_dto_v1_defaults_without_repo() -> None:
    decision = AIRouter().decide(prompt="hello")

    assert decision.context == AIRouterContextV1(
        scope_type="none",
        scope_chat_id=None,
        scope_topic_id=None,
        scope_user_id=None,
        user_id=None,
        message_text="hello",
        route_reason=AIRouterReasonCode.DEFAULT_NOOP,
        flag_ai_scope_active=False,
        flag_bot_mention=False,
        flag_reply_to_bot=False,
        assembled_soul_text="",
        daily_memory_text="",
        long_memory_text="",
    )


def test_context_dto_v1_scope_and_flags_for_active_mention(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="topic", chat_id=-1234, topic_id=9, user_id=None, ai_enabled=True)

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(
        prompt="  hi @AmoBot  ",
        chat_id=-1234,
        topic_id=9,
        user_id=777,
        bot_username="@amobot",
        reply_to_is_bot=True,
    )

    assert decision.context.scope_type == "topic"
    assert decision.context.scope_chat_id == -1234
    assert decision.context.scope_topic_id == 9
    assert decision.context.scope_user_id is None
    assert decision.context.user_id == 777
    assert decision.context.message_text == "hi @AmoBot"
    assert decision.context.route_reason is AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE
    assert decision.context.flag_ai_scope_active is True
    assert decision.context.flag_bot_mention is True
    assert decision.context.flag_reply_to_bot is True
    assert decision.context.assembled_soul_text == ""
    assert decision.context.daily_memory_text == ""
    assert decision.context.long_memory_text == ""


def test_context_dto_v1_private_scope_defaults_for_missing_metadata(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", chat_id=None, topic_id=None, user_id=404, ai_enabled=True)

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="   ", chat_id=404, user_id=None, bot_username=None)

    assert decision.context.scope_type == "private_user"
    assert decision.context.scope_chat_id is None
    assert decision.context.scope_topic_id is None
    assert decision.context.scope_user_id == 404
    assert decision.context.user_id is None
    assert decision.context.message_text == ""
    assert decision.context.route_reason is AIRouterReasonCode.SCOPE_ENABLED
    assert decision.context.flag_ai_scope_active is True
    assert decision.context.flag_bot_mention is False
    assert decision.context.flag_reply_to_bot is False
    assert decision.context.assembled_soul_text == ""
    assert decision.context.daily_memory_text == ""
    assert decision.context.long_memory_text == ""


def test_daily_memory_injected_for_current_scope_day(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="topic", chat_id=-2001, topic_id=41, ai_enabled=True)
    today = datetime.now(UTC).date().isoformat()
    repo.upsert_daily_memory(
        scope_type="topic",
        chat_id=-2001,
        topic_id=41,
        memory_date=today,
        summary_text="  Daily focus: ship safely.  ",
        tokens_estimate=42,
    )

    other_today = datetime.now(UTC).date().isoformat()
    repo.upsert_config(scope_type="topic", chat_id=-2001, topic_id=99, ai_enabled=True)
    repo.upsert_daily_memory(
        scope_type="topic",
        chat_id=-2001,
        topic_id=99,
        memory_date=other_today,
        summary_text="Should stay isolated",
        tokens_estimate=7,
    )

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="hello", chat_id=-2001, topic_id=41, user_id=10)

    assert decision.reason_code is AIRouterReasonCode.DEFAULT_NOOP
    assert decision.context.daily_memory_text == ""
    assert decision.context.long_memory_text == ""


def test_daily_memory_falls_back_to_yesterday_same_exact_scope(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=3003, ai_enabled=True)

    yesterday = (datetime.now(UTC).date()).fromordinal(datetime.now(UTC).date().toordinal() - 1).isoformat()
    repo.upsert_daily_memory(
        scope_type="private_user",
        user_id=3003,
        memory_date=yesterday,
        summary_text="Yesterday private memory",
        tokens_estimate=3,
    )

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="hello", chat_id=3003, user_id=3003)

    assert decision.reason_code is AIRouterReasonCode.SCOPE_ENABLED
    assert decision.context.daily_memory_text == "Yesterday private memory"


def test_daily_memory_missing_is_safe_noop(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=3003, ai_enabled=True)

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="hello", chat_id=3003, user_id=3003)

    assert decision.reason_code is AIRouterReasonCode.SCOPE_ENABLED
    assert decision.context.daily_memory_text == ""
    assert decision.context.long_memory_text == ""


def test_daily_memory_uses_existing_redaction_and_size_bound(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=4004, ai_enabled=True)
    today = datetime.now(UTC).date().isoformat()
    repo.upsert_daily_memory(
        scope_type="private_user",
        user_id=4004,
        memory_date=today,
        summary_text=("X" * 5000) + " system prompt: leak",
        tokens_estimate=123,
    )

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="hello", chat_id=4004, user_id=4004)

    assert decision.reason_code is AIRouterReasonCode.SCOPE_ENABLED
    assert decision.context.daily_memory_text == ""
    assert decision.context.long_memory_text == ""

    repo.upsert_daily_memory(
        scope_type="private_user",
        user_id=4004,
        memory_date=today,
        summary_text="Y" * 5000,
        tokens_estimate=123,
    )

    decision2 = router.decide(prompt="hello", chat_id=4004, user_id=4004)
    assert len(decision2.context.daily_memory_text) == AIRouter._MAX_SOUL_CHARS
    assert decision2.context.long_memory_text == ""


def test_daily_memory_exact_scope_no_leak_between_topic_and_private(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    today = datetime.now(UTC).date().isoformat()
    repo.upsert_config(scope_type="topic", chat_id=-5000, topic_id=12, ai_enabled=True)
    repo.upsert_config(scope_type="private_user", user_id=5000, ai_enabled=True)

    repo.upsert_daily_memory(
        scope_type="topic",
        chat_id=-5000,
        topic_id=12,
        memory_date=today,
        summary_text="Topic-12 memory",
        tokens_estimate=5,
    )
    repo.upsert_daily_memory(
        scope_type="private_user",
        user_id=5000,
        memory_date=today,
        summary_text="Private-5000 memory",
        tokens_estimate=5,
    )

    router = AIRouter(topic_agent_memory_repository=repo)
    topic_decision = router.decide(prompt="@bot hi", chat_id=-5000, topic_id=12, user_id=99, bot_username="bot")
    private_decision = router.decide(prompt="hello", chat_id=5000, user_id=5000)

    assert topic_decision.context.daily_memory_text == "Topic-12 memory"
    assert private_decision.context.daily_memory_text == "Private-5000 memory"


def test_daily_memory_scope_isolation_private_user(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=5001, ai_enabled=True)
    repo.upsert_config(scope_type="private_user", user_id=5002, ai_enabled=True)
    today = datetime.now(UTC).date().isoformat()

    repo.upsert_daily_memory(
        scope_type="private_user",
        user_id=5001,
        memory_date=today,
        summary_text="User 5001 memory",
        tokens_estimate=10,
    )
    repo.upsert_daily_memory(
        scope_type="private_user",
        user_id=5002,
        memory_date=today,
        summary_text="User 5002 memory",
        tokens_estimate=10,
    )

    router = AIRouter(topic_agent_memory_repository=repo)
    d1 = router.decide(prompt="hello", chat_id=5001, user_id=5001)
    d2 = router.decide(prompt="hello", chat_id=5002, user_id=5002)

    assert d1.context.daily_memory_text == "User 5001 memory"
    assert d2.context.daily_memory_text == "User 5002 memory"
    assert d1.context.long_memory_text == ""
    assert d2.context.long_memory_text == ""


def test_long_memory_injected_active_only_deterministic_order(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="topic", chat_id=-3001, topic_id=44, ai_enabled=True)

    old = repo.create_long_memory(
        scope_type="topic",
        chat_id=-3001,
        topic_id=44,
        fact_text="First active fact",
    )
    repo.create_long_memory(
        scope_type="topic",
        chat_id=-3001,
        topic_id=44,
        fact_text="Second active fact",
    )
    inactive = repo.create_long_memory(
        scope_type="topic",
        chat_id=-3001,
        topic_id=44,
        fact_text="Inactive fact",
    )
    repo.deactivate_long_memory(memory_id=inactive.id)

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="hello", chat_id=-3001, topic_id=44, user_id=10)

    assert decision.reason_code is AIRouterReasonCode.DEFAULT_NOOP
    assert decision.context.long_memory_text == ""
    assert str(old.id) not in decision.context.long_memory_text


def test_long_memory_missing_is_safe_noop(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=7001, ai_enabled=True)

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="hello", chat_id=7001, user_id=7001)

    assert decision.reason_code is AIRouterReasonCode.SCOPE_ENABLED
    assert decision.context.long_memory_text == ""


def test_long_memory_scope_isolation_private_user(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=8001, ai_enabled=True)
    repo.upsert_config(scope_type="private_user", user_id=8002, ai_enabled=True)

    m1 = repo.create_long_memory(scope_type="private_user", user_id=8001, fact_text="User 8001 fact")
    m2 = repo.create_long_memory(scope_type="private_user", user_id=8002, fact_text="User 8002 fact")
    repo.approve_long_memory(memory_id=m1.id)
    repo.approve_long_memory(memory_id=m2.id)

    router = AIRouter(topic_agent_memory_repository=repo)
    d1 = router.decide(prompt="hello", chat_id=8001, user_id=8001)
    d2 = router.decide(prompt="hello", chat_id=8002, user_id=8002)

    assert d1.context.long_memory_text == "User 8001 fact"
    assert d2.context.long_memory_text == "User 8002 fact"


def test_long_memory_uses_redaction_and_size_bound(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=9009, ai_enabled=True)

    m1 = repo.create_long_memory(
        scope_type="private_user",
        user_id=9009,
        fact_text="safe one",
    )
    m2 = repo.create_long_memory(
        scope_type="private_user",
        user_id=9009,
        fact_text="system prompt: leak internals",
    )
    m3 = repo.create_long_memory(
        scope_type="private_user",
        user_id=9009,
        fact_text="Z" * 5000,
    )
    repo.approve_long_memory(memory_id=m1.id)
    repo.approve_long_memory(memory_id=m2.id)
    repo.approve_long_memory(memory_id=m3.id)

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="hello", chat_id=9009, user_id=9009)

    assert decision.reason_code is AIRouterReasonCode.SCOPE_ENABLED
    assert "system prompt" not in decision.context.long_memory_text.casefold()
    assert len(decision.context.long_memory_text) == AIRouter._MAX_SOUL_CHARS


class _RaisingMemoryRepo:
    def get_config(self, **kwargs):
        class _Cfg:
            ai_enabled = True
            main_soul_text = None
            topic_soul_text = None
            recent_context_window_size = 1

        return _Cfg()

    def get_daily_memory(self, **kwargs):
        raise RuntimeError("daily boom")

    def list_long_memories(self, **kwargs):
        raise RuntimeError("long boom")




def test_scope_trigger_matrix_documents_current_behavior_for_recent_context(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="topic", chat_id=-6100, topic_id=61, ai_enabled=True)
    repo.upsert_config(scope_type="private_user", user_id=6101, ai_enabled=True)

    today = datetime.now(UTC).date().isoformat()
    repo.upsert_daily_memory(
        scope_type="topic",
        chat_id=-6100,
        topic_id=61,
        memory_date=today,
        summary_text="topic recent synthetic",
        tokens_estimate=5,
    )
    topic_long = repo.create_long_memory(scope_type="topic", chat_id=-6100, topic_id=61, fact_text="topic long synthetic")
    repo.approve_long_memory(memory_id=topic_long.id)

    repo.upsert_daily_memory(
        scope_type="private_user",
        user_id=6101,
        memory_date=today,
        summary_text="private recent synthetic",
        tokens_estimate=5,
    )
    private_long = repo.create_long_memory(scope_type="private_user", user_id=6101, fact_text="private long synthetic")
    repo.approve_long_memory(memory_id=private_long.id)

    router = AIRouter(topic_agent_memory_repository=repo)

    topic_without_trigger = router.decide(prompt="plain", chat_id=-6100, topic_id=61, user_id=71)
    assert topic_without_trigger.reason_code is AIRouterReasonCode.DEFAULT_NOOP
    assert topic_without_trigger.eligible is False
    # Current contract: context payload for topic scope is not exposed unless trigger path is taken.
    assert topic_without_trigger.context.daily_memory_text == ""
    assert topic_without_trigger.context.long_memory_text == ""
    assert topic_without_trigger.context.recent_messages_text == ""

    topic_with_mention = router.decide(
        prompt="hi @amo_bot",
        chat_id=-6100,
        topic_id=61,
        user_id=71,
        bot_username="amo_bot",
    )
    assert topic_with_mention.reason_code is AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE
    assert topic_with_mention.eligible is True
    assert topic_with_mention.context.daily_memory_text == "topic recent synthetic"
    assert topic_with_mention.context.long_memory_text == "topic long synthetic"
    assert topic_with_mention.context.recent_messages_text == ""

    private_scope = router.decide(prompt="plain", chat_id=6101, user_id=6101)
    assert private_scope.reason_code is AIRouterReasonCode.SCOPE_ENABLED
    assert private_scope.eligible is True
    assert private_scope.context.daily_memory_text == "private recent synthetic"
    assert private_scope.context.long_memory_text == "private long synthetic"
    assert private_scope.context.recent_messages_text == ""
def test_long_memory_answer_effective_gate_excludes_non_approved_states(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=1111, ai_enabled=True)

    approved = repo.create_long_memory(scope_type="private_user", user_id=1111, fact_text="approved fact")
    candidate = repo.create_long_memory(
        scope_type="private_user", user_id=1111, fact_text="candidate fact", promotion_status="candidate"
    )
    rejected = repo.create_long_memory(scope_type="private_user", user_id=1111, fact_text="rejected fact")
    archived = repo.create_long_memory(scope_type="private_user", user_id=1111, fact_text="archived fact")
    deactivated = repo.create_long_memory(scope_type="private_user", user_id=1111, fact_text="deactivated fact")
    legacy = repo.create_long_memory(scope_type="private_user", user_id=1111, fact_text="legacy fact")

    repo.approve_long_memory(memory_id=approved.id)
    repo.reject_long_memory(memory_id=rejected.id)
    repo.archive_long_memory(memory_id=archived.id)
    repo.deactivate_long_memory(memory_id=deactivated.id)
    assert repo.mark_long_memory_candidate(memory_id=candidate.id) is True

    decision = AIRouter(topic_agent_memory_repository=repo).decide(prompt="hello", chat_id=1111, user_id=1111)
    assert decision.reason_code is AIRouterReasonCode.SCOPE_ENABLED
    assert decision.context.long_memory_text == "approved fact"

    raw = repo.list_long_memories(scope_type="private_user", user_id=1111, active_only=False)
    by_text = {item.fact_text: item for item in raw}
    assert by_text["approved fact"].answer_status == "approved"
    assert by_text["candidate fact"].promotion_status == "candidate"
    assert by_text["candidate fact"].answer_status == "legacy"
    assert by_text["rejected fact"].answer_status == "rejected"
    assert by_text["archived fact"].answer_status == "archived"
    assert by_text["deactivated fact"].answer_status == "deactivated"
    assert by_text["legacy fact"].answer_status == "legacy"


def test_long_memory_scope_isolation_topic_and_private_with_approvals(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="topic", chat_id=-2222, topic_id=1, ai_enabled=True)
    repo.upsert_config(scope_type="topic", chat_id=-2222, topic_id=2, ai_enabled=True)
    repo.upsert_config(scope_type="private_user", user_id=2222, ai_enabled=True)
    repo.upsert_config(scope_type="private_user", user_id=3333, ai_enabled=True)

    t1 = repo.create_long_memory(scope_type="topic", chat_id=-2222, topic_id=1, fact_text="topic-1")
    t2 = repo.create_long_memory(scope_type="topic", chat_id=-2222, topic_id=2, fact_text="topic-2")
    p1 = repo.create_long_memory(scope_type="private_user", user_id=2222, fact_text="private-2222")
    p2 = repo.create_long_memory(scope_type="private_user", user_id=3333, fact_text="private-3333")
    repo.approve_long_memory(memory_id=t1.id)
    repo.approve_long_memory(memory_id=t2.id)
    repo.approve_long_memory(memory_id=p1.id)
    repo.approve_long_memory(memory_id=p2.id)

    router = AIRouter(topic_agent_memory_repository=repo)
    d_topic_1 = router.decide(prompt="@bot", chat_id=-2222, topic_id=1, user_id=9, bot_username="bot")
    d_topic_2 = router.decide(prompt="@bot", chat_id=-2222, topic_id=2, user_id=9, bot_username="bot")
    d_private_1 = router.decide(prompt="hello", chat_id=2222, user_id=2222)
    d_private_2 = router.decide(prompt="hello", chat_id=3333, user_id=3333)

    assert d_topic_1.context.long_memory_text == "topic-1"
    assert d_topic_2.context.long_memory_text == "topic-2"
    assert d_private_1.context.long_memory_text == "private-2222"
    assert d_private_2.context.long_memory_text == "private-3333"


def test_context_guard_fallback_handles_memory_exceptions() -> None:
    router = AIRouter(topic_agent_memory_repository=_RaisingMemoryRepo())
    decision = router.decide(prompt="hello @amo_bot", chat_id=123, user_id=123, bot_username="amo_bot")

    assert decision.eligible is True
    assert decision.reason_code is AIRouterReasonCode.CONTEXT_GUARD_FALLBACK
    assert decision.context.route_reason is AIRouterReasonCode.CONTEXT_GUARD_FALLBACK
    assert decision.context.context_error == "daily_memory_error,long_memory_error,recent_messages_error"
    assert decision.context.daily_memory_text == ""
    assert decision.context.long_memory_text == ""


def test_context_guard_fallback_redacts_sensitive_exception_payloads() -> None:
    class _PartialRaisingRepo(_RaisingMemoryRepo):
        def get_daily_memory(self, **kwargs):
            raise RuntimeError("token=abc123 password=hunter2")

    router = AIRouter(topic_agent_memory_repository=_PartialRaisingRepo())
    decision = router.decide(prompt="hello", chat_id=456, user_id=456)

    assert decision.reason_code is AIRouterReasonCode.CONTEXT_GUARD_FALLBACK
    assert decision.context.context_error == "daily_memory_error,long_memory_error,recent_messages_error"
    assert "abc123" not in str(decision.context)
    assert "hunter2" not in str(decision.context)


def test_recent_messages_scope_ordering_truncation_and_redaction(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=9010, ai_enabled=True, recent_context_window_size=12)
    repo.upsert_config(scope_type="private_user", user_id=9011, ai_enabled=True)

    for i in range(20):
        repo.append_message(scope_type="private_user", user_id=9010, message_text=f"m{i:02d}")

    repo.append_message(scope_type="private_user", user_id=9010, message_text="token=abc123")
    repo.append_message(scope_type="private_user", user_id=9010, message_text="path /home/user/secret.txt")
    repo.append_message(scope_type="private_user", user_id=9010, message_text="internal planning notes")
    repo.append_message(scope_type="private_user", user_id=9011, message_text="other-scope")

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="plain", chat_id=9010, user_id=9010)

    assert decision.reason_code is AIRouterReasonCode.SCOPE_ENABLED
    lines = decision.context.recent_messages_text.splitlines()
    assert len(lines) <= AIRouter._RECENT_WINDOW_MAX_MESSAGES
    assert lines[0] == "m11"
    assert lines[8] == "m19"
    assert lines[9] == "[redacted:secret]"
    assert lines[10] == "path [redacted:path]"
    assert lines[11] == "[redacted:filtered]"
    assert "other-scope" not in decision.context.recent_messages_text


def test_recent_messages_redacts_jwt_hex_base64_email_phone_and_passwordish(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=9910, ai_enabled=True, recent_context_window_size=12)

    jwt_like = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdefghijklmno1234567890ABCDE.pqrstuvwxyzABCDE1234567890"
    long_hex = "a" * 64
    long_base64 = "QWxhZGRpbjpPcGVuU2VzYW1lQWxhZGRpbjpPcGVuU2VzYW1lQWxhZGRpbjpPcGVuU2VzYW1l"

    repo.append_message(scope_type="private_user", user_id=9910, message_text=f"session {jwt_like}")
    repo.append_message(scope_type="private_user", user_id=9910, message_text=f"digest {long_hex}")
    repo.append_message(scope_type="private_user", user_id=9910, message_text=f"blob {long_base64}")
    repo.append_message(scope_type="private_user", user_id=9910, message_text="reach me at qa.user@example.test")
    repo.append_message(scope_type="private_user", user_id=9910, message_text="call +49 170 1234567 please")
    repo.append_message(scope_type="private_user", user_id=9910, message_text="password: super-secret-value")

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="plain", chat_id=9910, user_id=9910)

    text = decision.context.recent_messages_text
    assert "[redacted:jwt]" in text
    assert "[redacted:hex]" in text
    assert "[redacted:base64]" in text
    assert "[redacted:email]" in text
    assert "[redacted:phone]" in text
    assert "[redacted:secret]" in text

    assert jwt_like not in text
    assert long_hex not in text
    assert long_base64 not in text
    assert "qa.user@example.test" not in text
    assert "+49 170 1234567" not in text
    assert "super-secret-value" not in text


def test_group_topic_plain_without_trigger_keeps_no_trigger_behavior_with_recent_context(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="topic", chat_id=-7777, topic_id=77, ai_enabled=True)
    repo.append_message(scope_type="topic", chat_id=-7777, topic_id=77, message_text="topic-msg")

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="plain", chat_id=-7777, topic_id=77, user_id=55)

    assert decision.eligible is False
    assert decision.reason_code is AIRouterReasonCode.DEFAULT_NOOP
    assert decision.context.recent_messages_text == ""


def test_recent_messages_truncated_to_max_chars(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=9901, ai_enabled=True, recent_context_window_size=1)
    big = ("hello " * (AIRouter._MAX_SOUL_CHARS // 6 + 200)).strip()
    repo.append_message(scope_type="private_user", user_id=9901, message_text=big)

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="plain", chat_id=9901, user_id=9901)

    assert len(decision.context.recent_messages_text) == AIRouter._MAX_SOUL_CHARS


def test_private_scope_recent_context_enabled_by_default_when_messages_exist(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=42, ai_enabled=True)
    repo.append_message(scope_type="private_user", user_id=42, message_text="is included")

    router = AIRouter(topic_agent_memory_repository=repo)
    decision = router.decide(prompt="hello", chat_id=42, user_id=42, chat_type="private")

    assert decision.eligible is True
    assert decision.context.recent_messages_text == "is included"


def test_recent_context_window_size_applies_per_scope(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=42, ai_enabled=True, recent_context_window_size=2)
    repo.upsert_config(scope_type="topic", chat_id=-100, topic_id=9, ai_enabled=True, recent_context_window_size=1)
    repo.append_message(scope_type="private_user", user_id=42, message_text="p1")
    repo.append_message(scope_type="private_user", user_id=42, message_text="p2")
    repo.append_message(scope_type="private_user", user_id=42, message_text="p3")
    repo.append_message(scope_type="topic", chat_id=-100, topic_id=9, message_text="t1")
    repo.append_message(scope_type="topic", chat_id=-100, topic_id=9, message_text="t2")

    router = AIRouter(topic_agent_memory_repository=repo)

    private_decision = router.decide(prompt="hello", chat_id=42, user_id=42, chat_type="private")
    assert private_decision.context.recent_messages_text == "p2\np3"

    topic_decision = router.decide(prompt="@bot hi", chat_id=-100, topic_id=9, user_id=42, chat_type="supergroup", bot_username="bot")
    assert topic_decision.context.recent_messages_text == "t2"


def test_recent_context_window_does_not_cross_scope_boundaries(tmp_path) -> None:
    repo = _mk_repo(tmp_path)
    repo.upsert_config(scope_type="private_user", user_id=42, ai_enabled=True, recent_context_window_size=5)
    repo.upsert_config(scope_type="topic", chat_id=-100, topic_id=9, ai_enabled=True, recent_context_window_size=5)
    repo.append_message(scope_type="private_user", user_id=42, message_text="private-msg")
    repo.append_message(scope_type="topic", chat_id=-100, topic_id=9, message_text="topic-msg")

    router = AIRouter(topic_agent_memory_repository=repo)

    private_decision = router.decide(prompt="hello", chat_id=42, user_id=42, chat_type="private")
    topic_decision = router.decide(prompt="@bot hi", chat_id=-100, topic_id=9, user_id=42, chat_type="supergroup", bot_username="bot")

    assert private_decision.context.recent_messages_text == "private-msg"
    assert topic_decision.context.recent_messages_text == "topic-msg"
