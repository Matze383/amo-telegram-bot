from __future__ import annotations


from amo_bot.consent.service import (
    CONSENT_DECLINED,
    CONSENT_PENDING,
    CONSENT_UNREACHABLE,
    ConsentService,
)
from amo_bot.db.models import User
from amo_bot.telegram.client import TelegramApiError

class ConsentPromptService:
    def __init__(
        self,
        *,
        consent_service: ConsentService | None = None,
    ) -> None:
        self._consent_service = consent_service or ConsentService()

    async def maybe_prompt_user(self, *, user: User, send_private_message) -> bool:
        if not self._is_eligible(user):
            return False
        try:
            await send_private_message(user.telegram_user_id, self._build_prompt_text())
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
    def _is_unreachable_error(exc: TelegramApiError) -> bool:
        msg = str(exc).lower()
        return "forbidden" in msg and ("can't initiate conversation" in msg or "cannot initiate conversation" in msg)

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
