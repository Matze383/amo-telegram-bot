from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from amo_bot.auth.roles import Role
from amo_bot.consent import ConsentService
from amo_bot.db.models import (
    AuditEvent,
    BotPeer,
    ChatSeenUser,
    ChatUserRole,
    DbRole,
    ImageAnalyzeRoleQuota,
    Plugin,
    PluginActivationRequest,
    PluginPolicyAllowedGroup,
    PluginPolicyAllowedTopic,
    PluginPolicyOverride,
    PrivateChatPolicy,
    TelegramChat,
    TelegramTopic,
    TopicAgentConfig,
    TopicAiSession,
    TopicDailyMemory,
    UserMemoryProfile,
    TopicLongMemory,
    TopicRecentMessage,
    User,
    WebToolAuditEvent,
    WebToolQuotaCounter,
    WebToolRoleQuota,
)

if TYPE_CHECKING:
    from amo_bot.plugins.manifest import PluginManifest


@dataclass(slots=True)
class RoleChangeResult:
    changed: bool
    previous_role: Role | None
    new_role: Role


@dataclass(slots=True)
class PluginStatus:
    name: str
    enabled: bool
    activation_status: str = "activation_pending"
    worker_state: str | None = None
    worker_last_heartbeat_at: datetime | None = None
    worker_restart_count: int = 0
    worker_next_restart_at: datetime | None = None
    worker_last_error: str | None = None
    last_run_at: datetime | None = None
    last_status: str | None = None
    next_run_at: datetime | None = None


@dataclass(slots=True)
class PluginActivationRequestStatus:
    id: int
    plugin_name: str
    status: str
    requested_by_telegram_user_id: int | None = None
    resolved_by_telegram_user_id: int | None = None
    reason: str | None = None
    requested_at: datetime | None = None
    resolved_at: datetime | None = None


@dataclass(slots=True)
class PluginPolicyOverrideSnapshot:
    plugin_name: str
    roles_mode: str
    required_roles: list[Role]
    private_mode: str
    groups_mode: str
    topics_mode: str
    allowed_group_ids: list[int]
    allowed_topics: list[tuple[int, int]]


PRIVATE_CHAT_THRESHOLD_ROLES: tuple[Role, ...] = (Role.OWNER, Role.ADMIN, Role.VIP, Role.NORMAL)
BOT_PEER_ALLOWED_STATUSES: tuple[str, ...] = ("pending", "allowed", "blocked")


@dataclass(slots=True)
class BotPeerSeenResult:
    peer: BotPeer
    created: bool


@dataclass(slots=True)
class PrivateChatPolicySnapshot:
    min_ai_role: Role
    min_general_command_role: Role
    min_plugin_command_role: Role


class BotPeerRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_telegram_id(self, telegram_bot_id: int) -> BotPeer | None:
        return self._session.scalar(select(BotPeer).where(BotPeer.telegram_bot_id == telegram_bot_id))

    def mark_seen(
        self,
        *,
        telegram_bot_id: int,
        username: str | None,
        first_name: str | None,
        chat_id: int | None,
        chat_type: str | None,
        chat_title: str | None,
        message_thread_id: int | None,
        seen_at: datetime | None = None,
    ) -> BotPeerSeenResult:
        seen = seen_at or datetime.now(timezone.utc)
        row = self.get_by_telegram_id(telegram_bot_id)
        created = row is None
        if row is None:
            row = BotPeer(
                telegram_bot_id=telegram_bot_id,
                username=username,
                first_name=first_name,
                status="pending",
                first_seen_at=seen,
                last_seen_at=seen,
                last_seen_chat_id=chat_id,
                last_seen_chat_type=chat_type,
                last_seen_chat_title=chat_title,
                last_seen_message_thread_id=message_thread_id,
            )
            self._session.add(row)
            self._session.add(
                AuditEvent(
                    actor_telegram_user_id=telegram_bot_id,
                    event_type="bot_peer_detected",
                    payload_json=json.dumps(
                        {
                            "telegram_bot_id": telegram_bot_id,
                            "username": username,
                            "first_name": first_name,
                            "chat_id": chat_id,
                            "chat_type": chat_type,
                            "message_thread_id": message_thread_id,
                        }
                    ),
                )
            )
        else:
            row.username = username
            row.first_name = first_name
            row.last_seen_at = seen
            row.last_seen_chat_id = chat_id
            row.last_seen_chat_type = chat_type
            row.last_seen_chat_title = chat_title
            row.last_seen_message_thread_id = message_thread_id

        self._session.commit()
        self._session.refresh(row)
        return BotPeerSeenResult(peer=row, created=created)

    def set_status(
        self,
        *,
        telegram_bot_id: int,
        status: str,
        owner_telegram_user_id: int,
        decided_at: datetime | None = None,
    ) -> BotPeer | None:
        normalized_status = (status or "").strip().lower()
        if normalized_status not in BOT_PEER_ALLOWED_STATUSES:
            raise ValueError("invalid bot peer status")

        row = self.get_by_telegram_id(telegram_bot_id)
        if row is None:
            return None

        previous_status = row.status
        row.status = normalized_status
        row.owner_decided_by_telegram_user_id = owner_telegram_user_id
        row.owner_decided_at = decided_at or datetime.now(timezone.utc)
        self._session.add(
            AuditEvent(
                actor_telegram_user_id=owner_telegram_user_id,
                event_type="bot_peer_status_set",
                payload_json=json.dumps(
                    {
                        "telegram_bot_id": telegram_bot_id,
                        "previous_status": previous_status,
                        "new_status": normalized_status,
                    }
                ),
            )
        )
        self._session.commit()
        self._session.refresh(row)
        return row


class PrivateChatPolicyRepository:
    DEFAULT_MIN_AI_ROLE = Role.VIP
    DEFAULT_MIN_GENERAL_COMMAND_ROLE = Role.NORMAL
    DEFAULT_MIN_PLUGIN_COMMAND_ROLE = Role.NORMAL

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_policy(self) -> PrivateChatPolicySnapshot:
        row = self._session.scalar(select(PrivateChatPolicy).where(PrivateChatPolicy.id == 1))
        if row is None:
            return PrivateChatPolicySnapshot(
                min_ai_role=self.DEFAULT_MIN_AI_ROLE,
                min_general_command_role=self.DEFAULT_MIN_GENERAL_COMMAND_ROLE,
                min_plugin_command_role=self.DEFAULT_MIN_PLUGIN_COMMAND_ROLE,
            )

        return PrivateChatPolicySnapshot(
            min_ai_role=self._normalize_threshold_role(row.min_ai_role, default=self.DEFAULT_MIN_AI_ROLE),
            min_general_command_role=self._normalize_threshold_role(
                row.min_general_command_role,
                default=self.DEFAULT_MIN_GENERAL_COMMAND_ROLE,
            ),
            min_plugin_command_role=self._normalize_threshold_role(
                row.min_plugin_command_role,
                default=self.DEFAULT_MIN_PLUGIN_COMMAND_ROLE,
            ),
        )

    def update_policy(
        self,
        *,
        min_ai_role: str | Role,
        min_general_command_role: str | Role,
        min_plugin_command_role: str | Role,
    ) -> PrivateChatPolicySnapshot:
        normalized_ai = self.validate_threshold_role(min_ai_role)
        normalized_general = self.validate_threshold_role(min_general_command_role)
        normalized_plugin = self.validate_threshold_role(min_plugin_command_role)

        row = self._session.scalar(select(PrivateChatPolicy).where(PrivateChatPolicy.id == 1))
        if row is None:
            row = PrivateChatPolicy(id=1)
            self._session.add(row)

        row.min_ai_role = normalized_ai.value
        row.min_general_command_role = normalized_general.value
        row.min_plugin_command_role = normalized_plugin.value
        self._session.commit()
        self._session.refresh(row)
        return self.get_policy()

    @classmethod
    def validate_threshold_role(cls, role: str | Role) -> Role:
        try:
            normalized = role if isinstance(role, Role) else Role(str(role).strip().lower())
        except ValueError as exc:
            raise ValueError("invalid private chat threshold role") from exc
        if normalized not in PRIVATE_CHAT_THRESHOLD_ROLES:
            raise ValueError("invalid private chat threshold role")
        return normalized

    @classmethod
    def _normalize_threshold_role(cls, value: str | None, *, default: Role) -> Role:
        if value is None:
            return default
        try:
            return cls.validate_threshold_role(value)
        except ValueError:
            return default


@dataclass(slots=True)
class ImageAnalyzeRoleQuotaRecord:
    role: Role
    mode: str
    daily_limit: int | None
    updated_by_telegram_user_id: int | None


class ImageAnalyzeRoleQuotaRepository:
    ALLOWED_MODES = {"disabled", "unlimited", "limited"}
    DEFAULTS: dict[Role, tuple[str, int | None]] = {
        Role.OWNER: ("unlimited", None),
        Role.ADMIN: ("disabled", None),
        Role.VIP: ("disabled", None),
        Role.NORMAL: ("disabled", None),
        Role.IGNORE: ("disabled", None),
    }

    def __init__(self, session: Session) -> None:
        self._session = session

    @classmethod
    def _validate_role(cls, role: str | Role) -> Role:
        try:
            normalized = role if isinstance(role, Role) else Role(str(role).strip().lower())
        except ValueError as exc:
            raise ValueError("invalid role") from exc
        return normalized

    @classmethod
    def _validate_mode_and_limit(cls, *, role: Role, mode: str, daily_limit: int | None) -> tuple[str, int | None]:
        normalized_mode = (mode or "").strip().lower()
        if normalized_mode not in cls.ALLOWED_MODES:
            raise ValueError("invalid mode")

        if role is Role.IGNORE and normalized_mode == "unlimited":
            raise ValueError("ignore role cannot be unlimited")

        if normalized_mode == "limited":
            if not isinstance(daily_limit, int) or daily_limit < 1:
                raise ValueError("limited mode requires daily_limit >= 1")
            return normalized_mode, int(daily_limit)

        return normalized_mode, None

    @classmethod
    def _to_record(cls, row: ImageAnalyzeRoleQuota) -> ImageAnalyzeRoleQuotaRecord:
        return ImageAnalyzeRoleQuotaRecord(
            role=Role(row.role),
            mode=row.mode,
            daily_limit=row.daily_limit,
            updated_by_telegram_user_id=row.updated_by_telegram_user_id,
        )

    def get_role_quota(self, role: str | Role) -> ImageAnalyzeRoleQuotaRecord:
        normalized_role = self._validate_role(role)
        row = self._session.scalar(select(ImageAnalyzeRoleQuota).where(ImageAnalyzeRoleQuota.role == normalized_role.value))
        if row is None:
            default_mode, default_limit = self.DEFAULTS[normalized_role]
            return ImageAnalyzeRoleQuotaRecord(
                role=normalized_role,
                mode=default_mode,
                daily_limit=default_limit,
                updated_by_telegram_user_id=None,
            )
        return self._to_record(row)

    def list_role_quotas(self) -> list[ImageAnalyzeRoleQuotaRecord]:
        rows = self._session.scalars(select(ImageAnalyzeRoleQuota).order_by(ImageAnalyzeRoleQuota.role.asc())).all()
        by_role = {Role(row.role): self._to_record(row) for row in rows}
        result: list[ImageAnalyzeRoleQuotaRecord] = []
        for role in Role:
            if role in by_role:
                result.append(by_role[role])
            else:
                default_mode, default_limit = self.DEFAULTS[role]
                result.append(
                    ImageAnalyzeRoleQuotaRecord(
                        role=role,
                        mode=default_mode,
                        daily_limit=default_limit,
                        updated_by_telegram_user_id=None,
                    )
                )
        return result

    def upsert_role_quota(
        self,
        *,
        role: str | Role,
        mode: str,
        daily_limit: int | None = None,
        updated_by_telegram_user_id: int | None = None,
    ) -> ImageAnalyzeRoleQuotaRecord:
        normalized_role = self._validate_role(role)
        normalized_mode, normalized_limit = self._validate_mode_and_limit(
            role=normalized_role,
            mode=mode,
            daily_limit=daily_limit,
        )

        row = self._session.scalar(select(ImageAnalyzeRoleQuota).where(ImageAnalyzeRoleQuota.role == normalized_role.value))
        if row is None:
            row = ImageAnalyzeRoleQuota(
                role=normalized_role.value,
                mode=normalized_mode,
                daily_limit=normalized_limit,
                updated_by_telegram_user_id=updated_by_telegram_user_id,
            )
            self._session.add(row)
        else:
            row.mode = normalized_mode
            row.daily_limit = normalized_limit
            row.updated_by_telegram_user_id = updated_by_telegram_user_id

        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row)


@dataclass(slots=True)
class WebToolRoleQuotaRecord:
    role: Role
    mode: str
    daily_limit: int | None
    updated_by_telegram_user_id: int | None


@dataclass(slots=True)
class WebToolQuotaDecision:
    """Result of a webtool quota check.

    Attributes:
        allowed: whether the operation is permitted.
        decision: one of allow, deny, disabled, quota_exceeded, not_configured.
        role: the role that was evaluated.
        operation_type: the type of webtool operation (e.g. websearch, webscraping, browser).
        current_count: current daily counter value (0 if not yet counted or disabled).
        limit: daily limit from role config (0 if unlimited or not configured).
        remaining: remaining requests today (None if unlimited or disabled).
        reason: human-readable short reason code.
        error: error message if something went wrong during the check.
        timing_ms: milliseconds elapsed in the check (None if not timed).
    """

    allowed: bool
    decision: str
    role: Role
    operation_type: str
    current_count: int
    limit: int
    remaining: int | None
    reason: str
    error: str | None = None
    timing_ms: int | None = None


class WebToolRoleQuotaRepository:
    """Repository for webtool role quotas.

    Mirrors the ImageAnalyzeRoleQuotaRepository pattern. Owner/admin/vip/normal are
    unlimited by default; ignore role is disabled by default. Quota is enforced
    before webtool/subagent execution.

    Audit is metadata-only: role, user_id, chat_id, operation_type, decision,
    count/limit/remaining, reason/error/timing. No query content, URLs, prompts,
    or secrets.
    """

    ALLOWED_MODES = {"disabled", "unlimited", "limited"}
    DEFAULTS: dict[Role, tuple[str, int | None]] = {
        Role.OWNER: ("unlimited", None),
        Role.ADMIN: ("unlimited", None),
        Role.VIP: ("unlimited", None),
        Role.NORMAL: ("unlimited", None),
        Role.IGNORE: ("disabled", None),
    }

    def __init__(self, session: Session) -> None:
        self._session = session

    @classmethod
    def _validate_role(cls, role: str | Role) -> Role:
        try:
            normalized = role if isinstance(role, Role) else Role(str(role).strip().lower())
        except ValueError as exc:
            raise ValueError("invalid role") from exc
        return normalized

    @classmethod
    def _validate_mode_and_limit(cls, *, role: Role, mode: str, daily_limit: int | None) -> tuple[str, int | None]:
        normalized_mode = (mode or "").strip().lower()
        if normalized_mode not in cls.ALLOWED_MODES:
            raise ValueError("invalid mode")

        if role is Role.IGNORE and normalized_mode == "unlimited":
            raise ValueError("ignore role cannot be unlimited")

        if normalized_mode == "limited":
            if not isinstance(daily_limit, int) or daily_limit < 1:
                raise ValueError("limited mode requires daily_limit >= 1")
            return normalized_mode, int(daily_limit)

        return normalized_mode, None

    @classmethod
    def _to_record(cls, row: WebToolRoleQuota) -> WebToolRoleQuotaRecord:
        return WebToolRoleQuotaRecord(
            role=Role(row.role),
            mode=row.mode,
            daily_limit=row.daily_limit,
            updated_by_telegram_user_id=row.updated_by_telegram_user_id,
        )

    def get_role_quota(self, role: str | Role) -> WebToolRoleQuotaRecord:
        normalized_role = self._validate_role(role)
        row = self._session.scalar(select(WebToolRoleQuota).where(WebToolRoleQuota.role == normalized_role.value))
        if row is None:
            default_mode, default_limit = self.DEFAULTS[normalized_role]
            return WebToolRoleQuotaRecord(
                role=normalized_role,
                mode=default_mode,
                daily_limit=default_limit,
                updated_by_telegram_user_id=None,
            )
        return self._to_record(row)

    def list_role_quotas(self) -> list[WebToolRoleQuotaRecord]:
        rows = self._session.scalars(select(WebToolRoleQuota).order_by(WebToolRoleQuota.role.asc())).all()
        by_role = {Role(row.role): self._to_record(row) for row in rows}
        result: list[WebToolRoleQuotaRecord] = []
        for role in Role:
            if role in by_role:
                result.append(by_role[role])
            else:
                default_mode, default_limit = self.DEFAULTS[role]
                result.append(
                    WebToolRoleQuotaRecord(
                        role=role,
                        mode=default_mode,
                        daily_limit=default_limit,
                        updated_by_telegram_user_id=None,
                    )
                )
        return result

    def upsert_role_quota(
        self,
        *,
        role: str | Role,
        mode: str,
        daily_limit: int | None = None,
        updated_by_telegram_user_id: int | None = None,
    ) -> WebToolRoleQuotaRecord:
        normalized_role = self._validate_role(role)
        normalized_mode, normalized_limit = self._validate_mode_and_limit(
            role=normalized_role,
            mode=mode,
            daily_limit=daily_limit,
        )

        row = self._session.scalar(select(WebToolRoleQuota).where(WebToolRoleQuota.role == normalized_role.value))
        if row is None:
            row = WebToolRoleQuota(
                role=normalized_role.value,
                mode=normalized_mode,
                daily_limit=normalized_limit,
                updated_by_telegram_user_id=updated_by_telegram_user_id,
            )
            self._session.add(row)
        else:
            row.mode = normalized_mode
            row.daily_limit = normalized_limit
            row.updated_by_telegram_user_id = updated_by_telegram_user_id

        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row)

    def get_current_count(self, *, user_id: int, role: Role, chat_id: int, message_thread_id: int | None, day: str) -> int:
        """Return the current daily counter for the given scope. Returns 0 if no counter exists."""
        row = self._session.scalar(
            select(WebToolQuotaCounter).where(
                WebToolQuotaCounter.user_id == user_id,
                WebToolQuotaCounter.role == role.value,
                WebToolQuotaCounter.chat_id == chat_id,
                WebToolQuotaCounter.message_thread_id == message_thread_id,
                WebToolQuotaCounter.day == day,
            )
        )
        return 0 if row is None else int(row.count)

    def increment_count(self, *, user_id: int, role: Role, chat_id: int, message_thread_id: int | None, day: str) -> int:
        """Increment and return the new counter. Creates the counter row if it doesn't exist."""
        row = self._session.scalar(
            select(WebToolQuotaCounter).where(
                WebToolQuotaCounter.user_id == user_id,
                WebToolQuotaCounter.role == role.value,
                WebToolQuotaCounter.chat_id == chat_id,
                WebToolQuotaCounter.message_thread_id == message_thread_id,
                WebToolQuotaCounter.day == day,
            )
        )
        if row is None:
            row = WebToolQuotaCounter(
                user_id=user_id,
                role=role.value,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                day=day,
                count=1,
            )
            self._session.add(row)
        else:
            row.count = int(row.count) + 1
        self._session.commit()
        return int(row.count)

    def check_quota(
        self,
        *,
        user_id: int,
        role: Role,
        chat_id: int,
        message_thread_id: int | None,
        operation_type: str,
        day: str,
    ) -> WebToolQuotaDecision:
        """Evaluate whether the given user/role may perform the webtool operation.

        Returns a WebToolQuotaDecision with the result, metadata, and (on allow)
        updated counter. Writes a metadata-only audit event on every call.
        """
        import time

        start_ms = int(time.perf_counter() * 1000)

        quota_record = self.get_role_quota(role)
        mode = quota_record.mode
        limit = quota_record.daily_limit or 0

        # Determine decision and remaining
        if mode == "disabled":
            decision = "disabled"
            allowed = False
            reason = "role_disabled"
            current_count = 0
            remaining = None
        elif mode == "unlimited":
            decision = "allow"
            allowed = True
            reason = "unlimited"
            current_count = 0
            remaining = None
        elif mode == "limited":
            current_count = self.get_current_count(
                user_id=user_id,
                role=role,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                day=day,
            )
            if limit > 0 and current_count >= limit:
                decision = "quota_exceeded"
                allowed = False
                reason = "daily_limit_reached"
                remaining = 0
            else:
                decision = "allow"
                allowed = True
                reason = "within_limit"
                new_count = self.increment_count(
                    user_id=user_id,
                    role=role,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    day=day,
                )
                current_count = new_count
                remaining = max(0, limit - new_count) if limit > 0 else None
        else:
            decision = "not_configured"
            allowed = False
            reason = "quota_not_configured"
            current_count = 0
            remaining = None

        timing_ms = int(time.perf_counter() * 1000) - start_ms

        # Write metadata-only audit event
        self.write_audit(
            user_id=user_id,
            role=role,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            day=day,
            count=current_count,
            operation_type=operation_type,
            decision=decision,
            remaining=remaining,
            reason=reason,
            error=None,
            timing_ms=timing_ms,
        )

        return WebToolQuotaDecision(
            allowed=allowed,
            decision=decision,
            role=role,
            operation_type=operation_type,
            current_count=current_count,
            limit=limit if mode == "limited" else 0,
            remaining=remaining,
            reason=reason,
            error=None,
            timing_ms=timing_ms,
        )

    def write_audit(
        self,
        *,
        user_id: int,
        role: Role,
        chat_id: int,
        message_thread_id: int | None,
        day: str,
        count: int,
        operation_type: str,
        decision: str,
        remaining: int | None,
        reason: str,
        error: str | None,
        timing_ms: int,
    ) -> None:
        """Write a metadata-only audit event for a webtool quota decision.

        No query content, URLs, prompts, or secrets are stored.
        """
        self._session.add(
            WebToolAuditEvent(
                user_id=user_id,
                role=role.value,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                day=day,
                count=count,
                operation_type=operation_type,
                decision=decision,
                remaining=remaining,
                reason=reason,
                error=error,
                timing_ms=timing_ms,
            )
        )
        self._session.commit()


class PluginPolicyOverrideRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_snapshot(self, *, plugin_name: str) -> PluginPolicyOverrideSnapshot | None:
        row = self._session.scalar(select(PluginPolicyOverride).where(PluginPolicyOverride.plugin_name == plugin_name))
        if row is None:
            return None

        required_roles: list[Role] = []
        if row.required_roles_json:
            try:
                raw_roles = json.loads(row.required_roles_json)
            except json.JSONDecodeError:
                raw_roles = []
            if isinstance(raw_roles, list):
                for item in raw_roles:
                    if not isinstance(item, str):
                        continue
                    try:
                        required_roles.append(Role(item))
                    except ValueError:
                        continue

        allowed_group_ids = self._session.scalars(
            select(PluginPolicyAllowedGroup.chat_id)
            .where(PluginPolicyAllowedGroup.override_id == row.id)
            .order_by(PluginPolicyAllowedGroup.chat_id.asc())
        ).all()
        allowed_topics = self._session.execute(
            select(PluginPolicyAllowedTopic.chat_id, PluginPolicyAllowedTopic.message_thread_id)
            .where(PluginPolicyAllowedTopic.override_id == row.id)
            .order_by(PluginPolicyAllowedTopic.chat_id.asc(), PluginPolicyAllowedTopic.message_thread_id.asc())
        ).all()

        return PluginPolicyOverrideSnapshot(
            plugin_name=row.plugin_name,
            roles_mode=row.roles_mode,
            required_roles=required_roles,
            private_mode=row.private_mode,
            groups_mode=row.groups_mode,
            topics_mode=row.topics_mode,
            allowed_group_ids=list(allowed_group_ids),
            allowed_topics=[(int(chat_id), int(message_thread_id)) for chat_id, message_thread_id in allowed_topics],
        )

    def upsert_override(
        self,
        *,
        plugin_name: str,
        roles_mode: str,
        required_roles: list[Role],
        private_mode: str,
        groups_mode: str,
        topics_mode: str,
        allowed_group_ids: list[int] | None = None,
        allowed_topics: list[tuple[int, int]] | None = None,
    ) -> None:
        row = self._session.scalar(select(PluginPolicyOverride).where(PluginPolicyOverride.plugin_name == plugin_name))
        required_roles_json = json.dumps([role.value for role in required_roles])

        if row is None:
            row = PluginPolicyOverride(
                plugin_name=plugin_name,
                roles_mode=roles_mode,
                required_roles_json=required_roles_json,
                private_mode=private_mode,
                groups_mode=groups_mode,
                topics_mode=topics_mode,
            )
            self._session.add(row)
            self._session.flush()
        else:
            row.roles_mode = roles_mode
            row.required_roles_json = required_roles_json
            row.private_mode = private_mode
            row.groups_mode = groups_mode
            row.topics_mode = topics_mode

        if allowed_group_ids is not None:
            self._session.query(PluginPolicyAllowedGroup).filter(PluginPolicyAllowedGroup.override_id == row.id).delete()
            deduped_group_ids = sorted(set(allowed_group_ids))
            for chat_id in deduped_group_ids:
                self._session.add(PluginPolicyAllowedGroup(override_id=row.id, chat_id=chat_id))

        if allowed_topics is not None:
            self._session.query(PluginPolicyAllowedTopic).filter(PluginPolicyAllowedTopic.override_id == row.id).delete()
            deduped_topics = sorted(set(allowed_topics), key=lambda item: (item[0], item[1]))
            for chat_id, message_thread_id in deduped_topics:
                self._session.add(
                    PluginPolicyAllowedTopic(
                        override_id=row.id,
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                    )
                )

        self._session.commit()


class UserRoleRepository:
    """Minimal DB service for user-role lookup/set operations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_user_role(self, telegram_user_id: int) -> Role | None:
        user = self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is None:
            return None
        return Role(user.role.name)

    def get_user_by_telegram_id(self, telegram_user_id: int) -> User | None:
        return self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))

    def upsert_discovered_user(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        seen_at: datetime | None = None,
    ) -> User:
        seen = seen_at or datetime.now(timezone.utc)
        user = self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        is_new_user = user is None

        if user is None:
            normal_role = self._session.scalar(select(DbRole).where(DbRole.name == Role.NORMAL.value))
            if normal_role is None:
                raise ValueError("role not found in db: normal")
            user = User(
                telegram_user_id=telegram_user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                first_seen_at=seen,
                last_seen_at=seen,
                role_id=normal_role.id,
            )
            self._session.add(user)
        else:
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.last_seen_at = seen

        if is_new_user:
            ConsentService().ensure_pending_for_new_user(user, now=seen)

        self._session.commit()
        return user

    def bootstrap_owner_from_settings(self, *, owner_telegram_user_id: int | None) -> bool:
        """Ensure configured owner exists and has owner role.

        Returns True if a role/user change was applied, else False.
        """
        if owner_telegram_user_id is None:
            return False

        result = self.set_user_role(
            actor_telegram_user_id=owner_telegram_user_id,
            target_telegram_user_id=owner_telegram_user_id,
            role=Role.OWNER,
        )
        return result.changed

    def set_user_role(
        self,
        *,
        actor_telegram_user_id: int | None,
        target_telegram_user_id: int,
        role: Role,
    ) -> RoleChangeResult:
        role_row = self._session.scalar(select(DbRole).where(DbRole.name == role.value))
        if role_row is None:
            raise ValueError(f"role not found in db: {role.value}")

        user = self._session.scalar(select(User).where(User.telegram_user_id == target_telegram_user_id))
        previous_role: Role | None = None
        changed = False

        if user is None:
            user = User(telegram_user_id=target_telegram_user_id, role_id=role_row.id)
            self._session.add(user)
            changed = True
        else:
            previous_role = Role(user.role.name)
            if user.role_id != role_row.id:
                user.role_id = role_row.id
                changed = True

        if changed:
            event = AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="role_set",
                payload_json=json.dumps(
                    {
                        "target_telegram_user_id": target_telegram_user_id,
                        "previous_role": previous_role.value if previous_role else None,
                        "new_role": role.value,
                    }
                ),
            )
            self._session.add(event)

        self._session.commit()

        return RoleChangeResult(changed=changed, previous_role=previous_role, new_role=role)


class ChatScopedRoleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_group_role(self, *, chat_id: int, telegram_user_id: int) -> Role | None:
        user = self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is None:
            return None
        row = self._session.scalar(
            select(ChatUserRole).where(ChatUserRole.chat_id == chat_id, ChatUserRole.user_id == user.id)
        )
        if row is None:
            return None
        return Role(row.role.name)

    def set_group_role(
        self,
        *,
        chat_id: int,
        telegram_user_id: int,
        role: Role,
        actor_telegram_user_id: int | None = None,
        source: str | None = None,
        changed_at: datetime | None = None,
    ) -> RoleChangeResult:
        user = self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is None:
            normal_role = self._session.scalar(select(DbRole).where(DbRole.name == Role.NORMAL.value))
            if normal_role is None:
                raise ValueError("role not found in db: normal")
            user = User(telegram_user_id=telegram_user_id, role_id=normal_role.id)
            self._session.add(user)
            self._session.flush()

        role_row = self._session.scalar(select(DbRole).where(DbRole.name == role.value))
        if role_row is None:
            raise ValueError(f"role not found in db: {role.value}")

        row = self._session.scalar(
            select(ChatUserRole).where(ChatUserRole.chat_id == chat_id, ChatUserRole.user_id == user.id)
        )
        previous_role: Role | None = None
        changed = False
        if row is None:
            row = ChatUserRole(chat_id=chat_id, user_id=user.id, role_id=role_row.id)
            self._session.add(row)
            changed = True
        else:
            previous_role = Role(row.role.name)
            if row.role_id != role_row.id:
                row.role_id = role_row.id
                row.updated_at = changed_at or datetime.now(timezone.utc)
                changed = True

        if changed:
            self._session.add(
                AuditEvent(
                    actor_telegram_user_id=actor_telegram_user_id,
                    event_type="group_role_set",
                    payload_json=json.dumps(
                        {
                            "chat_id": chat_id,
                            "target_telegram_user_id": telegram_user_id,
                            "previous_role": previous_role.value if previous_role else None,
                            "new_role": role.value,
                            "source": source,
                        }
                    ),
                )
            )

        self._session.commit()
        return RoleChangeResult(changed=changed, previous_role=previous_role, new_role=role)

    def list_group_role_users(self, chat_id: int) -> list[User]:
        return self._session.scalars(
            select(User)
            .join(ChatUserRole, ChatUserRole.user_id == User.id)
            .where(ChatUserRole.chat_id == chat_id)
            .order_by(User.telegram_user_id.asc())
        ).all()

    def list_group_roles_for_users(
        self,
        *,
        chat_ids: Iterable[int],
        telegram_user_ids: Iterable[int],
    ) -> dict[tuple[int, int], Role]:
        chat_id_list = list(chat_ids)
        telegram_user_id_list = list(telegram_user_ids)
        if not chat_id_list or not telegram_user_id_list:
            return {}

        user_rows = self._session.scalars(
            select(User).where(User.telegram_user_id.in_(telegram_user_id_list))
        ).all()
        if not user_rows:
            return {}

        user_id_to_telegram_user_id = {row.id: row.telegram_user_id for row in user_rows}
        scoped_rows = self._session.scalars(
            select(ChatUserRole).where(
                ChatUserRole.chat_id.in_(chat_id_list),
                ChatUserRole.user_id.in_(user_id_to_telegram_user_id.keys()),
            )
        ).all()

        result: dict[tuple[int, int], Role] = {}
        for row in scoped_rows:
            telegram_user_id = user_id_to_telegram_user_id.get(row.user_id)
            if telegram_user_id is None:
                continue
            result[(row.chat_id, telegram_user_id)] = Role(row.role.name)
        return result

    def clear_group_role(
        self,
        *,
        chat_id: int,
        telegram_user_id: int,
        actor_telegram_user_id: int | None = None,
        source: str | None = None,
    ) -> bool:
        user = self._session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        if user is None:
            return False
        row = self._session.scalar(
            select(ChatUserRole).where(ChatUserRole.chat_id == chat_id, ChatUserRole.user_id == user.id)
        )
        if row is None:
            return False
        previous_role = Role(row.role.name)
        self._session.delete(row)
        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="group_role_clear",
                payload_json=json.dumps(
                    {
                        "chat_id": chat_id,
                        "target_telegram_user_id": telegram_user_id,
                        "previous_role": previous_role.value,
                        "new_role": Role.NORMAL.value,
                        "source": source,
                    }
                ),
            )
        )
        self._session.commit()
        return True


class ChatSeenUserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _ensure_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @classmethod
    def _normalize_row_timestamps(cls, row: ChatSeenUser) -> ChatSeenUser:
        row.first_seen_at = cls._ensure_utc(row.first_seen_at)
        row.last_seen_at = cls._ensure_utc(row.last_seen_at)
        return row

    def mark_seen(self, *, chat_id: int, telegram_user_id: int, seen_at: datetime | None = None) -> ChatSeenUser:
        seen = self._ensure_utc(seen_at or datetime.now(timezone.utc))
        row = self._session.scalar(
            select(ChatSeenUser).where(
                ChatSeenUser.chat_id == chat_id,
                ChatSeenUser.telegram_user_id == telegram_user_id,
            )
        )
        if row is None:
            row = ChatSeenUser(
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                first_seen_at=seen,
                last_seen_at=seen,
            )
            self._session.add(row)
        else:
            row.last_seen_at = seen

        self._session.commit()
        self._session.refresh(row)
        return self._normalize_row_timestamps(row)

    def list_seen_users_for_chat(self, *, chat_id: int) -> list[int]:
        rows = self._session.scalars(
            select(ChatSeenUser.telegram_user_id)
            .where(ChatSeenUser.chat_id == chat_id)
            .order_by(ChatSeenUser.telegram_user_id.asc())
        ).all()
        return list(rows)


class ChatTopicRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_chat(
        self,
        chat_id: int,
        chat_type: str,
        title: str | None = None,
        username: str | None = None,
        seen_at: datetime | None = None,
    ) -> TelegramChat:
        seen = seen_at or datetime.now(timezone.utc)
        row = self._session.scalar(select(TelegramChat).where(TelegramChat.chat_id == chat_id))
        if row is None:
            row = TelegramChat(
                chat_id=chat_id,
                chat_type=chat_type,
                title=title,
                username=username,
                first_seen_at=seen,
                last_seen_at=seen,
                updated_at=seen,
            )
            self._session.add(row)
        else:
            row.chat_type = chat_type
            row.title = title
            row.username = username
            row.last_seen_at = seen
            row.updated_at = seen

        self._session.commit()
        return row

    def upsert_topic(
        self,
        chat_id: int,
        message_thread_id: int,
        telegram_topic_name: str | None = None,
        seen_at: datetime | None = None,
    ) -> TelegramTopic:
        seen = seen_at or datetime.now(timezone.utc)
        row = self._session.scalar(
            select(TelegramTopic).where(
                TelegramTopic.chat_id == chat_id,
                TelegramTopic.message_thread_id == message_thread_id,
            )
        )
        if row is None:
            row = TelegramTopic(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                telegram_topic_name=telegram_topic_name,
                first_seen_at=seen,
                last_seen_at=seen,
                updated_at=seen,
            )
            self._session.add(row)
        else:
            cleaned_name = telegram_topic_name.strip() if isinstance(telegram_topic_name, str) else None
            if cleaned_name:
                row.telegram_topic_name = cleaned_name
            row.last_seen_at = seen
            row.updated_at = seen

        self._session.commit()
        return row

    def list_chats(self) -> list[TelegramChat]:
        return self._session.scalars(select(TelegramChat).order_by(TelegramChat.chat_id.asc())).all()

    def list_topics(self, chat_id: int) -> list[TelegramTopic]:
        return self._session.scalars(
            select(TelegramTopic)
            .where(TelegramTopic.chat_id == chat_id)
            .order_by(TelegramTopic.message_thread_id.asc())
        ).all()

    def update_topic_metadata(
        self,
        chat_id: int,
        message_thread_id: int,
        display_name: str | None = None,
        notes: str | None = None,
        enabled: bool = True,
        actor_telegram_user_id: int | None = None,
    ) -> TelegramTopic:
        topic = self._session.scalar(
            select(TelegramTopic).where(
                TelegramTopic.chat_id == chat_id,
                TelegramTopic.message_thread_id == message_thread_id,
            )
        )
        if topic is None:
            raise ValueError("topic not found")

        topic.display_name = display_name
        topic.notes = notes
        topic.enabled = enabled
        topic.updated_at = datetime.now(timezone.utc)

        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="topic_metadata_update",
                payload_json=json.dumps(
                    {
                        "chat_id": chat_id,
                        "message_thread_id": message_thread_id,
                        "display_name": display_name,
                        "notes": notes,
                        "enabled": enabled,
                    }
                ),
            )
        )

        self._session.commit()
        return topic


class PluginRepository:
    ACTIVATION_REQUEST_STATUSES = {"pending", "approved", "rejected", "blocked"}
    LEGACY_ACTIVATION_PENDING = "activation_pending"

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_from_manifest(self, manifest: PluginManifest) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == manifest.name))
        now = datetime.now(timezone.utc)
        if row is None:
            row = Plugin(
                name=manifest.name,
                version=manifest.version,
                enabled=0,
                activation_status=self.LEGACY_ACTIVATION_PENDING,
                manifest_json=manifest.model_dump_json(),
            )
            self._session.add(row)
            self._session.commit()
            return

        row.version = manifest.version
        row.manifest_json = manifest.model_dump_json()
        if not row.activation_status:
            row.activation_status = self.LEGACY_ACTIVATION_PENDING
        self._session.commit()

    def sync_discovered(self, manifests: Iterable[PluginManifest]) -> None:
        for manifest in manifests:
            row = self._session.scalar(select(Plugin).where(Plugin.name == manifest.name))
            if row is None:
                self._session.add(
                    Plugin(
                        name=manifest.name,
                        version=manifest.version,
                        enabled=0,
                        activation_status=self.LEGACY_ACTIVATION_PENDING,
                        manifest_json=manifest.model_dump_json(),
                    )
                )
                continue

            row.version = manifest.version
            row.manifest_json = manifest.model_dump_json()
            if not row.activation_status:
                row.activation_status = self.LEGACY_ACTIVATION_PENDING

        self._session.commit()

    def activate(self, plugin_name: str, *, actor_telegram_user_id: int | None = None) -> bool:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            raise ValueError("plugin not found")

        changed = bool(not row.enabled or row.activation_status != "active")
        row.enabled = 1
        row.activation_status = "active"

        if changed:
            self._session.add(
                AuditEvent(
                    actor_telegram_user_id=actor_telegram_user_id,
                    event_type="plugin_activate",
                    payload_json=json.dumps({"plugin_name": plugin_name}),
                )
            )

        self._session.commit()
        return changed

    def deactivate(self, plugin_name: str, *, actor_telegram_user_id: int | None = None) -> bool:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            raise ValueError("plugin not found")

        changed = bool(row.enabled)
        row.enabled = 0
        row.activation_status = self.LEGACY_ACTIVATION_PENDING

        if changed:
            self._session.add(
                AuditEvent(
                    actor_telegram_user_id=actor_telegram_user_id,
                    event_type="plugin_deactivate",
                    payload_json=json.dumps({"plugin_name": plugin_name}),
                )
            )

        self._session.commit()
        return changed

    def set_worker_state(
        self,
        *,
        plugin_name: str,
        state: str,
        heartbeat_at: datetime,
        restart_count: int,
        next_restart_at: datetime | None,
        last_error: str | None,
    ) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return
        row.worker_state = state
        row.worker_last_heartbeat_at = heartbeat_at
        row.worker_restart_count = restart_count
        row.worker_next_restart_at = next_restart_at
        row.worker_last_error = last_error
        row.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def set_run_state(
        self,
        *,
        plugin_name: str,
        last_run_at: datetime | None,
        last_status: str | None,
        next_run_at: datetime | None,
    ) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return
        row.last_run_at = last_run_at
        row.last_status = last_status
        row.next_run_at = next_run_at
        row.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def mark_scheduled_result(
        self,
        *,
        plugin_name: str,
        ran_at: datetime,
        status: str,
        next_run_at: datetime | None,
        actor_telegram_user_id: int | None = None,
        error: str | None = None,
    ) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return

        ran_at_naive = ran_at.replace(tzinfo=None) if ran_at.tzinfo is not None else ran_at
        next_run_naive = next_run_at.replace(tzinfo=None) if next_run_at is not None and next_run_at.tzinfo is not None else next_run_at

        row.last_run_at = ran_at_naive
        row.last_status = status
        row.next_run_at = next_run_naive
        row.updated_at = datetime.now(timezone.utc)

        payload: dict[str, object] = {
            "plugin_name": plugin_name,
            "status": status,
            "run_at": ran_at_naive.isoformat(),
            "next_run_at": next_run_naive.isoformat() if next_run_naive is not None else None,
        }
        if error is not None:
            payload["error"] = error

        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type="plugin_schedule_run",
                payload_json=json.dumps(payload),
            )
        )
        self._session.commit()

    def mark_worker_state(
        self,
        *,
        plugin_name: str,
        state: str,
        heartbeat_at: datetime,
        next_restart_at: datetime | None,
        last_error: str | None,
        increment_restart_count: bool = False,
    ) -> None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return
        restart_count = row.worker_restart_count + 1 if increment_restart_count else row.worker_restart_count
        self.set_worker_state(
            plugin_name=plugin_name,
            state=state,
            heartbeat_at=heartbeat_at,
            restart_count=restart_count,
            next_restart_at=next_restart_at,
            last_error=last_error,
        )

    def get_status(self, plugin_name: str) -> PluginStatus | None:
        row = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if row is None:
            return None
        return PluginStatus(
            name=row.name,
            enabled=row.enabled,
            activation_status=self._resolve_activation_status(row.name),
            worker_state=row.worker_state,
            worker_last_heartbeat_at=row.worker_last_heartbeat_at,
            worker_restart_count=row.worker_restart_count,
            worker_next_restart_at=row.worker_next_restart_at,
            worker_last_error=row.worker_last_error,
            last_run_at=row.last_run_at,
            last_status=row.last_status,
            next_run_at=row.next_run_at,
        )

    def list_due_scheduled_plugins(self, *, now: datetime) -> list[Plugin]:
        now_naive = now.replace(tzinfo=None) if now.tzinfo is not None else now
        rows = self._session.scalars(select(Plugin).order_by(Plugin.name.asc())).all()
        due: list[Plugin] = []
        for row in rows:
            if not row.enabled:
                continue
            if row.next_run_at is not None and row.next_run_at > now_naive:
                continue
            due.append(row)
        return due

    def list_plugins(self) -> list[Plugin]:
        return self._session.scalars(select(Plugin).order_by(Plugin.name.asc())).all()

    def list_statuses(self) -> list[PluginStatus]:
        rows = self._session.scalars(select(Plugin).order_by(Plugin.name.asc())).all()
        statuses: list[PluginStatus] = []
        for row in rows:
            activation_status = self._resolve_activation_status(row.name)
            statuses.append(
                PluginStatus(
                    name=row.name,
                    enabled=row.enabled,
                    activation_status=activation_status,
                    worker_state=row.worker_state,
                    worker_last_heartbeat_at=row.worker_last_heartbeat_at,
                    worker_restart_count=row.worker_restart_count,
                    worker_next_restart_at=row.worker_next_restart_at,
                    worker_last_error=row.worker_last_error,
                    last_run_at=row.last_run_at,
                    last_status=row.last_status,
                    next_run_at=row.next_run_at,
                )
            )
        return statuses

    def _resolve_activation_status(self, plugin_name: str) -> str:
        latest_request = self._session.scalar(
            select(PluginActivationRequest)
            .where(PluginActivationRequest.plugin_name == plugin_name)
            .order_by(PluginActivationRequest.requested_at.desc(), PluginActivationRequest.id.desc())
        )
        if latest_request is None:
            return "activation_pending"
        if latest_request.status == "pending":
            return "activation_pending"
        if latest_request.status == "approved":
            return "approved"
        if latest_request.status == "rejected":
            return "rejected"
        if latest_request.status == "blocked":
            return "blocked"
        return "activation_pending"

    def create_activation_request(
        self,
        plugin_name: str,
        *,
        actor_telegram_user_id: int | None,
        reason: str | None = None,
    ) -> PluginActivationRequestStatus:
        if actor_telegram_user_id is None:
            raise ValueError("actor required")
        return self.request_activation(
            plugin_name=plugin_name,
            requested_by_telegram_user_id=actor_telegram_user_id,
            reason=reason,
        )

    def request_activation(
        self,
        *,
        plugin_name: str,
        requested_by_telegram_user_id: int,
        reason: str | None,
    ) -> PluginActivationRequestStatus:
        plugin = self._session.scalar(select(Plugin).where(Plugin.name == plugin_name))
        if plugin is None:
            raise ValueError("plugin not found")

        existing_pending = self._session.scalar(
            select(PluginActivationRequest)
            .where(
                PluginActivationRequest.plugin_name == plugin_name,
                PluginActivationRequest.status == "pending",
            )
            .order_by(PluginActivationRequest.requested_at.desc(), PluginActivationRequest.id.desc())
        )
        if existing_pending is not None:
            return self._to_activation_request_status(existing_pending)

        request = PluginActivationRequest(
            plugin_name=plugin_name,
            status="pending",
            requested_by_telegram_user_id=requested_by_telegram_user_id,
            reason=reason,
        )
        self._session.add(request)
        self._session.commit()
        self._session.refresh(request)
        return self._to_activation_request_status(request)

    def get_activation_request(self, request_id: int) -> PluginActivationRequestStatus | None:
        request = self._session.scalar(select(PluginActivationRequest).where(PluginActivationRequest.id == request_id))
        if request is None:
            return None
        return self._to_activation_request_status(request)

    def resolve_activation_request(
        self,
        request_id: int,
        *,
        status: str,
        actor_telegram_user_id: int | None,
    ) -> bool:
        if actor_telegram_user_id is None:
            raise ValueError("actor required")
        resolved = self._resolve_activation_request_status(
            request_id=request_id,
            decision=status,
            resolved_by_telegram_user_id=actor_telegram_user_id,
            reason=None,
        )
        if resolved.plugin_name:
            plugin = self._session.scalar(select(Plugin).where(Plugin.name == resolved.plugin_name))
            if plugin is None:
                raise ValueError("plugin not found")
            event_type: str | None = None
            if resolved.status == "approved":
                plugin.enabled = 1
                plugin.activation_status = "active"
                event_type = "plugin_activation_request_approved"
                self._session.add(
                    AuditEvent(
                        actor_telegram_user_id=actor_telegram_user_id,
                        event_type="plugin_activate",
                        payload_json=json.dumps({"plugin_name": resolved.plugin_name}),
                    )
                )
            elif resolved.status in {"rejected", "blocked"}:
                plugin.enabled = 0
                plugin.activation_status = self.LEGACY_ACTIVATION_PENDING
                event_type = f"plugin_activation_request_{resolved.status}"
            if event_type is not None:
                self._session.add(
                    AuditEvent(
                        actor_telegram_user_id=actor_telegram_user_id,
                        event_type=event_type,
                        payload_json=json.dumps({"plugin_name": resolved.plugin_name, "request_id": request_id}),
                    )
                )
        self._session.commit()
        return True

    def _resolve_activation_request_status(
        self,
        *,
        request_id: int,
        decision: str,
        resolved_by_telegram_user_id: int,
        reason: str | None,
    ) -> PluginActivationRequestStatus:
        normalized_decision = decision.strip().lower()
        if normalized_decision not in {"approved", "rejected", "blocked"}:
            raise ValueError("invalid decision")

        request = self._session.scalar(select(PluginActivationRequest).where(PluginActivationRequest.id == request_id))
        if request is None:
            raise ValueError("request not found")

        request.status = normalized_decision
        request.resolved_by_telegram_user_id = resolved_by_telegram_user_id
        request.resolved_at = datetime.now(timezone.utc)
        if reason is not None:
            request.reason = reason

        self._session.commit()
        self._session.refresh(request)
        return self._to_activation_request_status(request)

    def list_activation_requests(
        self,
        *,
        plugin_name: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[PluginActivationRequestStatus]:
        normalized_status = None
        if status is not None:
            normalized_status = status.strip().lower()
            if normalized_status not in self.ACTIVATION_REQUEST_STATUSES:
                raise ValueError("invalid status")

        query = select(PluginActivationRequest)
        if plugin_name is not None:
            query = query.where(PluginActivationRequest.plugin_name == plugin_name)
        if normalized_status is not None:
            query = query.where(PluginActivationRequest.status == normalized_status)

        safe_limit = max(1, min(limit, 100))
        rows = self._session.scalars(
            query.order_by(PluginActivationRequest.requested_at.desc(), PluginActivationRequest.id.desc()).limit(safe_limit)
        ).all()
        return [self._to_activation_request_status(row) for row in rows]

    @staticmethod
    def _to_activation_request_status(row: PluginActivationRequest) -> PluginActivationRequestStatus:
        return PluginActivationRequestStatus(
            id=row.id,
            plugin_name=row.plugin_name,
            status=row.status,
            requested_by_telegram_user_id=row.requested_by_telegram_user_id,
            resolved_by_telegram_user_id=row.resolved_by_telegram_user_id,
            reason=row.reason,
            requested_at=row.requested_at,
            resolved_at=row.resolved_at,
        )


class AuthAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def write_login_event(self, *, event_type: str, remote_addr: str | None) -> None:
        self.log(
            actor_telegram_user_id=None,
            event_type=event_type,
            payload={"remote_addr": remote_addr},
        )

    def log(
        self,
        *,
        actor_telegram_user_id: int | None,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        self._session.add(
            AuditEvent(
                actor_telegram_user_id=actor_telegram_user_id,
                event_type=event_type,
                payload_json=json.dumps(payload),
            )
        )
        self._session.commit()


@dataclass(slots=True)
class TopicAgentConfigRecord:
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    ai_enabled: bool
    response_mode: str
    memory_retention_days: int
    tools_enabled: bool
    main_soul_text: str | None
    topic_soul_text: str | None
    topic_soul_owner_only_edit: bool
    recent_context_window_size: int
    image_analysis_mode: str


@dataclass(slots=True)
class TopicDailyMemoryRecord:
    id: int
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    memory_date: str
    summary_text: str
    tokens_estimate: int


@dataclass(slots=True)
class TopicLongMemoryRecord:
    id: int
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    fact_text: str
    is_active: bool
    source_daily_memory_id: int | None
    promotion_status: str
    answer_status: str


@dataclass(slots=True)
class TopicAiSessionRecord:
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    session_payload: dict[str, object]


@dataclass(slots=True)
class TopicRecentMessageRecord:
    id: int
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    message_text: str
    telegram_message_id: int | None = None
    telegram_author_user_id: int | None = None
    telegram_author_username: str | None = None
    telegram_author_is_bot: bool = False
    source: str = "user"
    created_at: datetime | None = None


@dataclass(slots=True)
class UserMemoryProfileRecord:
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int
    profile: dict[str, object]


class UserMemoryProfileRepository:
    ALLOWED_SCOPE_TYPES: tuple[str, ...] = ("private_user", "topic", "group_chat")
    ALLOWED_PROFILE_FIELDS: tuple[str, ...] = (
        "language",
        "timezone",
        "context_role",
        "communication_style",
        "tone_preference",
        "format_preference",
        "verbosity",
        "interests",
        "avoid_topics",
        "interaction_preferences",
    )
    ALLOWED_STRING_VALUES: dict[str, set[str]] = {
        "communication_style": {"brief", "balanced", "detailed"},
        "tone_preference": {"neutral", "friendly", "formal", "direct"},
        "format_preference": {"plain", "bullet_points", "step_by_step"},
        "verbosity": {"low", "medium", "high"},
    }

    def __init__(self, session: Session) -> None:
        self._session = session

    @classmethod
    def _normalize_scope(
        cls,
        *,
        scope_type: str,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
    ) -> tuple[str, int | None, int | None, int]:
        normalized_scope = (scope_type or "").strip().lower()
        if normalized_scope not in cls.ALLOWED_SCOPE_TYPES:
            raise ValueError("invalid scope_type")
        if user_id is None:
            raise ValueError("user_id is required")

        normalized_chat_id = chat_id
        normalized_topic_id = topic_id

        if normalized_scope == "private_user":
            normalized_chat_id = None
            normalized_topic_id = None
        elif normalized_scope == "group_chat":
            if normalized_chat_id is None:
                raise ValueError("chat_id is required for group_chat")
            normalized_topic_id = None
        elif normalized_scope == "topic":
            if normalized_chat_id is None or normalized_topic_id is None:
                raise ValueError("chat_id and topic_id are required for topic")

        return normalized_scope, normalized_chat_id, normalized_topic_id, int(user_id)

    @classmethod
    def _sanitize_profile(cls, profile: dict[str, object] | None) -> dict[str, object]:
        if not isinstance(profile, dict):
            return {}

        sanitized: dict[str, object] = {}
        for key in cls.ALLOWED_PROFILE_FIELDS:
            if key not in profile:
                continue
            value = profile[key]

            if key in {"language", "timezone", "context_role"}:
                if isinstance(value, str):
                    cleaned = value.strip()
                    if 1 <= len(cleaned) <= 80:
                        sanitized[key] = cleaned
                continue

            if key in cls.ALLOWED_STRING_VALUES:
                if isinstance(value, str):
                    cleaned = value.strip().lower()
                    if cleaned in cls.ALLOWED_STRING_VALUES[key]:
                        sanitized[key] = cleaned
                continue

            if key in {"interests", "avoid_topics", "interaction_preferences"}:
                if isinstance(value, list):
                    items: list[str] = []
                    for item in value:
                        if not isinstance(item, str):
                            continue
                        cleaned = item.strip()
                        if not cleaned:
                            continue
                        if len(cleaned) > 80:
                            cleaned = cleaned[:80]
                        items.append(cleaned)
                    deduped = list(dict.fromkeys(items))[:5]
                    if deduped:
                        sanitized[key] = deduped
                continue

        return sanitized

    @classmethod
    def _parse_profile_json(cls, raw_value: str | None) -> dict[str, object]:
        if not raw_value:
            return {}
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return cls._sanitize_profile(parsed)

    @classmethod
    def _to_record(cls, row: UserMemoryProfile) -> UserMemoryProfileRecord:
        return UserMemoryProfileRecord(
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=int(row.user_id),
            profile=cls._parse_profile_json(row.profile_json),
        )

    def replace_profile(
        self,
        *,
        scope_type: str,
        user_id: int,
        profile: dict[str, object],
        chat_id: int | None = None,
        topic_id: int | None = None,
    ) -> UserMemoryProfileRecord:
        normalized_scope, normalized_chat_id, normalized_topic_id, normalized_user_id = self._normalize_scope(
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
        )
        sanitized_profile = self._sanitize_profile(profile)

        row = self._session.scalar(
            select(UserMemoryProfile).where(
                UserMemoryProfile.scope_type == normalized_scope,
                UserMemoryProfile.chat_id == normalized_chat_id,
                UserMemoryProfile.topic_id == normalized_topic_id,
                UserMemoryProfile.user_id == normalized_user_id,
            )
        )
        if row is None:
            row = UserMemoryProfile(
                scope_type=normalized_scope,
                chat_id=normalized_chat_id,
                topic_id=normalized_topic_id,
                user_id=normalized_user_id,
            )
            self._session.add(row)

        row.profile_json = json.dumps(sanitized_profile, separators=(",", ":"), sort_keys=True)
        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row)

    def update_profile_from_candidate(
        self,
        *,
        scope_type: str,
        user_id: int,
        candidate: dict[str, object] | None,
        chat_id: int | None = None,
        topic_id: int | None = None,
    ) -> UserMemoryProfileRecord:
        normalized_scope, normalized_chat_id, normalized_topic_id, normalized_user_id = self._normalize_scope(
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
        )
        if normalized_scope in {"group_chat", "topic"} and normalized_chat_id is None:
            raise ValueError("chat_id is required for scoped profile update")

        sanitized_candidate = self._sanitize_profile(candidate)
        if not sanitized_candidate:
            return self.get_profile(
                scope_type=normalized_scope,
                chat_id=normalized_chat_id,
                topic_id=normalized_topic_id,
                user_id=normalized_user_id,
            )

        current = self.get_profile(
            scope_type=normalized_scope,
            chat_id=normalized_chat_id,
            topic_id=normalized_topic_id,
            user_id=normalized_user_id,
        )
        merged = dict(current.profile)
        merged.update(sanitized_candidate)
        return self.replace_profile(
            scope_type=normalized_scope,
            chat_id=normalized_chat_id,
            topic_id=normalized_topic_id,
            user_id=normalized_user_id,
            profile=merged,
        )

    def get_profile(
        self,
        *,
        scope_type: str,
        user_id: int,
        chat_id: int | None = None,
        topic_id: int | None = None,
    ) -> UserMemoryProfileRecord:
        normalized_scope, normalized_chat_id, normalized_topic_id, normalized_user_id = self._normalize_scope(
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
        )
        row = self._session.scalar(
            select(UserMemoryProfile).where(
                UserMemoryProfile.scope_type == normalized_scope,
                UserMemoryProfile.chat_id == normalized_chat_id,
                UserMemoryProfile.topic_id == normalized_topic_id,
                UserMemoryProfile.user_id == normalized_user_id,
            )
        )
        if row is None:
            return UserMemoryProfileRecord(
                scope_type=normalized_scope,
                chat_id=normalized_chat_id,
                topic_id=normalized_topic_id,
                user_id=normalized_user_id,
                profile={},
            )
        return self._to_record(row)

    def list_profiles_for_users(
        self,
        *,
        scope_type: str,
        user_ids: list[int] | tuple[int, ...],
        chat_id: int | None = None,
        topic_id: int | None = None,
        limit_users: int = 5,
    ) -> list[UserMemoryProfileRecord]:
        safe_limit = max(1, min(limit_users, 20))
        # Do NOT pre-truncate user_ids before the DB query; let the DB return
        # all matching rows, then cap the final result so we don't accidentally
        # exclude users at the tail who have real profiles.
        all_valid_users = list(dict.fromkeys(int(user_id) for user_id in user_ids if int(user_id) > 0))
        if not all_valid_users:
            return []

        first_user = all_valid_users[0]
        normalized_scope, normalized_chat_id, normalized_topic_id, _ = self._normalize_scope(
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=first_user,
        )
        rows = self._session.scalars(
            select(UserMemoryProfile).where(
                UserMemoryProfile.scope_type == normalized_scope,
                UserMemoryProfile.chat_id == normalized_chat_id,
                UserMemoryProfile.topic_id == normalized_topic_id,
                UserMemoryProfile.user_id.in_(all_valid_users),
            )
        ).all()
        by_user = {int(row.user_id): self._to_record(row) for row in rows}
        # Filter to users with non-empty profiles and respect limit_users
        result = [by_user[uid] for uid in all_valid_users if uid in by_user and by_user[uid].profile][:safe_limit]
        return result


class TopicAgentMemoryRepository:
    ALLOWED_PROMOTION_STATUSES = {"none", "candidate"}
    ALLOWED_ANSWER_STATUSES = {"legacy", "approved", "rejected", "archived", "deactivated"}
    ANSWER_EFFECTIVE_STATUS = "approved"

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_config(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        ai_enabled: bool = False,
        response_mode: str = "command",
        memory_retention_days: int = 30,
        tools_enabled: bool = False,
        main_soul_text: str | None = None,
        topic_soul_text: str | None = None,
        topic_soul_owner_only_edit: bool = True,
        recent_context_window_size: int = 20,
        image_analysis_mode: str = "inherit",
    ) -> TopicAgentConfigRecord:
        row = self._session.scalar(
            select(TopicAgentConfig).where(
                TopicAgentConfig.scope_type == scope_type,
                TopicAgentConfig.chat_id == chat_id,
                TopicAgentConfig.topic_id == topic_id,
                TopicAgentConfig.user_id == user_id,
            )
        )
        if row is None:
            row = TopicAgentConfig(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
            )
            self._session.add(row)

        row.ai_enabled = ai_enabled
        row.response_mode = response_mode
        row.memory_retention_days = memory_retention_days
        row.tools_enabled = tools_enabled
        row.main_soul_text = main_soul_text
        row.topic_soul_text = topic_soul_text
        row.topic_soul_owner_only_edit = topic_soul_owner_only_edit
        row.recent_context_window_size = max(0, min(recent_context_window_size, 50))
        normalized_image_analysis_mode = (image_analysis_mode or "inherit").strip().lower()
        if normalized_image_analysis_mode not in {"inherit", "enabled", "disabled"}:
            normalized_image_analysis_mode = "inherit"
        row.image_analysis_mode = normalized_image_analysis_mode
        self._session.commit()
        self._session.refresh(row)
        return self._to_config_record(row)

    def get_config(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
    ) -> TopicAgentConfigRecord | None:
        row = self._session.scalar(
            select(TopicAgentConfig).where(
                TopicAgentConfig.scope_type == scope_type,
                TopicAgentConfig.chat_id == chat_id,
                TopicAgentConfig.topic_id == topic_id,
                TopicAgentConfig.user_id == user_id,
            )
        )
        if row is None:
            return None
        return self._to_config_record(row)

    def upsert_daily_memory(
        self,
        *,
        scope_type: str,
        memory_date: str,
        summary_text: str,
        tokens_estimate: int,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
    ) -> TopicDailyMemoryRecord:
        row = self._session.scalar(
            select(TopicDailyMemory).where(
                TopicDailyMemory.scope_type == scope_type,
                TopicDailyMemory.chat_id == chat_id,
                TopicDailyMemory.topic_id == topic_id,
                TopicDailyMemory.user_id == user_id,
                TopicDailyMemory.memory_date == memory_date,
            )
        )
        if row is None:
            row = TopicDailyMemory(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
                memory_date=memory_date,
            )
            self._session.add(row)

        row.summary_text = summary_text
        row.tokens_estimate = tokens_estimate
        self._session.commit()
        self._session.refresh(row)
        return self._to_daily_record(row)

    def get_daily_memory(
        self,
        *,
        scope_type: str,
        memory_date: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
    ) -> TopicDailyMemoryRecord | None:
        row = self._session.scalar(
            select(TopicDailyMemory).where(
                TopicDailyMemory.scope_type == scope_type,
                TopicDailyMemory.chat_id == chat_id,
                TopicDailyMemory.topic_id == topic_id,
                TopicDailyMemory.user_id == user_id,
                TopicDailyMemory.memory_date == memory_date,
            )
        )
        if row is None:
            return None
        return self._to_daily_record(row)

    def list_daily_memories(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        limit: int = 30,
    ) -> list[TopicDailyMemoryRecord]:
        safe_limit = max(1, min(limit, 365))
        rows = self._session.scalars(
            select(TopicDailyMemory)
            .where(
                TopicDailyMemory.scope_type == scope_type,
                TopicDailyMemory.chat_id == chat_id,
                TopicDailyMemory.topic_id == topic_id,
                TopicDailyMemory.user_id == user_id,
            )
            .order_by(TopicDailyMemory.memory_date.desc())
            .limit(safe_limit)
        ).all()
        return [self._to_daily_record(row) for row in rows]

    def prune_daily_memories(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        retention_days: int = 30,
        today: date | None = None,
    ) -> int:
        effective_retention = max(1, retention_days)
        current_day = today or datetime.now(UTC).date()
        cutoff_date = (current_day - timedelta(days=effective_retention)).isoformat()

        rows = self._session.scalars(
            select(TopicDailyMemory).where(
                TopicDailyMemory.scope_type == scope_type,
                TopicDailyMemory.chat_id == chat_id,
                TopicDailyMemory.topic_id == topic_id,
                TopicDailyMemory.user_id == user_id,
                TopicDailyMemory.memory_date < cutoff_date,
            )
        ).all()

        if not rows:
            return 0

        deleted = len(rows)
        for row in rows:
            self._session.delete(row)
        self._session.commit()
        return deleted

    def count_recent_daily_memories_for_scope(
        self,
        *,
        scope_type: str,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
        lookback_days: int = 7,
    ) -> int:
        """Count daily-memory rows for a single scope within the lookback window.


        Used by the dreaming runtime to determine whether a scope has sufficient
        material to be worth processing (DREAMING_MIN_DAILY_MEMORIES gate).
        """
        lookback_date = (datetime.now(UTC).date() - timedelta(days=lookback_days)).isoformat()
        count = self._session.scalar(
            select(sqlalchemy.func.count(TopicDailyMemory.id))
            .where(
                TopicDailyMemory.scope_type == scope_type,
                TopicDailyMemory.chat_id == chat_id,
                TopicDailyMemory.topic_id == topic_id,
                TopicDailyMemory.user_id == user_id,
                TopicDailyMemory.memory_date >= lookback_date,
            )
        ) or 0
        return count

    def create_long_memory(
        self,
        *,
        scope_type: str,
        fact_text: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        source_daily_memory_id: int | None = None,
        promotion_status: str = "none",
        auto_commit: bool = True,
    ) -> TopicLongMemoryRecord:
        if promotion_status not in self.ALLOWED_PROMOTION_STATUSES:
            raise ValueError("invalid promotion_status")

        row = TopicLongMemory(
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
            fact_text=fact_text,
            is_active=True,
            source_daily_memory_id=source_daily_memory_id,
            promotion_status=promotion_status,
            answer_status="legacy",
        )
        self._session.add(row)
        if auto_commit:
            self._session.commit()
            self._session.refresh(row)
        else:
            self._session.flush()
        return self._to_long_record(row)

    def list_long_memories(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        active_only: bool = True,
        answer_effective_only: bool = False,
        limit: int = 100,
    ) -> list[TopicLongMemoryRecord]:
        safe_limit = max(1, min(limit, 1000))
        query = select(TopicLongMemory).where(
            TopicLongMemory.scope_type == scope_type,
            TopicLongMemory.chat_id == chat_id,
            TopicLongMemory.topic_id == topic_id,
            TopicLongMemory.user_id == user_id,
        )
        if active_only:
            query = query.where(TopicLongMemory.is_active.is_(True))
        if answer_effective_only:
            query = query.where(TopicLongMemory.answer_status == self.ANSWER_EFFECTIVE_STATUS)
        rows = self._session.scalars(query.order_by(TopicLongMemory.id.desc()).limit(safe_limit)).all()
        return [self._to_long_record(row) for row in rows]

    def deactivate_long_memory(self, *, memory_id: int) -> bool:
        row = self._session.scalar(select(TopicLongMemory).where(TopicLongMemory.id == memory_id))
        if row is None:
            return False
        changed = False
        if row.is_active:
            row.is_active = False
            changed = True
        if row.promotion_status != "none":
            row.promotion_status = "none"
            changed = True
        if row.answer_status != "deactivated":
            row.answer_status = "deactivated"
            changed = True
        if changed:
            self._session.commit()
        return True

    def mark_long_memory_candidate(self, *, memory_id: int) -> bool:
        row = self._session.scalar(select(TopicLongMemory).where(TopicLongMemory.id == memory_id))
        if row is None:
            return False
        if not row.is_active:
            return False
        changed = False
        if row.promotion_status != "candidate":
            row.promotion_status = "candidate"
            changed = True
        if row.answer_status != "legacy":
            row.answer_status = "legacy"
            changed = True
        if changed:
            self._session.commit()
        return True

    def clear_long_memory_candidate(self, *, memory_id: int) -> bool:
        row = self._session.scalar(select(TopicLongMemory).where(TopicLongMemory.id == memory_id))
        if row is None:
            return False
        changed = False
        if row.promotion_status != "none":
            row.promotion_status = "none"
            changed = True
        if row.answer_status != "legacy":
            row.answer_status = "legacy"
            changed = True
        if changed:
            self._session.commit()
        return True

    def set_long_memory_answer_status(self, *, memory_id: int, answer_status: str) -> bool:
        normalized = (answer_status or "").strip().lower()
        if normalized not in self.ALLOWED_ANSWER_STATUSES:
            raise ValueError("invalid answer_status")

        row = self._session.scalar(select(TopicLongMemory).where(TopicLongMemory.id == memory_id))
        if row is None:
            return False
        if not row.is_active and normalized == self.ANSWER_EFFECTIVE_STATUS:
            raise ValueError("inactive memory cannot be approved")

        changed = False
        if row.answer_status != normalized:
            row.answer_status = normalized
            changed = True
        if normalized in {"rejected", "archived", "deactivated", "legacy"} and row.promotion_status != "none":
            row.promotion_status = "none"
            changed = True
        if changed:
            self._session.commit()
        return True

    def approve_long_memory(self, *, memory_id: int) -> bool:
        return self.set_long_memory_answer_status(memory_id=memory_id, answer_status="approved")

    def reject_long_memory(self, *, memory_id: int) -> bool:
        return self.set_long_memory_answer_status(memory_id=memory_id, answer_status="rejected")

    def archive_long_memory(self, *, memory_id: int) -> bool:
        return self.set_long_memory_answer_status(memory_id=memory_id, answer_status="archived")

    def upsert_ai_session(
        self,
        *,
        scope_type: str,
        session_payload: dict[str, object],
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        last_message_at: datetime | None = None,
    ) -> TopicAiSessionRecord:
        row = self._session.scalar(
            select(TopicAiSession).where(
                TopicAiSession.scope_type == scope_type,
                TopicAiSession.chat_id == chat_id,
                TopicAiSession.topic_id == topic_id,
                TopicAiSession.user_id == user_id,
            )
        )
        if row is None:
            row = TopicAiSession(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
            )
            self._session.add(row)

        row.session_payload_json = json.dumps(session_payload)
        row.last_message_at = last_message_at
        self._session.commit()
        self._session.refresh(row)
        return self._to_session_record(row)

    def get_ai_session(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
    ) -> TopicAiSessionRecord | None:
        row = self._session.scalar(
            select(TopicAiSession).where(
                TopicAiSession.scope_type == scope_type,
                TopicAiSession.chat_id == chat_id,
                TopicAiSession.topic_id == topic_id,
                TopicAiSession.user_id == user_id,
            )
        )
        if row is None:
            return None
        return self._to_session_record(row)

    def add_message(
        self,
        *,
        scope_type: str,
        message_text: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        telegram_message_id: int | None = None,
        telegram_author_user_id: int | None = None,
        telegram_author_username: str | None = None,
        telegram_author_is_bot: bool = False,
        source: str = "user",
    ) -> TopicRecentMessageRecord:
        row = TopicRecentMessage(
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
            message_text=message_text,
            telegram_message_id=telegram_message_id,
            telegram_author_user_id=telegram_author_user_id,
            telegram_author_username=telegram_author_username,
            telegram_author_is_bot=telegram_author_is_bot,
            source=source,
        )
        self._session.add(row)
        self._session.flush()
        self._trim_recent_scope(scope_type=scope_type, chat_id=chat_id, topic_id=topic_id, user_id=user_id)
        return self._to_recent_record(row)

    def append_message(
        self,
        *,
        scope_type: str,
        message_text: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        telegram_message_id: int | None = None,
        telegram_author_user_id: int | None = None,
        telegram_author_username: str | None = None,
        telegram_author_is_bot: bool = False,
        source: str = "user",
    ) -> TopicRecentMessageRecord:
        record = self.add_message(
            scope_type=scope_type,
            message_text=message_text,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
            telegram_message_id=telegram_message_id,
            telegram_author_user_id=telegram_author_user_id,
            telegram_author_username=telegram_author_username,
            telegram_author_is_bot=telegram_author_is_bot,
            source=source,
        )
        self._session.commit()
        return record

    def get_recent_by_telegram_message_id(
        self,
        *,
        scope_type: str,
        telegram_message_id: int,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
    ) -> TopicRecentMessageRecord | None:
        row = self._session.scalar(
            select(TopicRecentMessage).where(
                TopicRecentMessage.scope_type == scope_type,
                TopicRecentMessage.chat_id == chat_id,
                TopicRecentMessage.topic_id == topic_id,
                TopicRecentMessage.user_id == user_id,
                TopicRecentMessage.telegram_message_id == telegram_message_id,
            ).order_by(TopicRecentMessage.id.desc())
        )
        return self._to_recent_record(row) if row is not None else None

    def list_recent(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        limit: int = 20,
        max_age_seconds: int | None = None,
    ) -> list[TopicRecentMessageRecord]:
        safe_limit = max(1, min(limit, 1000))
        query = select(TopicRecentMessage).where(
            TopicRecentMessage.scope_type == scope_type,
            TopicRecentMessage.chat_id == chat_id,
            TopicRecentMessage.topic_id == topic_id,
            TopicRecentMessage.user_id == user_id,
        )
        if max_age_seconds is not None:
            safe_age = max(1, max_age_seconds)
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=safe_age)
            query = query.where(TopicRecentMessage.created_at >= cutoff)

        rows = self._session.scalars(
            query.order_by(TopicRecentMessage.id.desc()).limit(safe_limit)
        ).all()
        rows = list(reversed(rows))
        return [self._to_recent_record(row) for row in rows]

    @staticmethod
    def _to_config_record(row: TopicAgentConfig) -> TopicAgentConfigRecord:
        return TopicAgentConfigRecord(
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=row.user_id,
            ai_enabled=row.ai_enabled,
            response_mode=row.response_mode,
            memory_retention_days=row.memory_retention_days,
            tools_enabled=row.tools_enabled,
            main_soul_text=row.main_soul_text,
            topic_soul_text=row.topic_soul_text,
            topic_soul_owner_only_edit=row.topic_soul_owner_only_edit,
            recent_context_window_size=max(0, min(int(getattr(row, "recent_context_window_size", 20)), 50)),
            image_analysis_mode=(getattr(row, "image_analysis_mode", "inherit") or "inherit").strip().lower(),
        )

    @staticmethod
    def _to_daily_record(row: TopicDailyMemory) -> TopicDailyMemoryRecord:
        return TopicDailyMemoryRecord(
            id=row.id,
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=row.user_id,
            memory_date=row.memory_date,
            summary_text=row.summary_text,
            tokens_estimate=row.tokens_estimate,
        )

    @staticmethod
    def _to_long_record(row: TopicLongMemory) -> TopicLongMemoryRecord:
        return TopicLongMemoryRecord(
            id=row.id,
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=row.user_id,
            fact_text=row.fact_text,
            is_active=row.is_active,
            source_daily_memory_id=row.source_daily_memory_id,
            promotion_status=row.promotion_status,
            answer_status=getattr(row, "answer_status", "legacy"),
        )

    @staticmethod
    def _to_session_record(row: TopicAiSession) -> TopicAiSessionRecord:
        payload: dict[str, object]
        try:
            raw = json.loads(row.session_payload_json or "{}")
            payload = raw if isinstance(raw, dict) else {}
        except json.JSONDecodeError:
            payload = {}

        return TopicAiSessionRecord(
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=row.user_id,
            session_payload=payload,
        )

    @staticmethod
    def _to_recent_record(row: TopicRecentMessage) -> TopicRecentMessageRecord:
        return TopicRecentMessageRecord(
            id=row.id,
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=row.user_id,
            message_text=row.message_text,
            telegram_message_id=getattr(row, "telegram_message_id", None),
            telegram_author_user_id=getattr(row, "telegram_author_user_id", None),
            telegram_author_username=getattr(row, "telegram_author_username", None),
            telegram_author_is_bot=bool(getattr(row, "telegram_author_is_bot", False)),
            source=getattr(row, "source", None) or "user",
            created_at=row.created_at,
        )

    def _trim_recent_scope(
        self,
        *,
        scope_type: str,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
    ) -> None:
        max_messages_per_scope = 50
        stale_rows = self._session.scalars(
            select(TopicRecentMessage.id)
            .where(
                TopicRecentMessage.scope_type == scope_type,
                TopicRecentMessage.chat_id == chat_id,
                TopicRecentMessage.topic_id == topic_id,
                TopicRecentMessage.user_id == user_id,
            )
            .order_by(TopicRecentMessage.id.desc())
            .offset(max_messages_per_scope)
        ).all()
        if stale_rows:
            self._session.query(TopicRecentMessage).filter(TopicRecentMessage.id.in_(stale_rows)).delete(
                synchronize_session=False
            )
