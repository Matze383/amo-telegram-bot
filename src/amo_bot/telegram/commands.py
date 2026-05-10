from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from typing import Awaitable, Callable


from amo_bot.ai.service import AIService, OllamaError
from amo_bot.auth.permissions import ADMIN_ASSIGNABLE_ROLES, can_assign_role, can_use_bot
from amo_bot.auth.roles import ROLE_PRIORITY, Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.models import GROUP_CHAT_TYPES, TelegramChat
from amo_bot.db.models import AuditEvent
from amo_bot.db.repositories import ChatScopedRoleRepository, UserRoleRepository
from amo_bot.webui.access_window import WebuiAccessWindowService


@dataclass(slots=True)
class CommandContext:
    chat_id: int
    user_id: int
    role: Role
    command_name: str
    argument: str | None

    @property
    def chat_type(self) -> str:
        return "private" if self.chat_id > 0 else "group"


CommandHandler = Callable[[CommandContext], Awaitable[str | None]]


@dataclass(slots=True)
class Command:
    name: str
    description: str
    allowed_roles: set[Role]
    handler: CommandHandler


logger = logging.getLogger(__name__)


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        key = command.name.casefold()
        if key in self._commands:
            raise ValueError(f"command already registered: {command.name}")
        self._commands[key] = Command(
            name=key,
            description=command.description,
            allowed_roles=set(command.allowed_roles),
            handler=command.handler,
        )

    def get(self, name: str) -> Command | None:
        return self._commands.get(name.casefold())

    def is_allowed(self, name: str, role: Role) -> bool:
        if not can_use_bot(role):
            return False
        command = self.get(name)
        if command is None:
            return False
        return role in command.allowed_roles

    def list_allowed(self, role: Role) -> list[Command]:
        if not can_use_bot(role):
            return []
        return sorted(
            (cmd for cmd in self._commands.values() if role in cmd.allowed_roles),
            key=lambda cmd: ROLE_PRIORITY.get(role, 1000) + len(cmd.name),
        )


class RoleResolver:
    async def resolve(self, user_id: int, *, chat_id: int | None = None, chat_type: str | None = None) -> Role:
        raise NotImplementedError


class StaticRoleResolver(RoleResolver):
    def __init__(self, mapping: dict[int, Role] | None = None, default_role: Role = Role.NORMAL) -> None:
        self._mapping = mapping or {}
        self._default_role = default_role

    async def resolve(self, user_id: int, *, chat_id: int | None = None, chat_type: str | None = None) -> Role:
        return self._mapping.get(user_id, self._default_role)


def create_builtin_registry(database_url: str | None = None, ai_service: AIService | None = None) -> CommandRegistry:
    registry = CommandRegistry()
    session_factory = create_session_factory(database_url) if database_url else None

    async def ping_handler(ctx: CommandContext) -> str:
        return "pong"

    async def role_handler(ctx: CommandContext) -> str:
        return f"your role: {ctx.role.value}"



    async def setrole_handler(ctx: CommandContext) -> str:
        if ctx.role not in {Role.OWNER, Role.ADMIN}:
            return "permission denied"

        if not ctx.argument:
            return "usage: /setrole <telegram_user_id> <role>"

        parts = ctx.argument.split()
        if len(parts) != 2:
            return "usage: /setrole <telegram_user_id> <role>"

        user_raw, role_raw = parts
        try:
            target_user_id = int(user_raw)
        except ValueError:
            return "invalid telegram_user_id"

        try:
            target_role = Role(role_raw.casefold())
        except ValueError:
            allowed = ", ".join(r.value for r in Role)
            return f"invalid role. allowed: {allowed}"

        if not can_assign_role(ctx.role, target_role):
            if ctx.role == Role.ADMIN:
                allowed = ", ".join(sorted(r.value for r in ADMIN_ASSIGNABLE_ROLES))
                return f"permission denied. admin may only assign: {allowed}"
            return "permission denied"

        if ctx.role == Role.OWNER and target_role == Role.OWNER:
            return "owner assignment via telegram is disabled in MVP (use webui)"

        if session_factory is None:
            return "role management not configured"

        with session_factory() as session:
            if ctx.chat_id < 0:
                chat_row = session.query(TelegramChat).filter(TelegramChat.chat_id == ctx.chat_id).one_or_none()
                if chat_row is not None and chat_row.chat_type in GROUP_CHAT_TYPES:
                    if target_role == Role.NORMAL:
                        previous_group_role = ChatScopedRoleRepository(session).get_group_role(
                            chat_id=ctx.chat_id,
                            telegram_user_id=target_user_id,
                        )
                        changed = ChatScopedRoleRepository(session).clear_group_role(
                            chat_id=ctx.chat_id,
                            telegram_user_id=target_user_id,
                            actor_telegram_user_id=ctx.user_id,
                            source="telegram_command",
                        )
                        result = type("GroupRoleClearResult", (), {
                            "changed": changed,
                            "previous_role": previous_group_role,
                            "new_role": Role.NORMAL,
                        })()
                    else:
                        result = ChatScopedRoleRepository(session).set_group_role(
                            chat_id=ctx.chat_id,
                            telegram_user_id=target_user_id,
                            role=target_role,
                            actor_telegram_user_id=ctx.user_id,
                            source="telegram_command",
                        )
                else:
                    repo = UserRoleRepository(session)
                    result = repo.set_user_role(
                        actor_telegram_user_id=ctx.user_id,
                        target_telegram_user_id=target_user_id,
                        role=target_role,
                    )
            else:
                repo = UserRoleRepository(session)
                result = repo.set_user_role(
                    actor_telegram_user_id=ctx.user_id,
                    target_telegram_user_id=target_user_id,
                    role=target_role,
                )

        if result.changed:
            prev = result.previous_role.value if result.previous_role else "<new>"
            return f"role updated: {target_user_id} {prev} -> {result.new_role.value}"
        return f"no change: {target_user_id} already {result.new_role.value}"

    async def ask_handler(ctx: CommandContext) -> str:
        if not ctx.argument or not ctx.argument.strip():
            return "usage: /ask <question>"
        if ai_service is None:
            return "AI service is not configured"

        try:
            return await ai_service.ask(ctx.argument)
        except OllamaError:
            logger.exception("/ask failed: ollama runtime error user_id=%s chat_id=%s", ctx.user_id, ctx.chat_id)
            return "Sorry, I cannot answer right now. Please try again later."
        except ValueError:
            logger.exception("/ask failed: invalid prompt user_id=%s chat_id=%s", ctx.user_id, ctx.chat_id)
            return "Sorry, I cannot answer right now. Please try again later."

    async def webui_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return "webui access control not configured"

        subcommand = (ctx.argument or "status").strip().casefold()
        if subcommand not in {"on", "off", "status"}:
            return "usage: /webui <on|off|status>"

        if ctx.chat_type != "private":
            with session_factory() as session:
                session.add(
                    AuditEvent(
                        actor_telegram_user_id=ctx.user_id,
                        event_type="webui_access_denied",
                        payload_json=json.dumps(
                            {
                                "reason": "not_private",
                                "chat_id": ctx.chat_id,
                                "chat_type": ctx.chat_type,
                                "command": subcommand,
                            }
                        ),
                    )
                )
                session.commit()
            return "permission denied"

        if ctx.role != Role.OWNER:
            with session_factory() as session:
                session.add(
                    AuditEvent(
                        actor_telegram_user_id=ctx.user_id,
                        event_type="webui_access_denied",
                        payload_json=json.dumps(
                            {
                                "reason": "not_owner",
                                "chat_id": ctx.chat_id,
                                "chat_type": ctx.chat_type,
                                "command": subcommand,
                            }
                        ),
                    )
                )
                session.commit()
            return "permission denied"

        service = WebuiAccessWindowService(session_factory)
        now = datetime.now(UTC)

        if subcommand == "on":
            enabled_until = service.enable_for_one_hour(actor_id=ctx.user_id, now_utc=now)
            with session_factory() as session:
                session.add(
                    AuditEvent(
                        actor_telegram_user_id=ctx.user_id,
                        event_type="webui_access_enabled",
                        payload_json=json.dumps(
                            {
                                "chat_id": ctx.chat_id,
                                "chat_type": ctx.chat_type,
                                "enabled_until": enabled_until.isoformat(),
                            }
                        ),
                    )
                )
                session.commit()
            return f"webui access: OPEN (~60m, until {enabled_until.strftime('%H:%M UTC')})"

        if subcommand == "off":
            service.disable(actor_id=ctx.user_id, now_utc=now)
            with session_factory() as session:
                session.add(
                    AuditEvent(
                        actor_telegram_user_id=ctx.user_id,
                        event_type="webui_access_disabled",
                        payload_json=json.dumps(
                            {
                                "chat_id": ctx.chat_id,
                                "chat_type": ctx.chat_type,
                            }
                        ),
                    )
                )
                session.commit()
            return "webui access: CLOSED"

        status = service.get_status(now_utc=now)
        remaining_minutes = max(0, status.remaining_seconds // 60)
        with session_factory() as session:
            session.add(
                AuditEvent(
                    actor_telegram_user_id=ctx.user_id,
                    event_type="webui_access_status",
                    payload_json=json.dumps(
                        {
                            "chat_id": ctx.chat_id,
                            "chat_type": ctx.chat_type,
                            "open": status.open,
                            "remaining_minutes": remaining_minutes,
                            "enabled_until": status.enabled_until.isoformat() if status.enabled_until is not None else None,
                        }
                    ),
                )
            )
            session.commit()

        if status.open:
            return f"webui access: OPEN (remaining: {remaining_minutes}m)"
        return "webui access: CLOSED"

    async def help_handler(ctx: CommandContext) -> str:
        allowed = registry.list_allowed(ctx.role)
        if not allowed:
            return "no commands available"
        lines = ["available commands:"]
        for cmd in allowed:
            lines.append(f"/{cmd.name} - {cmd.description}")
        return "\n".join(lines)

    normal_plus = {Role.OWNER, Role.ADMIN, Role.VIP, Role.NORMAL}
    ask_roles = {Role.OWNER, Role.ADMIN, Role.VIP}
    admin_plus = {Role.OWNER, Role.ADMIN}

    registry.register(Command(name="ping", description="Health check", allowed_roles=normal_plus, handler=ping_handler))
    registry.register(Command(name="help", description="List available commands", allowed_roles=normal_plus, handler=help_handler))
    registry.register(Command(name="role", description="Show your current role", allowed_roles=normal_plus, handler=role_handler))
    registry.register(Command(name="ask", description="Ask Ollama: /ask <question>", allowed_roles=ask_roles, handler=ask_handler))
    registry.register(
        Command(
            name="setrole",
            description="Set role: /setrole <telegram_user_id> <role>",
            allowed_roles=admin_plus,
            handler=setrole_handler,
        )
    )
    registry.register(
        Command(
            name="webui",
            description="WebUI access window: /webui <on|off|status>",
            allowed_roles={Role.OWNER, Role.ADMIN, Role.VIP, Role.NORMAL},
            handler=webui_handler,
        )
    )

    return registry
