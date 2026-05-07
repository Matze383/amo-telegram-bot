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
        ask_cmd.handler(CommandContext(chat_id=1, user_id=1, role=Role.VIP, command_name="ask", argument="   "))
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
        ask_cmd.handler(CommandContext(chat_id=1, user_id=1, role=Role.OWNER, command_name="ask", argument="Hi?"))
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
