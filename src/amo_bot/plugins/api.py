from __future__ import annotations

from dataclasses import dataclass

from amo_bot.auth.roles import Role


@dataclass(slots=True)
class PluginContext:
    actor_role: Role
    actor_telegram_user_id: int


class PluginAPI:
    """Kontrollierte API fuer Plugins, keine Vollzugriffe."""

    def __init__(self) -> None:
        self._outbox: list[tuple[int, str]] = []

    def send_text(self, chat_id: int, text: str) -> None:
        self._outbox.append((chat_id, text[:1000]))

    def get_pending_messages(self) -> list[tuple[int, str]]:
        return self._outbox.copy()
