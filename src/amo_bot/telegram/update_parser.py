from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class TelegramUser:
    id: int
    is_bot: bool
    first_name: str
    username: str | None


@dataclass(slots=True)
class TelegramChat:
    id: int
    type: str
    title: str | None
    username: str | None


@dataclass(slots=True)
class CommandMatch:
    name: str
    argument: str | None
    target_bot: str | None


@dataclass(slots=True)
class TelegramMessage:
    message_id: int
    from_user: TelegramUser
    chat: TelegramChat
    text: str
    message_thread_id: int | None = None

    def parse_command(self, bot_username: str | None = None) -> CommandMatch | None:
        raw = self.text.strip()
        if not raw.startswith("/"):
            return None

        first, sep, tail = raw.partition(" ")
        token = first[1:]
        if not token:
            return None

        cmd_name, at, cmd_bot = token.partition("@")
        if not cmd_name:
            return None

        if at:
            if not bot_username:
                return None
            if cmd_bot.casefold() != bot_username.casefold():
                return None

        argument = tail.strip() if sep else ""
        return CommandMatch(
            name=cmd_name.casefold(),
            argument=argument or None,
            target_bot=cmd_bot or None,
        )


@dataclass(slots=True)
class TelegramUpdate:
    update_id: int
    message: TelegramMessage | None


def _parse_user(raw: Any) -> TelegramUser | None:
    if not isinstance(raw, dict):
        return None
    try:
        return TelegramUser(
            id=int(raw["id"]),
            is_bot=bool(raw.get("is_bot", False)),
            first_name=str(raw.get("first_name", "")),
            username=str(raw["username"]) if raw.get("username") is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _parse_chat(raw: Any) -> TelegramChat | None:
    if not isinstance(raw, dict):
        return None
    try:
        return TelegramChat(
            id=int(raw["id"]),
            type=str(raw.get("type", "private")),
            title=str(raw["title"]) if raw.get("title") is not None else None,
            username=str(raw["username"]) if raw.get("username") is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _parse_message(raw: Any) -> TelegramMessage | None:
    if not isinstance(raw, dict):
        return None

    from_user = _parse_user(raw.get("from"))
    chat = _parse_chat(raw.get("chat"))
    text = raw.get("text")

    if from_user is None or chat is None or not isinstance(text, str):
        return None

    try:
        message_id = int(raw["message_id"])
    except (KeyError, TypeError, ValueError):
        return None

    message_thread_id_raw = raw.get("message_thread_id")
    message_thread_id: int | None
    if message_thread_id_raw is None:
        message_thread_id = None
    else:
        try:
            message_thread_id = int(message_thread_id_raw)
        except (TypeError, ValueError):
            message_thread_id = None

    return TelegramMessage(
        message_id=message_id,
        from_user=from_user,
        chat=chat,
        text=text,
        message_thread_id=message_thread_id,
    )


def parse_update(raw: Any) -> TelegramUpdate | None:
    if not isinstance(raw, dict):
        return None

    try:
        update_id = int(raw["update_id"])
    except (KeyError, TypeError, ValueError):
        return None

    return TelegramUpdate(
        update_id=update_id,
        message=_parse_message(raw.get("message")),
    )
