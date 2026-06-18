from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from amo_bot.auth.roles import Role
from amo_bot.core.context_filters import is_bot_authored_context_record, is_obvious_meta_status_message
from amo_bot.core.source_hosts import normalize_source_host
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
    PopgunAlertState,
    PopgunSetting,
    PopgunTopicSetting,
    PrivateChatPolicy,
    PromptContextDoc,
    ResearchEvalCase,
    ResearchProvider,
    ResearchProviderHealth,
    ResearchSourceObservation,
    ResearchSourcePreference,
    RetrievableMemory,
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


def _ensure_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


@dataclass(slots=True)
class PopgunTopicSettingSnapshot:
    chat_id: int
    thread_id: int | None
    enabled: bool
    symbols: list[str]
    timeframes: list[str]
    updated_at: str


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


@dataclass(slots=True)
class ResearchProviderHealthRecord:
    provider_name: str
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    rate_limit_count: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str = ""


@dataclass(frozen=True, slots=True)
class ResearchProviderRecord:
    provider_name: str
    source_name: str
    domain: str
    enabled: bool
    default_priority: int
    fallback_allowed: bool
    min_confidence: float
    max_age_seconds: int | None = None
    metadata: dict[str, object] | None = None
    selection_score: int | None = None
    health_penalty: int = 0
    observation_penalty: int = 0


@dataclass(frozen=True, slots=True)
class ResearchSourceObservationRecord:
    provider_name: str
    domain: str
    outcome: str
    confidence: float | None = None
    source_name: str | None = None
    source_hosts: tuple[str, ...] = ()
    source_count: int = 0
    warning_count: int = 0
    warning_codes: tuple[str, ...] = ()
    error_class: str | None = None
    timing_ms: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResearchEvalCaseRecord:
    case_key: str
    domain: str
    sanitized_prompt: str
    locale: str | None = None
    expected_status: str | None = None
    expected_metadata: dict[str, object] | None = None
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ResearchHostQualityRecord:
    host: str
    success_count: int = 0
    failure_count: int = 0
    warning_count: int = 0
    conflict_count: int = 0

    @property
    def usable(self) -> bool:
        return self.success_count > 0 and self.failure_count <= self.success_count + self.warning_count


@dataclass(frozen=True, slots=True)
class ResearchSourcePreferenceRecord:
    host: str
    domain: str
    signal: str
    weight: float
    scope_type: str = "global"
    chat_id: int | None = None
    topic_id: int | None = None
    user_id: int | None = None
    source: str = "feedback"
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def metadata(self) -> dict[str, object]:
        return {
            "source_preference_signal": self.signal,
            "source_preference_weight": self.weight,
            "source_preference_scope": self.scope_type,
            "source_preference_domain": self.domain,
            "source_preference_source": self.source,
        }


class ResearchProviderRepository:
    """Read configured research providers without exposing query or URL data."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_enabled_by_domain(self, domain: str) -> tuple[ResearchProviderRecord, ...]:
        normalized_domain = ResearchSourceObservationRepository._safe_label(
            domain,
            default="generic",
            max_len=64,
        )
        rows = self._session.scalars(
            select(ResearchProvider)
            .where(and_(ResearchProvider.domain == normalized_domain, ResearchProvider.enabled.is_(True)))
            .order_by(ResearchProvider.default_priority.asc(), ResearchProvider.provider_name.asc())
        ).all()
        return tuple(self._to_record(row) for row in rows)

    def list_ranked_by_domain(
        self,
        domain: str,
        *,
        observation_window: timedelta = timedelta(days=7),
        observation_limit: int = 500,
    ) -> tuple[ResearchProviderRecord, ...]:
        """Rank enabled providers from DB health and metadata-only observations."""

        normalized_domain = ResearchSourceObservationRepository._safe_label(
            domain,
            default="generic",
            max_len=64,
        )
        rows = self._session.scalars(
            select(ResearchProvider)
            .where(and_(ResearchProvider.domain == normalized_domain, ResearchProvider.enabled.is_(True)))
            .order_by(ResearchProvider.default_priority.asc(), ResearchProvider.provider_name.asc())
        ).all()
        if not rows:
            return ()

        providers = tuple(row.provider_name for row in rows)
        health_rows = self._session.scalars(
            select(ResearchProviderHealth).where(ResearchProviderHealth.provider_name.in_(providers))
        ).all()
        health_by_provider = {row.provider_name: row for row in health_rows}
        observation_penalties = self._observation_penalties(
            domain=normalized_domain,
            providers=providers,
            since=datetime.now(timezone.utc) - observation_window,
            limit=observation_limit,
        )

        ranked: list[ResearchProviderRecord] = []
        for row in rows:
            health_penalty = self._health_penalty(health_by_provider.get(row.provider_name))
            observation_penalty = observation_penalties.get(row.provider_name, 0)
            record = self._to_record(row)
            score = record.default_priority + health_penalty + observation_penalty
            ranked.append(
                ResearchProviderRecord(
                    provider_name=record.provider_name,
                    source_name=record.source_name,
                    domain=record.domain,
                    enabled=record.enabled,
                    default_priority=record.default_priority,
                    fallback_allowed=record.fallback_allowed,
                    min_confidence=record.min_confidence,
                    max_age_seconds=record.max_age_seconds,
                    metadata=record.metadata,
                    selection_score=score,
                    health_penalty=health_penalty,
                    observation_penalty=observation_penalty,
                )
            )
        return tuple(
            sorted(
                ranked,
                key=lambda record: (
                    record.selection_score if record.selection_score is not None else record.default_priority,
                    record.provider_name,
                ),
            )
        )

    @staticmethod
    def _to_record(row: ResearchProvider) -> ResearchProviderRecord:
        metadata: dict[str, object] | None = None
        if row.metadata_json:
            try:
                parsed = json.loads(row.metadata_json)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                metadata = {str(key): value for key, value in parsed.items() if isinstance(value, (str, int, float, bool)) or value is None}
        return ResearchProviderRecord(
            provider_name=row.provider_name,
            source_name=row.source_name,
            domain=row.domain,
            enabled=bool(row.enabled),
            default_priority=int(row.default_priority or 100),
            fallback_allowed=bool(row.fallback_allowed),
            min_confidence=float(row.min_confidence or 0.0),
            max_age_seconds=row.max_age_seconds,
            metadata=metadata,
        )

    @staticmethod
    def _health_penalty(row: ResearchProviderHealth | None) -> int:
        if row is None:
            return 0
        penalty = min(int(row.failure_count or 0), 5) * 10
        penalty += min(int(row.timeout_count or 0), 3) * 8
        penalty += min(int(row.rate_limit_count or 0), 3) * 12
        last_failure = _ensure_aware_utc(row.last_failure_at)
        last_success = _ensure_aware_utc(row.last_success_at)
        now = datetime.now(timezone.utc)
        if last_failure and now - last_failure < timedelta(minutes=15):
            penalty += 30
        if last_success and now - last_success < timedelta(minutes=15):
            penalty -= 10
        return penalty

    def _observation_penalties(
        self,
        *,
        domain: str,
        providers: Sequence[str],
        since: datetime,
        limit: int,
    ) -> dict[str, int]:
        if not providers:
            return {}
        rows = self._session.scalars(
            select(ResearchSourceObservation)
            .where(
                and_(
                    ResearchSourceObservation.domain == domain,
                    ResearchSourceObservation.provider_name.in_(tuple(providers)),
                    ResearchSourceObservation.created_at >= since,
                )
            )
            .order_by(ResearchSourceObservation.created_at.desc())
            .limit(max(1, min(int(limit), 2000)))
        ).all()
        stats: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "success": 0.0,
                "failure": 0.0,
                "warning": 0.0,
                "conflict": 0.0,
                "confidence_sum": 0.0,
                "confidence_count": 0.0,
            }
        )
        for row in rows:
            provider = row.provider_name
            warnings = ResearchSourceObservationRepository._warning_codes(row.warning_codes_json)
            outcome = (row.outcome or "").strip().lower()
            if outcome in {"confirmed", "allow", "search_completed", "scrape_completed", "browser_completed"}:
                stats[provider]["success"] += 1
            elif outcome and outcome != "unknown":
                stats[provider]["failure"] += 1
            stats[provider]["warning"] += len(warnings)
            if any("conflict" in warning or "mismatch" in warning for warning in warnings):
                stats[provider]["conflict"] += 1
            if row.confidence is not None:
                stats[provider]["confidence_sum"] += max(0.0, min(1.0, float(row.confidence)))
                stats[provider]["confidence_count"] += 1

        penalties: dict[str, int] = {}
        for provider, values in stats.items():
            penalty = 0
            penalty += int(min(values["failure"], 5) * 8)
            penalty += int(min(values["warning"], 10))
            penalty += int(min(values["conflict"], 3) * 15)
            penalty -= int(min(values["success"], 5) * 3)
            if values["confidence_count"]:
                average_confidence = values["confidence_sum"] / values["confidence_count"]
                if average_confidence < 0.5:
                    penalty += 10
                elif average_confidence >= 0.85:
                    penalty -= 4
            penalties[provider] = penalty
        return penalties


class ResearchProviderHealthRepository:
    """Persist provider health without storing query text, URLs, prompts, or secrets."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def load_provider_health(self, provider_name: str) -> ResearchProviderHealthRecord:
        normalized_name = self._normalize_provider_name(provider_name)
        row = self._session.scalar(
            select(ResearchProviderHealth).where(ResearchProviderHealth.provider_name == normalized_name)
        )
        if row is None:
            return ResearchProviderHealthRecord(provider_name=normalized_name)
        return self._to_record(row)

    def record_success(self, provider_name: str, *, occurred_at: datetime | None = None) -> ResearchProviderHealthRecord:
        when = occurred_at or datetime.now(timezone.utc)
        row = self._get_or_create(provider_name)
        normalized_name = row.provider_name
        self._session.execute(
            update(ResearchProviderHealth)
            .where(ResearchProviderHealth.provider_name == normalized_name)
            .values(
                success_count=func.coalesce(ResearchProviderHealth.success_count, 0) + 1,
                last_success_at=when,
                last_error="",
                updated_at=when,
            )
            .execution_options(synchronize_session=False)
        )
        self._session.commit()
        return self.load_provider_health(normalized_name)

    def record_failure(
        self,
        provider_name: str,
        *,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> ResearchProviderHealthRecord:
        return self._record_negative(provider_name, reason=reason, occurred_at=occurred_at)

    def record_timeout(
        self,
        provider_name: str,
        *,
        reason: str = "timeout",
        occurred_at: datetime | None = None,
    ) -> ResearchProviderHealthRecord:
        return self._record_negative(provider_name, reason=reason, occurred_at=occurred_at, timeout=True)

    def record_rate_limit(
        self,
        provider_name: str,
        *,
        reason: str = "rate_limit",
        occurred_at: datetime | None = None,
    ) -> ResearchProviderHealthRecord:
        return self._record_negative(provider_name, reason=reason, occurred_at=occurred_at, rate_limit=True)

    @staticmethod
    def _normalize_provider_name(provider_name: str) -> str:
        normalized = (provider_name or "").strip()
        if not normalized:
            raise ValueError("provider_name is required")
        return normalized[:128]

    @classmethod
    def _to_record(cls, row: ResearchProviderHealth) -> ResearchProviderHealthRecord:
        return ResearchProviderHealthRecord(
            provider_name=row.provider_name,
            success_count=int(row.success_count or 0),
            failure_count=int(row.failure_count or 0),
            timeout_count=int(row.timeout_count or 0),
            rate_limit_count=int(row.rate_limit_count or 0),
            last_success_at=row.last_success_at,
            last_failure_at=row.last_failure_at,
            last_error=row.last_error or "",
        )

    def _get_or_create(self, provider_name: str) -> ResearchProviderHealth:
        normalized_name = self._normalize_provider_name(provider_name)
        row = self._session.scalar(
            select(ResearchProviderHealth).where(ResearchProviderHealth.provider_name == normalized_name)
        )
        if row is not None:
            return row
        row = ResearchProviderHealth(provider_name=normalized_name)
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            row = self._session.scalar(
                select(ResearchProviderHealth).where(ResearchProviderHealth.provider_name == normalized_name)
            )
            if row is None:
                raise
        return row

    def _record_negative(
        self,
        provider_name: str,
        *,
        reason: str,
        occurred_at: datetime | None,
        timeout: bool = False,
        rate_limit: bool = False,
    ) -> ResearchProviderHealthRecord:
        when = occurred_at or datetime.now(timezone.utc)
        row = self._get_or_create(provider_name)
        normalized_name = row.provider_name
        values = {
            "failure_count": func.coalesce(ResearchProviderHealth.failure_count, 0) + 1,
            "last_failure_at": when,
            "last_error": (reason or "failure")[:512],
            "updated_at": when,
        }
        if timeout:
            values["timeout_count"] = func.coalesce(ResearchProviderHealth.timeout_count, 0) + 1
        if rate_limit:
            values["rate_limit_count"] = func.coalesce(ResearchProviderHealth.rate_limit_count, 0) + 1
        self._session.execute(
            update(ResearchProviderHealth)
            .where(ResearchProviderHealth.provider_name == normalized_name)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        self._session.commit()
        return self.load_provider_health(normalized_name)


class ResearchSourceObservationRepository:
    """Write metadata-only research source observations.

    Stored fields intentionally exclude raw user queries, full URLs, prompts,
    message text, and secrets. Source details are reduced to hostnames.
    """

    _SAFE_CODE_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")
    _URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
    _HOSTLIKE_RE = re.compile(r"(?:^|[^a-z0-9-])(?:[a-z0-9-]+\.)+[a-z]{2,}(?:\b|[/:?#])", re.I)
    _SENSITIVE_KEY_RE = re.compile(
        r"(?:query|prompt|message|raw|text|secret|token|api[_-]?key|password|authorization|bearer|url|uri|link|endpoint)",
        re.I,
    )
    _SENSITIVE_VALUE_RE = re.compile(
        r"(?:"
        r"bearer\s+[a-z0-9._~+/=-]{6,}|"
        r"(?:^|[^a-z0-9])(?:api[_-]?key|token|secret|password|authorization|bearer)[^a-z0-9]?[a-z0-9][a-z0-9._~+/=-]{5,}"
        r")",
        re.I,
    )
    _ERROR_CLASS_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Timeout|Failure))\b")

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_observation(
        self,
        *,
        provider_name: str,
        domain: str,
        outcome: str,
        confidence: float | None = None,
        source_name: str | None = None,
        source_hosts: Sequence[str] | None = None,
        source_urls: Sequence[str] | None = None,
        source_count: int | None = None,
        warning_codes: Sequence[str] | None = None,
        warning_count: int | None = None,
        error_class: str | None = None,
        timing_ms: int | None = None,
        created_at: datetime | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> ResearchSourceObservationRecord:
        hosts = self._safe_hosts((*tuple(source_hosts or ()), *tuple(source_urls or ())))
        warnings = self._safe_codes(warning_codes or ())
        normalized_provider = self._safe_label(provider_name, default="unknown_provider", max_len=128)
        normalized_domain = self._safe_label(domain, default="generic", max_len=64)
        normalized_outcome = self._safe_label(outcome, default="unknown", max_len=64)
        normalized_source_name = self._safe_optional_label(source_name, max_len=128)
        normalized_error_class = self._safe_error_class(error_class)
        normalized_timing = max(0, int(timing_ms)) if timing_ms is not None else None
        normalized_confidence = self._safe_confidence(confidence)
        normalized_source_count = max(0, int(source_count if source_count is not None else len(hosts)))
        normalized_warning_count = max(0, int(warning_count if warning_count is not None else len(warnings)))
        when = created_at or datetime.now(timezone.utc)

        safe_metadata = {
            "source_count": normalized_source_count,
            "warning_count": normalized_warning_count,
            "source_hosts": list(hosts[:10]),
        }
        if normalized_error_class:
            safe_metadata["error_class"] = normalized_error_class
        if normalized_timing is not None:
            safe_metadata["timing_ms"] = normalized_timing
        for key, value in (metadata or {}).items():
            metadata_key = self._safe_metadata_key(str(key))
            if not metadata_key:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe_metadata[metadata_key] = self._safe_metadata_value(value, key=metadata_key)

        self._session.add(
            ResearchSourceObservation(
                provider_name=normalized_provider,
                source_name=normalized_source_name,
                domain=normalized_domain,
                outcome=normalized_outcome,
                confidence=normalized_confidence,
                warning_codes_json=json.dumps(list(warnings), sort_keys=True),
                metadata_json=json.dumps(safe_metadata, sort_keys=True),
                created_at=when,
            )
        )
        self._session.commit()
        return ResearchSourceObservationRecord(
            provider_name=normalized_provider,
            source_name=normalized_source_name,
            domain=normalized_domain,
            outcome=normalized_outcome,
            confidence=normalized_confidence,
            source_hosts=hosts,
            source_count=normalized_source_count,
            warning_count=normalized_warning_count,
            warning_codes=warnings,
            error_class=normalized_error_class,
            timing_ms=normalized_timing,
            created_at=when,
        )

    def assess_recent_hosts(
        self,
        *,
        domain: str,
        source_hosts: Sequence[str],
        since: datetime | None = None,
        limit: int = 250,
    ) -> tuple[ResearchHostQualityRecord, ...]:
        hosts = set(self._safe_hosts(source_hosts))
        if not hosts:
            return ()
        normalized_domain = self._safe_label(domain, default="generic", max_len=64)
        conditions = [ResearchSourceObservation.domain == normalized_domain]
        if since is not None:
            conditions.append(ResearchSourceObservation.created_at >= since)
        rows = self._session.scalars(
            select(ResearchSourceObservation)
            .where(and_(*conditions))
            .order_by(ResearchSourceObservation.created_at.desc())
            .limit(max(1, min(int(limit), 1000)))
        ).all()

        counters = {host: {"success": 0, "failure": 0, "warning": 0, "conflict": 0} for host in hosts}
        for row in rows:
            row_hosts = self._metadata_hosts(row.metadata_json)
            matched = hosts.intersection(row_hosts)
            if not matched:
                continue
            warnings = self._warning_codes(row.warning_codes_json)
            outcome = (row.outcome or "").strip().lower()
            is_success = outcome in {"confirmed", "allow", "search_completed", "scrape_completed", "browser_completed"}
            is_failure = not is_success and outcome not in {"unknown"}
            has_conflict = any("conflict" in warning or "mismatch" in warning for warning in warnings)
            for host in matched:
                if is_success:
                    counters[host]["success"] += 1
                if is_failure:
                    counters[host]["failure"] += 1
                counters[host]["warning"] += len(warnings)
                if has_conflict:
                    counters[host]["conflict"] += 1

        return tuple(
            ResearchHostQualityRecord(
                host=host,
                success_count=values["success"],
                failure_count=values["failure"],
                warning_count=values["warning"],
                conflict_count=values["conflict"],
            )
            for host, values in sorted(counters.items())
        )

    def list_reliable_hosts(
        self,
        *,
        domain: str,
        since: datetime | None = None,
        limit: int = 500,
        max_hosts: int = 5,
        min_success_count: int = 1,
    ) -> tuple[ResearchHostQualityRecord, ...]:
        """Return recently successful hosts for a domain from metadata-only observations."""

        normalized_domain = self._safe_label(domain, default="generic", max_len=64)
        conditions = [ResearchSourceObservation.domain == normalized_domain]
        if since is not None:
            conditions.append(ResearchSourceObservation.created_at >= since)
        rows = self._session.scalars(
            select(ResearchSourceObservation)
            .where(and_(*conditions))
            .order_by(ResearchSourceObservation.created_at.desc())
            .limit(max(1, min(int(limit), 2000)))
        ).all()

        counters: dict[str, dict[str, int]] = defaultdict(
            lambda: {"success": 0, "failure": 0, "warning": 0, "conflict": 0}
        )
        for row in rows:
            hosts = self._metadata_hosts(row.metadata_json)
            if not hosts:
                continue
            warnings = self._warning_codes(row.warning_codes_json)
            outcome = (row.outcome or "").strip().lower()
            is_success = outcome in {"confirmed", "allow", "search_completed", "scrape_completed", "browser_completed"}
            is_failure = not is_success and outcome not in {"unknown"}
            has_conflict = any("conflict" in warning or "mismatch" in warning for warning in warnings)
            for host in hosts:
                if is_success:
                    counters[host]["success"] += 1
                if is_failure:
                    counters[host]["failure"] += 1
                counters[host]["warning"] += len(warnings)
                if has_conflict:
                    counters[host]["conflict"] += 1

        records = [
            ResearchHostQualityRecord(
                host=host,
                success_count=values["success"],
                failure_count=values["failure"],
                warning_count=values["warning"],
                conflict_count=values["conflict"],
            )
            for host, values in counters.items()
            if values["success"] >= max(1, int(min_success_count))
            and values["failure"] == 0
            and values["warning"] == 0
            and values["conflict"] == 0
        ]
        return tuple(
            sorted(
                records,
                key=lambda record: (
                    -(record.success_count - record.failure_count - record.conflict_count),
                    record.warning_count,
                    record.host,
                ),
            )[: max(1, min(int(max_hosts), 20))]
        )

    @classmethod
    def _metadata_hosts(cls, metadata_json: str | None) -> tuple[str, ...]:
        if not metadata_json:
            return ()
        try:
            payload = json.loads(metadata_json)
        except json.JSONDecodeError:
            return ()
        if not isinstance(payload, dict):
            return ()
        raw_hosts = payload.get("source_hosts")
        if not isinstance(raw_hosts, list):
            return ()
        return cls._safe_hosts(tuple(str(host) for host in raw_hosts))

    @classmethod
    def _warning_codes(cls, warning_codes_json: str | None) -> tuple[str, ...]:
        if not warning_codes_json:
            return ()
        try:
            payload = json.loads(warning_codes_json)
        except json.JSONDecodeError:
            return ()
        if not isinstance(payload, list):
            return ()
        return cls._safe_codes(tuple(str(item) for item in payload))

    @classmethod
    def _safe_label(cls, value: str | None, *, default: str, max_len: int) -> str:
        raw = (value or "").strip()
        if cls._contains_unsafe_text(raw):
            return default[:max_len]
        cleaned = cls._SAFE_CODE_RE.sub("_", raw)
        cleaned = cleaned.strip("._:-")
        return (cleaned or default)[:max_len]

    @classmethod
    def _safe_optional_label(cls, value: str | None, *, max_len: int) -> str | None:
        if not value:
            return None
        if cls._contains_unsafe_text(value):
            return None
        cleaned = cls._safe_label(value, default="", max_len=max_len)
        return cleaned or None

    @classmethod
    def _safe_codes(cls, values: Sequence[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            code = cls._safe_optional_label(str(value), max_len=96)
            if not code or code in seen:
                continue
            seen.add(code)
            result.append(code)
        return tuple(result[:20])

    @staticmethod
    def _safe_confidence(value: float | None) -> float | None:
        if value is None:
            return None
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _safe_hosts(cls, values: Sequence[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            host = cls._extract_host(value)
            if not host or host in seen:
                continue
            seen.add(host)
            result.append(host)
        return tuple(result[:10])

    @staticmethod
    def _extract_host(value: str) -> str:
        return normalize_source_host(value)

    @classmethod
    def _safe_metadata_key(cls, key: str) -> str | None:
        if cls._SENSITIVE_KEY_RE.search(key or ""):
            return None
        return cls._safe_optional_label(key, max_len=64)

    @classmethod
    def _safe_metadata_value(cls, value: object, *, key: str) -> object:
        if isinstance(value, str):
            if cls._contains_unsafe_text(value):
                return "redacted"
            if not cls._is_categorical_metadata_value(key=key, value=value):
                return "present"
            cleaned = cls._safe_label(value, default="value", max_len=64)
            return cleaned if cleaned != "value" else "present"
        return value

    @staticmethod
    def _is_categorical_metadata_value(*, key: str, value: str) -> bool:
        raw = (value or "").strip()
        if not raw or len(raw) > 64 or re.search(r"\s", raw):
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9_.:-]+", raw)) and any(
            marker in key
            for marker in (
                "status",
                "code",
                "reason",
                "decision",
                "operation",
                "outcome",
                "domain",
                "provider",
                "class",
                "type",
            )
        )

    @classmethod
    def _safe_error_class(cls, value: str | None) -> str | None:
        if not value:
            return None
        match = cls._ERROR_CLASS_RE.search(value)
        if match:
            return match.group(1)[:128]
        return cls._safe_optional_label(value, max_len=128)

    @classmethod
    def _contains_unsafe_text(cls, value: str | None) -> bool:
        raw = (value or "").strip()
        if not raw:
            return False
        return bool(cls._URL_RE.search(raw) or cls._HOSTLIKE_RE.search(raw) or cls._SENSITIVE_VALUE_RE.search(raw))


class ResearchSourcePreferenceRepository:
    """Store metadata-only source preferences and expose ranking hints by host."""

    _SIGNAL_WEIGHTS = {
        "preferred": -0.75,
        "trusted": -0.9,
        "avoid": 1.0,
        "rejected": 1.35,
        "low_quality": 0.9,
        "negative": 1.0,
    }
    _POSITIVE_SIGNALS = {"preferred", "trusted"}
    _NEGATIVE_SIGNALS = {"avoid", "rejected", "low_quality", "negative"}

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_preference(
        self,
        *,
        host: str | None = None,
        source_url: str | None = None,
        domain: str = "generic",
        signal: str,
        weight: float | None = None,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        source: str = "feedback",
        created_at: datetime | None = None,
    ) -> ResearchSourcePreferenceRecord:
        normalized_host = ResearchSourceObservationRepository._extract_host(host or source_url or "")
        if not normalized_host:
            raise ValueError("source preference requires a valid host")
        normalized_domain = ResearchSourceObservationRepository._safe_label(domain, default="generic", max_len=64)
        normalized_signal = self._safe_signal(signal)
        normalized_source = ResearchSourceObservationRepository._safe_label(source, default="feedback", max_len=32)
        normalized_weight = self._safe_weight(weight if weight is not None else self._SIGNAL_WEIGHTS[normalized_signal])
        scope_type = self._scope_type(chat_id=chat_id, topic_id=topic_id, user_id=user_id)
        when = created_at or datetime.now(timezone.utc)

        existing = self._find_existing(
            host=normalized_host,
            domain=normalized_domain,
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
        )
        if existing is None:
            row = ResearchSourcePreference(
                host=normalized_host,
                domain=normalized_domain,
                signal=normalized_signal,
                weight=normalized_weight,
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
                source=normalized_source,
                created_at=when,
                updated_at=when,
            )
            self._session.add(row)
        else:
            row = existing
            row.signal = normalized_signal
            row.weight = normalized_weight
            row.source = normalized_source
            row.updated_at = when
        self._session.commit()
        return self._to_record(row)

    def list_for_hosts(
        self,
        *,
        source_hosts: Sequence[str],
        domain: str = "generic",
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        include_observations: bool = True,
        observation_since: datetime | None = None,
    ) -> dict[str, ResearchSourcePreferenceRecord]:
        hosts = ResearchSourceObservationRepository._safe_hosts(source_hosts)
        if not hosts:
            return {}
        normalized_domain = ResearchSourceObservationRepository._safe_label(domain, default="generic", max_len=64)
        rows = self._session.scalars(
            select(ResearchSourcePreference)
            .where(
                and_(
                    ResearchSourcePreference.host.in_(hosts),
                    ResearchSourcePreference.domain.in_((normalized_domain, "generic")),
                    self._scope_filter(chat_id=chat_id, topic_id=topic_id, user_id=user_id),
                )
            )
            .order_by(ResearchSourcePreference.updated_at.desc(), ResearchSourcePreference.created_at.desc())
        ).all()

        by_host: dict[str, ResearchSourcePreferenceRecord] = {}
        best_keys: dict[str, tuple[int, int, float]] = {}
        for row in rows:
            record = self._to_record(row)
            key = (
                1 if record.domain == normalized_domain else 0,
                self._specificity(record),
                (record.updated_at or record.created_at or datetime.min).timestamp(),
            )
            if key > best_keys.get(record.host, (-1, -1, float("-inf"))):
                by_host[record.host] = record
                best_keys[record.host] = key

        if include_observations:
            observation_records = self._observation_preferences(
                hosts=hosts,
                domain=normalized_domain,
                since=observation_since,
            )
            for host, record in observation_records.items():
                by_host.setdefault(host, record)
        return by_host

    def _observation_preferences(
        self,
        *,
        hosts: Sequence[str],
        domain: str,
        since: datetime | None,
    ) -> dict[str, ResearchSourcePreferenceRecord]:
        qualities = ResearchSourceObservationRepository(self._session).assess_recent_hosts(
            domain=domain,
            source_hosts=hosts,
            since=since,
        )
        result: dict[str, ResearchSourcePreferenceRecord] = {}
        for quality in qualities:
            if quality.success_count <= 0 and quality.failure_count <= 0 and quality.warning_count <= 0:
                continue
            if quality.failure_count > quality.success_count or quality.conflict_count > 0:
                signal = "low_quality"
                weight = min(1.1, 0.35 + quality.failure_count * 0.2 + quality.conflict_count * 0.3)
            elif quality.success_count > quality.failure_count + quality.warning_count:
                signal = "trusted"
                weight = -min(0.45, 0.2 + quality.success_count * 0.08)
            else:
                continue
            result[quality.host] = ResearchSourcePreferenceRecord(
                host=quality.host,
                domain=domain,
                signal=signal,
                weight=weight,
                source="observation",
            )
        return result

    def _find_existing(
        self,
        *,
        host: str,
        domain: str,
        scope_type: str,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
    ) -> ResearchSourcePreference | None:
        return self._session.scalar(
            select(ResearchSourcePreference).where(
                and_(
                    ResearchSourcePreference.host == host,
                    ResearchSourcePreference.domain == domain,
                    ResearchSourcePreference.scope_type == scope_type,
                    ResearchSourcePreference.chat_id.is_(None)
                    if chat_id is None
                    else ResearchSourcePreference.chat_id == chat_id,
                    ResearchSourcePreference.topic_id.is_(None)
                    if topic_id is None
                    else ResearchSourcePreference.topic_id == topic_id,
                    ResearchSourcePreference.user_id.is_(None)
                    if user_id is None
                    else ResearchSourcePreference.user_id == user_id,
                )
            )
        )

    @staticmethod
    def _scope_filter(*, chat_id: int | None, topic_id: int | None, user_id: int | None):  # noqa: ANN205
        filters = [
            ResearchSourcePreference.scope_type == "global",
        ]
        if user_id is not None:
            filters.append(and_(ResearchSourcePreference.scope_type == "user", ResearchSourcePreference.user_id == user_id))
        if chat_id is not None:
            filters.append(and_(ResearchSourcePreference.scope_type == "chat", ResearchSourcePreference.chat_id == chat_id))
        if chat_id is not None and topic_id is not None:
            filters.append(
                and_(
                    ResearchSourcePreference.scope_type == "topic",
                    ResearchSourcePreference.chat_id == chat_id,
                    ResearchSourcePreference.topic_id == topic_id,
                )
            )
        return or_(*filters)

    @staticmethod
    def _scope_type(*, chat_id: int | None, topic_id: int | None, user_id: int | None) -> str:
        if chat_id is not None and topic_id is not None:
            return "topic"
        if chat_id is not None:
            return "chat"
        if user_id is not None:
            return "user"
        return "global"

    @classmethod
    def _safe_signal(cls, signal: str) -> str:
        normalized = ResearchSourceObservationRepository._safe_label(signal, default="preferred", max_len=32).casefold()
        aliases = {
            "prefer": "preferred",
            "good": "preferred",
            "trust": "trusted",
            "trusted": "trusted",
            "avoid": "avoid",
            "bad": "low_quality",
            "weak": "low_quality",
            "mull": "low_quality",
            "trash": "low_quality",
            "reject": "rejected",
            "rejected": "rejected",
            "negative": "negative",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in cls._POSITIVE_SIGNALS | cls._NEGATIVE_SIGNALS:
            return "preferred"
        return normalized

    @staticmethod
    def _safe_weight(value: float) -> float:
        try:
            return max(-2.0, min(2.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _specificity(record: ResearchSourcePreferenceRecord) -> int:
        return {"global": 0, "user": 1, "chat": 2, "topic": 3}.get(record.scope_type, 0)

    @staticmethod
    def _to_record(row: ResearchSourcePreference) -> ResearchSourcePreferenceRecord:
        return ResearchSourcePreferenceRecord(
            host=row.host,
            domain=row.domain,
            signal=row.signal,
            weight=float(row.weight),
            scope_type=row.scope_type,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            user_id=row.user_id,
            source=row.source,
            created_at=_ensure_aware_utc(row.created_at),
            updated_at=_ensure_aware_utc(row.updated_at),
        )


class ResearchEvalCaseRepository:
    """Persist sanitized research eval cases without retaining raw chat text."""

    _SAFE_SUMMARY_RE = re.compile(r"https?://\S+|(?:[a-z0-9-]+\.)+[a-z]{2,}", re.I)
    _DOMAIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("weather", re.compile(r"\b(?:wetter|weather|temperatur|temperature|regen|rain|forecast|vorhersage)\b", re.I)),
        ("sports", re.compile(r"\b(?:fußball|fussball|football|soccer|wm|weltmeisterschaft|world\s+cup|bundesliga|champions\s+league|tabelle|standings?|spielplan|fixtures?|score|ergebnis(?:se)?)\b", re.I)),
        ("crypto", re.compile(r"\b(?:btc|bitcoin|eth|ethereum|crypto|krypto|kryptow(?:ä|ae)hrung|coin|token|blockchain)\b", re.I)),
        ("finance", re.compile(r"\b(?:aktie|stock|share|shares|börse|boerse|nasdaq|nyse|dax|etf|fundamental|research|dividende|earnings|kurs|price|preis)\b", re.I)),
        ("news", re.compile(r"\b(?:news|nachrichten|breaking|latest|neueste(?:n)?|meldung(?:en)?|presse|bericht(?:e)?)\b", re.I)),
        ("local_info", re.compile(r"\b(?:in der n(?:ä|ae)he|near me|adresse|address|öffnungszeit(?:en)?|oeffnungszeit(?:en)?|restaurant|laden|geschäft|geschaeft|lokal|local)\b", re.I)),
        ("generic_current_facts", re.compile(r"\b(?:aktuell|current|heute|today|jetzt|now|202\d|neueste(?:n)?|latest|status|stand)\b", re.I)),
    )
    _FAILURE_CLASS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("source_quality", re.compile(r"\b(?:quelle|source|domain|seite|website|link|url|abschreiber|primärquelle|primaerquelle|corroborat|korrobor)\b", re.I)),
        ("insufficient_evidence", re.compile(r"\b(?:nicht genug|not enough|zu wenig|nur eine(?:r)? quelle|single[-\s]?source|oberfl(?:ä|ae)chlich|reicht nicht)\b", re.I)),
        ("incorrect_answer", re.compile(r"\b(?:falsch|wrong|incorrect|stimmt nicht|nicht korrekt|korrigier|correction|halluzin)\b", re.I)),
        ("stale_data", re.compile(r"\b(?:veraltet|stale|old|nicht aktuell|outdated|expired|alter stand)\b", re.I)),
        ("routing", re.compile(r"\b(?:nicht gesucht|nicht recherchiert|search weiter|such weiter|andere quellen|browser|scrape)\b", re.I)),
    )

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_from_negative_feedback(
        self,
        *,
        sanitized_summary: str,
        failure_label: str,
        domain: str = "generic",
        locale: str | None = None,
        evidence_status: str | None = None,
        source_hosts: Sequence[str] | None = None,
        expected_behavior: str = "improve future research answer quality without treating feedback as fact",
        enabled: bool = True,
    ) -> ResearchEvalCaseRecord:
        summary = self._sanitize_summary(sanitized_summary)
        if not summary:
            raise ValueError("sanitized_summary is required")
        normalized_failure = ResearchSourceObservationRepository._safe_label(
            failure_label,
            default="negative_feedback",
            max_len=64,
        )
        normalized_domain = self._classify_domain(summary, fallback=domain)
        failure_class = self._classify_failure_class(summary, fallback=normalized_failure)
        normalized_status = ResearchSourceObservationRepository._safe_optional_label(evidence_status, max_len=64)
        hosts = ResearchSourceObservationRepository._safe_hosts(source_hosts or ())
        expected_metadata = {
            "failure_label": normalized_failure,
            "failure_class": failure_class,
            "source_hosts": list(hosts),
            "expected_behavior": self._sanitize_summary(expected_behavior, max_len=256),
        }
        if normalized_status:
            expected_metadata["evidence_status"] = normalized_status
        case_key = self._case_key(
            domain=normalized_domain,
            summary=summary,
            failure_label=normalized_failure,
            evidence_status=normalized_status,
            source_hosts=hosts,
        )

        row = self._session.scalar(select(ResearchEvalCase).where(ResearchEvalCase.case_key == case_key))
        if row is None:
            row = ResearchEvalCase(
                case_key=case_key,
                domain=normalized_domain,
                locale=ResearchSourceObservationRepository._safe_optional_label(locale, max_len=16),
                sanitized_prompt=summary,
                expected_status=normalized_status or "needs_improvement",
                expected_metadata_json=json.dumps(expected_metadata, sort_keys=True),
                enabled=enabled,
            )
            self._session.add(row)
        else:
            row.domain = normalized_domain
            row.locale = ResearchSourceObservationRepository._safe_optional_label(locale, max_len=16)
            row.sanitized_prompt = summary
            row.expected_status = normalized_status or "needs_improvement"
            row.expected_metadata_json = json.dumps(expected_metadata, sort_keys=True)
            row.enabled = enabled
        self._session.commit()
        return ResearchEvalCaseRecord(
            case_key=case_key,
            domain=normalized_domain,
            locale=ResearchSourceObservationRepository._safe_optional_label(locale, max_len=16),
            sanitized_prompt=summary,
            expected_status=normalized_status or "needs_improvement",
            expected_metadata=expected_metadata,
            enabled=enabled,
        )

    def list_enabled(self, *, domain: str | None = None, limit: int = 100) -> tuple[ResearchEvalCaseRecord, ...]:
        conditions = [ResearchEvalCase.enabled.is_(True)]
        if domain:
            conditions.append(
                ResearchEvalCase.domain
                == ResearchSourceObservationRepository._safe_label(domain, default="generic", max_len=64)
            )
        rows = self._session.scalars(
            select(ResearchEvalCase)
            .where(and_(*conditions))
            .order_by(ResearchEvalCase.updated_at.desc(), ResearchEvalCase.id.desc())
            .limit(max(1, min(int(limit), 500)))
        ).all()
        return tuple(self._to_record(row) for row in rows)

    @classmethod
    def _to_record(cls, row: ResearchEvalCase) -> ResearchEvalCaseRecord:
        metadata: dict[str, object] | None = None
        if row.expected_metadata_json:
            try:
                parsed = json.loads(row.expected_metadata_json)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                metadata = parsed
        return ResearchEvalCaseRecord(
            case_key=row.case_key,
            domain=row.domain,
            locale=row.locale,
            sanitized_prompt=row.sanitized_prompt,
            expected_status=row.expected_status,
            expected_metadata=metadata,
            enabled=bool(row.enabled),
        )

    @classmethod
    def _sanitize_summary(cls, value: str, *, max_len: int = 512) -> str:
        cleaned = cls._SAFE_SUMMARY_RE.sub("[redacted-source]", value or "")
        cleaned = re.sub(r"\b(?:api[_-]?key|token|secret|password|authorization|bearer)\b\s*[:=]?\s*\S+", "[redacted-secret]", cleaned, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:max_len].rstrip()

    @classmethod
    def _classify_domain(cls, summary: str, *, fallback: str) -> str:
        normalized_fallback = ResearchSourceObservationRepository._safe_label(
            fallback,
            default="generic",
            max_len=64,
        )
        if normalized_fallback not in {"generic", "source_quality", "analysis"}:
            return normalized_fallback
        for domain, pattern in cls._DOMAIN_PATTERNS:
            if pattern.search(summary):
                if normalized_fallback == "source_quality" and domain == "generic_current_facts":
                    continue
                return domain
        return normalized_fallback

    @classmethod
    def _classify_failure_class(cls, summary: str, *, fallback: str) -> str:
        for failure_class, pattern in cls._FAILURE_CLASS_PATTERNS:
            if pattern.search(summary):
                return failure_class
        normalized_fallback = ResearchSourceObservationRepository._safe_label(
            fallback,
            default="negative_feedback",
            max_len=64,
        )
        if normalized_fallback == "negative_reaction_feedback":
            return "answer_quality_risk"
        if normalized_fallback == "negative_answer_feedback":
            return "answer_quality"
        if normalized_fallback == "source_quality_feedback":
            return "source_quality"
        return normalized_fallback

    @staticmethod
    def _case_key(
        *,
        domain: str,
        summary: str,
        failure_label: str,
        evidence_status: str | None,
        source_hosts: Sequence[str],
    ) -> str:
        digest = sha256(
            json.dumps(
                {
                    "domain": domain,
                    "summary": summary,
                    "failure_label": failure_label,
                    "evidence_status": evidence_status,
                    "source_hosts": list(source_hosts),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:24]
        return f"feedback:{domain}:{digest}"[:128]


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


class PopgunRepository:
    DEFAULTS_KEY = "defaults"
    LEGACY_IMPORT_KEY = "legacy_state_import"

    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def topic_key(chat_id: int, thread_id: int | None) -> str:
        return f"{chat_id}:{thread_id if thread_id is not None else 'root'}"

    def ensure_defaults(self, *, symbols: list[str], timeframes: list[str]) -> None:
        row = self._session.scalar(select(PopgunSetting).where(PopgunSetting.key == self.DEFAULTS_KEY))
        if row is None:
            self._session.add(
                PopgunSetting(
                    key=self.DEFAULTS_KEY,
                    value_json=json.dumps(
                        {"default_symbols": symbols, "default_timeframes": timeframes},
                        sort_keys=True,
                    ),
                )
            )
            self._session.commit()

    def set_defaults(self, *, symbols: list[str], timeframes: list[str]) -> None:
        payload = json.dumps({"default_symbols": symbols, "default_timeframes": timeframes}, sort_keys=True)
        row = self._session.scalar(select(PopgunSetting).where(PopgunSetting.key == self.DEFAULTS_KEY))
        if row is None:
            self._session.add(PopgunSetting(key=self.DEFAULTS_KEY, value_json=payload))
        else:
            row.value_json = payload
            row.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def get_defaults(self, *, fallback_symbols: list[str], fallback_timeframes: list[str]) -> tuple[list[str], list[str]]:
        row = self._session.scalar(select(PopgunSetting).where(PopgunSetting.key == self.DEFAULTS_KEY))
        if row is None:
            self.ensure_defaults(symbols=fallback_symbols, timeframes=fallback_timeframes)
            return list(fallback_symbols), list(fallback_timeframes)
        try:
            payload = json.loads(row.value_json)
        except json.JSONDecodeError:
            return list(fallback_symbols), list(fallback_timeframes)
        if not isinstance(payload, dict):
            return list(fallback_symbols), list(fallback_timeframes)
        symbols = payload.get("default_symbols")
        timeframes = payload.get("default_timeframes")
        return (
            list(symbols) if isinstance(symbols, list) else list(fallback_symbols),
            list(timeframes) if isinstance(timeframes, list) else list(fallback_timeframes),
        )

    def has_topics_or_alerts(self) -> bool:
        topic_count = self._session.scalar(select(func.count()).select_from(PopgunTopicSetting)) or 0
        alert_count = self._session.scalar(select(func.count()).select_from(PopgunAlertState)) or 0
        return bool(topic_count or alert_count)

    def is_legacy_import_completed(self) -> bool:
        row = self._session.scalar(select(PopgunSetting).where(PopgunSetting.key == self.LEGACY_IMPORT_KEY))
        return row is not None

    def mark_legacy_import_completed(self, *, state_path: str, topics_count: int, alerts_count: int) -> None:
        payload = json.dumps(
            {
                "state_path": state_path,
                "topics_count": topics_count,
                "alerts_count": alerts_count,
                "imported_at": datetime.now(timezone.utc).isoformat(),
            },
            sort_keys=True,
        )
        row = self._session.scalar(select(PopgunSetting).where(PopgunSetting.key == self.LEGACY_IMPORT_KEY))
        if row is None:
            self._session.add(PopgunSetting(key=self.LEGACY_IMPORT_KEY, value_json=payload))
        else:
            row.value_json = payload
            row.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    def get_topic(self, *, chat_id: int, thread_id: int | None) -> PopgunTopicSettingSnapshot | None:
        row = self._session.scalar(
            select(PopgunTopicSetting).where(PopgunTopicSetting.topic_key == self.topic_key(chat_id, thread_id))
        )
        return self._snapshot(row) if row is not None else None

    def upsert_topic(
        self,
        *,
        chat_id: int,
        thread_id: int | None,
        enabled: bool,
        symbols: list[str],
        timeframes: list[str],
    ) -> PopgunTopicSettingSnapshot:
        key = self.topic_key(chat_id, thread_id)
        row = self._session.scalar(select(PopgunTopicSetting).where(PopgunTopicSetting.topic_key == key))
        if row is None:
            row = PopgunTopicSetting(
                topic_key=key,
                chat_id=chat_id,
                message_thread_id=thread_id,
                enabled=enabled,
                symbols_json=json.dumps(symbols, sort_keys=True),
                timeframes_json=json.dumps(timeframes, sort_keys=True),
            )
            self._session.add(row)
        else:
            row.chat_id = chat_id
            row.message_thread_id = thread_id
            row.enabled = enabled
            row.symbols_json = json.dumps(symbols, sort_keys=True)
            row.timeframes_json = json.dumps(timeframes, sort_keys=True)
            row.updated_at = datetime.now(timezone.utc)
        self._session.commit()
        self._session.refresh(row)
        return self._snapshot(row)

    def list_enabled_topics(self) -> list[PopgunTopicSettingSnapshot]:
        rows = self._session.scalars(
            select(PopgunTopicSetting)
            .where(PopgunTopicSetting.enabled.is_(True))
            .order_by(PopgunTopicSetting.chat_id.asc(), PopgunTopicSetting.message_thread_id.asc())
        ).all()
        return [snapshot for row in rows if (snapshot := self._snapshot(row)).symbols]

    def record_alert_if_new(
        self,
        *,
        chat_id: int,
        thread_id: int | None,
        symbol: str,
        timeframe: str,
        signal_timestamp: int,
        inside_high: float | None,
        inside_low: float | None,
        outside_high: float | None,
        outside_low: float | None,
    ) -> bool:
        key = self.topic_key(chat_id, thread_id)
        self._session.add(
            PopgunAlertState(
                topic_key=key,
                chat_id=chat_id,
                message_thread_id=thread_id,
                symbol=symbol,
                timeframe=timeframe,
                signal_timestamp=signal_timestamp,
                inside_high=inside_high,
                inside_low=inside_low,
                outside_high=outside_high,
                outside_low=outside_low,
            )
        )
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            return False
        return True

    @staticmethod
    def _json_list(value: str) -> list[str]:
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, str)]

    def _snapshot(self, row: PopgunTopicSetting) -> PopgunTopicSettingSnapshot:
        updated_at = row.updated_at
        if updated_at is None:
            updated_at_text = datetime.now(timezone.utc).isoformat()
        else:
            updated_at_text = updated_at.isoformat()
        return PopgunTopicSettingSnapshot(
            chat_id=row.chat_id,
            thread_id=row.message_thread_id,
            enabled=bool(row.enabled),
            symbols=self._json_list(row.symbols_json),
            timeframes=self._json_list(row.timeframes_json),
            updated_at=updated_at_text,
        )


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
class DailyMemoryAggregationResult:
    scope_type: str
    chat_id: int | None
    topic_id: int | None
    user_id: int | None
    recent_rows_seen: int
    daily_rows_upserted: int
    skipped_no_new_data: bool

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
class PromptContextDocRecord:
    kind: str
    scope_type: str
    scope_key: str
    chat_id: int | None
    topic_id: int | None
    content: str
    enabled: bool
    updated_at: datetime | None = None


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
class RetrievableMemoryRecord:
    id: int
    chat_id: int | None
    message_thread_id: int | None
    user_id: int | None
    visibility: str
    memory_type: str
    content: str | None
    summary: str | None
    confidence: float
    source: str
    active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    use_count: int
    created_at: datetime | None
    updated_at: datetime | None
    score: float = 0.0

    @property
    def searchable_text(self) -> str:
        return " ".join(part.strip() for part in (self.summary, self.content) if part and part.strip()).strip()


@dataclass(slots=True)
class RetrievableMemoryBackfillStats:
    source: str
    source_rows: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    by_visibility: dict[str, int] | None = None


@dataclass(slots=True)
class RetrievableMemoryBackfillResult:
    dry_run: bool
    daily_memory: RetrievableMemoryBackfillStats
    long_memory: RetrievableMemoryBackfillStats

    @property
    def total_source_rows(self) -> int:
        return self.daily_memory.source_rows + self.long_memory.source_rows

    @property
    def total_created(self) -> int:
        return self.daily_memory.created + self.long_memory.created

    @property
    def total_updated(self) -> int:
        return self.daily_memory.updated + self.long_memory.updated

    @property
    def total_skipped(self) -> int:
        return self.daily_memory.skipped + self.long_memory.skipped


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


class PromptContextDocRepository:
    """DB-backed editable steering/context docs for prompt assembly.

    These records are explicit operator-authored context documents, not memory.
    They are resolved deterministically by kind with topic docs overriding global docs.
    """

    ALLOWED_KINDS = ("AGENT", "SOUL", "PLUGINS", "AUFGABE")
    ALLOWED_SCOPE_TYPES = {"global", "topic"}

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_doc(
        self,
        *,
        kind: str,
        scope_type: str,
        content: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        enabled: bool = True,
    ) -> PromptContextDocRecord:
        normalized_kind = self._normalize_kind(kind)
        normalized_scope_type = self._normalize_scope_type(scope_type)
        scope_key = self._scope_key(scope_type=normalized_scope_type, chat_id=chat_id, topic_id=topic_id)

        row = self._session.scalar(
            select(PromptContextDoc).where(
                PromptContextDoc.kind == normalized_kind,
                PromptContextDoc.scope_type == normalized_scope_type,
                PromptContextDoc.scope_key == scope_key,
            )
        )
        if row is None:
            row = PromptContextDoc(
                kind=normalized_kind,
                scope_type=normalized_scope_type,
                scope_key=scope_key,
                chat_id=chat_id if normalized_scope_type == "topic" else None,
                topic_id=topic_id if normalized_scope_type == "topic" else None,
            )
            self._session.add(row)

        row.content = content or ""
        row.enabled = bool(enabled)
        row.chat_id = chat_id if normalized_scope_type == "topic" else None
        row.topic_id = topic_id if normalized_scope_type == "topic" else None
        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row)

    def get_doc(
        self,
        *,
        kind: str,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
    ) -> PromptContextDocRecord | None:
        normalized_kind = self._normalize_kind(kind)
        normalized_scope_type = self._normalize_scope_type(scope_type)
        scope_key = self._scope_key(scope_type=normalized_scope_type, chat_id=chat_id, topic_id=topic_id)
        row = self._session.scalar(
            select(PromptContextDoc).where(
                PromptContextDoc.kind == normalized_kind,
                PromptContextDoc.scope_type == normalized_scope_type,
                PromptContextDoc.scope_key == scope_key,
            )
        )
        return self._to_record(row) if row is not None else None

    def delete_doc(
        self,
        *,
        kind: str,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
    ) -> bool:
        normalized_kind = self._normalize_kind(kind)
        normalized_scope_type = self._normalize_scope_type(scope_type)
        scope_key = self._scope_key(scope_type=normalized_scope_type, chat_id=chat_id, topic_id=topic_id)
        row = self._session.scalar(
            select(PromptContextDoc).where(
                PromptContextDoc.kind == normalized_kind,
                PromptContextDoc.scope_type == normalized_scope_type,
                PromptContextDoc.scope_key == scope_key,
            )
        )
        if row is None:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    def list_docs(
        self,
        *,
        scope_type: str | None = None,
        kind: str | None = None,
        chat_id: int | None = None,
        topic_id: int | None = None,
    ) -> list[PromptContextDocRecord]:
        stmt = select(PromptContextDoc)
        if kind:
            stmt = stmt.where(PromptContextDoc.kind == self._normalize_kind(kind))
        if scope_type:
            normalized_scope_type = self._normalize_scope_type(scope_type)
            stmt = stmt.where(PromptContextDoc.scope_type == normalized_scope_type)
            if normalized_scope_type == "topic":
                scope_key = self._scope_key(scope_type="topic", chat_id=chat_id, topic_id=topic_id)
                stmt = stmt.where(PromptContextDoc.scope_key == scope_key)
            else:
                stmt = stmt.where(PromptContextDoc.scope_key == "global")
        rows = self._session.scalars(stmt.order_by(PromptContextDoc.scope_type, PromptContextDoc.scope_key, PromptContextDoc.kind)).all()
        return [self._to_record(row) for row in rows]

    def resolve_docs(self, *, chat_id: int | None = None, topic_id: int | None = None) -> list[PromptContextDocRecord]:
        rows = self._session.scalars(select(PromptContextDoc).where(PromptContextDoc.enabled.is_(True))).all()
        by_scope_kind = {(row.scope_type, row.scope_key, row.kind): row for row in rows}
        topic_key = None
        if chat_id is not None and topic_id is not None:
            topic_key = self._scope_key(scope_type="topic", chat_id=chat_id, topic_id=topic_id)

        resolved: list[PromptContextDocRecord] = []
        for kind in self.ALLOWED_KINDS:
            row = by_scope_kind.get(("topic", topic_key, kind)) if topic_key is not None else None
            if row is None:
                row = by_scope_kind.get(("global", "global", kind))
            if row is not None and (row.content or "").strip():
                resolved.append(self._to_record(row))
        return resolved

    @classmethod
    def _normalize_kind(cls, kind: str) -> str:
        normalized = (kind or "").strip().upper()
        if normalized not in cls.ALLOWED_KINDS:
            raise ValueError("invalid prompt context doc kind")
        return normalized

    @classmethod
    def _normalize_scope_type(cls, scope_type: str) -> str:
        normalized = (scope_type or "").strip().lower()
        if normalized not in cls.ALLOWED_SCOPE_TYPES:
            raise ValueError("invalid prompt context doc scope_type")
        return normalized

    @staticmethod
    def _scope_key(*, scope_type: str, chat_id: int | None, topic_id: int | None) -> str:
        if scope_type == "global":
            return "global"
        if chat_id is None or topic_id is None:
            raise ValueError("topic prompt context docs require chat_id and topic_id")
        return f"telegram:{chat_id}:{topic_id}"

    @staticmethod
    def _to_record(row: PromptContextDoc) -> PromptContextDocRecord:
        return PromptContextDocRecord(
            kind=row.kind,
            scope_type=row.scope_type,
            scope_key=row.scope_key,
            chat_id=row.chat_id,
            topic_id=row.topic_id,
            content=row.content,
            enabled=bool(row.enabled),
            updated_at=row.updated_at,
        )


class RetrievableMemoryRepository:
    ALLOWED_VISIBILITIES = {"topic", "chat", "user", "global"}
    ALLOWED_MEMORY_TYPES = {"preference", "fact", "summary", "relationship", "warning"}
    ALLOWED_LEARNING_MEMORY_TYPES = {"preference", "warning", "summary"}
    ALLOWED_SOURCES = {"daily_memory", "long_memory", "manual", "auto", "plugin"}
    DEFAULT_LIMIT = 5
    MAX_LIMIT = 20

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_memory(
        self,
        *,
        visibility: str,
        memory_type: str,
        content: str | None = None,
        summary: str | None = None,
        chat_id: int | None = None,
        message_thread_id: int | None = None,
        user_id: int | None = None,
        confidence: float = 1.0,
        source: str = "manual",
        active: bool = True,
        expires_at: datetime | None = None,
    ) -> RetrievableMemoryRecord:
        normalized_visibility = self._normalize_visibility(visibility)
        normalized_type = self._normalize_memory_type(memory_type)
        normalized_source = self._normalize_source(source)
        safe_content = (content or "").strip() or None
        safe_summary = (summary or "").strip() or None
        if not (safe_content or safe_summary):
            raise ValueError("content or summary required")
        self._validate_scope(
            visibility=normalized_visibility,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            user_id=user_id,
        )

        row = RetrievableMemory(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            user_id=user_id,
            visibility=normalized_visibility,
            memory_type=normalized_type,
            content=safe_content,
            summary=safe_summary,
            confidence=max(0.0, min(float(confidence), 1.0)),
            source=normalized_source,
            active=bool(active),
            expires_at=expires_at,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return self._to_retrievable_record(row)

    def upsert_manual_memory(
        self,
        *,
        visibility: str,
        memory_type: str,
        content: str,
        chat_id: int | None = None,
        message_thread_id: int | None = None,
        user_id: int | None = None,
        confidence: float = 0.9,
        active: bool = True,
        expires_at: datetime | None = None,
    ) -> tuple[RetrievableMemoryRecord, bool]:
        normalized_visibility = self._normalize_visibility(visibility)
        normalized_type = self._normalize_memory_type(memory_type)
        safe_content = (content or "").strip()
        if not safe_content:
            raise ValueError("content required")
        self._validate_scope(
            visibility=normalized_visibility,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            user_id=user_id,
        )

        row = self._session.scalar(
            select(RetrievableMemory).where(
                RetrievableMemory.source == "manual",
                RetrievableMemory.memory_type == normalized_type,
                RetrievableMemory.visibility == normalized_visibility,
                RetrievableMemory.chat_id == chat_id,
                RetrievableMemory.message_thread_id == message_thread_id,
                RetrievableMemory.user_id == user_id,
                RetrievableMemory.content == safe_content,
            )
        )
        created = row is None
        if row is None:
            row = RetrievableMemory(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                user_id=user_id,
                visibility=normalized_visibility,
                memory_type=normalized_type,
                content=safe_content,
                summary=None,
                confidence=max(0.0, min(float(confidence), 1.0)),
                source="manual",
                active=bool(active),
                expires_at=expires_at,
            )
            self._session.add(row)
        else:
            row.confidence = max(float(row.confidence or 0.0), max(0.0, min(float(confidence), 1.0)))
            row.active = bool(active)
            if expires_at is not None:
                row.expires_at = expires_at

        self._session.commit()
        self._session.refresh(row)
        return self._to_retrievable_record(row), created

    def backfill_from_summarized_memories(
        self,
        *,
        dry_run: bool = True,
        include_daily: bool = True,
        include_long: bool = True,
        memory_type: str = "summary",
        confidence: float = 0.7,
    ) -> RetrievableMemoryBackfillResult:
        normalized_type = self._normalize_memory_type(memory_type)
        safe_confidence = max(0.0, min(float(confidence), 1.0))
        daily_stats = RetrievableMemoryBackfillStats(source="daily_memory", by_visibility={})
        long_stats = RetrievableMemoryBackfillStats(source="long_memory", by_visibility={})

        if include_daily:
            daily_by_scope: dict[tuple[str, int | None, int | None, int | None], list[str]] = defaultdict(list)
            for row in self._session.scalars(select(TopicDailyMemory).order_by(TopicDailyMemory.id.asc())).all():
                daily_stats.source_rows += 1
                scope = self._visibility_from_summary_scope(
                    scope_type=row.scope_type,
                    chat_id=row.chat_id,
                    topic_id=row.topic_id,
                    user_id=row.user_id,
                )
                safe_summary = (row.summary_text or "").strip()
                if scope is None or not safe_summary:
                    daily_stats.skipped += 1
                    continue
                daily_by_scope[scope].append(safe_summary)
            for scope, summaries in daily_by_scope.items():
                self._upsert_backfill_row(
                    stats=daily_stats,
                    source="daily_memory",
                    scope=scope,
                    summary="\n\n".join(summaries),
                    memory_type=normalized_type,
                    confidence=safe_confidence,
                    dry_run=dry_run,
                    active=True,
                )

        if include_long:
            long_by_scope: dict[tuple[str, int | None, int | None, int | None], list[str]] = defaultdict(list)
            for row in self._session.scalars(select(TopicLongMemory).order_by(TopicLongMemory.id.asc())).all():
                long_stats.source_rows += 1
                if not row.is_active:
                    long_stats.skipped += 1
                    continue
                scope = self._visibility_from_summary_scope(
                    scope_type=row.scope_type,
                    chat_id=row.chat_id,
                    topic_id=row.topic_id,
                    user_id=row.user_id,
                )
                safe_summary = (row.fact_text or "").strip()
                if scope is None or not safe_summary:
                    long_stats.skipped += 1
                    continue
                long_by_scope[scope].append(safe_summary)
            for scope, summaries in long_by_scope.items():
                self._upsert_backfill_row(
                    stats=long_stats,
                    source="long_memory",
                    scope=scope,
                    summary="\n\n".join(summaries),
                    memory_type=normalized_type,
                    confidence=safe_confidence,
                    dry_run=dry_run,
                    active=True,
                )

        if not dry_run:
            self._session.commit()

        return RetrievableMemoryBackfillResult(
            dry_run=dry_run,
            daily_memory=daily_stats,
            long_memory=long_stats,
        )

    def update_memory(
        self,
        memory_id: int,
        *,
        content: str | None = None,
        summary: str | None = None,
        confidence: float | None = None,
        active: bool | None = None,
        expires_at: datetime | None = None,
        memory_type: str | None = None,
    ) -> RetrievableMemoryRecord | None:
        row = self._session.scalar(select(RetrievableMemory).where(RetrievableMemory.id == memory_id))
        if row is None:
            return None
        if content is not None:
            row.content = content.strip() or None
        if summary is not None:
            row.summary = summary.strip() or None
        if not ((row.content or "").strip() or (row.summary or "").strip()):
            raise ValueError("content or summary required")
        if confidence is not None:
            row.confidence = max(0.0, min(float(confidence), 1.0))
        if active is not None:
            row.active = bool(active)
        if expires_at is not None:
            row.expires_at = expires_at
        if memory_type is not None:
            row.memory_type = self._normalize_memory_type(memory_type)
        self._session.commit()
        self._session.refresh(row)
        return self._to_retrievable_record(row)

    def recall_memories(
        self,
        *,
        query_text: str,
        chat_id: int | None = None,
        message_thread_id: int | None = None,
        user_id: int | None = None,
        limit: int = DEFAULT_LIMIT,
        memory_types: Iterable[str] | None = None,
        now: datetime | None = None,
        mark_used: bool = False,
    ) -> list[RetrievableMemoryRecord]:
        safe_limit = max(1, min(int(limit or self.DEFAULT_LIMIT), self.MAX_LIMIT))
        current = now or datetime.now(timezone.utc)
        query = select(RetrievableMemory).where(
            RetrievableMemory.active.is_(True),
            or_(RetrievableMemory.expires_at.is_(None), RetrievableMemory.expires_at > current),
            self._scope_filter(chat_id=chat_id, message_thread_id=message_thread_id, user_id=user_id),
        )
        if memory_types is not None:
            normalized_types = [self._normalize_memory_type(value) for value in memory_types]
            if normalized_types:
                query = query.where(RetrievableMemory.memory_type.in_(normalized_types))

        tokens = self._tokens(query_text)
        rows: list[RetrievableMemory]
        if tokens and self._is_mysql_backend():
            try:
                raw_terms = " ".join(sorted(tokens))
                ids = [
                    int(row_id)
                    for row_id in self._session.execute(
                        text(
                            "SELECT id FROM retrievable_memories "
                            "WHERE MATCH(summary, content) AGAINST (:terms IN NATURAL LANGUAGE MODE) "
                            "ORDER BY MATCH(summary, content) AGAINST (:terms IN NATURAL LANGUAGE MODE) DESC "
                            "LIMIT :limit"
                        ),
                        {"terms": raw_terms, "limit": safe_limit * 5},
                    ).scalars()
                ]
                if ids:
                    query = query.where(RetrievableMemory.id.in_(ids))
                else:
                    query = query.where(RetrievableMemory.id.in_([]))
            except Exception:
                # Fallback below keeps SQLite/tests and non-FULLTEXT MariaDB schemas usable.
                pass

        rows = list(self._session.scalars(query.limit(200)).all())
        scored = [self._score_record(self._to_retrievable_record(row), tokens=tokens, now=current) for row in rows]
        if tokens:
            scored = [record for record in scored if record.score > 0]
        scored.sort(key=lambda record: (record.score, record.confidence, record.id), reverse=True)
        selected = scored[:safe_limit]

        if mark_used and selected:
            used_at = current
            selected_ids = {record.id for record in selected}
            for row in rows:
                if row.id in selected_ids:
                    row.last_used_at = used_at
                    row.use_count = int(row.use_count or 0) + 1
            self._session.commit()
        return selected

    @classmethod
    def _scope_filter(cls, *, chat_id: int | None, message_thread_id: int | None, user_id: int | None):  # noqa: ANN206
        conditions = [RetrievableMemory.visibility == "global"]
        if chat_id is not None:
            conditions.append(and_(RetrievableMemory.visibility == "chat", RetrievableMemory.chat_id == chat_id))
            if message_thread_id is not None:
                conditions.append(
                    and_(
                        RetrievableMemory.visibility == "topic",
                        RetrievableMemory.chat_id == chat_id,
                        RetrievableMemory.message_thread_id == message_thread_id,
                    )
                )
        if user_id is not None:
            if chat_id is None:
                conditions.append(
                    and_(
                        RetrievableMemory.visibility == "user",
                        RetrievableMemory.user_id == user_id,
                        RetrievableMemory.chat_id.is_(None),
                    )
                )
            else:
                conditions.append(
                    and_(
                        RetrievableMemory.visibility == "user",
                        RetrievableMemory.user_id == user_id,
                        RetrievableMemory.chat_id == chat_id,
                    )
                )
        return or_(*conditions)

    def _upsert_backfill_row(
        self,
        *,
        stats: RetrievableMemoryBackfillStats,
        source: str,
        scope: tuple[str, int | None, int | None, int | None],
        summary: str | None,
        memory_type: str,
        confidence: float,
        dry_run: bool,
        active: bool,
    ) -> None:
        safe_summary = (summary or "").strip()
        if not safe_summary:
            stats.skipped += 1
            return

        visibility, target_chat_id, message_thread_id, target_user_id = scope
        if stats.by_visibility is not None:
            stats.by_visibility[visibility] = stats.by_visibility.get(visibility, 0) + 1
        existing = self._find_backfilled_memory(
            source=source,
            memory_type=memory_type,
            visibility=visibility,
            chat_id=target_chat_id,
            message_thread_id=message_thread_id,
            user_id=target_user_id,
        )
        if existing is None:
            stats.created += 1
            if not dry_run:
                self._session.add(
                    RetrievableMemory(
                        chat_id=target_chat_id,
                        message_thread_id=message_thread_id,
                        user_id=target_user_id,
                        visibility=visibility,
                        memory_type=memory_type,
                        content=None,
                        summary=safe_summary,
                        confidence=confidence,
                        source=source,
                        active=active,
                    )
                )
            return

        needs_update = (
            existing.summary != safe_summary
            or existing.content is not None
            or float(existing.confidence or 0.0) != confidence
            or bool(existing.active) != active
        )
        if not needs_update:
            stats.unchanged += 1
            return
        stats.updated += 1
        if not dry_run:
            existing.summary = safe_summary
            existing.content = None
            existing.confidence = confidence
            existing.active = active

    def _find_backfilled_memory(
        self,
        *,
        source: str,
        memory_type: str,
        visibility: str,
        chat_id: int | None,
        message_thread_id: int | None,
        user_id: int | None,
    ) -> RetrievableMemory | None:
        return self._session.scalar(
            select(RetrievableMemory).where(
                RetrievableMemory.source == source,
                RetrievableMemory.memory_type == memory_type,
                RetrievableMemory.visibility == visibility,
                RetrievableMemory.chat_id == chat_id,
                RetrievableMemory.message_thread_id == message_thread_id,
                RetrievableMemory.user_id == user_id,
            )
        )

    @classmethod
    def _visibility_from_summary_scope(
        cls,
        *,
        scope_type: str,
        chat_id: int | None,
        topic_id: int | None,
        user_id: int | None,
    ) -> tuple[str, int | None, int | None, int | None] | None:
        normalized_scope = (scope_type or "").strip().lower()
        if normalized_scope == "topic":
            if chat_id is None or topic_id is None:
                return None
            return ("topic", chat_id, topic_id, None)
        if normalized_scope in {"group_chat", "chat"}:
            if chat_id is None:
                return None
            return ("chat", chat_id, None, None)
        if normalized_scope in {"private_user", "user"}:
            if user_id is None:
                return None
            return ("user", chat_id, None, user_id)
        if normalized_scope == "global":
            if any(value is not None for value in (chat_id, topic_id, user_id)):
                return None
            return ("global", None, None, None)
        return None

    @classmethod
    def _score_record(cls, record: RetrievableMemoryRecord, *, tokens: set[str], now: datetime) -> RetrievableMemoryRecord:
        text_tokens = cls._tokens(record.searchable_text)
        if tokens:
            overlap = len(tokens & text_tokens)
            textual = overlap / max(len(tokens), 1)
            if overlap == 0:
                textual = 0.0
        else:
            textual = 0.2
        age_days = 0.0
        if record.updated_at is not None:
            age_delta = now - cls._as_aware_utc(record.updated_at)
            age_days = max(0.0, age_delta.total_seconds() / 86400)
        recency = 1.0 / (1.0 + min(age_days, 365.0) / 30.0)
        record.score = round((textual * 0.70) + (record.confidence * 0.20) + (recency * 0.10), 6)
        return record

    @staticmethod
    def _as_aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _tokens(value: str | None) -> set[str]:
        import re

        return {token.casefold() for token in re.findall(r"[A-Za-z0-9ÄÖÜäöüß_+-]+", value or "") if len(token) >= 3}

    def _is_mysql_backend(self) -> bool:
        bind = self._session.get_bind()
        return bind.dialect.name in {"mysql", "mariadb"}

    @classmethod
    def _normalize_visibility(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in cls.ALLOWED_VISIBILITIES:
            raise ValueError("invalid visibility")
        return normalized

    @classmethod
    def _normalize_memory_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in cls.ALLOWED_MEMORY_TYPES:
            raise ValueError("invalid memory_type")
        return normalized

    @classmethod
    def _normalize_source(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if not normalized:
            return "manual"
        if normalized not in cls.ALLOWED_SOURCES:
            return "plugin"
        return normalized

    @staticmethod
    def _validate_scope(
        *,
        visibility: str,
        chat_id: int | None,
        message_thread_id: int | None,
        user_id: int | None,
    ) -> None:
        if visibility == "topic" and (chat_id is None or message_thread_id is None):
            raise ValueError("topic memory requires chat_id and message_thread_id")
        if visibility == "chat" and chat_id is None:
            raise ValueError("chat memory requires chat_id")
        if visibility == "user" and user_id is None:
            raise ValueError("user memory requires user_id")
        if visibility == "global" and any(value is not None for value in (chat_id, message_thread_id, user_id)):
            raise ValueError("global memory must not set chat_id, message_thread_id, or user_id")

    @staticmethod
    def _to_retrievable_record(row: RetrievableMemory, *, score: float = 0.0) -> RetrievableMemoryRecord:
        return RetrievableMemoryRecord(
            id=row.id,
            chat_id=row.chat_id,
            message_thread_id=row.message_thread_id,
            user_id=row.user_id,
            visibility=row.visibility,
            memory_type=row.memory_type,
            content=row.content,
            summary=row.summary,
            confidence=float(row.confidence or 0.0),
            source=row.source,
            active=bool(row.active),
            expires_at=row.expires_at,
            last_used_at=row.last_used_at,
            use_count=int(row.use_count or 0),
            created_at=row.created_at,
            updated_at=row.updated_at,
            score=score,
        )


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

    def aggregate_recent_messages_to_daily_memory(
        self,
        *,
        scope_type: str,
        chat_id: int | None = None,
        topic_id: int | None = None,
        user_id: int | None = None,
        now: datetime | None = None,
        max_input_messages: int | None = None,
        max_chars_per_message: int | None = None,
        max_summary_chars: int | None = None,
        min_messages: int | None = None,
    ) -> DailyMemoryAggregationResult:
        current_now = now or datetime.now(UTC)
        fallback_day_key = current_now.date().isoformat()

        effective_max_input_messages = 1000 if max_input_messages is None else max(1, min(int(max_input_messages), 5000))
        effective_max_chars_per_message = 200 if max_chars_per_message is None else max(1, min(int(max_chars_per_message), 5000))
        effective_max_summary_chars = 6000 if max_summary_chars is None else max(1, min(int(max_summary_chars), 50000))
        effective_min_messages = 0 if min_messages is None else max(0, int(min_messages))

        rows = self.list_recent(
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
            limit=effective_max_input_messages,
        )
        recent_rows_seen = len(rows)
        if recent_rows_seen == 0:
            return DailyMemoryAggregationResult(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
                recent_rows_seen=0,
                daily_rows_upserted=0,
                skipped_no_new_data=True,
            )

        rows_by_day: dict[str, list[TopicRecentMessageRecord]] = defaultdict(list)
        rows_missing_created_at = 0
        for row in rows:
            created_at = row.created_at
            if created_at is None:
                rows_missing_created_at += 1
                day_key = fallback_day_key
            else:
                day_key = created_at.date().isoformat()
            rows_by_day[day_key].append(row)

        if not rows_by_day:
            return DailyMemoryAggregationResult(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
                recent_rows_seen=recent_rows_seen,
                daily_rows_upserted=0,
                skipped_no_new_data=True,
            )

        daily_rows_upserted = 0

        for day_key, day_rows in sorted(rows_by_day.items()):
            existing = self.get_daily_memory(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
                memory_date=day_key,
            )

            if len(day_rows) < effective_min_messages:
                continue

            author_ids = sorted({int(r.telegram_author_user_id) for r in day_rows if r.telegram_author_user_id is not None})
            distinct_author_count = len(author_ids)
            bots_count = sum(1 for r in day_rows if r.telegram_author_is_bot)
            source_user_count = sum(1 for r in day_rows if (r.source or 'user') == 'user')
            source_assistant_count = sum(1 for r in day_rows if (r.source or 'user') == 'assistant')
            first_ts = min((r.created_at for r in day_rows if r.created_at is not None), default=None)
            last_ts = max((r.created_at for r in day_rows if r.created_at is not None), default=None)
            first_iso = first_ts.isoformat() if first_ts is not None else None
            last_iso = last_ts.isoformat() if last_ts is not None else None

            content_lines: list[str] = []
            eligible_content_rows = [
                row
                for row in day_rows
                if not is_bot_authored_context_record(row)
                and not is_obvious_meta_status_message(row.message_text)
            ]
            for row in eligible_content_rows:
                clean_text = " ".join((row.message_text or "").strip().split())
                if not clean_text:
                    continue
                line = clean_text[:effective_max_chars_per_message]
                if len(clean_text) > effective_max_chars_per_message:
                    line += "…"
                content_lines.append(f"- {line}")
                if len(content_lines) >= 20:
                    break

            if not content_lines:
                continue

            summary_lines = [
                f"Daily memory summary for {day_key}",
                f"- scope_type={scope_type}",
                f"- chat_id={chat_id} topic_id={topic_id} user_id={user_id}",
                f"- recent_rows={len(day_rows)}",
                f"- distinct_authors={distinct_author_count}",
                f"- bot_messages={bots_count}",
                f"- source_user_messages={source_user_count}",
                f"- source_assistant_messages={source_assistant_count}",
                f"- eligible_content_messages={len(eligible_content_rows)}",
                f"- window_start={first_iso}",
                f"- window_end={last_iso}",
                f"- rows_missing_created_at={rows_missing_created_at}",
                "Content digest:",
                *content_lines,
            ]

            summary_text = "\n".join(summary_lines)
            summary_text = summary_text[:effective_max_summary_chars]
            tokens_estimate = max(1, min(4000, len(summary_text.split())))

            if existing is not None and existing.summary_text == summary_text and int(existing.tokens_estimate) == int(tokens_estimate):
                continue

            self.upsert_daily_memory(
                scope_type=scope_type,
                chat_id=chat_id,
                topic_id=topic_id,
                user_id=user_id,
                memory_date=day_key,
                summary_text=summary_text,
                tokens_estimate=tokens_estimate,
            )
            daily_rows_upserted += 1

        return DailyMemoryAggregationResult(
            scope_type=scope_type,
            chat_id=chat_id,
            topic_id=topic_id,
            user_id=user_id,
            recent_rows_seen=recent_rows_seen,
            daily_rows_upserted=daily_rows_upserted,
            skipped_no_new_data=(daily_rows_upserted == 0),
        )

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
        created_at: datetime | None = None,
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
            created_at=created_at,
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
        created_at: datetime | None = None,
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
            created_at=created_at,
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
