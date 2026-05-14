from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amo_bot.auth.roles import Role
from amo_bot.db.base import Base


GROUP_CHAT_TYPES: tuple[str, ...] = ("group", "supergroup")


class DbRole(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    consent_status: Mapped[str] = mapped_column(String(32), nullable=False, default="accepted", server_default="accepted")
    consent_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_prompted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_prompt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    role: Mapped[DbRole] = relationship()


class UpdateOffset(Base):
    __tablename__ = "update_offsets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default="telegram")
    last_update_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Plugin(Base):
    __tablename__ = "plugins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="activation_pending", server_default="activation_pending")
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    worker_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    worker_last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_restart_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    worker_next_restart_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PluginActivationRequest(Base):
    __tablename__ = "plugin_activation_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plugin_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    requested_by_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_by_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TelegramChat(Base):
    __tablename__ = "telegram_chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatUserRole(Base):
    __tablename__ = "chat_user_roles"
    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_chat_user_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("telegram_chats.chat_id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    role: Mapped[DbRole] = relationship()


class ChatSeenUser(Base):
    __tablename__ = "chat_seen_users"
    __table_args__ = (UniqueConstraint("chat_id", "telegram_user_id", name="uq_chat_seen_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("telegram_chats.chat_id"), index=True, nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WebuiAccessWindow(Base):
    __tablename__ = "webui_access_window"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TelegramTopic(Base):
    __tablename__ = "telegram_topics"
    __table_args__ = (UniqueConstraint("chat_id", "message_thread_id", name="uq_topic_chat_thread"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("telegram_chats.chat_id"), index=True, nullable=False)
    message_thread_id: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_topic_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PluginPolicyOverride(Base):
    __tablename__ = "plugin_policy_overrides"
    __table_args__ = (UniqueConstraint("plugin_name", name="uq_plugin_policy_override_plugin"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plugin_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    roles_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="inherit", server_default="inherit")
    required_roles_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    private_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="inherit", server_default="inherit")
    groups_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="inherit", server_default="inherit")
    topics_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="inherit", server_default="inherit")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PluginPolicyAllowedGroup(Base):
    __tablename__ = "plugin_policy_allowed_groups"
    __table_args__ = (UniqueConstraint("override_id", "chat_id", name="uq_plugin_policy_allowed_group"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    override_id: Mapped[int] = mapped_column(ForeignKey("plugin_policy_overrides.id", ondelete="CASCADE"), nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class PluginPolicyAllowedTopic(Base):
    __tablename__ = "plugin_policy_allowed_topics"
    __table_args__ = (UniqueConstraint("override_id", "chat_id", "message_thread_id", name="uq_plugin_policy_allowed_topic"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    override_id: Mapped[int] = mapped_column(ForeignKey("plugin_policy_overrides.id", ondelete="CASCADE"), nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    message_thread_id: Mapped[int] = mapped_column(Integer, nullable=False)


DEFAULT_ROLES: list[tuple[Role, int]] = [
    (Role.OWNER, 0),
    (Role.ADMIN, 10),
    (Role.VIP, 20),
    (Role.NORMAL, 30),
    (Role.IGNORE, 100),
]


class TopicAgentConfig(Base):
    __tablename__ = "topic_agent_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    response_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="command", server_default="command")
    memory_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    tools_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    main_soul_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_soul_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_soul_owner_only_edit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("scope_type", "chat_id", "topic_id", "user_id", name="uq_topic_agent_configs_scope"),
    )


class TopicDailyMemory(Base):
    __tablename__ = "topic_daily_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    memory_date: Mapped[str] = mapped_column(String(10), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    tokens_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("scope_type", "chat_id", "topic_id", "user_id", "memory_date", name="uq_topic_daily_memories_scope_day"),
    )


class TopicLongMemory(Base):
    __tablename__ = "topic_long_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    source_daily_memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TopicAiSession(Base):
    __tablename__ = "topic_ai_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    session_payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("scope_type", "chat_id", "topic_id", "user_id", name="uq_topic_ai_sessions_scope"),
    )
