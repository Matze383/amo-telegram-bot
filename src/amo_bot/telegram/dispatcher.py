from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol

from amo_bot.ai.current_time_context import DEFAULT_AI_PROMPT_TIMEZONE, build_current_time_context
from amo_bot.ai.compact_topic_state import build_compact_topic_state_payload, format_compact_topic_state_prompt
from amo_bot.ai.context_snapshot import build_context_snapshot
from amo_bot.ai.learning_feedback import LearningFeedbackScope, LearningFeedbackService
from amo_bot.ai.prompt_language import DEFAULT_RESPONSE_LANGUAGE_RULE
from amo_bot.ai.response_strategy import classify_response_strategy, draft_self_limitation_requires_research
from amo_bot.ai.router import AIRouter, AIRouterReasonCode
from amo_bot.auth.permissions import can_use_bot
from amo_bot.auth.roles import Role, role_meets_minimum
from amo_bot.consent import CONSENT_UNREACHABLE, ConsentService
from amo_bot.core.logging import (
    duration_timer,
    log_event,
    masked_id,
    new_request_id,
    set_request_id,
    get_request_id,
)
from amo_bot.current_info.models import CurrentInfoAnswer, CurrentInfoRequest
from amo_bot.db.base import create_session_factory
from amo_bot.db.repositories import (
    BotPeerRepository,
    ClaimRepository,
    PrivateChatPolicyRepository,
    PromptContextDocRepository,
    ResearchEvalCaseRepository,
    ResearchSourcePreferenceRepository,
    RetrievableMemoryRepository,
    TopicCompactStateRepository,
    TopicAgentMemoryRepository,
    TopicRecentMessageRecord,
    UserMemoryProfileRepository,
)
from amo_bot.db.models import AuditEvent, User
from amo_bot.plugins.command_runtime import CommandActor, CommandInvocation, PluginCommandExecutor
from amo_bot.telegram.commands import CommandContext, CommandRegistry, RestartRequest, RoleResolver, resolve_locale, t_text
from amo_bot.telegram.live_edit_adapter import DisabledTelegramLiveEditAdapter, TelegramLiveEditAdapter
from amo_bot.telegram.outbound_text import split_telegram_message_text
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.telegram.update_parser import TelegramMessage, TelegramReactionEvent, parse_update
from amo_bot.telegram.webtool_chat_integration import (
    build_webtool_request,
    format_webtool_fail_text,
    format_webtool_quota_text,
    format_webtool_success_text,
    parse_webtool_chat_trigger,
)
from amo_bot.telegram.webtool_auto_research import decide_auto_research
from amo_bot.telegram.webtool_evidence import classify_evidence_domain
from amo_bot.telegram.webtool_research_orchestrator import (
    DbBackedResearchSourceObservationWriter,
    DbBackedResearchSourceQualityReader,
    WebResearchOrchestrator,
    WebResearchOrchestratorRequest,
    sanitize_auto_research_user_response,
)


_COMPONENT = "telegram.dispatcher"

AUTOREPLY_ALLOWED_ROLES: set[Role] = {Role.OWNER, Role.ADMIN, Role.VIP}
BOT_PEER_V1_ALLOWED_COMMANDS: set[str] = {"help", "ping"}
RESTART_ACK_TIMEOUT_SECONDS = 3.0
CURRENT_INFO_SYNTHESIS_MAX_EVIDENCE_CHARS = 4500
CURRENT_INFO_SYNTHESIS_MAX_SOURCE_COUNT = 5
CURRENT_INFO_COMPACT_FALLBACK_MAX_BODY_CHARS = 3200


def _terminate_current_process() -> None:
    raise SystemExit(0)


AI_AUTOREPLY_ERROR_FALLBACK_TEXT = {
    "de": "Ich konnte gerade keine KI-Antwort erzeugen. Bitte versuch es gleich nochmal.",
    "en": "I couldn't generate an AI reply right now. Please try again in a moment.",
}
AI_CLARIFY_FALLBACK_TEXT = {
    "de": "Kannst du kurz konkretisieren, worauf sich das bezieht?",
    "en": "Can you briefly clarify what this refers to?",
}
CURRENT_INFO_UNAVAILABLE_FALLBACK_TEXT = {
    "de": "Dafuer brauche ich aktuelle Recherche, aber Current-Info ist gerade nicht verfuegbar oder nicht konfiguriert.",
    "en": "I need current research for that, but Current Info is not available or configured right now.",
}
CURRENT_INFO_RESEARCH_FAILED_FALLBACK_TEXT = {
    "de": "Dafuer brauche ich GPT-Researcher-Webrecherche, aber die Recherche konnte gerade nicht erfolgreich abgeschlossen werden.",
    "en": "I need GPT-Researcher web research for that, but the research could not be completed successfully right now.",
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
    webtool_dispatcher: Any | None = None
    web_evidence_pipeline: Any | None = None
    current_info_service: Any | None = None
    current_info_enabled: bool = False
    current_info_timeout_seconds: float = 8.0
    current_info_research_timeout_seconds: float = 300.0
    current_info_late_synthesis_timeout_seconds: float = 60.0
    current_info_max_results: int = 5
    current_info_max_documents: int = 3
    live_edit_adapter: TelegramLiveEditAdapter | None = None
    restart_terminator: Callable[[], None] = _terminate_current_process
    auto_image_followup_ttl_seconds: int = 180
    prompt_timezone: str = DEFAULT_AI_PROMPT_TIMEZONE
    _recent_auto_image_candidates: list[_RecentAutoImageCandidate] = field(default_factory=list)
    _current_info_background_tasks: set[asyncio.Task[None]] = field(default_factory=set)

    async def handle_raw_update(self, raw_update: object) -> None:
        update = parse_update(raw_update)
        if update is None:
            raw_type = type(raw_update).__name__
            log_event(
                logger, logging.WARNING,
                event="telegram.update.parse_failed",
                component=_COMPONENT,
                extra={"raw_type": raw_type},
            )
            return

        if update.message_reaction is not None:
            await self._handle_message_reaction(update.message_reaction, update_id=update.update_id)
            return

        callback_query = update.callback_query
        if callback_query is not None:
            log_event(
                logger, logging.INFO,
                event="telegram.callback.received",
                component=_COMPONENT,
                update_id=update.update_id,
                extra={
                    "callback_id": callback_query.id,
                    "data_prefix": (callback_query.data or "")[:32],
                    "data_len": len(callback_query.data or ""),
                    "has_message": callback_query.message is not None,
                },
            )
            await self._handle_callback_query(callback_query)
            return

        if update.message is None:
            log_event(
                logger, logging.WARNING,
                event="telegram.update.ignored",
                component=_COMPONENT,
                update_id=update.update_id,
                extra={
                    "kind": update.top_level_kind,
                    "keys": sorted(list(raw_update.keys())) if isinstance(raw_update, dict) else None,
                },
            )
            return

        message = update.message
        command = message.parse_command(bot_username=self.bot_username)
        if message.from_user.is_bot:
            bot_peer_allowed = await self._handle_bot_peer_message(message=message, update_id=update.update_id)
            if not bot_peer_allowed:
                return
            if command is None:
                await self._persist_allowed_bot_peer_message(message=message, update_id=update.update_id)
                log_event(
                    logger, logging.INFO,
                    event="bot_peer.message.skipped",
                    component=_COMPONENT,
                    update_id=update.update_id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    user_id=message.from_user.id,
                    extra={"reason": "allowed_bot_non_command"},
                )
                return
            if command.name not in BOT_PEER_V1_ALLOWED_COMMANDS:
                log_event(
                    logger, logging.INFO,
                    event="bot_peer.message.skipped",
                    component=_COMPONENT,
                    update_id=update.update_id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    user_id=message.from_user.id,
                    command=command.name,
                    extra={"reason": "command_not_allowed_for_bot_peer"},
                )
                return
        elif self.message_persistence is not None:
            try:
                await self.message_persistence.persist_message(message)
            except Exception:
                logger.exception("Failed to persist Telegram message; continuing update handling")

        role = await self.role_resolver.resolve(
            message.from_user.id,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
        )
        attachment_count = len(message.attachments)
        has_photo_attachment = any(item.type_hint == "image" or item.source_kind == "photo" for item in message.attachments)
        has_image_document = any(item.type_hint == "image_document" for item in message.attachments)

        log_event(
            logger, logging.INFO,
            event="telegram.message.parsed",
            component=_COMPONENT,
            update_id=update.update_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=message.message_thread_id,
            user_id=message.from_user.id,
            command=command.name if command is not None else None,
            extra={
                "chat_type": message.chat.type,
                "attachment_count": attachment_count,
                "has_photo_attachment": has_photo_attachment,
                "has_image_document": has_image_document,
                "role": role.value,
            },
        )

        if command is None:
            self._maybe_store_learning_text_feedback(message=message)
            is_addressed_for_auto_image = self._is_addressed_for_auto_image(message=message, bot_username=self.bot_username)
            followup_candidate = self._resolve_auto_image_followup_candidate(message=message)
            reply_to_attachment_source = self._resolve_reply_to_auto_image_attachments(message=message)
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
            elif reply_to_attachment_source is not None:
                followup_source = "reply_to_message"
                effective_attachments = reply_to_attachment_source

            if not message.attachments and followup_candidate is None and reply_to_attachment_source is None:
                log_event(
                    logger, logging.INFO,
                    event="auto_image.decision",
                    component=_COMPONENT,
                    update_id=update.update_id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "decision": "skipped_no_attachments",
                        "role": role.value,
                    },
                )
            elif not can_use_bot(role):
                log_event(
                    logger, logging.INFO,
                    event="auto_image.decision",
                    component=_COMPONENT,
                    update_id=update.update_id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "decision": "skipped_role",
                        "role": role.value,
                        "attachment_count": attachment_count,
                        "has_photo_attachment": has_photo_attachment,
                        "has_image_document": has_image_document,
                    },
                )
            elif self.plugin_command_executor is None:
                log_event(
                    logger, logging.INFO,
                    event="auto_image.decision",
                    component=_COMPONENT,
                    update_id=update.update_id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "decision": "skipped_no_executor",
                        "role": role.value,
                        "attachment_count": attachment_count,
                    },
                )
            elif message.chat.type != "private" and not is_addressed_for_auto_image:
                log_event(
                    logger, logging.INFO,
                    event="auto_image.decision",
                    component=_COMPONENT,
                    update_id=update.update_id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "decision": "skipped_not_addressed",
                        "role": role.value,
                        "attachment_count": attachment_count,
                    },
                )
            else:
                log_event(
                    logger, logging.INFO,
                    event="auto_image.decision",
                    component=_COMPONENT,
                    update_id=update.update_id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "decision": "invoked",
                        "role": role.value,
                        "attachment_count": attachment_count,
                    },
                )
                handled_image = await self.plugin_command_executor.analyze_image_automatically(
                    actor=CommandActor(telegram_user_id=message.from_user.id, role=role),
                    invocation=CommandInvocation(
                        command_name="auto_image",
                        argument=self._sanitize_prompt_for_autoreply(text=message.text or "", bot_username=self.bot_username)[0] or None,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        attachments=effective_attachments,
                    ),
                )
                if followup_candidate is not None:
                    log_event(
                        logger, logging.INFO,
                        event="auto_image.followup_bridge",
                        component=_COMPONENT,
                        update_id=update.update_id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        user_id=message.from_user.id,
                        extra={
                            "source": followup_source,
                            "image_message_id": followup_candidate.message_id,
                            "age_seconds": followup_candidate_age_seconds,
                            "attachment_count": len(effective_attachments),
                        },
                    )
                elif reply_to_attachment_source is not None:
                    log_event(
                        logger, logging.INFO,
                        event="auto_image.followup_bridge",
                        component=_COMPONENT,
                        update_id=update.update_id,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        user_id=message.from_user.id,
                        extra={
                            "source": followup_source,
                            "image_message_id": getattr(message, "reply_to_message_id", None),
                            "attachment_count": len(effective_attachments),
                        },
                    )
                log_event(
                    logger, logging.INFO,
                    event="auto_image.decision",
                    component=_COMPONENT,
                    update_id=update.update_id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "decision": "handled" if handled_image else "not_handled",
                        "role": role.value,
                    },
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
                log_event(
                    logger, logging.INFO,
                    event="plugin.command.handled",
                    component=_COMPONENT,
                    update_id=update.update_id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    command=command.name,
                    extra={"outcome": "handled"},
                )
                return
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
            message_thread_id=message.message_thread_id,
            reply_to_message_text=message.reply_to_message_text,
            locale=resolve_locale(
                explicit_arg=command.argument if command.name.casefold() in {"start", "help", "consent", "accept", "decline", "ask", "webui", "test", "ping", "role", "setrole", "remember", "restart"} else None,
                telegram_language_code=getattr(message.from_user, "language_code", None),
            ),
        )
        response = await command_def.handler(ctx)
        if isinstance(response, RestartRequest):
            if response.acknowledgement:
                try:
                    await asyncio.wait_for(
                        self._send_text(message.chat.id, response.acknowledgement, message.message_thread_id),
                        timeout=RESTART_ACK_TIMEOUT_SECONDS,
                    )
                except Exception:
                    logger.exception("Restart acknowledgement failed; terminating anyway")
            self.restart_terminator()
            return

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
                        log_event(
                            logger, logging.INFO,
                            event="test.private_route",
                            component=_COMPONENT,
                            chat_id=message.chat.id,
                            user_id=target_user_id,
                            command="test",
                            extra={
                                "is_group_like": is_group_like,
                                "dm_success": False,
                            },
                        )
                        await self.send_private_markup(target_user_id, text, reply_markup)
                    except Exception as exc:
                        msg = str(exc).casefold()
                        log_event(
                            logger, logging.INFO,
                            event="test.private_route",
                            component=_COMPONENT,
                            chat_id=message.chat.id,
                            user_id=target_user_id,
                            command="test",
                            extra={
                                "is_group_like": is_group_like,
                                "dm_success": False,
                                "error": str(exc),
                            },
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
                        log_event(
                            logger, logging.INFO,
                            event="test.private_route",
                            component=_COMPONENT,
                            chat_id=message.chat.id,
                            user_id=target_user_id,
                            command="test",
                            extra={"is_group_like": is_group_like, "dm_success": True},
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

        if data.startswith("bot_peer:"):
            await self._handle_bot_peer_callback(callback_query=callback_query, role=role, data=data)
            return

        if data in {"consent:accept", "consent:decline"}:
            await self._handle_consent_callback(callback_query=callback_query, role=role, data=data)
            return

        if data.startswith("yt_rss:") and self.plugin_command_executor is not None:
            message = callback_query.message
            log_event(
                logger, logging.INFO,
                event="telegram.callback.routing_candidate",
                component=_COMPONENT,
                extra={
                    "callback_id": callback_query.id,
                    "data_prefix": data[:32],
                    "data_len": len(data),
                    "has_message": message is not None,
                    "chat_id": message.chat.id if message is not None else None,
                    "thread_id": message.message_thread_id if message is not None else None,
                    "message_id": message.message_id if message is not None else None,
                },
            )
            if message is None:
                log_event(
                    logger, logging.WARNING,
                    event="telegram.callback.routing_skipped",
                    component=_COMPONENT,
                    extra={
                        "callback_id": callback_query.id,
                        "reason": "no_message",
                    },
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
            log_event(
                logger, logging.INFO,
                event="telegram.callback.routing_result",
                component=_COMPONENT,
                extra={
                    "callback_id": callback_query.id,
                    "command_prefix": data.split(":", 1)[0],
                    "handled": plugin_handled,
                    "chat_id": message.chat.id,
                    "thread_id": message.message_thread_id,
                    "message_id": message.message_id,
                },
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

    async def _handle_bot_peer_message(self, *, message: TelegramMessage, update_id: int) -> bool:
        if self.database_url is None:
            log_event(
                logger, logging.INFO,
                event="bot_peer.message.denied",
                component=_COMPONENT,
                update_id=update_id,
                chat_id=message.chat.id,
                message_id=message.message_id,
                user_id=message.from_user.id,
                extra={"reason": "database_unavailable"},
            )
            return False

        session_factory = create_session_factory(self.database_url)
        with session_factory() as session:
            result = BotPeerRepository(session).mark_seen(
                telegram_bot_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                chat_id=message.chat.id,
                chat_type=message.chat.type,
                chat_title=message.chat.title,
                message_thread_id=message.message_thread_id,
            )
            status = result.peer.status
            created = result.created

        if created and self.owner_notifier is not None:
            await self.owner_notifier.notify_new_bot_peer_discovered(message=message)

        allowed = status == "allowed"
        log_event(
            logger, logging.INFO,
            event="bot_peer.message.gate",
            component=_COMPONENT,
            update_id=update_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            user_id=message.from_user.id,
            extra={"status": status, "created": created, "allowed": allowed},
        )
        return allowed

    async def _persist_allowed_bot_peer_message(self, *, message: TelegramMessage, update_id: int) -> None:
        if self.message_persistence is None:
            return

        persist_bot_peer = getattr(self.message_persistence, "persist_bot_peer_recent_message", None)
        if persist_bot_peer is None:
            log_event(
                logger, logging.INFO,
                event="bot_peer.message.persistence_skipped",
                component=_COMPONENT,
                update_id=update_id,
                chat_id=message.chat.id,
                message_id=message.message_id,
                user_id=message.from_user.id,
                extra={"reason": "metadata_only_persistence"},
            )
            return

        try:
            await persist_bot_peer(message)
        except Exception:
            log_event(
                logger, logging.WARNING,
                event="bot_peer.message.persist_failed",
                component=_COMPONENT,
                update_id=update_id,
                chat_id=message.chat.id,
                message_id=message.message_id,
                user_id=message.from_user.id,
            )

    async def _handle_bot_peer_callback(self, *, callback_query: Any, role: Role, data: str) -> None:
        if self.database_url is None:
            if self.answer_callback is not None:
                await self.answer_callback(callback_query.id, "Bot-Freigabe nicht verfuegbar")
            return

        if not self._is_owner_callback_actor(callback_query=callback_query, role=role):
            if self.answer_callback is not None:
                await self.answer_callback(callback_query.id, "Nur der Owner darf Bot-Freigaben aendern")
            return

        parts = data.split(":")
        if len(parts) != 3 or parts[1] not in {"allow", "block"}:
            if self.answer_callback is not None:
                await self.answer_callback(callback_query.id, "Ungueltige Bot-Freigabe")
            return

        try:
            telegram_bot_id = int(parts[2])
        except ValueError:
            if self.answer_callback is not None:
                await self.answer_callback(callback_query.id, "Ungueltige Bot-ID")
            return

        status = "allowed" if parts[1] == "allow" else "blocked"
        session_factory = create_session_factory(self.database_url)
        with session_factory() as session:
            peer = BotPeerRepository(session).set_status(
                telegram_bot_id=telegram_bot_id,
                status=status,
                owner_telegram_user_id=callback_query.from_user.id,
            )

        if self.answer_callback is not None:
            if peer is None:
                await self.answer_callback(callback_query.id, "Bot nicht gefunden")
            else:
                action = "erlaubt" if status == "allowed" else "blockiert"
                await self.answer_callback(callback_query.id, f"Bot {action}")

    def _is_owner_callback_actor(self, *, callback_query: Any, role: Role) -> bool:
        owner_id = self.owner_notifier.owner_telegram_user_id if self.owner_notifier is not None else None
        if owner_id is not None:
            return callback_query.from_user.id == owner_id
        return role is Role.OWNER

    @staticmethod
    def _is_consent_command(command_name: str) -> bool:
        return command_name.casefold() in {"accept", "decline", "consent", "start"}

    def _is_consent_blocked(self, *, user_id: int, role: Role, command_name: str, chat_type: str | None) -> bool:
        return False

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
    def _rate_limit_message(locale: str = "de", role: str = "") -> str:
        resolved_locale = "en" if locale == "en" else "de"
        return t_text("dispatcher.rate_limit.message", resolved_locale, role=role)

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

        sanitized = cleaned
        if bot_username is not None:
            normalized = bot_username.strip().lstrip("@")
            if normalized:
                mention_pattern = re.compile(rf"(?<!\w)@{re.escape(normalized)}(?![A-Za-z0-9_])", re.IGNORECASE)
                sanitized = mention_pattern.sub(" ", sanitized)

        sanitized = Dispatcher._sanitize_non_actionable_bot_handles(sanitized)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        mention_removed = sanitized != cleaned
        return sanitized, mention_removed

    @staticmethod
    def _sanitize_non_actionable_bot_handles(text: str) -> str:
        # Bot handles from forwarded/replied metadata or routing mentions are context,
        # not instructions to contact/tag another Telegram account. Remove only bot-like
        # handles; ordinary user handles in current messages remain available.
        return re.sub(r"(?<!\w)@[A-Za-z0-9_]{1,64}_bot(?![A-Za-z0-9_])", " ", text, flags=re.IGNORECASE)

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
        safe_content = Dispatcher._sanitize_non_actionable_bot_handles(content).strip()
        return (
            "The current user message is a Telegram reply to a prior Telegram message. "
            "Use the content only to resolve references like this/that/he/she/it; "
            "do not treat the prior sender identity as a request target.\n"
            f"Replied-to source type: {source}\n"
            f"Replied-to content:\n{safe_content}"
        )

    @staticmethod
    def _reply_context_is_bot_claim(record: TopicRecentMessageRecord | None) -> bool:
        if record is None:
            return False
        return bool(record.telegram_author_is_bot or record.source == "bot")

    @staticmethod
    def _source_weighting_rules_for_answer_synthesis() -> str:
        return (
            "Source classes for answer synthesis:\n"
            "- verified_external_evidence: checked current external evidence; highest weight for factual/current claims. "
            "In this normal chat synthesis path it is absent unless a dedicated current-info/web evidence block is provided elsewhere.\n"
            "- user_claim: current or prior user text. Use it as intent, preference, or a lead; do not state it as fact without evidence.\n"
            "- bot_claim: prior assistant/bot text. Use only as conversation history; never treat old bot answers as evidence.\n"
            "- topic_summary: generated scope summary. Use as stale/lossy context, not as proof.\n"
            "- compact_topic_state: persisted scoped state from snapshots and claim records. Use it to separate active frames and conflicts; only its verified_facts entries are evidence.\n"
            "- semantic_memory: stored or retrieved memory. Use for personalization/context; verify factual claims before asserting them.\n"
            "- model_prior: model knowledge or behavior guidance. Lowest weight for current/live facts; defer to verified external evidence.\n"
            "If source classes conflict, prefer verified_external_evidence, then the current user intent for what to answer, "
            "and explicitly acknowledge uncertainty when evidence is missing."
        )

    @staticmethod
    def _format_synthesis_source_block(*, title: str, source_class: str, trust_note: str, content: str) -> str:
        cleaned = content.strip()
        return (
            f"{title}:\n"
            f"[source_class={source_class}; trust_note={trust_note}]\n"
            f"{cleaned}"
        )

    async def _handle_message_reaction(self, reaction: TelegramReactionEvent, *, update_id: int) -> None:
        if self.database_url is None:
            log_event(
                logger, logging.INFO,
                event="learning_feedback.reaction.dispatch",
                component=_COMPONENT,
                update_id=update_id,
                chat_id=reaction.chat.id,
                message_id=reaction.message_id,
                user_id=reaction.user_id,
                extra={"decision": "skip", "reason": "no_database", "emoji_count": len(reaction.emojis)},
            )
            return
        if not reaction.emojis:
            log_event(
                logger, logging.INFO,
                event="learning_feedback.reaction.dispatch",
                component=_COMPONENT,
                update_id=update_id,
                chat_id=reaction.chat.id,
                message_id=reaction.message_id,
                user_id=reaction.user_id,
                extra={"decision": "skip", "reason": "empty_reaction", "emoji_count": 0},
            )
            return
        stored_count = 0
        with create_session_factory(self.database_url)() as session:
            service = LearningFeedbackService(
                RetrievableMemoryRepository(session),
                eval_case_writer=ResearchEvalCaseRepository(session),
                source_preference_writer=ResearchSourcePreferenceRepository(session),
            )
            scope = LearningFeedbackScope(
                chat_id=reaction.chat.id,
                message_thread_id=reaction.message_thread_id,
                user_id=reaction.user_id,
            )
            # Telegram message_reaction updates do not include the reacted message body/author.
            # Treat the event as a service-level weak signal only; future wiring can add bot-message lookup.
            for emoji in reaction.emojis[:3]:
                result = service.process_reaction_feedback(
                    emoji=emoji,
                    scope=scope,
                    reacted_message_id=reaction.message_id,
                    reacted_message_is_bot=True,
                    reacted_message_thread_id=reaction.message_thread_id,
                )
                if result.stored:
                    stored_count += 1
        log_event(
            logger, logging.INFO,
            event="learning_feedback.reaction.dispatch",
            component=_COMPONENT,
            update_id=update_id,
            chat_id=reaction.chat.id,
            message_id=reaction.message_id,
            message_thread_id=reaction.message_thread_id,
            user_id=reaction.user_id,
            extra={"decision": "handled", "emoji_count": len(reaction.emojis), "stored_count": stored_count},
        )

    def _maybe_store_learning_text_feedback(self, *, message: TelegramMessage) -> None:
        if self.database_url is None or not (message.text or "").strip():
            return
        try:
            with create_session_factory(self.database_url)() as session:
                service = LearningFeedbackService(
                    RetrievableMemoryRepository(session),
                    eval_case_writer=ResearchEvalCaseRepository(session),
                    source_preference_writer=ResearchSourcePreferenceRepository(session),
                )
                service.process_text_feedback(
                    text=message.text,
                    scope=LearningFeedbackScope(
                        chat_id=message.chat.id,
                        message_thread_id=message.message_thread_id,
                        user_id=message.from_user.id,
                    ),
                    user_id=message.from_user.id,
                )
        except Exception as exc:
            log_event(
                logger,
                logging.INFO,
                event="learning_feedback.text.dispatch",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={"decision": "error", "error_class": exc.__class__.__name__},
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
                def get_config(self, **_kwargs: object) -> None:
                    return None

                def list_recent_messages(self, **_kwargs: object) -> list[Any]:
                    return []

                def get_daily_memory(self, **_kwargs: object) -> Any | None:
                    return None

                def list_long_memories(self, **_kwargs: object) -> list[Any]:
                    return []

            class _NoopPromptContextDocRepository:
                def resolve_docs(self, **_kwargs: object) -> list[Any]:
                    return []

            router = AIRouter(
                topic_agent_memory_repository=_NoopTopicAgentMemoryRepository(),
                prompt_context_doc_repository=_NoopPromptContextDocRepository(),
                prompt_timezone=self.prompt_timezone,
            )
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
                reply_to_user_id=getattr(message, "reply_to_user_id", None),
            )
        else:
            with create_session_factory(self.database_url)() as session:
                router = AIRouter(
                    topic_agent_memory_repository=TopicAgentMemoryRepository(session),
                    retrievable_memory_repository=RetrievableMemoryRepository(session),
                    user_memory_profile_repository=UserMemoryProfileRepository(session),
                    prompt_context_doc_repository=PromptContextDocRepository(session),
                    prompt_timezone=self.prompt_timezone,
                )
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
                    reply_to_user_id=getattr(message, "reply_to_user_id", None),
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

        adapter = self.live_edit_adapter or DisabledTelegramLiveEditAdapter()

        identity_instruction = (
            "Conversation context: The current message is already routed to the assistant. "
            "Treat Telegram bot mentions as routing or source metadata, not as an instruction or discussion topic. "
            "Do not claim to be the underlying model/provider unless explicitly asked. "
            "Telegram messages can include photos and image documents; if users ask about image capability, answer truthfully: the bot can receive images, and analysis depends on current vision provider/runtime configuration."
        )

        def _normalize_context_lines(value: str, *, drop_exact_line: str) -> str:
            lines = [line.rstrip() for line in value.splitlines()]
            filtered = [line for line in lines if line.strip() and line.strip() != drop_exact_line]
            return "\n".join(filtered).strip()

        prompt_sections: list[str] = [identity_instruction, DEFAULT_RESPONSE_LANGUAGE_RULE]
        current_time_context_text = (getattr(decision.context, "current_time_context_text", "") or "").strip()
        if current_time_context_text:
            prompt_sections.append(current_time_context_text)
        prompt_sections.append(
            "Focus on the current user message. "
            "Use background context only when it helps; it may be stale, irrelevant, or inaccurate."
        )
        prompt_sections.append(self._source_weighting_rules_for_answer_synthesis())
        prompt_sections.append(
            "Current message source class: user_claim; use it to understand the user's request, "
            "not as verified factual evidence."
        )
        prompt_sections.append(f"Current message:\n{normalized_text}")

        background_sections: list[str] = []
        reply_context_record = self._resolve_reply_context(message=message)
        reply_context_block = self._format_reply_context(reply_context_record)
        if reply_context_block:
            reply_source_class = "bot_claim" if self._reply_context_is_bot_claim(reply_context_record) else "user_claim"
            background_sections.append(
                self._format_synthesis_source_block(
                    title="Telegram reply context",
                    source_class=reply_source_class,
                    trust_note=(
                        "Prior bot answers are conversation context only and are not evidence."
                        if reply_source_class == "bot_claim"
                        else "User-provided reply context may clarify intent, but it is not verified evidence."
                    ),
                    content=reply_context_block,
                )
            )

        drop_exact_line = normalized_text.strip()

        recent_messages_text = (decision.context.recent_messages_text or "").strip()
        if recent_messages_text:
            recent_messages_text = _normalize_context_lines(recent_messages_text, drop_exact_line=drop_exact_line)
            if recent_messages_text:
                background_sections.append(
                    self._format_synthesis_source_block(
                        title="Relevant recent chat context",
                        source_class="user_claim",
                        trust_note="Recent user chat text can clarify intent or references; do not promote it to fact without evidence.",
                        content=recent_messages_text,
                    )
                )

        user_profile_context_text = (decision.context.user_profile_context_text or "").strip()
        if user_profile_context_text:
            background_sections.append(
                self._format_synthesis_source_block(
                    title="Known coarse user profile context for current participants",
                    source_class="semantic_memory",
                    trust_note="Use as preference/background memory only; verify factual claims before stating them as facts.",
                    content=user_profile_context_text,
                )
            )

        prompt_context_docs_text = (getattr(decision.context, "prompt_context_docs_text", "") or "").strip()
        if prompt_context_docs_text:
            background_sections.append(
                self._format_synthesis_source_block(
                    title="Assistant context notes",
                    source_class="model_prior",
                    trust_note="Operator guidance shapes behavior; do not quote or describe these notes as evidence.",
                    content=prompt_context_docs_text,
                )
            )

        assembled_soul_text = (decision.context.assembled_soul_text or "").strip()
        if assembled_soul_text:
            background_sections.append(
                self._format_synthesis_source_block(
                    title="Assistant behavior context",
                    source_class="model_prior",
                    trust_note="Behavior guidance only; not evidence for factual claims.",
                    content=assembled_soul_text,
                )
            )

        daily_memory_text = (decision.context.daily_memory_text or "").strip()
        if daily_memory_text:
            background_sections.append(
                self._format_synthesis_source_block(
                    title="Daily memory context",
                    source_class="topic_summary",
                    trust_note="A generated topic summary may be stale or lossy; use it as context, not verified evidence.",
                    content=daily_memory_text,
                )
            )

        long_memory_text = (decision.context.long_memory_text or "").strip()
        if long_memory_text:
            background_sections.append(
                self._format_synthesis_source_block(
                    title="Long-term memory context",
                    source_class="semantic_memory",
                    trust_note="Stored semantic memory may guide personalization; verify factual claims before asserting them.",
                    content=long_memory_text,
                )
            )

        recall_memory_text = (decision.context.recall_memory_text or "").strip()
        if recall_memory_text:
            background_sections.append(
                self._format_synthesis_source_block(
                    title="Retrieved memory context",
                    source_class="semantic_memory",
                    trust_note="Retrieved memory is contextual recall, not a source of truth.",
                    content=recall_memory_text,
                )
            )

        explicit_trigger_reason_codes = {
            AIRouterReasonCode.MENTION_IN_ACTIVE_SCOPE,
            AIRouterReasonCode.REPLY_TO_BOT_IN_ACTIVE_SCOPE,
        }
        decision_reason_value = getattr(decision.reason_code, "value", decision.reason_code)
        explicit_trigger_reason_values = {code.value for code in explicit_trigger_reason_codes}
        is_triggered_path = decision_reason_value in explicit_trigger_reason_values

        message_locale = self._locale_for_message(message)
        response_strategy = classify_response_strategy(
            normalized_text,
            context={
                "router_reason": decision.reason_code.value,
                "scope_type": decision.context.scope_type,
                "triggered_path": is_triggered_path,
            },
        )

        if response_strategy.label == "clarify":
            await self._send_text(message.chat.id, AI_CLARIFY_FALLBACK_TEXT[message_locale], message.message_thread_id)
            return

        auto_research_decision = decide_auto_research(normalized_text)

        followup_context_text = ""
        if reply_context_block:
            followup_context_text = (getattr(reply_context_record, "message_text", "") or "").strip()
        elif recent_messages_text:
            followup_context_text = recent_messages_text

        trigger = parse_webtool_chat_trigger(normalized_text)
        if trigger is not None and self.webtool_dispatcher is not None:
            req = build_webtool_request(
                trigger=trigger,
                user_id=message.from_user.id,
                role=role,
                chat_id=message.chat.id,
                topic_id=message.message_thread_id,
                locale=message_locale,
            )
            tool_result = self.webtool_dispatcher.execute(req)

            log_event(
                logger,
                logging.INFO,
                event="ai.webtool.chat_dispatch",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "operation": trigger.capability,
                    "scope": decision.context.scope_type,
                    "status": "allow" if tool_result.allowed else "deny",
                    "decision": tool_result.decision,
                    "reason": tool_result.reason,
                    "error_class": type(tool_result.error).__name__ if tool_result.error else None,
                    "source_count": len(tool_result.sources),
                    "host_count": len(tool_result.hosts),
                },
            )

            if not tool_result.allowed:
                if tool_result.decision in {"deny", "quota_exceeded", "disabled"} or "quota" in tool_result.reason:
                    await self._send_text(message.chat.id, format_webtool_quota_text(message_locale, role), message.message_thread_id)
                else:
                    await self._send_text(message.chat.id, format_webtool_fail_text(message_locale), message.message_thread_id)
                return

            if not (tool_result.text or "").strip():
                await self._send_text(message.chat.id, format_webtool_fail_text(message_locale), message.message_thread_id)
                return

            await self._send_text(
                message.chat.id,
                format_webtool_success_text(locale=message_locale, capability=trigger.capability, text=tool_result.text),
                message.message_thread_id,
            )
            return

        auto_note = ""
        if trigger is None:
            if await self._maybe_handle_current_info_autoreply(
                message=message,
                role=role,
                normalized_text=normalized_text,
                locale=message_locale,
                force=response_strategy.requires_research,
                fallback_to_ai_on_incomplete=not response_strategy.requires_research,
                strategy_reason=response_strategy.reason,
            ):
                return

        if trigger is None and self.webtool_dispatcher is not None:
            research_session_factory = create_session_factory(self.database_url) if self.database_url is not None else None
            research_result = WebResearchOrchestrator(
                webtool_dispatcher=self.webtool_dispatcher,
                evidence_pipeline=self.web_evidence_pipeline,
                source_quality_reader=(
                    DbBackedResearchSourceQualityReader(session_factory=research_session_factory)
                    if research_session_factory is not None
                    else None
                ),
                source_observation_writer=(
                    DbBackedResearchSourceObservationWriter(session_factory=research_session_factory)
                    if research_session_factory is not None
                    else None
                ),
            ).execute(
                WebResearchOrchestratorRequest(
                    message=message,
                    normalized_text=normalized_text,
                    role=role,
                    locale=message_locale,
                    is_triggered_path=is_triggered_path,
                    reply_context_text=followup_context_text,
                    scope=decision.context.scope_type,
                )
            )
            auto_note = research_result.auto_note
            if research_result.user_response:
                await self._send_text(message.chat.id, research_result.user_response, message.message_thread_id)
                return

        if auto_note:
            prompt_sections.insert(0, auto_note)

        context_snapshot = build_context_snapshot(
            current_message=text,
            normalized_current_message=normalized_text,
            router_context=decision.context,
            reply_context_text=reply_context_block,
            existing_current_info_signal=auto_research_decision.enabled,
            verified_external_evidence_available=_auto_note_has_verified_external_evidence(auto_note),
        )
        prompt_sections.append(
            "Structured runtime context snapshot (diagnostic; use it to resolve frame conflicts, do not quote it):\n"
            f"{context_snapshot.to_prompt_text()}"
        )
        if context_snapshot.current_info_decision.fail_closed_instruction:
            prompt_sections.append(
                "Current-info decision before synthesis:\n"
                f"{context_snapshot.current_info_decision.fail_closed_instruction}"
            )
        prompt_sections.append(
            "Response strategy before synthesis:\n"
            f"- strategy: {response_strategy.label}\n"
            f"- reason: {response_strategy.reason}\n"
            f"- signals: {', '.join(response_strategy.signals) if response_strategy.signals else 'none'}\n"
            "If strategy is research_needed, do not answer mutable external facts from model prior. "
            "Use only verified external evidence or trigger/await current-info research."
        )

        compact_state_text = ""
        compact_state_record = None
        if self.database_url is not None and decision.context.scope_type in {"topic", "group_chat", "private_user"}:
            with create_session_factory(self.database_url)() as session:
                compact_repo = TopicCompactStateRepository(session)
                existing_state = compact_repo.get_state(
                    scope_type=decision.context.scope_type,
                    chat_id=decision.context.scope_chat_id,
                    topic_id=decision.context.scope_topic_id,
                    user_id=decision.context.scope_user_id,
                )
                claims = ClaimRepository(session).list_claims(
                    scope_type=decision.context.scope_type,
                    chat_id=decision.context.scope_chat_id,
                    topic_id=decision.context.scope_topic_id,
                    user_id=decision.context.scope_user_id,
                    limit=100,
                )
                compact_payload = build_compact_topic_state_payload(
                    snapshot=context_snapshot,
                    claims=claims,
                    existing=existing_state,
                )
                compact_state_record = compact_repo.upsert_state(
                    scope_type=decision.context.scope_type,
                    chat_id=decision.context.scope_chat_id,
                    topic_id=decision.context.scope_topic_id,
                    user_id=decision.context.scope_user_id,
                    active_subjects=compact_payload.active_subjects,
                    frames=compact_payload.frames,
                    conflicts=compact_payload.conflicts,
                    verified_facts=compact_payload.verified_facts,
                    discarded_assumptions=compact_payload.discarded_assumptions,
                    last_snapshot=compact_payload.last_snapshot,
                    updated_from_message_id=message.message_id,
                )
            compact_state_text = format_compact_topic_state_prompt(compact_state_record)

        if compact_state_text:
            background_sections.insert(
                0,
                self._format_synthesis_source_block(
                    title="Compact topic state",
                    source_class="compact_topic_state",
                    trust_note=(
                        "Persisted scoped state separates active subjects, frames, conflicts, verified facts, and discarded assumptions. "
                        "Only verified_facts are factual evidence."
                    ),
                    content=compact_state_text,
                ),
            )

        if background_sections:
            prompt_sections.append("Background context:")
            prompt_sections.extend(background_sections)

        prompt_sections.append(f"User message:\n{normalized_text}")
        llm_prompt = "\n\n".join(prompt_sections)

        log_event(
            logger,
            logging.INFO,
            event="ai.context_snapshot",
            component=_COMPONENT,
            chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=message.message_thread_id,
            user_id=message.from_user.id,
            extra={
                "router_reason": decision.reason_code.value,
                "current_user_intent": context_snapshot.current_user_intent,
                "active_subject": context_snapshot.active_subject,
                "frame_candidates": [candidate.frame for candidate in context_snapshot.frame_candidates],
                "conflict_count": len(context_snapshot.conflicts),
                "requires_current_info": context_snapshot.requires_current_info,
            },
        )
        if self.database_url is not None:
            with create_session_factory(self.database_url)() as session:
                self._write_ai_audit(
                    session=session,
                    actor_user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    event_type="ai_context_snapshot",
                    payload={
                        "router_reason": decision.reason_code.value,
                        "context_snapshot": context_snapshot.to_dict(),
                    },
                )
                session.commit()

        # Structured log: AI autoreply attempt
        timing: dict[str, Any] = {}
        with duration_timer(timing):
            try:
                try:
                    response = await self.ai_service.ask(llm_prompt, task_type="answer_synthesis")
                except TypeError as exc:
                    if "task_type" not in str(exc):
                        raise
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
                            payload={
                                "reason": "ai_error",
                                "router_reason": decision.reason_code.value,
                                "context_snapshot": context_snapshot.to_dict(),
                            },
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
                    log_event(
                        logger, logging.INFO,
                        event="ai.live_edit.degraded",
                        component=_COMPONENT,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                        extra={"stage": "consume", "code": "adapter_error"},
                    )
                    break

                if str(getattr(event, "get", lambda *_args, **_kwargs: None)("event", "")).casefold() in terminal_events:
                    terminal_seen = True

        if not response:
            return

        if auto_note:
            response = sanitize_auto_research_user_response(response)

        if draft_self_limitation_requires_research(message=normalized_text, draft=response):
            if await self._maybe_handle_current_info_autoreply(
                message=message,
                role=role,
                normalized_text=normalized_text,
                locale=message_locale,
                force=True,
                fallback_to_ai_on_incomplete=False,
                strategy_reason="draft_self_limitation_guard",
            ):
                return
            log_event(
                logger,
                logging.INFO,
                event="ai.autoreply.draft_discarded",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={"reason": "draft_self_limitation_requires_research"},
            )
            return

        await self._send_text(message.chat.id, response, message.message_thread_id)

        log_event(
            logger, logging.INFO,
            event="ai.autoreply.sent",
            component=_COMPONENT,
            chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=message.message_thread_id,
            user_id=message.from_user.id,
            extra={
                "router_reason": decision.reason_code.value,
                "mention_removed": mention_removed,
                "duration_ms": timing.get("duration_ms"),
                "context_snapshot_conflicts": len(context_snapshot.conflicts),
                "context_snapshot_requires_current_info": context_snapshot.requires_current_info,
            },
        )

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
                        "context_snapshot": context_snapshot.to_dict(),
                    },
                )
                session.commit()

    async def _maybe_handle_current_info_autoreply(
        self,
        *,
        message: TelegramMessage,
        role: Role,
        normalized_text: str,
        locale: str,
        force: bool = False,
        fallback_to_ai_on_incomplete: bool = True,
        strategy_reason: str = "",
    ) -> bool:
        decision = decide_auto_research(normalized_text)
        current_info_query = normalized_text if decision.url and normalized_text.strip() else decision.query
        original_capability = decision.capability
        capability = "webresearch"
        research_report_type = str(getattr(decision, "research_report_type", "") or "research_report").strip() or "research_report"
        reason = decision.reason
        if force and not current_info_query:
            current_info_query = normalized_text.strip()
            reason = strategy_reason or "response_strategy_research_needed"
        should_research = bool((decision.enabled or force) and current_info_query)

        if not self.current_info_enabled or self.current_info_service is None or self.ai_service is None:
            if should_research:
                await self._send_text(
                    message.chat.id,
                    CURRENT_INFO_UNAVAILABLE_FALLBACK_TEXT["en" if locale == "en" else "de"],
                    message.message_thread_id,
                )
                log_event(
                    logger,
                    logging.INFO,
                    event="current_info.telegram.unavailable",
                    component=_COMPONENT,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "enabled": bool(self.current_info_enabled),
                        "has_service": self.current_info_service is not None,
                        "has_ai_service": self.ai_service is not None,
                        "response_strategy_reason": strategy_reason,
                    },
                )
                return True
            return False

        if not should_research:
            return False

        timeout_seconds = max(
            float(self.current_info_timeout_seconds),
            float(self.current_info_research_timeout_seconds),
            0.001,
        )
        current_info_now = datetime.now(UTC)
        current_time_context_text = build_current_time_context(
            now=current_info_now,
            timezone_name=self.prompt_timezone,
        )
        request = CurrentInfoRequest(
            query=current_info_query,
            locale=locale,
            domain_hint=classify_evidence_domain(current_info_query),
            max_results=max(1, min(int(self.current_info_max_results), 10)),
            max_documents=max(0, min(int(self.current_info_max_documents), 10)),
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            topic_id=message.message_thread_id,
            role=role,
            metadata={
                "telegram_message_id": message.message_id,
                "auto_research_reason": reason,
                "response_strategy_reason": strategy_reason,
                "capability": capability,
                "requested_capability": original_capability,
                "auto_research_capability": capability,
                "research_report_type": research_report_type,
                "gpt_researcher_report_type": research_report_type,
                "deep_research": research_report_type == "deep_research",
                "direct_url": decision.url,
                "forced_by_response_strategy": force,
                "require_gpt_researcher": True,
                "current_time_context_text": current_time_context_text,
                "now": current_info_now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "timezone": self.prompt_timezone,
            },
        )

        started = time.perf_counter()
        retrieval_task = asyncio.create_task(asyncio.to_thread(self.current_info_service.answer, request))
        try:
            answer = await asyncio.wait_for(
                asyncio.shield(retrieval_task),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            retrieval_task.cancel()
            log_event(
                logger,
                logging.INFO,
                event="current_info.telegram.fallback",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "reason": "timeout",
                    "stage": "retrieval",
                    "timeout_seconds": timeout_seconds,
                    "late_delivery": "disabled",
                },
            )
            await self._send_text(
                message.chat.id,
                CURRENT_INFO_RESEARCH_FAILED_FALLBACK_TEXT["en" if locale == "en" else "de"],
                message.message_thread_id,
            )
            return True
        except Exception as exc:
            log_event(
                logger,
                logging.INFO,
                event="current_info.telegram.fallback",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "reason": "service_error",
                    "error_class": exc.__class__.__name__,
                },
            )
            await self._send_text(
                message.chat.id,
                CURRENT_INFO_RESEARCH_FAILED_FALLBACK_TEXT["en" if locale == "en" else "de"],
                message.message_thread_id,
            )
            return True

        if isinstance(answer, CurrentInfoAnswer) and answer.status in {"empty_evidence", "unverified_evidence"}:
            text = self._format_current_info_insufficient_answer(answer=answer, locale=locale)
            if text.strip():
                await self._send_text(message.chat.id, text, message.message_thread_id)
                log_event(
                    logger,
                    logging.INFO,
                    event="current_info.telegram.sent",
                    component=_COMPONENT,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "status": answer.status,
                        "confidence": answer.confidence,
                        "source_count": len(answer.sources),
                        "warning_count": len(answer.warnings),
                        "reason": "insufficient_evidence",
                    },
                )
                return True

        if not isinstance(answer, CurrentInfoAnswer) or not answer.answered:
            log_event(
                logger,
                logging.INFO,
                event="current_info.telegram.fallback",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "reason": "not_answered",
                    "status": getattr(answer, "status", "invalid_response"),
                    "warning_count": len(getattr(answer, "warnings", ()) or ()),
                },
            )
            await self._send_text(
                message.chat.id,
                CURRENT_INFO_RESEARCH_FAILED_FALLBACK_TEXT["en" if locale == "en" else "de"],
                message.message_thread_id,
            )
            return True

        retrieval_elapsed_seconds = time.perf_counter() - started
        synthesis_timeout = max(float(self.current_info_late_synthesis_timeout_seconds), 0.001)
        try:
            synthesized = await asyncio.wait_for(
                self._synthesize_current_info_answer(answer=answer, locale=locale),
                timeout=synthesis_timeout,
            )
        except TimeoutError:
            text = self._format_current_info_compact_fallback_answer(answer=answer, locale=locale)
            if text.strip():
                log_event(
                    logger,
                    logging.INFO,
                    event="current_info.telegram.sent",
                    component=_COMPONENT,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "status": answer.status,
                        "confidence": answer.confidence,
                        "source_count": len(answer.sources),
                        "warning_count": len(answer.warnings),
                        "reason": "synthesis_timeout_compact_fallback",
                        "stage": "synthesis",
                        "timeout_seconds": timeout_seconds,
                        "late_synthesis_timeout_seconds": synthesis_timeout,
                        "retrieval_elapsed_seconds": round(retrieval_elapsed_seconds, 3),
                    },
                )
                await self._send_text(message.chat.id, text, message.message_thread_id)
                return True
            log_event(
                logger,
                logging.INFO,
                event="current_info.telegram.fallback",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "reason": "timeout",
                    "stage": "synthesis",
                    "timeout_seconds": timeout_seconds,
                    "late_synthesis_timeout_seconds": synthesis_timeout,
                    "retrieval_elapsed_seconds": round(retrieval_elapsed_seconds, 3),
                    "status": answer.status,
                    "confidence": answer.confidence,
                },
            )
            await self._send_text(
                message.chat.id,
                CURRENT_INFO_RESEARCH_FAILED_FALLBACK_TEXT["en" if locale == "en" else "de"],
                message.message_thread_id,
            )
            return True
        except Exception as exc:
            text = self._format_current_info_compact_fallback_answer(answer=answer, locale=locale)
            if text.strip():
                log_event(
                    logger,
                    logging.INFO,
                    event="current_info.telegram.sent",
                    component=_COMPONENT,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "status": answer.status,
                        "confidence": answer.confidence,
                        "source_count": len(answer.sources),
                        "warning_count": len(answer.warnings),
                        "reason": "synthesis_error_compact_fallback",
                        "error_class": exc.__class__.__name__,
                    },
                )
                await self._send_text(message.chat.id, text, message.message_thread_id)
                return True
            log_event(
                logger,
                logging.INFO,
                event="current_info.telegram.fallback",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "reason": "synthesis_error",
                    "error_class": exc.__class__.__name__,
                    "status": answer.status,
                    "confidence": answer.confidence,
                },
            )
            await self._send_text(
                message.chat.id,
                CURRENT_INFO_RESEARCH_FAILED_FALLBACK_TEXT["en" if locale == "en" else "de"],
                message.message_thread_id,
            )
            return True

        text = self._format_current_info_telegram_answer(answer=answer, synthesized=synthesized, locale=locale)
        if not text.strip():
            await self._send_text(
                message.chat.id,
                CURRENT_INFO_RESEARCH_FAILED_FALLBACK_TEXT["en" if locale == "en" else "de"],
                message.message_thread_id,
            )
            return True

        await self._send_text(message.chat.id, text, message.message_thread_id)
        log_event(
            logger,
            logging.INFO,
            event="current_info.telegram.sent",
            component=_COMPONENT,
            chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=message.message_thread_id,
            user_id=message.from_user.id,
            extra={
                "status": answer.status,
                "confidence": answer.confidence,
                "source_count": len(answer.sources),
                "warning_count": len(answer.warnings),
                "retrieval_elapsed_seconds": round(retrieval_elapsed_seconds, 3),
            },
        )
        return True

    def _schedule_late_current_info_answer(
        self,
        *,
        retrieval_task: asyncio.Task[CurrentInfoAnswer],
        message: TelegramMessage,
        locale: str,
        timeout_seconds: float,
    ) -> None:
        task = asyncio.create_task(
            self._send_late_current_info_answer(
                retrieval_task=retrieval_task,
                message=message,
                locale=locale,
                timeout_seconds=timeout_seconds,
            )
        )
        self._current_info_background_tasks.add(task)
        task.add_done_callback(self._current_info_background_tasks.discard)
        task.add_done_callback(self._log_current_info_background_task_result)

    @staticmethod
    async def _completed_current_info_answer(answer: CurrentInfoAnswer) -> CurrentInfoAnswer:
        return answer

    @staticmethod
    def _log_current_info_background_task_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                event="current_info.telegram.late_task_failed",
                component=_COMPONENT,
                extra={"error_class": exc.__class__.__name__},
            )

    async def _send_late_current_info_answer(
        self,
        *,
        retrieval_task: asyncio.Task[CurrentInfoAnswer],
        message: TelegramMessage,
        locale: str,
        timeout_seconds: float,
    ) -> None:
        try:
            answer = await retrieval_task
        except Exception as exc:
            log_event(
                logger,
                logging.INFO,
                event="current_info.telegram.late_fallback",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "reason": "service_error",
                    "error_class": exc.__class__.__name__,
                    "timeout_seconds": timeout_seconds,
                },
            )
            return

        if isinstance(answer, CurrentInfoAnswer) and answer.status in {"empty_evidence", "unverified_evidence"}:
            text = self._format_current_info_insufficient_answer(answer=answer, locale=locale)
            if text.strip():
                await self._send_text(message.chat.id, text, message.message_thread_id)
                log_event(
                    logger,
                    logging.INFO,
                    event="current_info.telegram.late_sent",
                    component=_COMPONENT,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "status": answer.status,
                        "confidence": answer.confidence,
                        "source_count": len(answer.sources),
                        "warning_count": len(answer.warnings),
                        "reason": "insufficient_evidence",
                        "timeout_seconds": timeout_seconds,
                    },
                )
            return

        if not isinstance(answer, CurrentInfoAnswer) or not answer.answered:
            log_event(
                logger,
                logging.INFO,
                event="current_info.telegram.late_fallback",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "reason": "not_answered",
                    "status": getattr(answer, "status", "invalid_response"),
                    "warning_count": len(getattr(answer, "warnings", ()) or ()),
                    "timeout_seconds": timeout_seconds,
                },
            )
            return

        synthesis_timeout = max(float(self.current_info_late_synthesis_timeout_seconds), 0.001)
        try:
            synthesized = await asyncio.wait_for(
                self._synthesize_current_info_answer(answer=answer, locale=locale),
                timeout=synthesis_timeout,
            )
        except TimeoutError:
            text = self._format_current_info_compact_fallback_answer(answer=answer, locale=locale)
            if text.strip():
                await self._send_text(message.chat.id, text, message.message_thread_id)
                log_event(
                    logger,
                    logging.INFO,
                    event="current_info.telegram.late_sent",
                    component=_COMPONENT,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "status": answer.status,
                        "confidence": answer.confidence,
                        "source_count": len(answer.sources),
                        "warning_count": len(answer.warnings),
                        "reason": "synthesis_timeout_compact_fallback",
                        "timeout_seconds": timeout_seconds,
                        "late_synthesis_timeout_seconds": synthesis_timeout,
                    },
                )
                return
            log_event(
                logger,
                logging.INFO,
                event="current_info.telegram.late_fallback",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "reason": "timeout",
                    "stage": "synthesis",
                    "timeout_seconds": timeout_seconds,
                    "late_synthesis_timeout_seconds": synthesis_timeout,
                    "status": answer.status,
                    "confidence": answer.confidence,
                },
            )
            return
        except Exception as exc:
            text = self._format_current_info_compact_fallback_answer(answer=answer, locale=locale)
            if text.strip():
                await self._send_text(message.chat.id, text, message.message_thread_id)
                log_event(
                    logger,
                    logging.INFO,
                    event="current_info.telegram.late_sent",
                    component=_COMPONENT,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    message_thread_id=message.message_thread_id,
                    user_id=message.from_user.id,
                    extra={
                        "status": answer.status,
                        "confidence": answer.confidence,
                        "source_count": len(answer.sources),
                        "warning_count": len(answer.warnings),
                        "reason": "synthesis_error_compact_fallback",
                        "error_class": exc.__class__.__name__,
                        "timeout_seconds": timeout_seconds,
                    },
                )
                return
            log_event(
                logger,
                logging.INFO,
                event="current_info.telegram.late_fallback",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "reason": "synthesis_error",
                    "error_class": exc.__class__.__name__,
                    "timeout_seconds": timeout_seconds,
                    "status": answer.status,
                    "confidence": answer.confidence,
                },
            )
            return

        text = self._format_current_info_telegram_answer(answer=answer, synthesized=synthesized, locale=locale)
        if not text.strip():
            log_event(
                logger,
                logging.INFO,
                event="current_info.telegram.late_fallback",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "reason": "empty_formatted_answer",
                    "timeout_seconds": timeout_seconds,
                    "status": answer.status,
                    "confidence": answer.confidence,
                },
            )
            return

        await self._send_text(message.chat.id, text, message.message_thread_id)
        log_event(
            logger,
            logging.INFO,
            event="current_info.telegram.late_sent",
            component=_COMPONENT,
            chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=message.message_thread_id,
            user_id=message.from_user.id,
            extra={
                "status": answer.status,
                "confidence": answer.confidence,
                "source_count": len(answer.sources),
                "warning_count": len(answer.warnings),
                "timeout_seconds": timeout_seconds,
                "late_synthesis_timeout_seconds": synthesis_timeout,
            },
        )

    async def _synthesize_current_info_answer(self, *, answer: CurrentInfoAnswer, locale: str) -> str:
        prompt = self._current_info_synthesis_prompt(
            answer=answer,
            locale=locale,
            timezone_name=self.prompt_timezone,
        )
        try:
            return await self.ai_service.ask(prompt, task_type="answer_synthesis")
        except TypeError as exc:
            if "task_type" not in str(exc):
                raise
            return await self.ai_service.ask(prompt)

    @staticmethod
    def _current_info_synthesis_prompt(
        *,
        answer: CurrentInfoAnswer,
        locale: str,
        timezone_name: str = DEFAULT_AI_PROMPT_TIMEZONE,
    ) -> str:
        query = answer.request.query if answer.request is not None else ""
        target_language = "German" if locale != "en" else "English"
        evidence = _current_info_evidence_text(answer)
        freshness = answer.evidence.freshness if answer.evidence is not None else ""
        warnings = ", ".join(answer.warnings) if answer.warnings else "none"
        current_time_context = _current_info_current_time_context(answer=answer, timezone_name=timezone_name)
        return (
            "Synthesize a concise Telegram answer from the checked current-info evidence only.\n"
            f"Target language: {target_language}.\n"
            f"{current_time_context}\n"
            "Source class: verified_external_evidence. Treat checked current external evidence as the highest-weight factual source.\n"
            "Use only the checked evidence below; do not use prior model knowledge.\n"
            "Do not treat user claims, prior bot answers, topic summaries, semantic memory, or model prior as evidence for this answer.\n"
            "Do not invent facts, links, dates, numbers, securities listings, exchange listings, or derivatives.\n"
            "The current date/time context above is authoritative for tense and relative date logic. Compare planned, "
            "scheduled, expected, upcoming, or future-dated claims in the evidence against the Current date. If an "
            "evidence date is before the Current date, do not describe it as future, planned, upcoming, or 'bis dahin'; "
            "say that the source reports a date that has already passed and whether the checked evidence confirms the "
            "actual outcome after that date. Use concrete absolute dates for time-sensitive statements.\n"
            "If the evidence does not directly establish a claim, say that the available sources do not establish it.\n"
            "For finance, listing, ticker, token, derivative, or exchange questions, avoid categorical claims unless the "
            "evidence directly supports them.\n"
            "For finance listing questions such as whether a company is börsennotiert/an der Börse/publicly listed, "
            "answer the listing status from direct listing indicators such as Aktie, WKN, ISIN, ticker/symbol, "
            "exchange/listing pages, or official investor-relations share/stock areas. Do not turn a listing answer "
            "into a current-price verification failure unless the user specifically asked for a current price or movement.\n"
            "Mention uncertainty briefly when confidence is low or warnings are present.\n"
            "Use the source landscape below to distinguish checked sources, user-provided links, "
            "official-source candidates, corroborating sources, weak/snippet-only sources, stale sources, "
            "and rejected evidence gaps.\n"
            "Do not include a source list; the application appends sources separately.\n\n"
            f"User question:\n{query}\n\n"
            f"Evidence confidence: {answer.confidence:.2f}\n"
            f"Evidence freshness: {freshness or 'unknown'}\n"
            f"Warnings: {warnings}\n\n"
            f"Deterministic evidence verdict:\n{_current_info_verdict_summary(answer)}\n\n"
            f"Source landscape:\n{_current_info_source_landscape(answer)}\n\n"
            f"Checked evidence:\n{evidence}"
        )

    @staticmethod
    def _format_current_info_telegram_answer(*, answer: CurrentInfoAnswer, synthesized: str, locale: str) -> str:
        body = " ".join((synthesized or "").split())
        if not body:
            body = " ".join(answer.answer_text.split())
        if not body:
            return ""
        body = _rewrite_expired_planned_future_language(body=body, answer=answer, locale=locale)

        sources = tuple(dict.fromkeys(source for source in answer.sources if source))[:CURRENT_INFO_SYNTHESIS_MAX_SOURCE_COUNT]
        if not sources:
            return body

        label = "Sources" if locale == "en" else "Quellen"
        source_lines = [f"{index}. {url}" for index, url in enumerate(sources, start=1)]
        return f"{body}\n\n{label}:\n" + "\n".join(source_lines)

    @staticmethod
    def _format_current_info_compact_fallback_answer(*, answer: CurrentInfoAnswer, locale: str) -> str:
        if not answer.answered:
            return ""
        if not tuple(source for source in answer.sources if source):
            return ""
        if answer.evidence is None:
            return ""
        if not (answer.evidence.chunks or answer.evidence.documents or answer.evidence.sources):
            return ""

        body = " ".join(answer.answer_text.split())
        if len(body) > CURRENT_INFO_COMPACT_FALLBACK_MAX_BODY_CHARS:
            body = body[:CURRENT_INFO_COMPACT_FALLBACK_MAX_BODY_CHARS].rstrip() + " ..."
        compact_answer = replace(answer, answer_text=body)
        return Dispatcher._format_current_info_telegram_answer(answer=compact_answer, synthesized="", locale=locale)

    @staticmethod
    def _format_current_info_insufficient_answer(*, answer: CurrentInfoAnswer, locale: str) -> str:
        sources = tuple(dict.fromkeys(source for source in answer.sources if source))[
            :CURRENT_INFO_SYNTHESIS_MAX_SOURCE_COUNT
        ]
        if locale == "en":
            body = "The available sources and candidates are not sufficient to answer this reliably."
            label = "Sources/candidates considered"
        else:
            body = "Die verfügbaren Quellen und Kandidaten reichen nicht aus, um das verlässlich zu beantworten."
            label = "Berücksichtigte Quellen/Kandidaten"
        if not sources:
            return body
        source_lines = [f"{index}. {url}" for index, url in enumerate(sources, start=1)]
        return f"{body}\n\n{label}:\n" + "\n".join(source_lines)

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

    def _resolve_reply_to_auto_image_attachments(self, *, message: TelegramMessage) -> tuple[Any, ...] | None:
        if message.attachments:
            return None
        if message.chat.type == "private":
            return None
        if not self._is_addressed_for_auto_image(message=message, bot_username=self.bot_username):
            return None

        reply_to_message = getattr(message, "reply_to_message", None)
        if reply_to_message is None:
            return None
        if reply_to_message.chat_id != message.chat.id:
            return None
        if reply_to_message.message_thread_id != message.message_thread_id:
            return None

        attachments = tuple(
            item
            for item in getattr(reply_to_message, "attachments", ())
            if getattr(item, "type_hint", None) in {"image", "image_document"}
        )
        return attachments or None

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
            log_event(
                logger, logging.INFO,
                event="auto_image.followup_bridge",
                component=_COMPONENT,
                chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=message.message_thread_id,
                user_id=message.from_user.id,
                extra={
                    "decision": "not_found",
                    "source": "recent_same_scope",
                },
            )
        return matched

    async def _send_text(self, chat_id: int, text: str, message_thread_id: int | None) -> None:
        for chunk in split_telegram_message_text(text):
            if message_thread_id is None:
                result = await self.send_text(chat_id, chunk)
                await self._persist_bot_send_result(chat_id=chat_id, message_thread_id=None, text=chunk, result=result)
                continue
            try:
                result = await self.send_text(chat_id, chunk, message_thread_id)
            except TypeError:
                result = await self.send_text(chat_id, chunk)
            await self._persist_bot_send_result(chat_id=chat_id, message_thread_id=message_thread_id, text=chunk, result=result)

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


def _current_info_evidence_text(answer: CurrentInfoAnswer) -> str:
    chunks = answer.evidence.chunks if answer.evidence is not None else ()
    lines: list[str] = []
    for index, chunk in enumerate(chunks[:CURRENT_INFO_SYNTHESIS_MAX_SOURCE_COUNT], start=1):
        text = " ".join(chunk.text.split())
        if not text:
            continue
        if len(text) > 900:
            text = text[:900].rstrip() + " ..."
        title = " ".join((chunk.source_title or "").split())
        source = chunk.source_url or ""
        heading = f"[{index}]"
        if title:
            heading = f"{heading} {title}"
        if source:
            heading = f"{heading} ({source})"
        lines.append(f"{heading}\n{text}")

    if not lines:
        fallback = " ".join(answer.answer_text.split())
        if fallback:
            lines.append(fallback[:CURRENT_INFO_SYNTHESIS_MAX_EVIDENCE_CHARS])

    text = "\n\n".join(lines).strip()
    if len(text) > CURRENT_INFO_SYNTHESIS_MAX_EVIDENCE_CHARS:
        text = text[:CURRENT_INFO_SYNTHESIS_MAX_EVIDENCE_CHARS].rstrip() + " ..."
    return text


def _current_info_current_time_context(*, answer: CurrentInfoAnswer, timezone_name: str) -> str:
    metadata = answer.request.metadata if answer.request is not None else {}
    for key in ("current_time_context_text", "current_time_context"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata_timezone = metadata.get("timezone")
    tz_name = metadata_timezone if isinstance(metadata_timezone, str) and metadata_timezone.strip() else timezone_name
    return build_current_time_context(now=_parse_current_info_now(metadata.get("now")), timezone_name=tz_name)


def _parse_current_info_now(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


_MONTHS: dict[str, int] = {
    "januar": 1,
    "jan": 1,
    "january": 1,
    "februar": 2,
    "feb": 2,
    "february": 2,
    "maerz": 3,
    "märz": 3,
    "mar": 3,
    "march": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "may": 5,
    "juni": 6,
    "jun": 6,
    "june": 6,
    "juli": 7,
    "jul": 7,
    "july": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "december": 12,
    "dec": 12,
}
_MONTH_PATTERN = "|".join(sorted((re.escape(item) for item in _MONTHS), key=len, reverse=True))
_DMY_DATE_RE = re.compile(rf"\b(?:den\s+)?(\d{{1,2}})\.\s*({_MONTH_PATTERN})\s+(\d{{4}})\b", re.IGNORECASE)
_MDY_DATE_RE = re.compile(rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})(?:,)?\s+(\d{{4}})\b", re.IGNORECASE)


def _rewrite_expired_planned_future_language(*, body: str, answer: CurrentInfoAnswer, locale: str) -> str:
    if not re.search(r"\b(bis dahin|until then|planned|geplant|scheduled|upcoming|erwartet)\b", body, re.IGNORECASE):
        return body
    metadata = answer.request.metadata if answer.request is not None else {}
    now = _parse_current_info_now(metadata.get("now"))
    if now is None:
        return body
    expired_date_texts = _expired_date_texts(body, current_date=now.date())
    if not expired_date_texts:
        return body
    rewritten = body
    for date_text in expired_date_texts:
        escaped = re.escape(date_text)
        rewritten = re.sub(
            rf"\bist\s+für\s+(?:den\s+)?({escaped})\s+([^.!?]*?)\s+geplant\b",
            r"wurde für \1 \2 als geplant beschrieben",
            rewritten,
            flags=re.IGNORECASE,
        )
        rewritten = re.sub(
            rf"\bis\s+planned\s+for\s+({escaped})\s*([^.!?]*?)\b",
            r"was described as planned for \1 \2",
            rewritten,
            flags=re.IGNORECASE,
        )
    if locale == "en":
        rewritten = re.sub(r"\s*,?\s*but\s+until\s+then\b[^.!?]*(?=[.!?])", "", rewritten, flags=re.IGNORECASE)
    else:
        rewritten = re.sub(r"\s*,?\s*aber\s+bis\s+dahin\b[^.!?]*(?=[.!?])", "", rewritten, flags=re.IGNORECASE)
    return " ".join(rewritten.split())


def _expired_date_texts(text: str, *, current_date) -> tuple[str, ...]:
    expired: list[str] = []
    for match in _DMY_DATE_RE.finditer(text):
        day = int(match.group(1))
        month = _MONTHS[match.group(2).casefold()]
        year = int(match.group(3))
        try:
            candidate = datetime(year, month, day, tzinfo=UTC).date()
        except ValueError:
            continue
        if candidate < current_date:
            expired.append(match.group(0))
    for match in _MDY_DATE_RE.finditer(text):
        month = _MONTHS[match.group(1).casefold()]
        day = int(match.group(2))
        year = int(match.group(3))
        try:
            candidate = datetime(year, month, day, tzinfo=UTC).date()
        except ValueError:
            continue
        if candidate < current_date:
            expired.append(match.group(0))
    return tuple(dict.fromkeys(expired))


def _current_info_source_landscape(answer: CurrentInfoAnswer) -> str:
    if answer.evidence is None or not answer.evidence.sources:
        if answer.warnings:
            return "No checked sources. Evidence gaps: " + ", ".join(answer.warnings)
        return "No checked sources."

    lines: list[str] = []
    for index, source in enumerate(answer.evidence.sources[:CURRENT_INFO_SYNTHESIS_MAX_SOURCE_COUNT], start=1):
        role = source.source_role or "corroborating_source"
        quality = source.quality_label or ("stale" if source.stale else "checked_source" if source.fetched else "weak_source")
        status = "checked" if source.fetched else "not_fetched"
        stale = ", stale" if source.stale else ""
        title = f" - {' '.join(source.title.split())}" if source.title else ""
        lines.append(f"[{index}] {status}, role={role}, quality={quality}{stale}, host={source.host}{title}")
    if answer.warnings:
        lines.append("Evidence gaps: " + ", ".join(answer.warnings))
    return "\n".join(lines)


def _current_info_verdict_summary(answer: CurrentInfoAnswer) -> str:
    verdict = answer.metadata.get("listing_verdict") if isinstance(answer.metadata, dict) else None
    if not isinstance(verdict, dict):
        return "No deterministic verdict available."
    summary = str(verdict.get("summary") or "").strip()
    classification = str(verdict.get("classification") or "unknown")
    conflict = bool(verdict.get("conflict"))
    counts = (
        f"supports_listed={verdict.get('supports_listed_count', 0)}, "
        f"supports_private={verdict.get('supports_private_count', 0)}"
    )
    if not summary:
        summary = f"classification={classification}, conflict={conflict}, {counts}."
    return f"{summary} classification={classification}; conflict={conflict}; {counts}."


def _auto_note_has_verified_external_evidence(auto_note: str) -> bool:
    note = (auto_note or "").strip()
    if not note:
        return False
    upper = note.upper()
    no_usable_markers = (
        "NO USABLE RESULT",
        "SOURCE CHECK INCONCLUSIVE",
        "NO USABLE LIVE RESULT",
        "NO ADDITIONAL USABLE CONFIRMATION",
    )
    if any(marker in upper for marker in no_usable_markers):
        return False
    return "AUTO-RESEARCH" in upper and (
        "LIVE WEB" in upper
        or "WEB TOOL RESULT IS AVAILABLE" in upper
        or "CHECKED SOURCE EVIDENCE" in upper
    )
