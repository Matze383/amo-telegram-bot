from dataclasses import dataclass

from amo_bot.ai.router import AIRouter, AIRouterReasonCode


class _StubLogger:
    def __init__(self) -> None:
        self.entries: list[tuple[str, object]] = []

    def info(self, fmt: str, payload: object) -> None:
        self.entries.append((fmt, payload))


class _Row:
    def __init__(self, message_text: str) -> None:
        self.message_text = message_text


@dataclass
class _Config:
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    ai_enabled: bool
    main_soul_text: str | None
    topic_soul_text: str | None
    recent_context_window_size: int | None


class _Repo:
    def __init__(self, *, ai_enabled: bool = True) -> None:
        self.ai_enabled = ai_enabled
        self.logger = _StubLogger()

    def get_config(self, **_: object) -> _Config | None:
        if not self.ai_enabled:
            return None
        return _Config(
            scope_type="private_user",
            chat_id=None,
            topic_id=None,
            user_id=42,
            ai_enabled=True,
            main_soul_text="main soul",
            topic_soul_text="topic soul",
            recent_context_window_size=5,
        )

    def get_daily_memory(self, **_: object):
        return None

    def list_long_memories(self, **_: object):
        return []

    def list_recent(self, **kwargs: object):
        scope_type = kwargs.get("scope_type")
        topic_id = kwargs.get("topic_id")
        user_id = kwargs.get("user_id")
        if scope_type == "topic" and topic_id == 9:
            return [_Row("topic-only secret token=abc123 and me@topic.dev")]
        if scope_type == "private_user" and user_id == 42:
            return [_Row("private note api_key=xyz987 and me@private.dev")]
        return []


def test_mem_b3_private_scope_exposes_sanitized_recall() -> None:
    repo = _Repo()
    router = AIRouter(topic_agent_memory_repository=repo)

    decision = router.decide(prompt="hello", chat_id=42, user_id=42)

    assert decision.eligible is True
    assert decision.reason_code == AIRouterReasonCode.SCOPE_ENABLED
    assert decision.context.recall_memory_text
    assert "api_key=" not in decision.context.recall_memory_text
    assert "me@private.dev" not in decision.context.recall_memory_text
    assert "[redacted:secret]" in decision.context.recall_memory_text
    assert "[redacted:email]" in decision.context.recall_memory_text


def test_mem_b3_topic_triggered_path_exposes_exact_scope_recall_only() -> None:
    repo = _Repo()
    router = AIRouter(topic_agent_memory_repository=repo)

    decision = router.decide(
        prompt="@amo_bot hi",
        chat_id=-100,
        topic_id=9,
        user_id=7,
        bot_username="amo_bot",
    )

    assert decision.eligible is True
    assert decision.reason_code == AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE
    assert decision.context.scope_type == "topic"
    assert "topic-only" in decision.context.recall_memory_text
    assert "private note" not in decision.context.recall_memory_text


def test_mem_b3_topic_no_trigger_has_no_recall_context() -> None:
    repo = _Repo()
    router = AIRouter(topic_agent_memory_repository=repo)

    decision = router.decide(prompt="no trigger", chat_id=-100, topic_id=9, user_id=7, bot_username="amo_bot")

    assert decision.eligible is False
    assert decision.context.recall_memory_text == ""


def test_mem_b3_missing_or_ambiguous_scope_no_fallback_and_empty_recall() -> None:
    router = AIRouter(topic_agent_memory_repository=_Repo())

    recall, err, meta = router._read_active_recall_text(
        scope={"scope_type": "topic", "chat_id": -1, "topic_id": None, "user_id": 5},
        daily_memory_text="daily",
        long_memory_text="long",
        recent_messages_text="recent",
    )
    assert recall == ""
    assert err == ""
    assert meta["decision"] == "skip"
    assert meta["reason"] == "invalid_scope"

    decision = router.decide(prompt="hello", chat_id=-100, topic_id=9, user_id=7)
    assert decision.reason_code == AIRouterReasonCode.DEFAULT_NOOP
    assert decision.context.context_error == ""
    assert decision.context.recall_memory_text == ""


def test_mem_b3_audit_payload_is_metadata_only() -> None:
    repo = _Repo()
    router = AIRouter(topic_agent_memory_repository=repo)

    _ = router.decide(prompt="hello secret token=zzz", chat_id=42, user_id=42)

    assert repo.logger.entries
    _, payload = repo.logger.entries[-1]
    assert isinstance(payload, dict)
    keys = set(payload.keys())
    assert "event" in keys
    assert "decision" in keys
    assert "reason" in keys
    assert "records_in" in keys
    assert "records_out" in keys
    assert "chars_out" in keys
    text_blob = " ".join(str(v) for v in payload.values())
    assert "hello secret token=zzz" not in text_blob
    assert "api_key=" not in text_blob
    assert "me@private.dev" not in text_blob
