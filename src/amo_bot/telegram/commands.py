from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from typing import Awaitable, Callable, Literal


from amo_bot.ai.service import AIService, OllamaError
from amo_bot.auth.permissions import ADMIN_ASSIGNABLE_ROLES, can_assign_role, can_use_bot
from amo_bot.auth.roles import ROLE_PRIORITY, Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.models import GROUP_CHAT_TYPES, AuditEvent, TelegramChat, User
from amo_bot.consent import CONSENT_ACCEPTED, CONSENT_DECLINED, CONSENT_PENDING, CONSENT_UNREACHABLE, ConsentService
from amo_bot.consent.prompt_service import ConsentPromptService
from amo_bot.db.repositories import ChatScopedRoleRepository, UserRoleRepository
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.webui.access_window import WebuiAccessWindowService


@dataclass(slots=True)
class CommandContext:
    chat_id: int
    user_id: int
    role: Role
    command_name: str
    argument: str | None
    locale: Locale = "de"

    @property
    def chat_type(self) -> str:
        return "private" if self.chat_id > 0 else "group"


CommandHandler = Callable[[CommandContext], Awaitable[str | dict[str, object] | None]]


@dataclass(slots=True)
class Command:
    name: str
    description: str
    allowed_roles: set[Role]
    handler: CommandHandler
    description_de: str | None = None
    description_en: str | None = None


logger = logging.getLogger(__name__)


Locale = Literal["de", "en"]


def resolve_locale(*, explicit_arg: str | None, telegram_language_code: str | None = None) -> Locale:
    if explicit_arg:
        normalized = explicit_arg.strip().casefold()
        if normalized in {"de", "de-de", "deutsch", "german"}:
            return "de"
        if normalized in {"en", "en-us", "en-gb", "english"}:
            return "en"

    if telegram_language_code:
        language = telegram_language_code.strip().casefold()
        if language.startswith("de"):
            return "de"
        if language.startswith("en"):
            return "en"

    return "de"


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
            description_de=command.description_de,
            description_en=command.description_en,
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


def create_builtin_registry(
    database_url: str | None = None,
    ai_service: AIService | None = None,
    owner_notifier: OwnerNotifier | None = None,
) -> CommandRegistry:
    registry = CommandRegistry()
    session_factory = create_session_factory(database_url) if database_url else None

    async def ping_handler(ctx: CommandContext) -> str:
        return _lang(ctx, "pong", "pong")

    def _lang(ctx: CommandContext, de: str, en: str) -> str:
        return de if ctx.locale == "de" else en

    def _consent_status_explanation(status: str, locale: Locale) -> str:
        messages = {
            CONSENT_ACCEPTED: {"de": "Du hast zugestimmt.", "en": "You agreed to consent."},
            CONSENT_DECLINED: {"de": "Du hast abgelehnt.", "en": "You declined consent."},
            CONSENT_PENDING: {"de": "Consent ist noch ausstehend.", "en": "Consent is still pending."},
            CONSENT_UNREACHABLE: {"de": "Consent ist als nicht erreichbar markiert.", "en": "Consent marked as unreachable."},
        }
        fallback = {"de": "Consent-Status gespeichert.", "en": "Consent status recorded."}
        return messages.get(status, fallback)[locale]

    async def start_handler(ctx: CommandContext) -> dict[str, object] | str:
        if session_factory is None:
            return _lang(ctx, "Consent-Verwaltung ist nicht konfiguriert.", "Consent management is not configured.")
        if ctx.chat_type != "private":
            return _lang(ctx, "Bitte öffne die Policy privat über den Button.", "Please open the policy in a private chat via the button.")

        with session_factory() as session:
            user = session.query(User).filter(User.telegram_user_id == ctx.user_id).one_or_none()
            if user is None:
                user = UserRoleRepository(session).upsert_discovered_user(
                    telegram_user_id=ctx.user_id,
                    username=None,
                    first_name=None,
                    last_name=None,
                )
                session.commit()

            status = ConsentService().get_status(user)
            if status == CONSENT_UNREACHABLE:
                user.consent_status = CONSENT_PENDING
                session.commit()
                status = CONSENT_PENDING

        if status == CONSENT_PENDING:
            return {
                "text": ConsentPromptService.build_prompt_text(ctx.locale),
                "reply_markup": ConsentPromptService.build_prompt_markup(ctx.locale),
            }

        if status == CONSENT_ACCEPTED:
            return _lang(ctx, "Consent ist bereits akzeptiert. ✅", "Consent has already been accepted. ✅")

        if status == CONSENT_DECLINED:
            return _lang(ctx, "Consent ist aktuell abgelehnt. Du kannst mit /accept wieder zustimmen.", "Consent is currently declined. You can agree again with /accept.")

        return {
            "text": ConsentPromptService.build_prompt_text(ctx.locale),
            "reply_markup": ConsentPromptService.build_prompt_markup(ctx.locale),
        }

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

    async def accept_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return _lang(ctx, "Consent-Verwaltung ist nicht konfiguriert.", "Consent management is not configured.")
        with session_factory() as session:
            user = session.query(User).filter(User.telegram_user_id == ctx.user_id).one_or_none()
            if user is None:
                return _lang(ctx, "Benutzerprofil fehlt noch. Sende /ping und versuche es erneut.", "User profile not found yet. Send /ping and try again.")
            ConsentService().accept(user)
            session.commit()
            if owner_notifier is not None:
                await owner_notifier.notify_consent_decision(user=user, accepted=True, source="command:/accept")
        return _lang(ctx, "Consent akzeptiert. Danke — mit /decline kannst du das jederzeit ändern.", "Consent accepted. Thanks — you can use /decline anytime to change this.")

    async def decline_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return _lang(ctx, "Consent-Verwaltung ist nicht konfiguriert.", "Consent management is not configured.")
        with session_factory() as session:
            user = session.query(User).filter(User.telegram_user_id == ctx.user_id).one_or_none()
            if user is None:
                return _lang(ctx, "Benutzerprofil fehlt noch. Sende /ping und versuche es erneut.", "User profile not found yet. Send /ping and try again.")
            ConsentService().decline(user)
            session.commit()
            if owner_notifier is not None:
                await owner_notifier.notify_consent_decision(user=user, accepted=False, source="command:/decline")
        return _lang(ctx, "Consent abgelehnt. Mit /accept kannst du später wieder zustimmen.", "Consent declined. You can re-enable later with /accept.")

    async def consent_handler(ctx: CommandContext) -> str:
        if ctx.chat_type != "private":
            return _lang(ctx, "Aus Datenschutzgründen nutze bitte /consent im privaten Chat mit mir.", "For privacy, please use /consent in a private chat with me.")
        if session_factory is None:
            return _lang(ctx, "Consent-Verwaltung ist nicht konfiguriert.", "Consent management is not configured.")
        with session_factory() as session:
            user = session.query(User).filter(User.telegram_user_id == ctx.user_id).one_or_none()
            if user is None:
                return _lang(
                    ctx,
                    "Consent-Status: unbekannt\nSende zuerst einen beliebigen Befehl im privaten Chat und versuche dann /consent erneut.",
                    "Consent status: unknown\nSend any command in private first, then retry /consent.",
                )
            status = ConsentService().get_status(user)

        return _lang(
            ctx,
            (
                f"Consent-Status: {status}\n"
                f"{_consent_status_explanation(status, 'de')}\n"
                "Nutze /accept oder /decline, um deine Entscheidung zu ändern."
            ),
            (
                f"Consent status: {status}\n"
                f"{_consent_status_explanation(status, 'en')}\n"
                "Use /accept or /decline to change your choice."
            ),
        )

    async def ask_handler(ctx: CommandContext) -> str:
        if not ctx.argument or not ctx.argument.strip():
            return _lang(ctx, "Nutzung: /ask <frage>", "usage: /ask <question>")
        if ai_service is None:
            return _lang(ctx, "AI-Service ist nicht konfiguriert.", "AI service is not configured")

        try:
            return await ai_service.ask(ctx.argument)
        except OllamaError:
            logger.exception("/ask failed: ollama runtime error user_id=%s chat_id=%s", ctx.user_id, ctx.chat_id)
            return _lang(ctx, "Sorry, ich kann gerade nicht antworten. Bitte versuche es später erneut.", "Sorry, I cannot answer right now. Please try again later.")
        except ValueError:
            logger.exception("/ask failed: invalid prompt user_id=%s chat_id=%s", ctx.user_id, ctx.chat_id)
            return _lang(ctx, "Sorry, ich kann gerade nicht antworten. Bitte versuche es später erneut.", "Sorry, I cannot answer right now. Please try again later.")

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

    async def test_handler(ctx: CommandContext) -> dict[str, object]:
        payload: dict[str, object] = {
            "text": "Inline-Button-Test: Bitte klicken.",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": "✅ Test Button",
                            "callback_data": "test:ok",
                        }
                    ]
                ]
            },
        }
        if ctx.chat_type != "private":
            payload["target_user_id"] = ctx.user_id
            payload["group_success_text"] = _lang(ctx, "Ich habe dir den Button-Test privat geschickt.", "I sent you the button test in private.")
            payload["group_fallback_text"] = _lang(ctx, "Ich kann dir aktuell keine private Nachricht senden. Bitte starte den Bot zuerst privat mit /start.", "I can't send you a private message right now. Please start the bot in private first with /start.")
        return payload

    async def help_handler(ctx: CommandContext) -> str:
        allowed = registry.list_allowed(ctx.role)
        if not allowed:
            return _lang(ctx, "Keine Befehle verfügbar.", "no commands available")
        lines = [_lang(ctx, "Verfügbare Befehle:", "available commands:")]
        for cmd in allowed:
            description = cmd.description_de if ctx.locale == "de" else cmd.description_en
            if not description:
                description = cmd.description
            lines.append(f"/{cmd.name} - {description}")
        return "\n".join(lines)

    normal_plus = {Role.OWNER, Role.ADMIN, Role.VIP, Role.NORMAL}
    ask_roles = {Role.OWNER, Role.ADMIN, Role.VIP}
    admin_plus = {Role.OWNER, Role.ADMIN}

    registry.register(Command(name="ping", description="Health check", description_de="Bot-Erreichbarkeit prüfen", description_en="Check bot health", allowed_roles=normal_plus, handler=ping_handler))
    registry.register(Command(name="help", description="List available commands", description_de="Verfügbare Befehle anzeigen", description_en="List available commands", allowed_roles=normal_plus, handler=help_handler))
    registry.register(Command(name="role", description="Show your current role", description_de="Deine aktuelle Rolle anzeigen", description_en="Show your current role", allowed_roles=normal_plus, handler=role_handler))
    registry.register(Command(name="start", description="Start consent flow in private chat", description_de="Consent-Flow im privaten Chat starten", description_en="Start consent flow in private chat", allowed_roles=normal_plus, handler=start_handler))
    registry.register(Command(name="accept", description="Accept consent", description_de="Consent akzeptieren", description_en="Accept consent", allowed_roles=normal_plus, handler=accept_handler))
    registry.register(Command(name="decline", description="Decline consent", description_de="Consent ablehnen", description_en="Decline consent", allowed_roles=normal_plus, handler=decline_handler))
    registry.register(Command(name="consent", description="Show consent status", description_de="Consent-Status anzeigen", description_en="Show consent status", allowed_roles=normal_plus, handler=consent_handler))
    registry.register(Command(name="ask", description="Ask Ollama: /ask <question>", description_de="Ollama fragen: /ask <frage>", description_en="Ask Ollama: /ask <question>", allowed_roles=ask_roles, handler=ask_handler))
    registry.register(
        Command(
            name="setrole",
            description="Set role: /setrole <telegram_user_id> <role>",
            description_de="Rolle setzen: /setrole <telegram_user_id> <rolle>",
            description_en="Set role: /setrole <telegram_user_id> <role>",
            allowed_roles=admin_plus,
            handler=setrole_handler,
        )
    )
    registry.register(
        Command(
            name="test",
            description="Send inline-button smoke test",
            description_de="Inline-Button-Smoketest senden",
            description_en="Send inline button smoke test",
            allowed_roles=admin_plus,
            handler=test_handler,
        )
    )
    registry.register(
        Command(
            name="webui",
            description="WebUI access window: /webui <on|off|status>",
            description_de="WebUI-Zugriff: /webui <on|off|status>",
            description_en="WebUI access window: /webui <on|off|status>",
            allowed_roles={Role.OWNER, Role.ADMIN, Role.VIP, Role.NORMAL},
            handler=webui_handler,
        )
    )

    return registry
