from __future__ import annotations

from datetime import datetime, timezone

from amo_bot.consent.prompt_service import ConsentPromptService
from amo_bot.db.models import User
from amo_bot.telegram.client import TelegramApiError


def _user(status: str = "pending", prompt_count: int = 0, prompted_at: datetime | None = None) -> User:
    u = User(telegram_user_id=123, role_id=1)
    u.consent_status = status
    u.consent_prompt_count = prompt_count
    u.consent_prompted_at = prompted_at
    return u


def test_pending_user_gets_prompt_and_recorded() -> None:
    svc = ConsentPromptService()
    user = _user(status="pending")
    sent: list[tuple[int, str]] = []

    async def _send(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    import asyncio
    changed = asyncio.run(svc.maybe_prompt_user(user=user, send_private_message=_send))

    assert changed is True
    assert len(sent) == 1
    assert sent[0][0] == 123
    assert "/accept" in sent[0][1]
    assert user.consent_prompt_count == 1
    assert user.consent_prompted_at is not None


def test_non_pending_status_does_not_send(status: str = "accepted") -> None:
    svc = ConsentPromptService()

    for state in ("accepted", "declined", "unreachable"):
        user = _user(status=state)
        calls = 0

        async def _send(_chat_id: int, _text: str) -> None:
            nonlocal calls
            calls += 1

        import asyncio
        sent = asyncio.run(svc.maybe_prompt_user(user=user, send_private_message=_send))
        assert sent is False
        assert calls == 0


def test_pending_user_with_existing_prompt_count_is_not_prompted() -> None:
    svc = ConsentPromptService()
    user = _user(status="pending", prompt_count=1)

    async def _send(_chat_id: int, _text: str) -> None:
        raise AssertionError("should not send")

    import asyncio
    sent = asyncio.run(svc.maybe_prompt_user(user=user, send_private_message=_send))
    assert sent is False


def test_pending_user_with_prompt_timestamp_is_not_prompted_again() -> None:
    svc = ConsentPromptService()
    user = _user(status="pending", prompt_count=2, prompted_at=datetime.now(timezone.utc))

    async def _send(_chat_id: int, _text: str) -> None:
        raise AssertionError("should not send")

    import asyncio
    sent = asyncio.run(svc.maybe_prompt_user(user=user, send_private_message=_send))
    assert sent is False


def test_forbidden_dm_marks_unreachable() -> None:
    svc = ConsentPromptService()
    user = _user(status="pending")

    async def _send(_chat_id: int, _text: str) -> None:
        raise TelegramApiError("Forbidden: bot can't initiate conversation with a user")

    import asyncio
    sent = asyncio.run(svc.maybe_prompt_user(user=user, send_private_message=_send))

    assert sent is False
    assert user.consent_status == "unreachable"


def test_non_unreachable_api_error_is_raised() -> None:
    svc = ConsentPromptService()
    user = _user(status="pending")

    async def _send(_chat_id: int, _text: str) -> None:
        raise TelegramApiError("HTTP 500: internal")

    import asyncio
    import pytest
    with pytest.raises(TelegramApiError):
        asyncio.run(svc.maybe_prompt_user(user=user, send_private_message=_send))
