from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol

from amo_bot.ai.router import AIRouter, AIRouterReasonCode
from amo_bot.auth.permissions import can_use_bot
from amo_bot.auth.roles import Role, role_meets_minimum
from amo_bot.consent import CONSENT_UNREACHABLE, ConsentService
from amo_bot.db.base import create_session_factory
from amo_bot.db.repositories import PrivateChatPolicyRepository, TopicAgentMemoryRepository, TopicRecentMessageRecord
from amo_bot.db.models import AuditEvent, User
from amo_bot.plugins.command_runtime import CommandActor, CommandInvocation, PluginCommandExecutor
from amo_bot.telegram.commands import CommandContext, CommandRegistry, RoleResolver, resolve_locale, t_text
from amo_bot.telegram.live_edit_adapter import DisabledTelegramLiveEditAdapter, TelegramLiveEditAdapter
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.telegram.update_parser import TelegramMessage, parse_update


AUTOREPLY_ALLOWED_ROLES: set[Role] = {Role.OWNER, Role.ADMIN, Role.VIP}
AI_AUTOREPLY_ERROR_FALLBACK_TEXT = {
    "de": "Ich konnte gerade keine KI-Antwort erzeugen. Bitte versuch es gleich nochmal.",
    "en": "I couldn't generate an AI reply right now. Please try again in a moment.",
}

SendTextFn = Callable[[int, str, int | None], Awaitable[object]]
SendMarkupFn = Callable[[int, str, dict[str, Any], int | None], Awaitable[object]]
SendPrivateMarkupFn = Callable[[int, str, dict[str, Any]], Awaitable[object]]
AnswerCallbackFn = Callable[[str, str | None], Awaitable[object]]

logger = logging.getLogger(__name__)


class MessagePersistence(Protocol):
    async def persist_message(self, message: TelegramMessage) -> None: ...

    async def persist_bot_sent_message(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        message_id: int,
        text: str,
        bot_username: str | None = None,
    ) -> None: ...


@dataclass(slots=True)
class _RecentAutoImageCandidate:
    chat_id: int
    message_thread_id: int | None
    user_id: int
    message_id: int
    attachments: tuple[Any, ...]
    observed_at: datetime


@dataclass(slots=True)
class Dispatcher:
    command_registry: CommandRegistry
    role_resolver: RoleResolver
    send_text: SendTextFn
    send_markup: SendMarkupFn | None = None
    send_private_markup: SendPrivateMarkupFn | None = None
    answer_callback: AnswerCallbackFn | None = None
    bot_username: str | None = None
    message_persistence: MessagePersistence | None = None
    plugin_command_executor: PluginCommandExecutor | None = None
    database_url: str | None = None
    owner_notifier: OwnerNotifier | None = None
    ai_service: Any | None = None
    live_edit_adapter: TelegramLiveEditAdapter | None = None
    auto_image_followup_ttl_seconds: int = 180
    _recent_auto_image_candidates: list[_RecentAutoImageCandidate] = field(default_factory=list)

    async def handle_raw_update(self, raw_update: object) -> None:
        update = parse_update(raw_update)
        if update is None:
            logger.warning("telegram update parse failed: raw_type=%s", type(raw_update).__name__)
            return

        callback_query = update.callback_query
        if callback_query is not None:
            logger.info(
                "telegram callback parsed update_id=%s kind=%s callback_id=%s data_prefix=%s data_len=%s has_message=%s has_maybe_inaccessible=%s",
                update.update_id,
                update.top_level_kind,
                callback_query.id,
                (callback_query.data or "")[:32],
                len(callback_query.data or ""),
                callback_query.message is not None,
                isinstance(raw_update, dict) and isinstance(raw_update.get("callback_query"), dict) and raw_update.get("callback_query", {}).get("maybe_inaccessible_message") is not None,
            )
            await self._handle_callback_query(callback_query)
            return

        if update.message is None:
            logger.warning(
                "telegram update ignored update_id=%s kind=%s keys=%s",
                update.update_id,
                update.top_level_kind,
                sorted(list(raw_update.keys())) if isinstance(raw_update, dict) else None,
            )
            return

        message = update.message
        if message.from_user.is_bot:
            return

        if self.message_persistence is not None:
            try:
                await self.message_persistence.persist_message(message)
            except Exception:
                logger.exception("Failed to persist Telegram message; continuing update handling")

        command = message.parse_command(bot_username=self.bot_username)

        role = await self.role_resolver.resolve(
            message.from_user.id,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
        )
        attachment_count = len(message.attachments)
        has_photo_attachment = any(item.type_hint == "image" or item.source_kind == "photo" for item in message.attachments)
        has_image_document = any(item.type_hint == "image_document" for item in message.attachments)
        logger.info(
            "telegram message parsed update_id=%s chat_id=%s message_thread_id=%s message_id=%s user_id=%s chat_type=%s text_len=%s attachment_count=%s has_photo_attachment=%s has_image_document=%s command=%s role=%s",
            update.update_id,
            message.chat.id,
            message.message_thread_id,
            message.message_id,
            message.from_user.id,
            message.chat.type,
            len(message.text or ""),
            attachment_count,
            has_photo_attachment,
            has_image_document,
            command.name if command is not None else None,
            role.value,
        )

        if command is None:
            is_addressed_for_auto_image = self._is_addressed_for_auto_image(message=message, bot_username=self.bot_username)
            followup_candidate = self._resolve_auto_image_followup_candidate(message=message)
            followup_source = "none"
            followup_candidate_age_seconds: int | None = None
            effective_attachments = message.attachments
            if followup_candidate is not None:
                followup_source = "recent_same_scope"
                followup_candidate_age_seconds = max(
                    0,
                    int((datetime.now(UTC) - followup_candidate.observed_at).total_seconds()),
                )
                effective_attachments = followup_candidate.attachments

            if not message.attachments and followup_candidate is None:
                logger.info(
                    "auto_image decision=skipped_no_attachments update_id=%s chat_id=%s message_thread_id=%s message_id=%s user_id=%s role=%s",
                    update.update_id,
                    message.chat.id,
                    message.message_thread_id,
                    message.message_id,
                    message.from_user.id,
                    role.value,
                )
            elif not can_use_bot(role):
                logger.info(
                    "auto_image decision=skipped_role update_id=%s chat_id=%s message_thread_id=%s message_id=%s user_id=%s role=%s attachment_count=%s has_photo_attachment=%s has_image_document=%s",
                    update.update_id,
                    message.chat.id,
                    message.message_thread_id,
                    message.message_id,
                    message.from_user.id,
                    role.value,
                    attachment_count,
                    has_photo_attachment,
                    has_image_document,
                )
            elif self.plugin_command_executor is None:
                logger.info(
                    "auto_image decision=skipped_no_executor update_id=%s chat_id=%s message_thread_id=%s message_id=%s user_id=%s role=%s attachment_count=%s has_photo_attachment=%s has_image_document=%s",
                    update.update_id,
                    message.chat.id,
                    message.message_thread_id,
                    message.message_id,
                    message.from_user.id,
                    role.value,
                    attachment_count,
                    has_photo_attachment,
                    has_image_document,
                )
            elif not is_addressed_for_auto_image:
                logger.info(
                    "auto_image decision=skipped_not_addressed update_id=%s chat_id=%s message_thread_id=%s message_id=%s user_id=%s role=%s attachment_count=%s has_photo_attachment=%s has_image_document=%s",
                    update.update_id,
                    message.chat.id,
                    message.message_thread_id,
                    message.message_id,
                    message.from_user.id,
                    role.value,
                    attachment_count,
                    has_photo_attachment,
                    has_image_document,
                )
            else:
                logger.info(
                    "auto_image decision=invoked update_id=%s chat_id=%s message_thread_id=%s message_id=%s user_id=%s role=%s attachment_count=%s has_photo_attachment=%s has_image_document=%s",
                    update.update_id,
                    message.chat.id,
                    message.message_thread_id,
                    message.message_id,
                    message.from_user.id,
                    role.value,
                    attachment_count,
                    has_photo_attachment,
                    has_image_document,
                )
                handled_image = await self.plugin_command_executor.analyze_image_automatically(
                    actor=CommandActor(telegram_user_id=message.from_user.id, role=role),
                    invocation=CommandInvocation(
                        command_name="auto_image",
                        argument=message.text or None,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        attachments=effective_attachments,
                    ),
                )
                if followup_candidate is not None:
                    logger.info(
                        "auto_image followup_bridge decision=resolved source=%s chat_id=%s message_thread_id=%s message_id=%s user_id=%s image_message_id=%s age_seconds=%s attachment_count=%s",
                        followup_source,
                        message.chat.id,
                        message.message_thread_id,
                        message.message_id,
                        message.from_user.id,
                        followup_candidate.message_id,
                        followup_candidate_age_seconds,
                        len(effective_attachments),
                    )
                logger.info(
                    "auto_image decision=%s update_id=%s chat_id=%s message_thread_id=%s message_id=%s user_id=%s role=%s handled=%s",
                    "handled" if handled_image else "not_handled",
                    update.update_id,
                    message.chat.id,
                    message.message_thread_id,
                    message.message_id,
                    message.from_user.id,
                    role.value,
                    handled_image,
                )
                if handled_image:
                    return

            if message.attachments:
                self._remember_auto_image_candidate(message=message)

            await self._maybe_handle_ai_autoreply(
                message=message,
                role=role,
                bot_username=self.bot_username,
                from_parsed_update=True,
            )
            return

        if not can_use_bot(role):
            return

        command_name = command.name
        if command is not None and self._is_consent_command(command_name):
            pass
        elif self._is_consent_blocked(
            user_id=message.from_user.id,
            role=role,
            command_name=command_name,
            chat_type=message.chat.type,
        ):
            if command is not None:
                response = self._consent_block_message(
                    chat_type=message.chat.type,
                    blocked_as_unreachable=self._is_user_unreachable(message.from_user.id),
                    locale=resolve_locale(
                        explicit_arg=None,
                        telegram_language_code=getattr(message.from_user, "language_code", None),
                    ),
                )
                if response:
                    await self._send_text(message.chat.id, response, message.message_thread_id)
            return

        command_def = self.command_registry.get(command.name)
        if command_def is None:
            if message.chat.type == "private" and self.database_url is not None:
                with create_session_factory(self.database_url)() as session:
                    min_plugin_command_role = PrivateChatPolicyRepository(session).get_policy().min_plugin_command_role
                if not role_meets_minimum(role, min_plugin_command_role):
                    return

            plugin_handled = False
            if self.plugin_command_executor is not None:
                plugin_handled = await self.plugin_command_executor.execute(
                    actor=CommandActor(telegram_user_id=message.from_user.id, role=role),
                    invocation=CommandInvocation(
                        command_name=command.name,
                        argument=command.argument,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        attachments=message.attachments,
                    ),
                )
            if plugin_handled:
                return
            await self._send_text(message.chat.id, self._unknown_command_message(message=message, command_name=command.name), message.message_thread_id)
            return

        if message.chat.type == "private" and self.database_url is not None:
            with create_session_factory(self.database_url)() as session:
                min_general_command_role = PrivateChatPolicyRepository(session).get_policy().min_general_command_role
            if not role_meets_minimum(role, min_general_command_role):
                return

        if not self.command_registry.is_allowed(command.name, role):
            return

        ctx = CommandContext(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            role=role,
            command_name=command.name,
            argument=command.argument,
            locale=resolve_locale(
                explicit_arg=command.argument if command.name.casefold() in {"start", "help", "consent", "accept", "decline", "ask", "webui", "test", "ping", "role", "setrole"} else None,
                telegram_language_code=getattr(message.from_user, "language_code", None),
            ),
        )
        response = await command_def.handler(ctx)
        if isinstance(response, dict):
            text = response.get("text")
            reply_markup = response.get("reply_markup")
            target_user_id = response.get("target_user_id")
            group_fallback_text = response.get("group_fallback_text")
            group_success_text = response.get("group_success_text")

            if isinstance(text, str) and text:
                is_group_like = (
                    message.chat.id < 0
                    or message.chat.type != "private"
                    or message.message_thread_id is not None
                )

                if (
                    isinstance(target_user_id, int)
                    and target_user_id > 0
                    and is_group_like
                    and isinstance(reply_markup, dict)
                    and self.send_private_markup is not None
                ):
                    try:
                        logger.info(
                            "/test private route: chat_id=%s user_id=%s is_group_like=%s dm_success=false",
                            message.chat.id,
                            target_user_id,
                            is_group_like,
                        )
                        await self.send_private_markup(target_user_id, text, reply_markup)
                    except Exception as exc:
                        msg = str(exc).casefold()
                        logger.info(
                            "/test private route: chat_id=%s user_id=%s is_group_like=%s dm_success=false",
                            message.chat.id,
                            target_user_id,
                            is_group_like,
                        )
                        blocked = any(
                            marker in msg
                            for marker in (
                                "forbidden",
                                "bot was blocked",
                                "chat not found",
                                "cannot initiate conversation",
                                "can't initiate conversation",
                            )
                        )
                        if blocked and isinstance(group_fallback_text, str) and group_fallback_text:
                            await self._send_text(message.chat.id, group_fallback_text, message.message_thread_id)
                        else:
                            raise
                    else:
                        logger.info(
                            "/test private route: chat_id=%s user_id=%s is_group_like=%s dm_success=true",
                            message.chat.id,
                            target_user_id,
                            is_group_like,
                        )
                        if isinstance(group_success_text, str) and group_success_text:
                            await self._send_text(message.chat.id, group_success_text, message.message_thread_id)
                    return

                if isinstance(reply_markup, dict) and self.send_markup is not None:
                    await self.send_markup(message.chat.id, text, reply_markup, message.message_thread_id)
                else:
                    await self._send_text(message.chat.id, text, message.message_thread_id)
            return
        if response:
            await self._send_text(message.chat.id, response, message.message_thread_id)

    async def _handle_callback_query(self, callback_query: Any) -> None:
        if callback_query.from_user.is_bot:
            return

        role = await self.role_resolver.resolve(
            callback_query.from_user.id,
            chat_id=callback_query.message.chat.id if callback_query.message is not None else None,
            chat_type=callback_query.message.chat.type if callback_query.message is not None else None,
        )
        if not can_use_bot(role):
            return

        data = callback_query.data or ""

        if data in {"consent:accept", "consent:decline"}:
            await self._handle_consent_callback(callback_query=callback_query, role=role, data=data)
            return

        if data.startswith("yt_rss:") and self.plugin_command_executor is not None:
            message = callback_query.message
            logger.info(
                "telegram callback routing candidate update_kind=callback_query callback_id=%s data_prefix=%s data_len=%s has_message=%s chat_id=%s thread_id=%s message_id=%s",
                callback_query.id,
                data[:32],
                len(data),
                message is not None,
                message.chat.id if message is not None else None,
                message.message_thread_id if message is not None else None,
                message.message_id if message is not None else None,
            )
            if message is None:
                logger.warning(
                    "telegram callback routing skipped reason=no_message callback_id=%s data_prefix=%s data_len=%s",
                    callback_query.id,
                    data[:32],
                    len(data),
                )
                if self.answer_callback is not None:
                    await self.answer_callback(callback_query.id, "Callback expired")
                return
            plugin_handled = await self.plugin_command_executor.execute_callback(
                actor=CommandActor(telegram_user_id=callback_query.from_user.id, role=role),
                callback_data=data,
                callback_query_id=callback_query.id,
                chat_id=message.chat.id,
                user_id=callback_query.from_user.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                answer_callback=self.answer_callback,
            )
            logger.info(
                "telegram callback routing result callback_id=%s command_prefix=%s handled=%s chat_id=%s thread_id=%s message_id=%s",
                callback_query.id,
                data.split(":", 1)[0],
                plugin_handled,
                message.chat.id,
                message.message_thread_id,
                message.message_id,
            )
            if plugin_handled:
                return

        if data != "test:ok":
            return

        message_locale = self._message_locale_from_callback(callback_query)
        button_test_ok = {"de": "Button-Test ok", "en": "Button test ok"}[message_locale]

        if self.answer_callback is not None:
            await self.answer_callback(callback_query.id, button_test_ok)
            return

        if callback_query.message is not None:
            await self._send_text(callback_query.message.chat.id, button_test_ok, callback_query.message.message_thread_id)

    async def _handle_consent_callback(self, *, callback_query: Any, role: Role, data: str) -> None:
        if self.database_url is None:
            if self.answer_callback is not None:
                await self.answer_callback(callback_query.id, self._consent_callback_message("unavailable", callback_query))
            return

        session_factory = create_session_factory(self.database_url)
        with session_factory() as session:
            user = session.query(User).filter(User.telegram_user_id == callback_query.from_user.id).one_or_none()
            if user is None:
                if self.answer_callback is not None:
                    await self.answer_callback(callback_query.id, self._consent_callback_message("profile_missing", callback_query))
                return

            consent_service = ConsentService()
            if data == "consent:accept":
                consent_service.accept(user)
                session.commit()
                if self.owner_notifier is not None:
                    await self.owner_notifier.notify_consent_decision(user=user, accepted=True, source="button:consent:accept")
                if self.answer_callback is not None:
                    await self.answer_callback(callback_query.id, self._consent_callback_message("accepted", callback_query))
                return

            if data == "consent:decline":
                consent_service.decline(user)
                session.commit()
                if self.owner_notifier is not None:
                    await self.owner_notifier.notify_consent_decision(user=user, accepted=False, source="button:consent:decline")
                if self.answer_callback is not None:
                    await self.answer_callback(callback_query.id, self._consent_callback_message("declined", callback_query))
                return

    @staticmethod
    def _is_consent_command(command_name: str) -> bool:
        return command_name.casefold() in {"accept", "decline", "consent", "start"}

    def _is_consent_blocked(self, *, user_id: int, role: Role, command_name: str, chat_type: str | None) -> bool:
        if self.database_url is None:
            return False
        session_factory = create_session_factory(self.database_url)
        with session_factory() as session:
            user = session.query(User).filter(User.telegram_user_id == user_id).one_or_none()
            if user is None:
                return False
            return ConsentService().is_effectively_blocked(
                user,
                global_role=role,
                is_owner=role is Role.OWNER,
            )

    def _is_user_unreachable(self, user_id: int) -> bool:
        if self.database_url is None:
            return False
        session_factory = create_session_factory(self.database_url)
        with session_factory() as session:
            user = session.query(User).filter(User.telegram_user_id == user_id).one_or_none()
            if user is None:
                return False
            return ConsentService().get_status(user) == CONSENT_UNREACHABLE

    @staticmethod
    def _message_locale_from_callback(callback_query: Any) -> str:
        language_code = getattr(getattr(callback_query, "from_user", None), "language_code", None)
        if isinstance(language_code, str) and language_code.casefold().startswith("en"):
            return "en"
        return "de"

    @classmethod
    def _consent_callback_message(cls, key: str, callback_query: Any) -> str:
        locale = cls._message_locale_from_callback(callback_query)
        key_map = {
            "unavailable": "dispatcher.consent.callback.unavailable",
            "profile_missing": "dispatcher.consent.callback.profile_missing",
            "accepted": "dispatcher.consent.callback.accepted",
            "declined": "dispatcher.consent.callback.declined",
        }
        return t_text(key_map.get(key, "dispatcher.consent.callback.unavailable"), locale)

    @staticmethod
    def _unknown_command_message(*, message: TelegramMessage, command_name: str) -> str:
        locale = resolve_locale(explicit_arg=None, telegram_language_code=getattr(message.from_user, "language_code", None))
        return t_text("dispatcher.unknown_command", locale, command_name=command_name)

    @staticmethod
    def _consent_block_message(*, chat_type: str | None, blocked_as_unreachable: bool, locale: str = "de") -> str:
        resolved_locale = "en" if locale == "en" else "de"
        if chat_type in {"group", "supergroup"}:
            return t_text("dispatcher.consent.block.group", resolved_locale)
        if blocked_as_unreachable:
            return t_text("dispatcher.consent.block.unreachable", resolved_locale)
        return t_text("dispatcher.consent.block.default", resolved_locale)


    @staticmethod
    def _sanitize_prompt_for_autoreply(*, text: str, bot_username: str | None) -> tuple[str, bool]:
        cleaned = text.strip()
        if not cleaned:
            return "", False

        if bot_username is None:
            return cleaned, False

        normalized = bot_username.strip().lstrip("@")
        if not normalized:
            return cleaned, False

        mention_pattern = re.compile(rf"(?<!\w)@{re.escape(normalized)}(?![A-Za-z0-9_])", re.IGNORECASE)
        without_mention = mention_pattern.sub(" ", cleaned)
        sanitized = re.sub(r"\s+", " ", without_mention).strip()
        mention_removed = sanitized != cleaned
        return (sanitized or cleaned), mention_removed

    @staticmethod
    def _locale_for_message(message: TelegramMessage) -> str:
        locale = resolve_locale(
            explicit_arg=None,
            telegram_language_code=getattr(message.from_user, "language_code", None),
        )
        return "en" if locale == "en" else "de"

    @classmethod
    def _is_addressed_for_auto_image(cls, *, message: TelegramMessage, bot_username: str | None) -> bool:
        if cls._is_reply_to_current_bot(message=message, bot_username=bot_username):
            return True

        normalized = (bot_username or "").strip().lstrip("@")
        if not normalized:
            return False

        text = message.text or ""
        pattern = rf"(?<!\\w)@{re.escape(normalized)}(?![A-Za-z0-9_])"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None

    @staticmethod
    def _is_reply_to_current_bot(*, message: TelegramMessage, bot_username: str | None) -> bool:
        if not getattr(message, "reply_to_is_bot", False):
            return False
        if not getattr(message, "reply_to_user_is_bot", False):
            return False
        if not bot_username:
            return False

        configured = bot_username.strip().lstrip("@").casefold()
        if not configured:
            return False

        reply_username = (getattr(message, "reply_to_username", None) or "").strip().lstrip("@").casefold()
        return bool(reply_username) and reply_username == configured


    def _resolve_reply_context(self, *, message: TelegramMessage) -> TopicRecentMessageRecord | None:
        reply_to_message_id = getattr(message, "reply_to_message_id", None)
        if reply_to_message_id is None:
            return None

        reply_to_message = getattr(message, "reply_to_message", None)
        inline_text = (getattr(message, "reply_to_message_text", None) or "").strip()
        inline_user = reply_to_message.from_user if reply_to_message is not None else None
        inline_record: TopicRecentMessageRecord | None = None
        if inline_text:
            inline_record = TopicRecentMessageRecord(
                id=0,
                scope_type="inline",
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                user_id=None,
                message_text=inline_text,
                telegram_message_id=reply_to_message_id,
                telegram_author_user_id=inline_user.id if inline_user is not None else getattr(message, "reply_to_user_id", None),
                telegram_author_username=inline_user.username if inline_user is not None else getattr(message, "reply_to_username", None),
                telegram_author_is_bot=bool(inline_user.is_bot if inline_user is not None else getattr(message, "reply_to_user_is_bot", False)),
                source="bot" if bool(inline_user.is_bot if inline_user is not None else getattr(message, "reply_to_user_is_bot", False)) else "user",
            )

        if self.database_url is None:
            return inline_record

        scope: tuple[str, int | None, int | None, int | None] | None = None
        if message.chat.type in {"group", "supergroup"}:
            if message.message_thread_id is not None:
                scope = ("topic", message.chat.id, message.message_thread_id, None)
            else:
                scope = ("group_chat", message.chat.id, None, None)
        elif message.chat.type == "private":
            scope = ("private_user", None, None, message.from_user.id)

        if scope is None:
            return inline_record

        scope_type, chat_id, topic_id, user_id = scope
        with create_session_factory(self.database_url)() as session:
            stored = TopicAgentMemoryRepository(session).get_recent_by_telegram_message_id(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
                telegram_message_id=reply_to_message_id,
            )
        return stored or inline_record

    @staticmethod
    def _format_reply_context(record: TopicRecentMessageRecord | None) -> str | None:
        if record is None:
            return None
        content = (record.message_text or "").strip()
        if not content:
            return None
        source = "bot" if record.telegram_author_is_bot or record.source == "bot" else "user"
        author = source
        if record.telegram_author_username:
            author = f"{source} @{record.telegram_author_username.strip().lstrip('@')}"
        elif record.telegram_author_user_id is not None and record.telegram_author_user_id != 0:
            author = f"{source} user_id={record.telegram_author_user_id}"
        return (
            "The current user message is a Telegram reply to this prior message. "
            "Use it to resolve references like this/that/he/she/it.\n"
            f"Replied-to author/source: {author}\n"
            f"Replied-to content:\n{content}"
        )

    async def _maybe_handle_ai_autoreply(
        self,
        *,
        message: TelegramMessage,
        role: Role,
        bot_username: str | None,
        from_parsed_update: bool = False,
    ) -> None:
        if self.ai_service is None:
            return

        raw_text = message.text or ""
        text = raw_text.strip()
        if not text:
            return

        if self.database_url is None:
            class _NoopTopicAgentMemoryRepository:
                pass

            router = AIRouter(topic_agent_memory_repository=_NoopTopicAgentMemoryRepository())
            topic_id = message.message_thread_id
            normalized_text, mention_removed = self._sanitize_prompt_for_autoreply(text=text, bot_username=bot_username)
            decision = router.decide(
                prompt=text,
                chat_id=message.chat.id,
                topic_id=topic_id,
                user_id=message.from_user.id,
                chat_type=message.chat.type,
                bot_username=bot_username,
                reply_to_is_bot=self._is_reply_to_current_bot(message=message, bot_username=bot_username),
            )
        else:
            with create_session_factory(self.database_url)() as session:
                router = AIRouter(topic_agent_memory_repository=__import__("amo_bot.db.repositories", fromlist=["TopicAgentMemoryRepository"]).TopicAgentMemoryRepository(session))
                topic_id = message.message_thread_id
                normalized_text, mention_removed = self._sanitize_prompt_for_autoreply(text=text, bot_username=bot_username)
                decision = router.decide(
                    prompt=text,
                    chat_id=message.chat.id,
                    topic_id=topic_id,
                    user_id=message.from_user.id,
                    chat_type=message.chat.type,
                    bot_username=bot_username,
                    reply_to_is_bot=self._is_reply_to_current_bot(message=message, bot_username=bot_username),
                )

        allowed_reason_codes = {
                AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE,
                AIRouterReasonCode.REPLY_TO_BOT_IN_ACTIVE_SCOPE,
            }
        if (
            decision.reason_code == AIRouterReasonCode.SCOPE_ENABLED
            and decision.context.scope_type == "private_user"
            and message.chat.type == "private"
        ):
            allowed_reason_codes.add(AIRouterReasonCode.SCOPE_ENABLED)

        # Context fallback is only safe when it came from a true reply trigger.
        # Mention-trigger fallback is intentionally blocked to prevent false-positive
        # mention detection from producing unsolicited group replies.
        if (
            decision.reason_code == AIRouterReasonCode.CONTEXT_GUARD_FALLBACK
            and decision.context.flag_reply_to_bot
        ):
            allowed_reason_codes.add(AIRouterReasonCode.CONTEXT_GUARD_FALLBACK)

        if decision.reason_code not in allowed_reason_codes:
            return

        # Hard invariant: group/supergroup/forum AI replies require explicit trigger
        # (mention or reply-to-bot), independent of resolved scope/config/role.
        is_group_chat = message.chat.type in {"group", "supergroup"}
        explicit_group_trigger = decision.context.flag_bot_mention or decision.context.flag_reply_to_bot
        if is_group_chat and not explicit_group_trigger:
            if self.database_url is not None:
                with create_session_factory(self.database_url)() as session:
                    self._write_ai_audit(
                        session=session,
                        actor_user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        event_type="ai_autoreply_denied",
                        payload={
                            "reason": "missing_group_trigger",
                            "router_reason": decision.reason_code.value,
                            "chat_type": message.chat.type,
                            "scope_type": decision.context.scope_type,
                            "flag_bot_mention": decision.context.flag_bot_mention,
                            "flag_reply_to_bot": decision.context.flag_reply_to_bot,
                        },
                    )
                    session.commit()
            return

        min_ai_role = Role.OWNER
        if self.database_url is not None:
            with create_session_factory(self.database_url)() as session:
                min_ai_role = PrivateChatPolicyRepository(session).get_policy().min_ai_role
        if decision.context.scope_type == "private_user" and not role_meets_minimum(role, min_ai_role):
            if self.database_url is not None:
                with create_session_factory(self.database_url)() as session:
                    self._write_ai_audit(
                        session=session,
                        actor_user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        event_type="ai_autoreply_denied",
                        payload={
                            "reason": "role_denied",
                            "router_reason": decision.reason_code.value,
                            "role": role.value,
                            "required_role": min_ai_role.value,
                        },
                    )
                    session.commit()
            return

        if decision.context.scope_type != "private_user" and role not in AUTOREPLY_ALLOWED_ROLES:
            if self.database_url is not None:
                with create_session_factory(self.database_url)() as session:
                    self._write_ai_audit(
                        session=session,
                        actor_user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        event_type="ai_autoreply_denied",
                        payload={"reason": "role_denied", "router_reason": decision.reason_code.value, "role": role.value},
                    )
                    session.commit()
            return

        if self.database_url is not None:
            with create_session_factory(self.database_url)() as session:
                user = session.query(User).filter(User.telegram_user_id == message.from_user.id).one_or_none()
                if user is None:
                    self._write_ai_audit(
                        session=session,
                        actor_user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        event_type="ai_autoreply_denied",
                        payload={"reason": "user_missing", "router_reason": decision.reason_code.value},
                    )
                    session.commit()
                    return

                if ConsentService().is_effectively_blocked(user, global_role=role, is_owner=role is Role.OWNER):
                    self._write_ai_audit(
                        session=session,
                        actor_user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        event_type="ai_autoreply_denied",
                        payload={"reason": "consent_denied", "router_reason": decision.reason_code.value},
                    )
                    session.commit()
                    return

        adapter = self.live_edit_adapter or DisabledTelegramLiveEditAdapter()

        identity_label = bot_username.strip().lstrip("@") if isinstance(bot_username, str) and bot_username.strip() else "this Telegram bot"
        identity_instruction = (
            f"System note: You are the Telegram topic assistant @{identity_label}. "
            "The message was addressed to this bot; treat own-bot mentions as routing triggers, not user intent. "
            "Do not claim to be the underlying model/provider unless explicitly asked."
        )

        def _normalize_context_lines(value: str, *, drop_exact_line: str) -> str:
            lines = [line.rstrip() for line in value.splitlines()]
            filtered = [line for line in lines if line.strip() and line.strip() != drop_exact_line]
            return "\n".join(filtered).strip()

        prompt_sections: list[str] = [identity_instruction]
        prompt_sections.append(
            "Use provided context only as background. Prioritize the current user message when determining intent and reply."
        )

        reply_context_block = self._format_reply_context(self._resolve_reply_context(message=message))
        if reply_context_block:
            prompt_sections.append(f"Telegram reply context:\n{reply_context_block}")

        drop_exact_line = normalized_text.strip()

        recent_messages_text = (decision.context.recent_messages_text or "").strip()
        if recent_messages_text:
            recent_messages_text = _normalize_context_lines(recent_messages_text, drop_exact_line=drop_exact_line)
            if recent_messages_text:
                prompt_sections.append(f"Relevant recent chat context (same scope):\n{recent_messages_text}")

        assembled_soul_text = (decision.context.assembled_soul_text or "").strip()
        if assembled_soul_text:
            prompt_sections.append(f"Assistant behavior context:\n{assembled_soul_text}")

        daily_memory_text = (decision.context.daily_memory_text or "").strip()
        if daily_memory_text:
            prompt_sections.append(f"Daily memory context:\n{daily_memory_text}")

        long_memory_text = (decision.context.long_memory_text or "").strip()
        if long_memory_text:
            prompt_sections.append(f"Long-term memory context:\n{long_memory_text}")

        prompt_sections.append(f"User message:\n{normalized_text}")
        llm_prompt = "\n\n".join(prompt_sections)

        explicit_trigger_reason_codes = {
            AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE,
            AIRouterReasonCode.REPLY_TO_BOT_IN_ACTIVE_SCOPE,
        }
        decision_reason_value = getattr(decision.reason_code, "value", decision.reason_code)
        explicit_trigger_reason_values = {code.value for code in explicit_trigger_reason_codes}
        is_triggered_path = decision_reason_value in explicit_trigger_reason_values

        message_locale = self._locale_for_message(message)

        try:
            response = await self.ai_service.ask(llm_prompt)
        except Exception:
            logger.exception("ai_autoreply failed: user_id=%s chat_id=%s", message.from_user.id, message.chat.id)
            if self.database_url is not None:
                with create_session_factory(self.database_url)() as session:
                    self._write_ai_audit(
                        session=session,
                        actor_user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        event_type="ai_autoreply_error",
                        payload={"reason": "ai_error", "router_reason": decision.reason_code.value},
                    )
                    session.commit()

            if is_triggered_path:
                await self._send_text(
                    message.chat.id,
                    AI_AUTOREPLY_ERROR_FALLBACK_TEXT[message_locale],
                    message.message_thread_id,
                )
            return

        client = getattr(self.ai_service, "client", None)
        # Safety distinction: live-edit streaming is only allowed when we have
        # request-scoped Telegram context from parsed updates (handle_raw_update).
        # Direct/internal calls without current request context must degrade safely.
        has_request_scoped_context = bool(from_parsed_update)
        live_edit_enabled = bool(
            is_triggered_path
            and has_request_scoped_context
            and client is not None
            and getattr(client, "request_endpoint", None) == "chat"
            and getattr(client, "streaming_mode", None) == "live_edit"
            and adapter is not None
        )

        if live_edit_enabled:
            terminal_seen = False
            terminal_events = {"done", "error", "cancel", "timeout"}
            for event in getattr(self.ai_service, "last_stream_events", []) or []:
                if terminal_seen:
                    break

                try:
                    await adapter.consume(chat_id=message.chat.id, message_thread_id=message.message_thread_id, event=event)
                except Exception:
                    logger.info("ai_live_edit_degraded stage=consume code=adapter_error")
                    break

                if str(getattr(event, "get", lambda *_args, **_kwargs: None)("event", "")).casefold() in terminal_events:
                    terminal_seen = True

        if not response:
            return

        await self._send_text(message.chat.id, response, message.message_thread_id)

        if self.database_url is not None:
            with create_session_factory(self.database_url)() as session:
                self._write_ai_audit(
                    session=session,
                    actor_user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    event_type="ai_autoreply_sent",
                    payload={
                        "router_reason": decision.reason_code.value,
                        "mention_removed": mention_removed,
                        "bot_identity": identity_label,
                    },
                )
                session.commit()

    def _auto_image_followup_cache(self) -> list[_RecentAutoImageCandidate]:
        return self._recent_auto_image_candidates

    def _remember_auto_image_candidate(self, *, message: TelegramMessage) -> None:
        now = datetime.now(UTC)
        cache = self._auto_image_followup_cache()
        max_age = timedelta(seconds=max(self.auto_image_followup_ttl_seconds, 1))
        pruned = [
            item
            for item in cache
            if now - item.observed_at <= max_age
        ]
        pruned.append(
            _RecentAutoImageCandidate(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                message_id=message.message_id,
                attachments=message.attachments,
                observed_at=now,
            )
        )
        pruned.sort(key=lambda item: item.observed_at, reverse=True)
        setattr(self, "_recent_auto_image_candidates", pruned[:128])

    def _resolve_auto_image_followup_candidate(self, *, message: TelegramMessage) -> _RecentAutoImageCandidate | None:
        if message.attachments:
            return None
        if not self._is_addressed_for_auto_image(message=message, bot_username=self.bot_username):
            return None

        now = datetime.now(UTC)
        max_age = timedelta(seconds=max(self.auto_image_followup_ttl_seconds, 1))
        cache = self._auto_image_followup_cache()

        filtered: list[_RecentAutoImageCandidate] = []
        matched: _RecentAutoImageCandidate | None = None
        for candidate in cache:
            age = now - candidate.observed_at
            if age > max_age:
                continue
            filtered.append(candidate)
            if matched is not None:
                continue
            if candidate.chat_id != message.chat.id:
                continue
            if candidate.message_thread_id != message.message_thread_id:
                continue
            if candidate.user_id != message.from_user.id:
                continue
            matched = candidate

        setattr(self, "_recent_auto_image_candidates", filtered[:128])

        if matched is None:
            logger.info(
                "auto_image followup_bridge decision=not_found source=recent_same_scope chat_id=%s message_thread_id=%s message_id=%s user_id=%s",
                message.chat.id,
                message.message_thread_id,
                message.message_id,
                message.from_user.id,
            )
        return matched

    async def _send_text(self, chat_id: int, text: str, message_thread_id: int | None) -> None:
        if message_thread_id is None:
            result = await self.send_text(chat_id, text)
            await self._persist_bot_send_result(chat_id=chat_id, message_thread_id=None, text=text, result=result)
            return
        try:
            result = await self.send_text(chat_id, text, message_thread_id)
        except TypeError:
            result = await self.send_text(chat_id, text)
        await self._persist_bot_send_result(chat_id=chat_id, message_thread_id=message_thread_id, text=text, result=result)


    async def _persist_bot_send_result(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        text: str,
        result: object,
    ) -> None:
        if self.message_persistence is None or not hasattr(self.message_persistence, "persist_bot_sent_message"):
            return
        if not isinstance(result, dict):
            return
        message_id_raw = result.get("message_id")
        try:
            message_id = int(message_id_raw)
        except (TypeError, ValueError):
            return
        try:
            await self.message_persistence.persist_bot_sent_message(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                message_id=message_id,
                text=text,
                bot_username=self.bot_username,
            )
        except Exception:
            logger.exception("Failed to persist bot-sent Telegram message; continuing")


    @staticmethod
    def _write_ai_audit(
        *,
        session: Any,
        actor_user_id: int,
        chat_id: int,
        message_id: int,
        message_thread_id: int | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        session.add(
            AuditEvent(
                event_type=event_type,
                actor_telegram_user_id=actor_user_id,
                payload_json=json.dumps({
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "message_thread_id": message_thread_id,
                    **payload,
                }, ensure_ascii=False),
            )
        )
