from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from amo_bot.auth.permissions import can_use_bot
from amo_bot.telegram.commands import CommandContext, CommandRegistry, RoleResolver
from amo_bot.telegram.update_parser import parse_update

SendTextFn = Callable[[int, str], Awaitable[object]]


@dataclass(slots=True)
class Dispatcher:
    command_registry: CommandRegistry
    role_resolver: RoleResolver
    send_text: SendTextFn
    bot_username: str | None = None

    async def handle_raw_update(self, raw_update: object) -> None:
        update = parse_update(raw_update)
        if update is None or update.message is None:
            return

        message = update.message
        command = message.parse_command(bot_username=self.bot_username)
        if command is None:
            return

        role = await self.role_resolver.resolve(message.from_user.id)
        if not can_use_bot(role):
            return

        if not self.command_registry.is_allowed(command.name, role):
            return

        command_def = self.command_registry.get(command.name)
        if command_def is None:
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
            await self.send_text(message.chat.id, response)
