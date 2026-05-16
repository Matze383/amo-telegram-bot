from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class TelegramUser:
    id: int
    is_bot: bool
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


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
    telegram_topic_name: str | None = None
    reply_to_is_bot: bool = False

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
class TelegramCallbackQuery:
    id: str
    from_user: TelegramUser
    message: TelegramMessage | None
    data: str | None


@dataclass(slots=True)
class TelegramUpdate:
    update_id: int
    message: TelegramMessage | None
    callback_query: TelegramCallbackQuery | None


def _parse_user(raw: Any) -> TelegramUser | None:
    if not isinstance(raw, dict):
        return None
    try:
        return TelegramUser(
            id=int(raw["id"]),
            is_bot=bool(raw.get("is_bot", False)),
            first_name=str(raw.get("first_name", "")),
            last_name=str(raw["last_name"]) if raw.get("last_name") is not None else None,
            username=str(raw["username"]) if raw.get("username") is not None else None,
            language_code=str(raw["language_code"]) if raw.get("language_code") is not None else None,
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

    if from_user is None or chat is None:
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

    def _extract_topic_name(event_container: Any) -> str | None:
        if not isinstance(event_container, dict):
            return None
        for topic_event_key in ("forum_topic_created", "forum_topic_edited"):
            topic_event_raw = event_container.get(topic_event_key)
            if not isinstance(topic_event_raw, dict):
                continue
            name_raw = topic_event_raw.get("name")
            if isinstance(name_raw, str):
                cleaned_name = name_raw.strip()
                if cleaned_name:
                    return cleaned_name
        return None

    reply_to_message_raw = raw.get("reply_to_message")
    telegram_topic_name = _extract_topic_name(raw)
    if telegram_topic_name is None:
        telegram_topic_name = _extract_topic_name(reply_to_message_raw)

    reply_to_is_bot = False
    if isinstance(reply_to_message_raw, dict):
        reply_to_user = _parse_user(reply_to_message_raw.get("from"))
        reply_to_is_bot = bool(reply_to_user.is_bot) if reply_to_user is not None else False
        if reply_to_is_bot and message_thread_id is not None:
            try:
                reply_to_message_id = int(reply_to_message_raw.get("message_id"))
            except (TypeError, ValueError):
                reply_to_message_id = None
            # In forum topics Telegram may include the topic-root message in reply_to_message
            # for ordinary thread messages. Treat that as thread context, not explicit reply.
            if reply_to_message_id == message_thread_id:
                reply_to_is_bot = False

    return TelegramMessage(
        message_id=message_id,
        from_user=from_user,
        chat=chat,
        text=text if isinstance(text, str) else "",
        message_thread_id=message_thread_id,
        telegram_topic_name=telegram_topic_name,
        reply_to_is_bot=reply_to_is_bot,
    )


def _parse_callback_query(raw: Any) -> TelegramCallbackQuery | None:
    if not isinstance(raw, dict):
        return None

    callback_id_raw = raw.get("id")
    if not isinstance(callback_id_raw, str) or not callback_id_raw:
        return None

    from_user = _parse_user(raw.get("from"))
    if from_user is None:
        return None

    message = _parse_message(raw.get("message"))
    data_raw = raw.get("data")
    data = data_raw if isinstance(data_raw, str) else None
    return TelegramCallbackQuery(
        id=callback_id_raw,
        from_user=from_user,
        message=message,
        data=data,
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
        callback_query=_parse_callback_query(raw.get("callback_query")),
    )
