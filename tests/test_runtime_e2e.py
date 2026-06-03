from __future__ import annotations

import asyncio

from sqlalchemy import select

from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import AuditEvent, DbRole, User
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.role_resolver import DBRoleResolver


class FakeAIService:
    def __init__(self, answer: str = "fake-ai-answer") -> None:
        self.answer = answer
        self.prompts: list[str] = []

    async def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answer


class CapturingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, int | None]] = []

    async def send_text(self, chat_id: int, text: str, message_thread_id: int | None = None) -> object:
        self.sent.append((chat_id, text, message_thread_id))
        return {"ok": True}



def _mk_update(*, uid: int, chat_id: int, text: str, update_id: int = 1) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 100,
            "from": {"id": uid, "is_bot": False, "first_name": "U", "username": f"u{uid}"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def _bootstrap_runtime(db_url: str, *, ai_answer: str = "fake-ai-answer") -> tuple[Dispatcher, CapturingSender, FakeAIService]:
    init_db(db_url)
    sf = create_session_factory(db_url)
    ai = FakeAIService(answer=ai_answer)
    registry = create_builtin_registry(database_url=db_url, ai_service=ai)
    sender = CapturingSender()
    dispatcher = Dispatcher(
        command_registry=registry,
        role_resolver=DBRoleResolver(sf),
        send_text=sender.send_text,
        bot_username="AmoBot",
    )
    return dispatcher, sender, ai


def _seed_roles(db_url: str) -> None:
    sf = create_session_factory(db_url)
    with sf() as session:
        role_map = {row.name: row.id for row in session.scalars(select(DbRole)).all()}
        session.add_all(
            [
                User(telegram_user_id=1000, role_id=role_map["owner"]),
                User(telegram_user_id=2000, role_id=role_map["admin"]),
                User(telegram_user_id=3000, role_id=role_map["vip"]),
                User(telegram_user_id=4000, role_id=role_map["normal"]),
                User(telegram_user_id=5000, role_id=role_map["ignore"]),
            ]
        )
        session.commit()


def test_runtime_bootstrap_without_real_telegram_or_ollama(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'runtime_bootstrap.db'}"
    dispatcher, sender, _ai = _bootstrap_runtime(db_url)

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=42, chat_id=7, text="/ping")))

    assert sender.sent == [(7, "pong", None)]


def test_dispatcher_e2e_ping_role_help_setrole_and_ask_ignore_is_silent(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'runtime_e2e.db'}"
    dispatcher, sender, ai = _bootstrap_runtime(db_url, ai_answer="answer-from-fake-ai")
    _seed_roles(db_url)

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=4000, chat_id=40, text="/ping", update_id=1)))
    assert sender.sent[-1] == (40, "pong", None)

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=4000, chat_id=40, text="/help", update_id=2)))
    help_normal = sender.sent[-1][1]
    assert "/ask" not in help_normal
    assert "/setrole" not in help_normal

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=2000, chat_id=20, text="/help", update_id=3)))
    help_admin = sender.sent[-1][1]
    assert "/ask" in help_admin
    assert "/setrole" in help_admin

    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=3000, chat_id=30, text="/role", update_id=4)))
    assert sender.sent[-1] == (30, "deine rolle: vip", None)

    asyncio.run(
        dispatcher.handle_raw_update(_mk_update(uid=2000, chat_id=20, text="/setrole 4000 vip", update_id=5))
    )
    assert sender.sent[-1][1].startswith("rolle aktualisiert: 4000")

    sf = create_session_factory(db_url)
    with sf() as session:
        user_4000 = session.scalar(select(User).where(User.telegram_user_id == 4000))
        assert user_4000 is not None
        assert user_4000.role.name == "vip"
        role_events = session.scalars(select(AuditEvent).where(AuditEvent.event_type == "role_set")).all()
        assert len(role_events) >= 1

    for uid, chat_id in [(1000, 10), (2000, 20), (3000, 30)]:
        asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=uid, chat_id=chat_id, text="/ask hi ai", update_id=100 + uid)))
        assert sender.sent[-1] == (chat_id, "answer-from-fake-ai", None)

    prompts = ai.prompts[:]
    assert len(prompts) == 3
    for prompt in prompts:
        assert "Context:" in prompt
        assert "system-provided" not in prompt
        assert "higher priority" not in prompt
        assert "memory/recent chat" not in prompt
        assert "model training date" not in prompt
        assert "Current date:" in prompt
        assert "Timezone: Europe/Berlin" in prompt
        assert prompt.endswith("User message:\nhi ai")

    before = len(sender.sent)
    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=4000, chat_id=40, text="/ask blocked", update_id=6)))
    assert sender.sent[-1] == (40, "answer-from-fake-ai", None)

    ai_calls_before_ignore = len(ai.prompts)
    asyncio.run(dispatcher.handle_raw_update(_mk_update(uid=5000, chat_id=50, text="/ask blocked", update_id=7)))
    assert len(sender.sent) == before + 1  # ignore stays silent for blocked commands
    assert len(ai.prompts) == ai_calls_before_ignore  # ignore must not trigger AI requests


def test_image_analysis_prompt_gets_default_german_language_rule() -> None:
    from amo_bot.ai.image_analyze_orchestrator import (
        ImageAnalyzeOrchestrator,
        ImageAnalyzeOrchestratorRequest,
        ImageAnalyzeProviderRequest,
        ImageAnalyzeProviderResult,
    )
    from amo_bot.auth.roles import Role

    class RecordingProvider:
        name = "vision"

        def __init__(self) -> None:
            self.requests: list[ImageAnalyzeProviderRequest] = []

        def analyze(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult:
            self.requests.append(request)
            return ImageAnalyzeProviderResult(provider=self.name, summary="ok")

    provider = RecordingProvider()
    orchestrator = ImageAnalyzeOrchestrator(provider=provider)
    result = orchestrator.evaluate_and_maybe_invoke_provider(
        request=ImageAnalyzeOrchestratorRequest(
            user_id=1,
            role=Role.ADMIN,
            chat_id=2,
            message_thread_id=None,
            command="auto_image",
            reply_to_image={"ok": True, "type_hint": "image", "file_unique_id": "img1"},
            prompt="Was ist auf dem Bild?",
        )
    )

    assert result.allowed is True
    assert len(provider.requests) == 1
    prompt = provider.requests[0].prompt
    assert "Antworte standardmäßig auf Deutsch" in prompt
    assert "Wenn der Nutzer klar eine andere Sprache nutzt" in prompt
    assert "Nutzeranfrage:\nWas ist auf dem Bild?" in prompt
    assert "system-provided" not in prompt
    assert "higher priority" not in prompt
