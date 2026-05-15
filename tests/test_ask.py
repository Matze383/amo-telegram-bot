import asyncio

from amo_bot.ai.ollama import OllamaError
from amo_bot.ai.service import AIService
from amo_bot.auth.roles import Role
from amo_bot.telegram.commands import CommandContext, create_builtin_registry


class FakeAIService:
    def __init__(self, answer: str = "ok", fail: bool = False) -> None:
        self.answer = answer
        self.fail = fail

    async def ask(self, prompt: str) -> str:
        if self.fail:
            raise OllamaError("boom")
        return self.answer


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
