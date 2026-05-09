from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from amo_bot.auth.permissions import can_use_bot
from amo_bot.plugins.command_runtime import CommandActor, CommandInvocation, PluginCommandExecutor
from amo_bot.telegram.commands import CommandContext, CommandRegistry, RoleResolver
from amo_bot.telegram.update_parser import TelegramMessage, parse_update

SendTextFn = Callable[[int, str, int | None], Awaitable[object]]

logger = logging.getLogger(__name__)


class MessagePersistence(Protocol):
    async def persist_message(self, message: TelegramMessage) -> None: ...


@dataclass(slots=True)
class Dispatcher:
    command_registry: CommandRegistry
    role_resolver: RoleResolver
    send_text: SendTextFn
    bot_username: str | None = None
    message_persistence: MessagePersistence | None = None
    plugin_command_executor: PluginCommandExecutor | None = None

    async def handle_raw_update(self, raw_update: object) -> None:
        update = parse_update(raw_update)
        if update is None or update.message is None:
            return

        message = update.message
        if self.message_persistence is not None:
            try:
                await self.message_persistence.persist_message(message)
            except Exception:
                logger.exception("Failed to persist Telegram message; continuing update handling")

        command = message.parse_command(bot_username=self.bot_username)
        if command is None:
            return

        role = await self.role_resolver.resolve(message.from_user.id)
        if not can_use_bot(role):
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
        if response:
            await self.send_text(message.chat.id, response, message.message_thread_id)
