"""PostgreSQL pgvector baseline

Revision ID: 20260623_0001
Revises:
Create Date: 2026-06-23 00:00:00 UTC
"""
from __future__ import annotations

from alembic import op

from amo_bot.db.base import Base
from amo_bot.db import models  # noqa: F401 - imported for metadata registration
from amo_bot.db.init_db import _init_postgresql_extensions_and_indexes


revision = "20260623_0001"
down_revision = None
branch_labels = None
depends_on = None

REVISION_OWNED_TABLES = (
    "chat_user_roles",
    "users",
    "telegram_topics",
    "plugin_policy_allowed_topics",
    "plugin_policy_allowed_groups",
    "current_info_fetch_runs",
    "current_info_document_chunks",
    "chat_seen_users",
    "webui_access_window",
    "webtool_role_quotas",
    "webtool_quota_counters",
    "webtool_audit_events",
    "user_memory_profiles",
    "update_offsets",
    "topic_recent_messages",
    "telegram_queue_failures",
    "telegram_process_health",
    "telegram_outgoing_queue",
    "telegram_incoming_queue",
    "topic_long_memories",
    "topic_daily_memories",
    "topic_compact_states",
    "topic_ai_sessions",
    "topic_agent_configs",
    "telegram_chats",
    "roles",
    "retrievable_memories",
    "research_source_preferences",
    "research_source_observations",
    "research_providers",
    "research_provider_health",
    "research_eval_cases",
    "prompt_context_docs",
    "private_chat_policies",
    "popgun_topic_settings",
    "popgun_settings",
    "popgun_alert_states",
    "plugins",
    "plugin_policy_overrides",
    "plugin_activation_requests",
    "image_analyze_topic_policies",
    "image_analyze_role_quotas",
    "image_analyze_quota_counters",
    "image_analyze_audit_events",
    "current_info_query_runs",
    "current_info_documents",
    "claims",
    "bot_peers",
    "audit_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name == "postgresql":
        _init_postgresql_extensions_and_indexes(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS current_info_chunk_vectors")
    for table_name in REVISION_OWNED_TABLES:
        table = Base.metadata.tables[table_name]
        table.drop(bind=bind, checkfirst=True)
