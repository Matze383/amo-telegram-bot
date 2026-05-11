from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from amo_bot.auth.permissions import can_use_bot
from amo_bot.auth.roles import Role
from amo_bot.consent import CONSENT_UNREACHABLE, ConsentService
from amo_bot.db.base import create_session_factory
from amo_bot.db.models import User
from amo_bot.plugins.command_runtime import CommandActor, CommandInvocation, PluginCommandExecutor
from amo_bot.telegram.commands import CommandContext, CommandRegistry, RoleResolver
from amo_bot.telegram.update_parser import TelegramMessage, parse_update

SendTextFn = Callable[[int, str, int | None], Awaitable[object]]
SendMarkupFn = Callable[[int, str, dict[str, Any], int | None], Awaitable[object]]
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
    answer_callback: AnswerCallbackFn | None = None
    bot_username: str | None = None
    message_persistence: MessagePersistence | None = None
    plugin_command_executor: PluginCommandExecutor | None = None
    database_url: str | None = None

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
        if command is None:
            return

        role = await self.role_resolver.resolve(
            message.from_user.id,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
        )
        if not can_use_bot(role):
            return

        if self._is_consent_command(command.name):
            pass
        elif self._is_consent_blocked(
            user_id=message.from_user.id,
            role=role,
            command_name=command.name,
            chat_type=message.chat.type,
        ):
            response = self._consent_block_message(chat_type=message.chat.type, blocked_as_unreachable=self._is_user_unreachable(message.from_user.id))
            if response:
                await self.send_text(message.chat.id, response, message.message_thread_id)
            return

        command_def = self.command_registry.get(command.name)
        if command_def is None:
            if self.plugin_command_executor is not None:
                await self.plugin_command_executor.execute(
                    actor=CommandActor(telegram_user_id=message.from_user.id, role=role),
                    invocation=CommandInvocation(
                        command_name=command.name,
                        argument=command.argument,
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        message_thread_id=message.message_thread_id,
                    ),
                )
            return

        if not self.command_registry.is_allowed(command.name, role):
            return

        ctx = CommandContext(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            role=role,
            command_name=command.name,
            argument=command.argument,
        )
        response = await command_def.handler(ctx)
        if isinstance(response, dict):
            text = response.get("text")
            reply_markup = response.get("reply_markup")
            if isinstance(text, str) and text:
                if isinstance(reply_markup, dict) and self.send_markup is not None:
                    await self.send_markup(message.chat.id, text, reply_markup, message.message_thread_id)
                else:
                    await self.send_text(message.chat.id, text, message.message_thread_id)
            return
        if response:
            await self.send_text(message.chat.id, response, message.message_thread_id)

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
            await self.send_text(
                callback_query.message.chat.id,
                "Button test ok",
                callback_query.message.message_thread_id,
            )

    async def _handle_consent_callback(self, *, callback_query: Any, role: Role, data: str) -> None:
        if self.database_url is None:
            if self.answer_callback is not None:
                await self.answer_callback(callback_query.id, "Consent nicht verfügbar")
            return

        session_factory = create_session_factory(self.database_url)
        with session_factory() as session:
            user = session.query(User).filter(User.telegram_user_id == callback_query.from_user.id).one_or_none()
            if user is None:
                if self.answer_callback is not None:
                    await self.answer_callback(callback_query.id, "Profil nicht gefunden")
                return

            consent_service = ConsentService()
            if data == "consent:accept":
                consent_service.accept(user)
                session.commit()
                if self.answer_callback is not None:
                    await self.answer_callback(callback_query.id, "Consent akzeptiert")
                return

            if data == "consent:decline":
                consent_service.decline(user)
                session.commit()
                if self.answer_callback is not None:
                    await self.answer_callback(callback_query.id, "Consent abgelehnt")
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
    def _consent_block_message(*, chat_type: str | None, blocked_as_unreachable: bool) -> str:
        if chat_type in {"group", "supergroup"}:
            return "Bitte kläre Consent privat mit dem Bot."
        if blocked_as_unreachable:
            return "Bitte starte den Bot privat und bestätige mit /accept."
        return "Bitte bestätige zuerst mit /accept oder prüfe /consent."
