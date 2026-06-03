import asyncio

from amo_bot.ai.ollama import OllamaError
from amo_bot.ai.service import AIService
from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import TopicAgentMemoryRepository, UserMemoryProfileRepository
from amo_bot.telegram.commands import CommandContext, create_builtin_registry


class FakeAIService:
    def __init__(self, answer: str = "ok", fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail

    async def ask(self, prompt: str) -> str:
        if self.fail:
            raise OllamaError("boom")
        return self.answer


class CapturingAIService:
    def __init__(self) -> None:
        self.prompt = ""

    async def ask(self, prompt: str) -> str:
        self.prompt = prompt
        return "ok"


def test_ask_permissions_and_help_visibility() -> None:
    reg = create_builtin_registry(ai_service=FakeAIService())

    assert reg.is_allowed("ask", Role.VIP) is True
    assert reg.is_allowed("ask", Role.ADMIN) is True
    assert reg.is_allowed("ask", Role.OWNER) is True
    assert reg.is_allowed("ask", Role.NORMAL) is False
    assert reg.is_allowed("ask", Role.IGNORE) is False

    help_cmd = reg.get("help")
    assert help_cmd is not None

    out_normal = asyncio.run(
        help_cmd.handler(CommandContext(chat_id=1, user_id=1, role=Role.NORMAL, command_name="help", argument=None))
    )
    out_vip = asyncio.run(
        help_cmd.handler(CommandContext(chat_id=1, user_id=1, role=Role.VIP, command_name="help", argument=None))
    )

    assert out_normal is not None and "/ask" not in out_normal
    assert out_vip is not None and "/ask" in out_vip


def test_ask_usage_for_empty_argument() -> None:
    reg = create_builtin_registry(ai_service=FakeAIService())
    ask_cmd = reg.get("ask")
    assert ask_cmd is not None

    out = asyncio.run(
        ask_cmd.handler(CommandContext(chat_id=1, user_id=1, role=Role.VIP, command_name="ask", argument="   ", locale="en"))
    )
    assert out == "usage: /ask <question>"


def test_ask_returns_answer_from_service() -> None:
    reg = create_builtin_registry(ai_service=FakeAIService(answer="hello from ollama"))
    ask_cmd = reg.get("ask")
    assert ask_cmd is not None

    out = asyncio.run(
        ask_cmd.handler(CommandContext(chat_id=1, user_id=1, role=Role.ADMIN, command_name="ask", argument="Hi?"))
    )
    assert out == "hello from ollama"


def test_ask_prompt_includes_current_time_context_without_logging_user_text() -> None:
    ai = CapturingAIService()
    reg = create_builtin_registry(ai_service=ai, prompt_timezone="Europe/Berlin")
    ask_cmd = reg.get("ask")
    assert ask_cmd is not None

    out = asyncio.run(
        ask_cmd.handler(CommandContext(chat_id=1, user_id=1, role=Role.ADMIN, command_name="ask", argument="Hi?"))
    )

    assert out == "ok"
    assert "Current time context (system-provided, higher priority than memory/recent chat):" in ai.prompt
    assert "Current date:" in ai.prompt
    assert "Timezone: Europe/Berlin" in ai.prompt
    assert "User message:\nHi?" in ai.prompt


def test_ask_includes_only_current_scoped_user_profile(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ask_profile_scope.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)
    with sf() as session:
        profile_repo = UserMemoryProfileRepository(session)
        profile_repo.replace_profile(scope_type="topic", chat_id=-9001, topic_id=7, user_id=42, profile={"language": "de"})
        profile_repo.replace_profile(scope_type="topic", chat_id=-9001, topic_id=8, user_id=42, profile={"language": "en"})
        profile_repo.replace_profile(scope_type="private_user", user_id=42, profile={"tone_preference": "direct"})

    ai = CapturingAIService()
    reg = create_builtin_registry(database_url=db_url, ai_service=ai)
    ask_cmd = reg.get("ask")
    assert ask_cmd is not None

    out = asyncio.run(
        ask_cmd.handler(
            CommandContext(
                chat_id=-9001,
                user_id=42,
                role=Role.VIP,
                command_name="ask",
                argument="Hi?",
                message_thread_id=7,
            )
        )
    )

    assert out == "ok"
    assert "language" in ai.prompt
    assert "de" in ai.prompt
    assert "language=\":\"en\"" not in ai.prompt, "topic scope should not leak other topic language"
    assert "tone_preference" not in ai.prompt, "private_user scope should not leak private profile to topic"


def test_ask_handles_service_error_cleanly() -> None:
    reg = create_builtin_registry(ai_service=FakeAIService(fail=True))
    ask_cmd = reg.get("ask")
    assert ask_cmd is not None

    out = asyncio.run(
        ask_cmd.handler(CommandContext(chat_id=1, user_id=1, role=Role.OWNER, command_name="ask", argument="Hi?", locale="en"))
    )
    assert out == "Sorry, I cannot answer right now. Please try again later."


def test_ai_service_empty_prompt_guard() -> None:
    class DummyClient:
        async def generate(self, prompt: str) -> str:
            return prompt

    svc = AIService(client=DummyClient())

    try:
        asyncio.run(svc.ask("   "))
        assert False, "expected ValueError"
    except ValueError:
        assert True


def test_ai_service_ask_passes_through_without_router_integration() -> None:
    class DummyClient:
        async def generate(self, prompt: str) -> str:
            return f"ok:{prompt}"

    svc = AIService(client=DummyClient())
    out = asyncio.run(svc.ask("  Hi there  "))
    assert out == "ok:Hi there"


def test_start_and_help_locale_argument_selection() -> None:
    reg = create_builtin_registry()

    help_cmd = reg.get("help")
    start_cmd = reg.get("start")
    assert help_cmd is not None
    assert start_cmd is not None

    out_help_en = asyncio.run(
        help_cmd.handler(CommandContext(chat_id=1, user_id=1, role=Role.NORMAL, command_name="help", argument="en", locale="en"))
    )
    assert out_help_en is not None
    assert out_help_en.startswith("available commands:")
    assert "/ping - Check bot health" in out_help_en
    assert "/consent - Show consent status" in out_help_en

    out_help_de = asyncio.run(
        help_cmd.handler(CommandContext(chat_id=1, user_id=1, role=Role.NORMAL, command_name="help", argument="de", locale="de"))
    )
    assert out_help_de is not None
    assert out_help_de.startswith("Verfügbare Befehle:")
    assert "/ping - Bot-Erreichbarkeit prüfen" in out_help_de
    assert "/consent - Consent-Status anzeigen" in out_help_de

    out_start_group_en = asyncio.run(
        start_cmd.handler(CommandContext(chat_id=-1, user_id=1, role=Role.NORMAL, command_name="start", argument="en", locale="en"))
    )
    assert out_start_group_en == "Consent management is not configured."


def test_consent_group_privacy_message_is_localized() -> None:
    reg = create_builtin_registry()
    consent_cmd = reg.get("consent")
    assert consent_cmd is not None

    out_group_de = asyncio.run(
        consent_cmd.handler(CommandContext(chat_id=-1, user_id=1, role=Role.NORMAL, command_name="consent", argument=None, locale="de"))
    )
    out_group_en = asyncio.run(
        consent_cmd.handler(CommandContext(chat_id=-1, user_id=1, role=Role.NORMAL, command_name="consent", argument=None, locale="en"))
    )

    assert out_group_de == "Aus Datenschutzgründen nutze bitte /consent im privaten Chat mit mir."
    assert out_group_en == "For privacy, please use /consent in a private chat with me."


def test_ask_error_and_usage_messages_are_localized() -> None:
    reg_fail = create_builtin_registry(ai_service=FakeAIService(fail=True))
    ask_cmd_fail = reg_fail.get("ask")
    assert ask_cmd_fail is not None

    out_usage_de = asyncio.run(
        ask_cmd_fail.handler(CommandContext(chat_id=1, user_id=1, role=Role.ADMIN, command_name="ask", argument="   ", locale="de"))
    )
    assert out_usage_de == "Nutzung: /ask <frage>"

    out_error_de = asyncio.run(
        ask_cmd_fail.handler(CommandContext(chat_id=1, user_id=1, role=Role.ADMIN, command_name="ask", argument="Hi?", locale="de"))
    )
    assert out_error_de == "Sorry, ich kann gerade nicht antworten. Bitte versuche es später erneut."

    out_error_en = asyncio.run(
        ask_cmd_fail.handler(CommandContext(chat_id=1, user_id=1, role=Role.ADMIN, command_name="ask", argument="Hi?", locale="en"))
    )
    assert out_error_en == "Sorry, I cannot answer right now. Please try again later."


def test_ask_scope_is_private_user_isolated_per_sender(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ask_private_scope.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url, ai_service=FakeAIService(answer="ok"))
    ask_cmd = reg.get("ask")
    assert ask_cmd is not None

    asyncio.run(ask_cmd.handler(CommandContext(chat_id=101, user_id=101, role=Role.VIP, command_name="ask", argument="one")))
    asyncio.run(ask_cmd.handler(CommandContext(chat_id=202, user_id=202, role=Role.VIP, command_name="ask", argument="two")))

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        s1 = repo.get_ai_session(scope_type="private_user", user_id=101)
        s2 = repo.get_ai_session(scope_type="private_user", user_id=202)
        assert s1 is not None
        assert s2 is not None
        assert s1.user_id == 101
        assert s2.user_id == 202
        assert s1.session_payload["session_id"] != s2.session_payload["session_id"]


def test_ask_scope_is_group_chat_shared_per_chat(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ask_group_scope.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url, ai_service=FakeAIService(answer="ok"))
    ask_cmd = reg.get("ask")
    assert ask_cmd is not None

    asyncio.run(ask_cmd.handler(CommandContext(chat_id=-7001, user_id=11, role=Role.VIP, command_name="ask", argument="one")))
    asyncio.run(ask_cmd.handler(CommandContext(chat_id=-7001, user_id=22, role=Role.VIP, command_name="ask", argument="two")))

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        s = repo.get_ai_session(scope_type="group_chat", chat_id=-7001)
        assert s is not None
        assert s.chat_id == -7001
        assert s.user_id is None


def test_new_and_reset_rotate_current_scope_session(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'ask_new_reset.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url, ai_service=FakeAIService(answer="ok"))
    ask_cmd = reg.get("ask")
    new_cmd = reg.get("new")
    reset_cmd = reg.get("reset")
    assert ask_cmd is not None and new_cmd is not None and reset_cmd is not None

    ctx = CommandContext(chat_id=333, user_id=333, role=Role.VIP, command_name="ask", argument="seed")
    asyncio.run(ask_cmd.handler(ctx))

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        before = repo.get_ai_session(scope_type="private_user", user_id=333)
        assert before is not None
        before_id = str(before.session_payload["session_id"])

    out_new = asyncio.run(new_cmd.handler(CommandContext(chat_id=333, user_id=333, role=Role.VIP, command_name="new", argument=None, locale="en")))
    assert out_new == "Started a new AI session."

    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        after_new = repo.get_ai_session(scope_type="private_user", user_id=333)
        assert after_new is not None
        assert str(after_new.session_payload["session_id"]) != before_id
        assert after_new.session_payload["reset_reason"] == "explicit_reset"
        new_id = str(after_new.session_payload["session_id"])

    out_reset = asyncio.run(reset_cmd.handler(CommandContext(chat_id=333, user_id=333, role=Role.VIP, command_name="reset", argument=None, locale="en")))
    assert out_reset == "AI session reset."

    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        after_reset = repo.get_ai_session(scope_type="private_user", user_id=333)
        assert after_reset is not None
        assert str(after_reset.session_payload["session_id"]) != new_id
        assert after_reset.session_payload["reset_reason"] == "explicit_reset"


def test_ask_idle_timeout_resets_and_day_rollover_resets(tmp_path, monkeypatch) -> None:
    db_url = f"sqlite:///{tmp_path / 'ask_lifecycle_rollover.db'}"
    init_db(db_url)
    reg = create_builtin_registry(database_url=db_url, ai_service=FakeAIService(answer="ok"))
    ask_cmd = reg.get("ask")
    assert ask_cmd is not None

    ctx = CommandContext(chat_id=444, user_id=444, role=Role.VIP, command_name="ask", argument="first")
    asyncio.run(ask_cmd.handler(ctx))

    sf = create_session_factory(db_url)
    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        row = repo.get_ai_session(scope_type="private_user", user_id=444)
        assert row is not None
        seed_id = str(row.session_payload["session_id"])
        created_at = row.session_payload["created_at"]

        stale = dict(row.session_payload)
        stale["last_activity_at"] = "2000-01-01T00:00:00+00:00"
        stale["last_activity_day"] = "2000-01-01"
        repo.upsert_ai_session(scope_type="private_user", user_id=444, session_payload=stale)

    asyncio.run(ask_cmd.handler(CommandContext(chat_id=444, user_id=444, role=Role.VIP, command_name="ask", argument="second")))

    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        idle_reset = repo.get_ai_session(scope_type="private_user", user_id=444)
        assert idle_reset is not None
        assert str(idle_reset.session_payload["session_id"]) != seed_id
        assert idle_reset.session_payload["reset_reason"] == "idle_timeout"

        rollover = dict(idle_reset.session_payload)
        rollover["created_at"] = created_at
        rollover["last_activity_at"] = idle_reset.session_payload["last_activity_at"]
        rollover["last_activity_day"] = "2001-02-03"
        repo.upsert_ai_session(scope_type="private_user", user_id=444, session_payload=rollover)

    asyncio.run(ask_cmd.handler(CommandContext(chat_id=444, user_id=444, role=Role.VIP, command_name="ask", argument="third")))

    with sf() as session:
        repo = TopicAgentMemoryRepository(session)
        day_reset = repo.get_ai_session(scope_type="private_user", user_id=444)
        assert day_reset is not None
        assert day_reset.session_payload["reset_reason"] == "day_rollover"
        assert day_reset.session_payload["created_at"] != created_at
