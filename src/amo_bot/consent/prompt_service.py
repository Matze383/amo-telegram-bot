from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import Signature, signature
from typing import cast

from amo_bot.consent.service import (
    CONSENT_DECLINED,
    CONSENT_PENDING,
    CONSENT_UNREACHABLE,
    ConsentService,
)
from amo_bot.db.models import User
from amo_bot.telegram.client import TelegramApiError

SendPrivateMessageFn = Callable[[int, str], Awaitable[object]]
SendPrivateMessageWithMarkupFn = Callable[[int, str, dict[str, object]], Awaitable[object]]


class ConsentPromptService:
    def __init__(
        self,
        *,
        consent_service: ConsentService | None = None,
    ) -> None:
        self._consent_service = consent_service or ConsentService()

    async def maybe_prompt_user(
        self,
        *,
        user: User,
        send_private_message: SendPrivateMessageFn | SendPrivateMessageWithMarkupFn,
    ) -> bool:
        if not self._is_eligible(user):
            return False
        try:
            await self._send_prompt_message(
                send_private_message=send_private_message,
                chat_id=user.telegram_user_id,
                text=self._build_prompt_text(),
                reply_markup=self._build_prompt_markup(),
            )
        except TelegramApiError as exc:
            if self._is_unreachable_error(exc):
                self._consent_service.mark_unreachable(user)
                return False
            raise

        self._consent_service.record_prompt(user)
        return True

    def _is_eligible(self, user: User) -> bool:
        status = self._consent_service.get_status(user)
        if status in {CONSENT_DECLINED, CONSENT_UNREACHABLE} or status != CONSENT_PENDING:
            return False

        # One-shot auto prompt policy: only users with no prior successful prompt record are eligible.
        return int(user.consent_prompt_count or 0) == 0

    @staticmethod
    async def _send_prompt_message(
        *,
        send_private_message: SendPrivateMessageFn | SendPrivateMessageWithMarkupFn,
        chat_id: int,
        text: str,
        reply_markup: dict[str, object],
    ) -> object:
        if ConsentPromptService._supports_reply_markup(send_private_message):
            send_with_markup = cast(SendPrivateMessageWithMarkupFn, send_private_message)
            return await send_with_markup(chat_id, text, reply_markup)

        send_without_markup = cast(SendPrivateMessageFn, send_private_message)
        return await send_without_markup(chat_id, text)

    @staticmethod
    def _supports_reply_markup(
        send_private_message: SendPrivateMessageFn | SendPrivateMessageWithMarkupFn,
    ) -> bool:
        try:
            sig: Signature = signature(send_private_message)
        except (TypeError, ValueError):
            return True

        has_varargs = any(param.kind == param.VAR_POSITIONAL for param in sig.parameters.values())
        if has_varargs:
            return True

        positional_params = [
            param
            for param in sig.parameters.values()
            if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
        ]
        return len(positional_params) >= 3

    @staticmethod
    def _is_unreachable_error(exc: TelegramApiError) -> bool:
        msg = str(exc).lower()
        return "forbidden" in msg and ("can't initiate conversation" in msg or "cannot initiate conversation" in msg)

    @staticmethod
    def _build_prompt_markup() -> dict[str, object]:
        return {
            "inline_keyboard": [
                [
                    {"text": "✅ Akzeptieren", "callback_data": "consent:accept"},
                    {"text": "❌ Ablehnen", "callback_data": "consent:decline"},
                ]
            ]
        }

    @staticmethod
    def _build_prompt_text() -> str:
        return (
            "Hallo! Bevor ich dir antworten oder deine Nachrichten verarbeiten darf, "
            "brauche ich kurz dein Einverständnis.\n\n"
            "Wenn du zustimmst, kann der Bot deine Telegram-Nutzerinformationen "
            "speichern und verwenden, um Rollen, Gruppenfunktionen und Bot-Antworten "
            "bereitzustellen.\n\n"
            "Du kannst zustimmen mit:\n"
            "/accept\n\n"
            "Du kannst ablehnen mit:\n"
            "/decline\n\n"
            "Deinen aktuellen Status siehst du mit:\n"
            "/consent"
        )
