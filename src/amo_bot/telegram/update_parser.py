from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class TelegramAttachment:
    source_kind: str
    type_hint: str
    file_id: str
    file_unique_id: str | None = None
    width: int | None = None
    height: int | None = None
    size: int | None = None
    mime_type: str | None = None


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
class TelegramReplyToMessage:
    message_id: int
    from_user: TelegramUser | None = None
    text: str = ""
    chat_id: int | None = None
    message_thread_id: int | None = None
    attachments: tuple[TelegramAttachment, ...] = ()


@dataclass(slots=True)
class TelegramMessage:
    message_id: int
    from_user: TelegramUser
    chat: TelegramChat
    text: str
    message_thread_id: int | None = None
    telegram_topic_name: str | None = None
    reply_to_message_id: int | None = None
    reply_to_message_text: str = ""
    reply_to_is_bot: bool = False
    reply_to_user_id: int | None = None
    reply_to_username: str | None = None
    reply_to_user_is_bot: bool = False
    reply_to_message: TelegramReplyToMessage | None = None
    attachments: tuple[TelegramAttachment, ...] = ()

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
class TelegramReactionEvent:
    chat: TelegramChat
    message_id: int
    from_user: TelegramUser | None = None
    user_id: int | None = None
    message_thread_id: int | None = None
    emojis: tuple[str, ...] = ()


@dataclass(slots=True)
class TelegramUpdate:
    update_id: int
    message: TelegramMessage | None
    callback_query: TelegramCallbackQuery | None
    top_level_kind: str | None = None
    message_reaction: TelegramReactionEvent | None = None


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


def _safe_int(raw: Any) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_attachments(raw: Any) -> tuple[TelegramAttachment, ...]:
    if not isinstance(raw, dict):
        return ()

    attachments: list[TelegramAttachment] = []

    photo_raw = raw.get("photo")
    if isinstance(photo_raw, list):
        best_photo: dict[str, Any] | None = None
        best_area = -1
        for item in photo_raw:
            if not isinstance(item, dict):
                continue
            file_id_raw = item.get("file_id")
            if not isinstance(file_id_raw, str) or not file_id_raw:
                continue
            width = _safe_int(item.get("width"))
            height = _safe_int(item.get("height"))
            area = (width or 0) * (height or 0)
            if best_photo is None or area >= best_area:
                best_photo = item
                best_area = area
        if isinstance(best_photo, dict):
            file_id = best_photo.get("file_id")
            if isinstance(file_id, str) and file_id:
                attachments.append(
                    TelegramAttachment(
                        source_kind="photo",
                        type_hint="image",
                        file_id=file_id,
                        file_unique_id=(
                            str(best_photo.get("file_unique_id"))
                            if best_photo.get("file_unique_id") is not None
                            else None
                        ),
                        width=_safe_int(best_photo.get("width")),
                        height=_safe_int(best_photo.get("height")),
                        size=_safe_int(best_photo.get("file_size")),
                        mime_type="image/*",
                    )
                )

    document_raw = raw.get("document")
    if isinstance(document_raw, dict):
        mime_type_raw = document_raw.get("mime_type")
        mime_type = mime_type_raw.strip().casefold() if isinstance(mime_type_raw, str) else ""
        if mime_type.startswith("image/"):
            file_id = document_raw.get("file_id")
            if isinstance(file_id, str) and file_id:
                attachments.append(
                    TelegramAttachment(
                        source_kind="document",
                        type_hint="image_document",
                        file_id=file_id,
                        file_unique_id=(
                            str(document_raw.get("file_unique_id"))
                            if document_raw.get("file_unique_id") is not None
                            else None
                        ),
                        width=_safe_int(document_raw.get("width")),
                        height=_safe_int(document_raw.get("height")),
                        size=_safe_int(document_raw.get("file_size")),
                        mime_type=mime_type,
                    )
                )

    return tuple(attachments)


def _parse_message(raw: Any) -> TelegramMessage | None:
    if not isinstance(raw, dict):
        return None

    from_user = _parse_user(raw.get("from"))
    chat = _parse_chat(raw.get("chat"))
    text = raw.get("text")
    if not isinstance(text, str):
        text = raw.get("caption")

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

    reply_to_message_id: int | None = None
    reply_to_message_text = ""
    reply_to_is_bot = False
    reply_to_user_id: int | None = None
    reply_to_username: str | None = None
    reply_to_user_is_bot = False
    reply_to_message: TelegramReplyToMessage | None = None
    if isinstance(reply_to_message_raw, dict):
        try:
            reply_to_message_id = int(reply_to_message_raw.get("message_id"))
        except (TypeError, ValueError):
            reply_to_message_id = None

        reply_to_user = _parse_user(reply_to_message_raw.get("from"))
        if reply_to_user is not None:
            reply_to_user_id = reply_to_user.id
            reply_to_username = reply_to_user.username
            reply_to_user_is_bot = bool(reply_to_user.is_bot)
            reply_to_is_bot = reply_to_user_is_bot

        reply_text_raw = reply_to_message_raw.get("text")
        if not isinstance(reply_text_raw, str):
            reply_text_raw = reply_to_message_raw.get("caption")
        reply_to_message_text = reply_text_raw if isinstance(reply_text_raw, str) else ""

        if reply_to_message_id is not None:
            reply_to_message = TelegramReplyToMessage(
                message_id=reply_to_message_id,
                from_user=reply_to_user,
                text=reply_to_message_text,
                chat_id=chat.id,
                message_thread_id=message_thread_id,
                attachments=_parse_attachments(reply_to_message_raw),
            )

        if reply_to_is_bot and message_thread_id is not None:
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
        reply_to_message_id=reply_to_message_id,
        reply_to_message_text=reply_to_message_text,
        reply_to_is_bot=reply_to_is_bot,
        reply_to_user_id=reply_to_user_id,
        reply_to_username=reply_to_username,
        reply_to_user_is_bot=reply_to_user_is_bot,
        reply_to_message=reply_to_message,
        attachments=_parse_attachments(raw),
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
    if message is None:
        message = _parse_message(raw.get("maybe_inaccessible_message"))
    data_raw = raw.get("data")
    data = data_raw if isinstance(data_raw, str) else None
    return TelegramCallbackQuery(
        id=callback_id_raw,
        from_user=from_user,
        message=message,
        data=data,
    )


def _parse_reaction_type(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("type") != "emoji":
        return None
    emoji = raw.get("emoji")
    return emoji if isinstance(emoji, str) and emoji else None


def _parse_message_reaction(raw: Any) -> TelegramReactionEvent | None:
    if not isinstance(raw, dict):
        return None
    chat = _parse_chat(raw.get("chat"))
    if chat is None:
        return None
    message_id = _safe_int(raw.get("message_id"))
    if message_id is None:
        return None
    from_user = _parse_user(raw.get("user"))
    actor_chat = _parse_chat(raw.get("actor_chat"))
    user_id = from_user.id if from_user is not None else actor_chat.id if actor_chat is not None else None
    emojis = tuple(
        emoji
        for item in raw.get("new_reaction", [])
        for emoji in (_parse_reaction_type(item),)
        if emoji is not None
    )
    return TelegramReactionEvent(
        chat=chat,
        message_id=message_id,
        from_user=from_user,
        user_id=user_id,
        message_thread_id=_safe_int(raw.get("message_thread_id")),
        emojis=emojis,
    )


def parse_update(raw: Any) -> TelegramUpdate | None:
    if not isinstance(raw, dict):
        return None

    try:
        update_id = int(raw["update_id"])
    except (KeyError, TypeError, ValueError):
        return None

    message = _parse_message(raw.get("message"))
    callback_query = _parse_callback_query(raw.get("callback_query"))
    message_reaction = _parse_message_reaction(raw.get("message_reaction"))

    top_level_kind: str | None = None
    for key in raw.keys():
        if key == "update_id":
            continue
        top_level_kind = str(key)
        break

    return TelegramUpdate(
        update_id=update_id,
        message=message,
        callback_query=callback_query,
        top_level_kind=top_level_kind,
        message_reaction=message_reaction,
    )
