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


_PROMPT_TEXTS: dict[str, str] = {
    "de": (
        "Hi! 👋\n\n"
        "bevor wir schreiben, brauche ich kurz dein Okay zu unseren Nutzungsbedingungen.\n\n"
        "Bitte bestätige mit den Buttons unten:\n"
        "• ✅ /accept = akzeptieren\n"
        "• ❌ /decline = ablehnen\n\n"
        "Danke!"
    ),
    "en": (
        "Hi! 👋\n\n"
        "before we chat, I need your quick consent to our terms.\n\n"
        "Please confirm using the buttons below:\n"
        "• ✅ /accept = accept\n"
        "• ❌ /decline = decline\n\n"
        "Thanks!"
    ),
}

_PROMPT_MARKUP: dict[str, dict[str, object]] = {
    "de": {
        "inline_keyboard": [
            [
                {"text": "✅ Akzeptieren", "callback_data": "consent:accept"},
                {"text": "❌ Ablehnen", "callback_data": "consent:decline"},
            ]
        ]
    },
    "en": {
        "inline_keyboard": [
            [
                {"text": "✅ Accept", "callback_data": "consent:accept"},
                {"text": "❌ Decline", "callback_data": "consent:decline"},
            ]
        ]
    },
}


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
    ) -> str:
        if not self._is_eligible(user):
            return "skipped"
        try:
            await self._send_prompt_message(
                send_private_message=send_private_message,
                chat_id=user.telegram_user_id,
                text=self.build_prompt_text(),
                reply_markup=self.build_prompt_markup(),
            )
        except TelegramApiError as exc:
            if self._is_unreachable_error(exc):
                self._consent_service.mark_unreachable(user)
                return "unreachable"
            raise

        self._consent_service.record_prompt(user)
        return "prompted"

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

        has_varkw = any(param.kind == param.VAR_KEYWORD for param in sig.parameters.values())
        if has_varkw:
            return True

        reply_markup_param = sig.parameters.get("reply_markup")
        if reply_markup_param is None:
            return False

        return reply_markup_param.kind in (
            reply_markup_param.POSITIONAL_ONLY,
            reply_markup_param.POSITIONAL_OR_KEYWORD,
            reply_markup_param.KEYWORD_ONLY,
        )

    @staticmethod
    def _is_unreachable_error(exc: TelegramApiError) -> bool:
        msg = str(exc).casefold()
        return any(
            marker in msg
            for marker in (
                "chat not found",
                "bot was blocked",
                "user is deactivated",
                "can't initiate conversation",
                "cannot initiate conversation",
            )
        )

    @staticmethod
    def _normalize_locale(locale: str | None) -> str:
        if isinstance(locale, str) and locale.strip().casefold().startswith("en"):
            return "en"
        return "de"

    @classmethod
    def build_prompt_markup(cls, locale: str | None = None) -> dict[str, object]:
        return _PROMPT_MARKUP[cls._normalize_locale(locale)]

    @classmethod
    def build_prompt_text(cls, locale: str | None = None) -> str:
        return _PROMPT_TEXTS[cls._normalize_locale(locale)]
