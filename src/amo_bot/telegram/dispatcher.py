from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from amo_bot.ai.router import AIRouter, AIRouterReasonCode
from amo_bot.auth.permissions import can_use_bot
from amo_bot.auth.roles import Role, role_meets_minimum
from amo_bot.consent import CONSENT_UNREACHABLE, ConsentService
from amo_bot.db.base import create_session_factory
from amo_bot.db.repositories import PrivateChatPolicyRepository
from amo_bot.db.models import AuditEvent, User
from amo_bot.plugins.command_runtime import CommandActor, CommandInvocation, PluginCommandExecutor
from amo_bot.telegram.commands import CommandContext, CommandRegistry, RoleResolver, resolve_locale
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.telegram.update_parser import TelegramMessage, parse_update


AUTOREPLY_ALLOWED_ROLES: set[Role] = {Role.OWNER, Role.ADMIN, Role.VIP}

SendTextFn = Callable[[int, str, int | None], Awaitable[object]]
SendMarkupFn = Callable[[int, str, dict[str, Any], int | None], Awaitable[object]]
SendPrivateMarkupFn = Callable[[int, str, dict[str, Any]], Awaitable[object]]
AnswerCallbackFn = Callable[[str, str | None], Awaitable[object]]

logger = logging.getLogger(__name__)


class MessagePersistence(Protocol):
    async def persist_message(self, message: TelegramMessage) -> None: ...


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

    async def handle_raw_update(self, raw_update: object) -> None:
        update = parse_update(raw_update)
        if update is None:
            return

        callback_query = update.callback_query
        if callback_query is not None:
            await self._handle_callback_query(callback_query)
            return

        if update.message is None:
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

        if command is None:
            await self._maybe_handle_ai_autoreply(message=message, role=role, bot_username=self.bot_username)
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
                response = self._consent_block_message(chat_type=message.chat.type, blocked_as_unreachable=self._is_user_unreachable(message.from_user.id))
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

        if data != "test:ok":
            return

        if self.answer_callback is not None:
            await self.answer_callback(callback_query.id, "Button test ok")
            return

        if callback_query.message is not None:
            await self._send_text(callback_query.message.chat.id, "Button test ok", callback_query.message.message_thread_id)

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
        messages = {
            "unavailable": {"de": "Consent nicht verfügbar", "en": "Consent unavailable"},
            "profile_missing": {"de": "Profil nicht gefunden", "en": "Profile not found"},
            "accepted": {"de": "Consent akzeptiert", "en": "Consent accepted"},
            "declined": {"de": "Consent abgelehnt", "en": "Consent declined"},
        }
        return messages.get(key, messages["unavailable"])[locale]

    @staticmethod
    def _unknown_command_message(*, message: TelegramMessage, command_name: str) -> str:
        locale = resolve_locale(explicit_arg=None, telegram_language_code=getattr(message.from_user, "language_code", None))
        if locale == "de":
            return f"Unbekannter Befehl: /{command_name}. Nutze /help für verfügbare Befehle."
        return f"Unknown command: /{command_name}. Use /help for available commands."

    @staticmethod
    def _consent_block_message(*, chat_type: str | None, blocked_as_unreachable: bool) -> str:
        if chat_type in {"group", "supergroup"}:
            return "Bitte kläre Consent privat mit dem Bot."
        if blocked_as_unreachable:
            return "Bitte starte den Bot privat und bestätige mit /accept."
        return "Bitte bestätige zuerst mit /accept oder prüfe /consent."


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

    async def _maybe_handle_ai_autoreply(self, *, message: TelegramMessage, role: Role, bot_username: str | None) -> None:
        if self.ai_service is None or self.database_url is None:
            return

        raw_text = message.text or ""
        text = raw_text.strip()
        if not text:
            return

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
                reply_to_is_bot=message.reply_to_is_bot,
            )

            allowed_reason_codes = {
                AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE,
                AIRouterReasonCode.REPLY_TO_BOT_IN_ACTIVE_SCOPE,
            }
            if decision.reason_code == AIRouterReasonCode.SCOPE_ENABLED and decision.context.scope_type == "private_user":
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

            min_ai_role = PrivateChatPolicyRepository(session).get_policy().min_ai_role
            if decision.context.scope_type == "private_user" and not role_meets_minimum(role, min_ai_role):
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

        identity_label = bot_username.strip().lstrip("@") if isinstance(bot_username, str) and bot_username.strip() else "this Telegram bot"
        identity_instruction = (
            f"System note: You are the Telegram topic assistant @{identity_label}. "
            "The message was addressed to this bot; treat own-bot mentions as routing triggers, not user intent. "
            "Do not claim to be the underlying model/provider unless explicitly asked."
        )
        llm_prompt = f"{identity_instruction}\n\nUser message:\n{normalized_text}"

        try:
            response = await self.ai_service.ask(llm_prompt)
        except Exception:
            logger.exception("ai_autoreply failed: user_id=%s chat_id=%s", message.from_user.id, message.chat.id)
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
            return

        if not response:
            return

        await self._send_text(message.chat.id, response, message.message_thread_id)

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

    async def _send_text(self, chat_id: int, text: str, message_thread_id: int | None) -> None:
        if message_thread_id is None:
            await self.send_text(chat_id, text)
            return
        try:
            await self.send_text(chat_id, text, message_thread_id)
        except TypeError:
            await self.send_text(chat_id, text)


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
