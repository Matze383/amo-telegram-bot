from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
import json
import logging
from typing import Awaitable, Callable, Literal


from amo_bot.ai.memory_c2_service import MemoryC2Service, MemoryScope
from amo_bot.ai.service import AIService, OllamaError
from amo_bot.auth.permissions import ADMIN_ASSIGNABLE_ROLES, can_assign_role, can_use_bot
from amo_bot.auth.roles import ROLE_PRIORITY, Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import GROUP_CHAT_TYPES, AuditEvent, TelegramChat, User
from amo_bot.consent import CONSENT_ACCEPTED, CONSENT_DECLINED, CONSENT_PENDING, CONSENT_UNREACHABLE, ConsentService
from amo_bot.consent.prompt_service import ConsentPromptService
from amo_bot.db.repositories import (
    ChatScopedRoleRepository,
    PromptContextDocRepository,
    TopicAgentMemoryRepository,
    UserMemoryProfileRepository,
    UserRoleRepository,
    WebToolRoleQuotaRepository,
)
from amo_bot.telegram.owner_notify import OwnerNotifier
from amo_bot.webui.access_window import WebuiAccessWindowService


@dataclass(slots=True)
class CommandContext:
    chat_id: int
    user_id: int
    role: Role
    command_name: str
    argument: str | None
    message_thread_id: int | None = None
    locale: Locale = "de"
    reply_to_message_text: str = ""

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

AI_SESSION_IDLE_TIMEOUT = timedelta(hours=8)
PROMPT_CONTEXT_DOC_MAX_CHARS = 6000
TELEGRAM_SAFE_MESSAGE_CHARS = 3900


def _ai_scope_from_ctx(ctx: CommandContext) -> tuple[str, int | None, int | None, int | None]:
    if ctx.chat_type == "private":
        return ("private_user", None, None, ctx.user_id)
    if ctx.message_thread_id is not None:
        return ("topic", ctx.chat_id, ctx.message_thread_id, None)
    return ("group_chat", ctx.chat_id, None, None)


def _profile_scope_from_ctx(ctx: CommandContext) -> MemoryScope:
    if ctx.chat_type == "private":
        return MemoryScope(scope_type="private_user", user_id=ctx.user_id)
    if ctx.message_thread_id is not None:
        return MemoryScope(scope_type="topic", chat_id=ctx.chat_id, topic_id=ctx.message_thread_id, user_id=ctx.user_id)
    return MemoryScope(scope_type="group_chat", chat_id=ctx.chat_id, user_id=ctx.user_id)


def _ai_scope_key(scope_type: str, chat_id: int | None, topic_id: int | None, user_id: int | None) -> str:
    return f"{scope_type}:{chat_id}:{topic_id}:{user_id}"


def _load_or_create_scoped_ai_session(*, repo: TopicAgentMemoryRepository, scope_type: str, chat_id: int | None, topic_id: int | None, user_id: int | None, now: datetime) -> tuple[dict[str, object], str]:
    current_day = now.astimezone(timezone.utc).date().isoformat()
    row = repo.get_ai_session(scope_type=scope_type, chat_id=chat_id, topic_id=topic_id, user_id=user_id)
    if row is None:
        payload = {"session_id": f"{now.timestamp():.6f}", "created_at": now.isoformat(), "last_activity_at": now.isoformat(), "last_activity_day": current_day}
        repo.upsert_ai_session(scope_type=scope_type, chat_id=chat_id, topic_id=topic_id, user_id=user_id, session_payload=payload, last_message_at=now)
        return payload, "create"

    payload = dict(row.session_payload or {})
    last_activity = payload.get("last_activity_at")
    last_day = payload.get("last_activity_day")
    reason = "reuse"
    reset_reason = None

    last_dt = None
    if isinstance(last_activity, str):
        try:
            last_dt = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
        except ValueError:
            last_dt = None

    if last_dt is not None and last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)

    if last_dt is not None and (now - last_dt) > AI_SESSION_IDLE_TIMEOUT:
        reason = "reset"
        reset_reason = "idle_timeout"
    elif isinstance(last_day, str) and last_day != current_day:
        reason = "reset"
        reset_reason = "day_rollover"

    if reason == "reset":
        payload = {"session_id": f"{now.timestamp():.6f}", "created_at": now.isoformat(), "last_activity_at": now.isoformat(), "last_activity_day": current_day, "reset_reason": reset_reason}
    else:
        payload["last_activity_at"] = now.isoformat()
        payload["last_activity_day"] = current_day

    repo.upsert_ai_session(scope_type=scope_type, chat_id=chat_id, topic_id=topic_id, user_id=user_id, session_payload=payload, last_message_at=now)
    return payload, reason


def _reset_scoped_ai_session(*, repo: TopicAgentMemoryRepository, scope_type: str, chat_id: int | None, topic_id: int | None, user_id: int | None, now: datetime) -> None:
    payload = {"session_id": f"{now.timestamp():.6f}", "created_at": now.isoformat(), "last_activity_at": now.isoformat(), "last_activity_day": now.astimezone(timezone.utc).date().isoformat(), "reset_reason": "explicit_reset"}
    repo.upsert_ai_session(scope_type=scope_type, chat_id=chat_id, topic_id=topic_id, user_id=user_id, session_payload=payload, last_message_at=now)



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


TELEGRAM_TEXTS: dict[str, dict[Locale, str]] = {
    "ask.usage": {"de": "Nutzung: /ask <frage>", "en": "usage: /ask <question>"},
    "help.header": {"de": "Verfügbare Befehle:", "en": "available commands:"},
    "help.none": {"de": "Keine Befehle verfügbar.", "en": "no commands available"},
    "dispatcher.unknown_command": {
        "de": "Unbekannter Befehl: /{command_name}. Nutze /help für verfügbare Befehle.",
        "en": "Unknown command: /{command_name}. Use /help for available commands.",
    },
    "consent.status.accepted": {"de": "Du hast zugestimmt.", "en": "You agreed to consent."},
    "consent.status.declined": {"de": "Du hast abgelehnt.", "en": "You declined consent."},
    "consent.status.pending": {"de": "Consent ist noch ausstehend.", "en": "Consent is still pending."},
    "consent.status.unreachable": {"de": "Consent ist als nicht erreichbar markiert.", "en": "Consent marked as unreachable."},
    "consent.status.fallback": {"de": "Consent-Status gespeichert.", "en": "Consent status recorded."},
    "consent.unavailable": {"de": "Consent-Verwaltung ist nicht konfiguriert.", "en": "Consent management is not configured."},
    "consent.start.group": {"de": "Bitte öffne die Policy privat über den Button.", "en": "Please open the policy in a private chat via the button."},
    "consent.start.accepted": {"de": "Consent ist bereits akzeptiert. ✅", "en": "Consent has already been accepted. ✅"},
    "consent.start.declined": {"de": "Consent ist aktuell abgelehnt. Du kannst mit /accept wieder zustimmen.", "en": "Consent is currently declined. You can agree again with /accept."},
    "consent.user_missing": {"de": "Benutzerprofil fehlt noch. Sende /ping und versuche es erneut.", "en": "User profile not found yet. Send /ping and try again."},
    "consent.accept.ok": {"de": "Consent akzeptiert. Danke — mit /decline kannst du das jederzeit ändern.", "en": "Consent accepted. Thanks — you can use /decline anytime to change this."},
    "consent.decline.ok": {"de": "Consent abgelehnt. Mit /accept kannst du später wieder zustimmen.", "en": "Consent declined. You can re-enable later with /accept."},
    "consent.private_only": {"de": "Aus Datenschutzgründen nutze bitte /consent im privaten Chat mit mir.", "en": "For privacy, please use /consent in a private chat with me."},
    "consent.status.unknown": {
        "de": "Consent-Status: unbekannt\nSende zuerst einen beliebigen Befehl im privaten Chat und versuche dann /consent erneut.",
        "en": "Consent status: unknown\nSend any command in private first, then retry /consent.",
    },
    "consent.status.template": {
        "de": "Consent-Status: {status}\n{explanation}\nNutze /accept oder /decline, um deine Entscheidung zu ändern.",
        "en": "Consent status: {status}\n{explanation}\nUse /accept or /decline to change your choice.",
    },
    "dispatcher.consent.callback.unavailable": {"de": "Consent nicht verfügbar", "en": "Consent unavailable"},
    "dispatcher.consent.callback.profile_missing": {"de": "Profil nicht gefunden", "en": "Profile not found"},
    "dispatcher.consent.callback.accepted": {"de": "Consent akzeptiert", "en": "Consent accepted"},
    "dispatcher.consent.callback.declined": {"de": "Consent abgelehnt", "en": "Consent declined"},
    "dispatcher.consent.block.group": {"de": "Bitte kläre Consent privat mit dem Bot.", "en": "Please resolve consent privately with the bot."},
    "dispatcher.consent.block.unreachable": {"de": "Bitte starte den Bot privat und bestätige mit /accept.", "en": "Please start the bot in private and confirm with /accept."},
    "dispatcher.consent.block.default": {"de": "Bitte bestätige zuerst mit /accept oder prüfe /consent.", "en": "Please confirm with /accept first or check /consent."},
    "dispatcher.rate_limit.message": {
        "de": "Rate-Limit für deine Rolle ({role}) erreicht. Bitte warte bis morgen für weitere Webtool-Anfragen.",
        "en": "Rate limit for your role ({role}) reached. Please wait until tomorrow for more webtool requests.",
    },
    "role.current": {"de": "deine rolle: {role}", "en": "your role: {role}"},
    "setrole.permission_denied": {"de": "keine berechtigung", "en": "permission denied"},
    "setrole.usage": {"de": "nutzung: /setrole <telegram_user_id> <rolle>", "en": "usage: /setrole <telegram_user_id> <role>"},
    "setrole.invalid_user_id": {"de": "ungültige telegram_user_id", "en": "invalid telegram_user_id"},
    "setrole.invalid_role": {"de": "ungültige rolle. erlaubt: {allowed}", "en": "invalid role. allowed: {allowed}"},
    "setrole.admin_assign_restricted": {
        "de": "keine berechtigung. admin darf nur zuweisen: {allowed}",
        "en": "permission denied. admin may only assign: {allowed}"
    },
    "setrole.owner_assignment_disabled": {
        "de": "owner-zuweisung via telegram ist im MVP deaktiviert (nutze webui)",
        "en": "owner assignment via telegram is disabled in MVP (use webui)"
    },
    "setrole.not_configured": {"de": "rollenverwaltung ist nicht konfiguriert", "en": "role management not configured"},
    "setrole.updated": {"de": "rolle aktualisiert: {target_user_id} {prev} -> {new_role}", "en": "role updated: {target_user_id} {prev} -> {new_role}"},
    "setrole.no_change": {"de": "keine änderung: {target_user_id} bereits {new_role}", "en": "no change: {target_user_id} already {new_role}"},
    "webui.not_configured": {"de": "webui access control not configured", "en": "webui access control not configured"},
    "webui.usage": {"de": "usage: /webui <on|off|status>", "en": "usage: /webui <on|off|status>"},
    "webui.permission_denied": {"de": "permission denied", "en": "permission denied"},
    "webui.open_until": {"de": "webui access: OPEN (~60m, until {time_utc})", "en": "webui access: OPEN (~60m, until {time_utc})"},
    "webui.closed": {"de": "webui access: CLOSED", "en": "webui access: CLOSED"},
    "webui.open_remaining": {"de": "webui access: OPEN (remaining: {remaining_minutes}m)", "en": "webui access: OPEN (remaining: {remaining_minutes}m)"},
    "test.inline_button_prompt": {"de": "Inline-Button-Test: Bitte klicken.", "en": "Inline button test: please click."},
    "test.inline_button": {"de": "✅ Test Button", "en": "✅ Test button"},
    "memory_profile.private_only": {"de": "Bitte nutze diesen Befehl im privaten Chat.", "en": "Please use this command in a private chat."},
    "memory_profile.empty": {"de": "Kein Profil gespeichert.", "en": "No profile stored."},
    "memory_profile.current": {"de": "Dein Memory-Profil: {profile}", "en": "Your memory profile: {profile}"},
    "memory_profile.set.usage": {"de": "Nutzung: /memory_profile_set key=value[, key=value]", "en": "usage: /memory_profile_set key=value[, key=value]"},
    "memory_profile.set.updated": {"de": "Profil aktualisiert. Gespeicherte Felder: {accepted}", "en": "Profile updated. Stored fields: {accepted}"},
    "memory_profile.set.rejected": {"de": "Keine erlaubten Felder. Erlaubt: {allowed}", "en": "No allowed fields. Allowed: {allowed}"},
    "memory_profile.set.partial": {"de": "Profil teilweise aktualisiert. Gespeichert: {accepted}; ignoriert: {rejected}", "en": "Profile partially updated. Stored: {accepted}; ignored: {rejected}"},
    "memory_profile.delete.done": {"de": "Dein Memory-Profil wurde gelöscht.", "en": "Your memory profile was deleted."},
}


def t_text(key: str, locale: Locale = "de", **kwargs: object) -> str:
    entry = TELEGRAM_TEXTS.get(key)
    if entry is None:
        raise KeyError(f"unknown telegram text key: {key}")
    value = entry["en" if locale == "en" else "de"]
    return value.format(**kwargs) if kwargs else value


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
    if database_url:
        init_db(database_url)
    session_factory = create_session_factory(database_url) if database_url else None

    async def ping_handler(ctx: CommandContext) -> str:
        return _lang(ctx, "pong", "pong")

    def _lang(ctx: CommandContext, de: str, en: str) -> str:
        return de if ctx.locale == "de" else en

    def _consent_status_explanation(status: str, locale: Locale) -> str:
        key_map = {
            CONSENT_ACCEPTED: "consent.status.accepted",
            CONSENT_DECLINED: "consent.status.declined",
            CONSENT_PENDING: "consent.status.pending",
            CONSENT_UNREACHABLE: "consent.status.unreachable",
        }
        return t_text(key_map.get(status, "consent.status.fallback"), locale)

    async def start_handler(ctx: CommandContext) -> dict[str, object] | str:
        if session_factory is None:
            return t_text("consent.unavailable", ctx.locale)
        if ctx.chat_type != "private":
            return t_text("consent.start.group", ctx.locale)

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
            return t_text("consent.start.accepted", ctx.locale)

        if status == CONSENT_DECLINED:
            return t_text("consent.start.declined", ctx.locale)

        return {
            "text": ConsentPromptService.build_prompt_text(ctx.locale),
            "reply_markup": ConsentPromptService.build_prompt_markup(ctx.locale),
        }

    async def role_handler(ctx: CommandContext) -> str:
        return t_text("role.current", ctx.locale, role=ctx.role.value)

    async def webtoolquota_handler(ctx: CommandContext) -> str:
        """Display or set webtool rate limits per role.

        Owner/admin can set with: /webtoolquota <role> <mode> [daily_limit]
        Everyone can view with: /webtoolquota
        """
        if session_factory is None:
            return _lang(ctx, "Datenbank nicht konfiguriert.", "Database not configured.")

        parts = (ctx.argument or "").strip().split()
        # Parse: /webtoolquota [role [mode [limit]]]
        if len(parts) >= 1 and parts[0]:
            # Setting/quering a specific role
            role_raw = parts[0]
            try:
                target_role = Role(role_raw.casefold())
            except ValueError:
                allowed = ", ".join(r.value for r in Role)
                return t_text("setrole.invalid_role", ctx.locale, allowed=allowed)

            if len(parts) >= 2:
                # Setting mode + optional limit
                if ctx.role not in {Role.OWNER, Role.ADMIN}:
                    return t_text("setrole.permission_denied", ctx.locale)

                mode_raw = parts[1].casefold()
                if mode_raw not in {"disabled", "unlimited", "limited"}:
                    return _lang(ctx, "Modus muss: disabled | unlimited | limited", "Mode must be: disabled | unlimited | limited")

                daily_limit: int | None = None
                if mode_raw == "limited":
                    if len(parts) >= 3:
                        try:
                            daily_limit = int(parts[2])
                        except ValueError:
                            return _lang(ctx, "Limit muss eine positive Zahl sein.", "Limit must be a positive number.")
                        if daily_limit < 1:
                            return _lang(ctx, "Limit muss >= 1 sein.", "Limit must be >= 1.")
                    else:
                        return _lang(ctx, "Limited-Modus braucht ein daily_limit.", "Limited mode requires a daily_limit.")

                with session_factory() as session:
                    repo = WebToolRoleQuotaRepository(session)
                    record = repo.upsert_role_quota(
                        role=target_role,
                        mode=mode_raw,
                        daily_limit=daily_limit,
                        updated_by_telegram_user_id=ctx.user_id,
                    )
                    mode_label = record.mode
                    limit_label = str(record.daily_limit) if record.daily_limit else "-"
                    locale = ctx.locale
                    return f"{locale == 'de' and 'Rolle' or 'Role'} {target_role.value}: mode={mode_label} limit={limit_label}"
            else:
                # Just query this role's quota
                with session_factory() as session:
                    repo = WebToolRoleQuotaRepository(session)
                    record = repo.get_role_quota(target_role)
                    mode_label = record.mode
                    limit_label = str(record.daily_limit) if record.daily_limit else "-"
                    return f"{target_role.value}: mode={mode_label} limit={limit_label}"
        else:
            # No args: list all role quotas
            with session_factory() as session:
                repo = WebToolRoleQuotaRepository(session)
                all_quotas = repo.list_role_quotas()
            lines = [(_lang(ctx, "Webtool Rate-Limits:", "Webtool Rate Limits:"))]
            for q in all_quotas:
                limit_label = str(q.daily_limit) if q.daily_limit else "-"
                lines.append(f"  {q.role.value}: mode={q.mode} limit={limit_label}")
            return "\n".join(lines)

    def _ctxdoc_parse_kind_scope(ctx: CommandContext, *, require_text: bool = False) -> tuple[str, str, str | None] | str:
        parts = (ctx.argument or "").split(maxsplit=2)
        minimum = 3 if require_text else 2
        if len(parts) < minimum:
            cmd = ctx.command_name
            usage = f"usage: /{cmd} <kind> <scope>" + (" <text...>" if require_text else "")
            return usage
        kind, scope = parts[0].upper(), parts[1].casefold()
        if kind not in PromptContextDocRepository.ALLOWED_KINDS:
            return "invalid kind. allowed: AGENT, SOUL, PLUGINS, AUFGABE"
        if scope not in PromptContextDocRepository.ALLOWED_SCOPE_TYPES:
            return "invalid scope. allowed: global, topic"
        text = parts[2] if len(parts) >= 3 else None
        return kind, scope, text

    def _ctxdoc_scope_args(ctx: CommandContext, scope: str) -> tuple[int | None, int | None] | str:
        if scope == "global":
            return None, None
        if ctx.chat_type == "private" or ctx.message_thread_id is None:
            return "topic scope requires running the command inside a Telegram topic"
        return ctx.chat_id, ctx.message_thread_id

    def _ctxdoc_audit(
        session: object,
        *,
        ctx: CommandContext,
        action: str,
        kind: str | None,
        scope: str | None,
        content_length: int | None = None,
        success: bool,
        reason: str | None = None,
    ) -> None:
        # Metadata only: never include prompt context content.
        session.add(  # type: ignore[attr-defined]
            AuditEvent(
                actor_telegram_user_id=ctx.user_id,
                event_type=f"prompt_context_doc_{action}",
                payload_json=json.dumps(
                    {
                        "kind": kind,
                        "scope_type": scope,
                        "chat_id": ctx.chat_id,
                        "topic_id": ctx.message_thread_id,
                        "content_length": content_length,
                        "success": success,
                        "reason": reason,
                    },
                    separators=(",", ":"),
                ),
            )
        )

    async def ctxdoc_set_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return "database not configured"
        parsed = _ctxdoc_parse_kind_scope(ctx, require_text=False)
        if isinstance(parsed, str):
            return parsed
        kind, scope, tail_text = parsed
        scoped = _ctxdoc_scope_args(ctx, scope)
        if isinstance(scoped, str):
            return scoped
        chat_id, topic_id = scoped
        content = (ctx.reply_to_message_text or "").strip() if ctx.reply_to_message_text.strip() else (tail_text or "").strip()
        if not content:
            return "usage: /ctxdoc_set <kind> <scope> <text...> (or reply to a text/caption message)"
        if len(content) > PROMPT_CONTEXT_DOC_MAX_CHARS:
            with session_factory() as session:
                _ctxdoc_audit(session, ctx=ctx, action="set", kind=kind, scope=scope, content_length=len(content), success=False, reason="content_too_long")
                session.commit()
            return f"content too long: {len(content)} chars (max {PROMPT_CONTEXT_DOC_MAX_CHARS})"
        with session_factory() as session:
            repo = PromptContextDocRepository(session)
            repo.upsert_doc(kind=kind, scope_type=scope, chat_id=chat_id, topic_id=topic_id, content=content, enabled=True)
            _ctxdoc_audit(session, ctx=ctx, action="set", kind=kind, scope=scope, content_length=len(content), success=True)
            session.commit()
        logger.info("prompt_context_doc action=set kind=%s scope=%s actor=%s chat_id=%s topic_id=%s content_length=%s success=true", kind, scope, ctx.user_id, ctx.chat_id, ctx.message_thread_id, len(content))
        return f"ctxdoc saved: {kind} {scope} ({len(content)} chars)"

    async def ctxdoc_get_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return "database not configured"
        parsed = _ctxdoc_parse_kind_scope(ctx)
        if isinstance(parsed, str):
            return parsed
        kind, scope, _ = parsed
        scoped = _ctxdoc_scope_args(ctx, scope)
        if isinstance(scoped, str):
            return scoped
        chat_id, topic_id = scoped
        with session_factory() as session:
            row = PromptContextDocRepository(session).get_doc(kind=kind, scope_type=scope, chat_id=chat_id, topic_id=topic_id)
            _ctxdoc_audit(session, ctx=ctx, action="get", kind=kind, scope=scope, content_length=len(row.content) if row else 0, success=row is not None)
            session.commit()
        if row is None:
            return f"ctxdoc not found: {kind} {scope}"
        status = "enabled" if row.enabled else "disabled"
        header = f"{kind} {scope} ({status}, {len(row.content)} chars):\n"
        available = TELEGRAM_SAFE_MESSAGE_CHARS - len(header)
        if len(row.content) > available:
            return header + row.content[:available] + f"\n… truncated for Telegram; stored length {len(row.content)} chars"
        return header + row.content

    async def ctxdoc_del_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return "database not configured"
        parsed = _ctxdoc_parse_kind_scope(ctx)
        if isinstance(parsed, str):
            return parsed
        kind, scope, _ = parsed
        scoped = _ctxdoc_scope_args(ctx, scope)
        if isinstance(scoped, str):
            return scoped
        chat_id, topic_id = scoped
        with session_factory() as session:
            deleted = PromptContextDocRepository(session).delete_doc(kind=kind, scope_type=scope, chat_id=chat_id, topic_id=topic_id)
            _ctxdoc_audit(session, ctx=ctx, action="del", kind=kind, scope=scope, content_length=0, success=deleted)
            session.commit()
        logger.info("prompt_context_doc action=del kind=%s scope=%s actor=%s chat_id=%s topic_id=%s success=%s", kind, scope, ctx.user_id, ctx.chat_id, ctx.message_thread_id, deleted)
        return f"ctxdoc deleted: {kind} {scope}" if deleted else f"ctxdoc not found: {kind} {scope}"

    async def ctxdoc_list_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return "database not configured"
        parts = (ctx.argument or "").split()
        scope = parts[0].casefold() if len(parts) >= 1 else None
        kind = parts[1].upper() if len(parts) >= 2 else None
        if scope is not None and scope not in PromptContextDocRepository.ALLOWED_SCOPE_TYPES:
            # Also allow /ctxdoc_list AGENT shorthand.
            if scope.upper() in PromptContextDocRepository.ALLOWED_KINDS and kind is None:
                kind = scope.upper()
                scope = None
            else:
                return "invalid scope. allowed: global, topic"
        if kind is not None and kind not in PromptContextDocRepository.ALLOWED_KINDS:
            return "invalid kind. allowed: AGENT, SOUL, PLUGINS, AUFGABE"
        chat_id: int | None = None
        topic_id: int | None = None
        if scope == "topic":
            scoped = _ctxdoc_scope_args(ctx, scope)
            if isinstance(scoped, str):
                return scoped
            chat_id, topic_id = scoped
        with session_factory() as session:
            repo = PromptContextDocRepository(session)
            if scope is None:
                rows = repo.list_docs(scope_type="global", kind=kind)
                if ctx.chat_type != "private" and ctx.message_thread_id is not None:
                    rows.extend(repo.list_docs(scope_type="topic", kind=kind, chat_id=ctx.chat_id, topic_id=ctx.message_thread_id))
            else:
                rows = repo.list_docs(scope_type=scope, kind=kind, chat_id=chat_id, topic_id=topic_id)
            _ctxdoc_audit(session, ctx=ctx, action="list", kind=kind, scope=scope, content_length=None, success=True)
            session.commit()
        if not rows:
            return "no ctxdocs found"
        lines = ["ctxdocs:"]
        for row in rows:
            status = "enabled" if row.enabled else "disabled"
            scope_label = row.scope_type if row.scope_type == "global" else f"topic:{row.chat_id}:{row.topic_id}"
            updated = row.updated_at.isoformat() if row.updated_at is not None else "-"
            lines.append(f"- {row.kind} {scope_label} {status} chars={len(row.content)} updated_at={updated}")
        return "\n".join(lines)

    async def setrole_handler(ctx: CommandContext) -> str:
        if ctx.role not in {Role.OWNER, Role.ADMIN}:
            return t_text("setrole.permission_denied", ctx.locale)

        if not ctx.argument:
            return t_text("setrole.usage", ctx.locale)

        parts = ctx.argument.split()
        if len(parts) != 2:
            return t_text("setrole.usage", ctx.locale)

        user_raw, role_raw = parts
        try:
            target_user_id = int(user_raw)
        except ValueError:
            return t_text("setrole.invalid_user_id", ctx.locale)

        try:
            target_role = Role(role_raw.casefold())
        except ValueError:
            allowed = ", ".join(r.value for r in Role)
            return t_text("setrole.invalid_role", ctx.locale, allowed=allowed)

        if not can_assign_role(ctx.role, target_role):
            if ctx.role == Role.ADMIN:
                allowed = ", ".join(sorted(r.value for r in ADMIN_ASSIGNABLE_ROLES))
                return t_text("setrole.admin_assign_restricted", ctx.locale, allowed=allowed)
            return t_text("setrole.permission_denied", ctx.locale)

        if ctx.role == Role.OWNER and target_role == Role.OWNER:
            return t_text("setrole.owner_assignment_disabled", ctx.locale)

        if session_factory is None:
            return t_text("setrole.not_configured", ctx.locale)

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
            return t_text("setrole.updated", ctx.locale, target_user_id=target_user_id, prev=prev, new_role=result.new_role.value)
        return t_text("setrole.no_change", ctx.locale, target_user_id=target_user_id, new_role=result.new_role.value)

    async def accept_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return t_text("consent.unavailable", ctx.locale)
        with session_factory() as session:
            user = session.query(User).filter(User.telegram_user_id == ctx.user_id).one_or_none()
            if user is None:
                return t_text("consent.user_missing", ctx.locale)
            ConsentService().accept(user)
            session.commit()
            if owner_notifier is not None:
                await owner_notifier.notify_consent_decision(user=user, accepted=True, source="command:/accept")
        return t_text("consent.accept.ok", ctx.locale)

    async def decline_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return t_text("consent.unavailable", ctx.locale)
        with session_factory() as session:
            user = session.query(User).filter(User.telegram_user_id == ctx.user_id).one_or_none()
            if user is None:
                return t_text("consent.user_missing", ctx.locale)
            ConsentService().decline(user)
            session.commit()
            if owner_notifier is not None:
                await owner_notifier.notify_consent_decision(user=user, accepted=False, source="command:/decline")
        return t_text("consent.decline.ok", ctx.locale)

    async def consent_handler(ctx: CommandContext) -> str:
        if ctx.chat_type != "private":
            return t_text("consent.private_only", ctx.locale)
        if session_factory is None:
            return t_text("consent.unavailable", ctx.locale)
        with session_factory() as session:
            user = session.query(User).filter(User.telegram_user_id == ctx.user_id).one_or_none()
            if user is None:
                return t_text("consent.status.unknown", ctx.locale)
            status = ConsentService().get_status(user)

        return t_text(
            "consent.status.template",
            ctx.locale,
            status=status,
            explanation=_consent_status_explanation(status, ctx.locale),
        )

    async def ask_handler(ctx: CommandContext) -> str:
        if not ctx.argument or not ctx.argument.strip():
            return t_text("ask.usage", ctx.locale)
        if ai_service is None:
            return _lang(ctx, "AI-Service ist nicht konfiguriert.", "AI service is not configured")

        now = datetime.now(timezone.utc)
        scope_type, scope_chat_id, scope_topic_id, scope_user_id = _ai_scope_from_ctx(ctx)
        ai_prompt = ctx.argument
        if session_factory is not None:
            with session_factory() as session:
                repo = TopicAgentMemoryRepository(session)
                _payload, lifecycle = _load_or_create_scoped_ai_session(
                    repo=repo,
                    scope_type=scope_type,
                    chat_id=scope_chat_id,
                    topic_id=scope_topic_id,
                    user_id=scope_user_id,
                    now=now,
                )
                profile_scope = _profile_scope_from_ctx(ctx)
                profile = UserMemoryProfileRepository(session).get_profile(
                    scope_type=profile_scope.scope_type,
                    chat_id=profile_scope.chat_id,
                    topic_id=profile_scope.topic_id,
                    user_id=ctx.user_id,
                ).profile
                if profile:
                    ai_prompt = (
                        "Known coarse user profile context for the current user in this scope:\n"
                        f"{_profile_to_text(profile)}\n\n"
                        f"User message:\n{ctx.argument}"
                    )
            logger.info(
                "ai_session_lifecycle event=resolve resolver_path=ask scope_type=%s scope_key=%s action=%s",
                scope_type,
                _ai_scope_key(scope_type, scope_chat_id, scope_topic_id, scope_user_id),
                lifecycle,
            )

        try:
            return await ai_service.ask(ai_prompt)
        except OllamaError:
            logger.exception("/ask failed: ollama runtime error user_id=%s chat_id=%s", ctx.user_id, ctx.chat_id)
            return _lang(ctx, "Sorry, ich kann gerade nicht antworten. Bitte versuche es später erneut.", "Sorry, I cannot answer right now. Please try again later.")
        except ValueError:
            logger.exception("/ask failed: invalid prompt user_id=%s chat_id=%s", ctx.user_id, ctx.chat_id)
            return _lang(ctx, "Sorry, ich kann gerade nicht antworten. Bitte versuche es später erneut.", "Sorry, I cannot answer right now. Please try again later.")

    async def webui_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return t_text("webui.not_configured", ctx.locale)

        subcommand = (ctx.argument or "status").strip().casefold()
        if subcommand not in {"on", "off", "status"}:
            return t_text("webui.usage", ctx.locale)

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
            return t_text("webui.permission_denied", ctx.locale)

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
            return t_text("webui.permission_denied", ctx.locale)

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
            return t_text("webui.open_until", ctx.locale, time_utc=enabled_until.strftime('%H:%M UTC'))

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
            return t_text("webui.closed", ctx.locale)

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
            return t_text("webui.open_remaining", ctx.locale, remaining_minutes=remaining_minutes)
        return t_text("webui.closed", ctx.locale)

    async def test_handler(ctx: CommandContext) -> dict[str, object]:
        payload: dict[str, object] = {
            "text": t_text("test.inline_button_prompt", ctx.locale),
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": t_text("test.inline_button", ctx.locale),
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


    def _profile_to_text(profile: dict[str, object]) -> str:
        return json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _parse_profile_candidate(argument: str | None) -> dict[str, object] | None:
        if argument is None:
            return None
        raw = argument.strip()
        if not raw:
            return None
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None

        candidate: dict[str, object] = {}
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        for part in parts:
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            k = key.strip()
            v = value.strip()
            if not k:
                continue
            if v.startswith("["):
                try:
                    parsed_list = json.loads(v)
                except json.JSONDecodeError:
                    parsed_list = [item.strip() for item in v.split("|") if item.strip()]
                candidate[k] = parsed_list
            else:
                candidate[k] = v
        return candidate if candidate else None

    async def memory_profile_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return t_text("memory_profile.empty", ctx.locale)
        scope = _profile_scope_from_ctx(ctx)
        with session_factory() as session:
            profile = UserMemoryProfileRepository(session).get_profile(
                scope_type=scope.scope_type,
                chat_id=scope.chat_id,
                topic_id=scope.topic_id,
                user_id=ctx.user_id,
            ).profile
        if not profile:
            return t_text("memory_profile.empty", ctx.locale)
        return t_text("memory_profile.current", ctx.locale, profile=_profile_to_text(profile))

    async def memory_profile_set_handler(ctx: CommandContext) -> str:
        candidate = _parse_profile_candidate(ctx.argument)
        if candidate is None:
            return t_text("memory_profile.set.usage", ctx.locale)
        if session_factory is None:
            return t_text("memory_profile.set.rejected", ctx.locale, allowed=", ".join(UserMemoryProfileRepository.ALLOWED_PROFILE_FIELDS))
        scope = _profile_scope_from_ctx(ctx)
        with session_factory() as session:
            profile_repo = UserMemoryProfileRepository(session)
            service = MemoryC2Service(repository=TopicAgentMemoryRepository(session), profile_repository=profile_repo)
            result = service.apply_profile_candidate(scope=scope, candidate=candidate)
            session.add(
                AuditEvent(
                    actor_telegram_user_id=ctx.user_id,
                    event_type="user_profile_command_update",
                    payload_json=json.dumps(
                        {
                            "scope_type": scope.scope_type,
                            "chat_id": scope.chat_id,
                            "topic_id": scope.topic_id,
                            "user_id": ctx.user_id,
                            "accepted_keys": list(result.accepted_keys),
                            "rejected_keys": list(result.rejected_keys),
                        }
                    ),
                )
            )
            session.commit()

        if not result.accepted_keys:
            return t_text("memory_profile.set.rejected", ctx.locale, allowed=", ".join(UserMemoryProfileRepository.ALLOWED_PROFILE_FIELDS))
        accepted = ", ".join(result.accepted_keys)
        if result.rejected_keys:
            rejected = ", ".join(result.rejected_keys)
            return t_text("memory_profile.set.partial", ctx.locale, accepted=accepted, rejected=rejected)
        return t_text("memory_profile.set.updated", ctx.locale, accepted=accepted)

    async def memory_profile_delete_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return t_text("memory_profile.delete.done", ctx.locale)
        scope = _profile_scope_from_ctx(ctx)
        with session_factory() as session:
            UserMemoryProfileRepository(session).replace_profile(
                scope_type=scope.scope_type,
                chat_id=scope.chat_id,
                topic_id=scope.topic_id,
                user_id=ctx.user_id,
                profile={},
            )
            session.add(
                AuditEvent(
                    actor_telegram_user_id=ctx.user_id,
                    event_type="user_profile_command_delete",
                    payload_json=json.dumps(
                        {
                            "scope_type": scope.scope_type,
                            "chat_id": scope.chat_id,
                            "topic_id": scope.topic_id,
                            "user_id": ctx.user_id,
                        }
                    ),
                )
            )
            session.commit()
        return t_text("memory_profile.delete.done", ctx.locale)

    async def help_handler(ctx: CommandContext) -> str:
        allowed = registry.list_allowed(ctx.role)
        if not allowed:
            return t_text("help.none", ctx.locale)
        lines = [t_text("help.header", ctx.locale)]
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
    registry.register(Command(name="webtoolquota", description="View or set webtool rate limits per role", description_de="Webtool Rate-Limits pro Rolle anzeigen oder setzen", description_en="View or set webtool rate limits per role", allowed_roles=admin_plus, handler=webtoolquota_handler))
    registry.register(Command(name="start", description="Start consent flow in private chat", description_de="Consent-Flow im privaten Chat starten", description_en="Start consent flow in private chat", allowed_roles=normal_plus, handler=start_handler))
    registry.register(Command(name="accept", description="Accept consent", description_de="Consent akzeptieren", description_en="Accept consent", allowed_roles=normal_plus, handler=accept_handler))
    registry.register(Command(name="decline", description="Decline consent", description_de="Consent ablehnen", description_en="Decline consent", allowed_roles=normal_plus, handler=decline_handler))
    registry.register(Command(name="consent", description="Show consent status", description_de="Consent-Status anzeigen", description_en="Show consent status", allowed_roles=normal_plus, handler=consent_handler))
    registry.register(Command(name="memory_profile", description="Show your coarse memory profile", description_de="Eigenes grobes Memory-Profil anzeigen", description_en="Show your coarse memory profile", allowed_roles=normal_plus, handler=memory_profile_handler))
    registry.register(Command(name="memory_profile_set", description="Update profile: /memory_profile_set key=value[, key=value]", description_de="Profil aktualisieren: /memory_profile_set key=value[, key=value]", description_en="Update profile: /memory_profile_set key=value[, key=value]", allowed_roles=normal_plus, handler=memory_profile_set_handler))
    registry.register(Command(name="memory_profile_delete", description="Delete your memory profile", description_de="Eigenes Memory-Profil löschen", description_en="Delete your memory profile", allowed_roles=normal_plus, handler=memory_profile_delete_handler))
    registry.register(Command(name="ctxdoc_set", description="Set prompt context doc: /ctxdoc_set <kind> <global|topic> <text>", description_de="Prompt-Kontextdoc setzen: /ctxdoc_set <kind> <global|topic> <text>", description_en="Set prompt context doc: /ctxdoc_set <kind> <global|topic> <text>", allowed_roles=admin_plus, handler=ctxdoc_set_handler))
    registry.register(Command(name="ctxdoc_get", description="Read prompt context doc: /ctxdoc_get <kind> <global|topic>", description_de="Prompt-Kontextdoc lesen: /ctxdoc_get <kind> <global|topic>", description_en="Read prompt context doc: /ctxdoc_get <kind> <global|topic>", allowed_roles=admin_plus, handler=ctxdoc_get_handler))
    registry.register(Command(name="ctxdoc_del", description="Delete prompt context doc: /ctxdoc_del <kind> <global|topic>", description_de="Prompt-Kontextdoc löschen: /ctxdoc_del <kind> <global|topic>", description_en="Delete prompt context doc: /ctxdoc_del <kind> <global|topic>", allowed_roles=admin_plus, handler=ctxdoc_del_handler))
    registry.register(Command(name="ctxdoc_list", description="List prompt context docs: /ctxdoc_list [scope] [kind]", description_de="Prompt-Kontextdocs listen: /ctxdoc_list [scope] [kind]", description_en="List prompt context docs: /ctxdoc_list [scope] [kind]", allowed_roles=admin_plus, handler=ctxdoc_list_handler))
    async def new_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return _lang(ctx, "Session-Speicher ist nicht konfiguriert.", "Session storage is not configured.")
        now = datetime.now(timezone.utc)
        scope_type, scope_chat_id, scope_topic_id, scope_user_id = _ai_scope_from_ctx(ctx)
        with session_factory() as session:
            repo = TopicAgentMemoryRepository(session)
            _reset_scoped_ai_session(
                repo=repo,
                scope_type=scope_type,
                chat_id=scope_chat_id,
                topic_id=scope_topic_id,
                user_id=scope_user_id,
                now=now,
            )
        logger.info("ai_session_lifecycle event=reset resolver_path=command scope_type=%s scope_key=%s action=explicit_new", scope_type, _ai_scope_key(scope_type, scope_chat_id, scope_topic_id, scope_user_id))
        return _lang(ctx, "Neue KI-Session gestartet.", "Started a new AI session.")

    async def reset_handler(ctx: CommandContext) -> str:
        if session_factory is None:
            return _lang(ctx, "Session-Speicher ist nicht konfiguriert.", "Session storage is not configured.")
        now = datetime.now(timezone.utc)
        scope_type, scope_chat_id, scope_topic_id, scope_user_id = _ai_scope_from_ctx(ctx)
        with session_factory() as session:
            repo = TopicAgentMemoryRepository(session)
            _reset_scoped_ai_session(
                repo=repo,
                scope_type=scope_type,
                chat_id=scope_chat_id,
                topic_id=scope_topic_id,
                user_id=scope_user_id,
                now=now,
            )
        logger.info("ai_session_lifecycle event=reset resolver_path=command scope_type=%s scope_key=%s action=explicit_reset", scope_type, _ai_scope_key(scope_type, scope_chat_id, scope_topic_id, scope_user_id))
        return _lang(ctx, "KI-Session zurückgesetzt.", "AI session reset.")

    registry.register(Command(name="ask", description="Ask Ollama: /ask <question>", description_de="Ollama fragen: /ask <frage>", description_en="Ask Ollama: /ask <question>", allowed_roles=ask_roles, handler=ask_handler))
    registry.register(Command(name="new", description="Start a new AI session", allowed_roles=ask_roles, handler=new_handler))
    registry.register(Command(name="reset", description="Reset current AI session", allowed_roles=ask_roles, handler=reset_handler))
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
