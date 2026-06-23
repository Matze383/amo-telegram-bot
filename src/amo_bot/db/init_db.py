from __future__ import annotations

import json

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from amo_bot.db.base import Base, create_session_factory
from amo_bot.db.models import (
    DEFAULT_ROLES,
    DbRole,
    ImageAnalyzeRoleQuota,
    PrivateChatPolicy,
    ResearchProvider,
    UpdateOffset,
    WebToolRoleQuota,
)


POSTGRES_VECTOR_TABLE = "current_info_chunk_vectors"


def _is_postgresql_backend(engine) -> bool:  # noqa: ANN001 - SQLAlchemy engine is runtime-typed
    return engine.dialect.name == "postgresql"


def _execute_optional_postgresql_ddl(connection, statement: str) -> None:  # noqa: ANN001 - runtime SQLAlchemy type
    try:
        with connection.begin_nested():
            connection.execute(text(statement))
    except SQLAlchemyError:
        pass


def _execute_postgresql_extensions_and_indexes(connection) -> None:  # noqa: ANN001 - SQLAlchemy connection is runtime-typed
    for extension in ("vector", "pg_trgm", "pgcrypto"):
        connection.execute(text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))

    # TimescaleDB is useful for future telemetry/time-series work, but AMO
    # must stay bootable on PostgreSQL clusters without it enabled. A savepoint
    # keeps optional DDL failures from aborting Alembic's enclosing transaction.
    _execute_optional_postgresql_ddl(connection, "CREATE EXTENSION IF NOT EXISTS timescaledb")

    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS current_info_chunk_vectors (
                id BIGSERIAL PRIMARY KEY,
                point_id UUID NOT NULL UNIQUE,
                chunk_id INTEGER NOT NULL UNIQUE
                    REFERENCES current_info_document_chunks(id) ON DELETE CASCADE,
                document_id INTEGER NOT NULL
                    REFERENCES current_info_documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                embedding vector NOT NULL,
                embedding_dimension INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_current_info_chunk_vectors_document
            ON current_info_chunk_vectors (document_id, chunk_index)
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_current_info_document_chunks_text_trgm
            ON current_info_document_chunks
            USING gin ((coalesce(title, '') || ' ' || coalesce(text_excerpt, '')) gin_trgm_ops)
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_retrievable_memories_text_trgm
            ON retrievable_memories
            USING gin ((coalesce(summary, '') || ' ' || coalesce(content, '')) gin_trgm_ops)
            """
        )
    )

    _execute_optional_postgresql_ddl(
        connection,
        """
        CREATE INDEX IF NOT EXISTS ix_current_info_chunk_vectors_embedding
        ON current_info_chunk_vectors USING hnsw (embedding vector_cosine_ops)
        """,
    )


def _init_postgresql_extensions_and_indexes(bind) -> None:  # noqa: ANN001 - SQLAlchemy bind is runtime-typed
    if hasattr(bind, "execute"):
        _execute_postgresql_extensions_and_indexes(bind)
        return

    with bind.begin() as connection:
        _execute_postgresql_extensions_and_indexes(connection)


def _topic_compact_scope_key(
    *,
    row_id: int,
    scope_type: str | None,
    chat_id: int | None,
    topic_id: int | None,
    user_id: int | None,
) -> str:
    normalized_scope = (scope_type or "").strip().lower()
    if normalized_scope == "topic" and chat_id is not None and topic_id is not None:
        return f"topic:{chat_id}:{topic_id}"
    if normalized_scope == "group_chat" and chat_id is not None:
        return f"group_chat:{chat_id}"
    if normalized_scope == "private_user" and user_id is not None:
        return f"private_user:{user_id}"
    return f"legacy:{row_id}"


def init_db(database_url: str) -> None:
    session_factory = create_session_factory(database_url)
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(bind=engine)
    if _is_postgresql_backend(engine):
        _init_postgresql_extensions_and_indexes(engine)

    inspector = inspect(engine)


    # Legacy bootstrap/migration DDL from the SQLite era. A fresh MariaDB database
    # is created via Base.metadata.create_all above; live SQLite-to-MariaDB data
    # migration should use a separate dialect-aware migration/export step.
    table_creation_migrations: dict[str, str] = {
        "topic_agent_configs": """
            CREATE TABLE topic_agent_configs (
                id INTEGER NOT NULL PRIMARY KEY,
                scope_type VARCHAR(32) NOT NULL,
                chat_id BIGINT,
                topic_id BIGINT,
                user_id BIGINT,
                ai_enabled BOOLEAN NOT NULL DEFAULT 0,
                response_mode VARCHAR(32) NOT NULL DEFAULT 'command',
                memory_retention_days INTEGER NOT NULL DEFAULT 30,
                tools_enabled BOOLEAN NOT NULL DEFAULT 0,
                recent_context_window_size INTEGER NOT NULL DEFAULT 20,
                image_analysis_mode VARCHAR(16) NOT NULL DEFAULT 'inherit',
                main_soul_text TEXT,
                topic_soul_text TEXT,
                topic_soul_owner_only_edit BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_topic_agent_configs_scope UNIQUE (scope_type, chat_id, topic_id, user_id)
            )
        """,
        "prompt_context_docs": """
            CREATE TABLE prompt_context_docs (
                id INTEGER NOT NULL PRIMARY KEY,
                kind VARCHAR(16) NOT NULL,
                scope_type VARCHAR(16) NOT NULL,
                scope_key VARCHAR(128) NOT NULL,
                chat_id BIGINT,
                topic_id BIGINT,
                content TEXT NOT NULL DEFAULT '',
                enabled BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_prompt_context_docs_kind_scope UNIQUE (kind, scope_type, scope_key)
            )
        """,
        "topic_daily_memories": """
            CREATE TABLE topic_daily_memories (
                id INTEGER NOT NULL PRIMARY KEY,
                scope_type VARCHAR(32) NOT NULL,
                chat_id BIGINT,
                topic_id BIGINT,
                user_id BIGINT,
                memory_date VARCHAR(10) NOT NULL,
                summary_text TEXT NOT NULL DEFAULT '',
                tokens_estimate INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_topic_daily_memories_scope_day UNIQUE (scope_type, chat_id, topic_id, user_id, memory_date)
            )
        """,
        "topic_long_memories": """
            CREATE TABLE topic_long_memories (
                id INTEGER NOT NULL PRIMARY KEY,
                scope_type VARCHAR(32) NOT NULL,
                chat_id BIGINT,
                topic_id BIGINT,
                user_id BIGINT,
                fact_text TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                source_daily_memory_id INTEGER,
                promotion_status VARCHAR(16) NOT NULL DEFAULT 'none',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "topic_recent_messages": """
            CREATE TABLE topic_recent_messages (
                id INTEGER NOT NULL PRIMARY KEY,
                scope_type VARCHAR(32) NOT NULL,
                chat_id BIGINT,
                topic_id BIGINT,
                user_id BIGINT,
                message_text TEXT NOT NULL,
                telegram_message_id BIGINT,
                telegram_author_user_id BIGINT,
                telegram_author_username VARCHAR(255),
                telegram_author_is_bot BOOLEAN NOT NULL DEFAULT 0,
                source VARCHAR(32) NOT NULL DEFAULT 'user',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_topic_recent_messages_id UNIQUE (id)
            )
        """,
        "topic_ai_sessions": """
            CREATE TABLE topic_ai_sessions (
                id INTEGER NOT NULL PRIMARY KEY,
                scope_type VARCHAR(32) NOT NULL,
                chat_id BIGINT,
                topic_id BIGINT,
                user_id BIGINT,
                session_payload_json TEXT NOT NULL DEFAULT '{}',
                last_message_at DATETIME,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_topic_ai_sessions_scope UNIQUE (scope_type, chat_id, topic_id, user_id)
            )
        """,
        "private_chat_policies": """
            CREATE TABLE private_chat_policies (
                id INTEGER NOT NULL PRIMARY KEY,
                min_ai_role VARCHAR(32) NOT NULL DEFAULT 'vip',
                min_general_command_role VARCHAR(32) NOT NULL DEFAULT 'normal',
                min_plugin_command_role VARCHAR(32) NOT NULL DEFAULT 'normal',
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "bot_peers": """
            CREATE TABLE bot_peers (
                id INTEGER NOT NULL PRIMARY KEY,
                telegram_bot_id BIGINT NOT NULL UNIQUE,
                username VARCHAR(255),
                first_name VARCHAR(255),
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_chat_id BIGINT,
                last_seen_chat_type VARCHAR(32),
                last_seen_chat_title VARCHAR(255),
                last_seen_message_thread_id INTEGER,
                owner_decided_by_telegram_user_id BIGINT,
                owner_decided_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "user_memory_profiles": """
            CREATE TABLE user_memory_profiles (
                id INTEGER NOT NULL PRIMARY KEY,
                scope_type VARCHAR(32) NOT NULL,
                chat_id BIGINT,
                topic_id BIGINT,
                user_id BIGINT,
                profile_json TEXT NOT NULL DEFAULT '{}',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_user_memory_profiles_scope UNIQUE (scope_type, chat_id, topic_id, user_id)
            )
        """,
        "image_analyze_topic_policies": """
            CREATE TABLE image_analyze_topic_policies (
                id INTEGER NOT NULL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                message_thread_id INTEGER,
                enabled BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_image_analyze_topic_policy UNIQUE (chat_id, message_thread_id)
            )
        """,
        "image_analyze_quota_counters": """
            CREATE TABLE image_analyze_quota_counters (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role VARCHAR(32) NOT NULL,
                chat_id BIGINT NOT NULL,
                message_thread_id INTEGER,
                day VARCHAR(10) NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_image_analyze_quota_counter UNIQUE (user_id, role, chat_id, message_thread_id, day)
            )
        """,
        "image_analyze_audit_events": """
            CREATE TABLE image_analyze_audit_events (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role VARCHAR(32) NOT NULL,
                chat_id BIGINT NOT NULL,
                message_thread_id INTEGER,
                day VARCHAR(10) NOT NULL,
                count INTEGER NOT NULL,
                command VARCHAR(64),
                provider VARCHAR(64),
                outcome VARCHAR(64) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "image_analyze_role_quotas": """
            CREATE TABLE image_analyze_role_quotas (
                id INTEGER NOT NULL PRIMARY KEY,
                role VARCHAR(32) NOT NULL,
                mode VARCHAR(16) NOT NULL DEFAULT 'disabled',
                daily_limit INTEGER,
                updated_by_telegram_user_id BIGINT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_image_analyze_role_quota_role UNIQUE (role)
            )
        """,
        "claims": """
            CREATE TABLE claims (
                id INTEGER NOT NULL PRIMARY KEY,
                text TEXT NOT NULL,
                normalized_subject VARCHAR(255) NOT NULL DEFAULT '',
                source_type VARCHAR(32) NOT NULL,
                source_message_id BIGINT,
                scope VARCHAR(128) NOT NULL DEFAULT '',
                scope_type VARCHAR(32) NOT NULL,
                chat_id BIGINT,
                topic_id BIGINT,
                user_id BIGINT,
                timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                verification_status VARCHAR(32) NOT NULL DEFAULT 'unverified',
                confidence FLOAT NOT NULL DEFAULT 0,
                evidence_ref VARCHAR(2048),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "topic_compact_states": """
            CREATE TABLE topic_compact_states (
                id INTEGER NOT NULL PRIMARY KEY,
                schema_version VARCHAR(32) NOT NULL DEFAULT 'topic_compact_state_v1',
                scope VARCHAR(128) NOT NULL DEFAULT '',
                scope_type VARCHAR(32) NOT NULL,
                chat_id BIGINT,
                topic_id BIGINT,
                user_id BIGINT,
                active_subjects_json TEXT NOT NULL DEFAULT '[]',
                frames_json TEXT NOT NULL DEFAULT '[]',
                conflicts_json TEXT NOT NULL DEFAULT '[]',
                verified_facts_json TEXT NOT NULL DEFAULT '[]',
                discarded_assumptions_json TEXT NOT NULL DEFAULT '[]',
                last_snapshot_json TEXT NOT NULL DEFAULT '{}',
                updated_from_message_id BIGINT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_topic_compact_states_scope UNIQUE (scope)
            )
        """,
        "webtool_role_quotas": """
            CREATE TABLE webtool_role_quotas (
                id INTEGER NOT NULL PRIMARY KEY,
                role VARCHAR(32) NOT NULL,
                mode VARCHAR(16) NOT NULL DEFAULT 'unlimited',
                daily_limit INTEGER,
                updated_by_telegram_user_id BIGINT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_webtool_role_quota_role UNIQUE (role)
            )
        """,
        "webtool_quota_counters": """
            CREATE TABLE webtool_quota_counters (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role VARCHAR(32) NOT NULL,
                chat_id BIGINT NOT NULL,
                message_thread_id INTEGER,
                day VARCHAR(10) NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_webtool_quota_counter UNIQUE (user_id, role, chat_id, message_thread_id, day)
            )
        """,
        "webtool_audit_events": """
            CREATE TABLE webtool_audit_events (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role VARCHAR(32) NOT NULL,
                chat_id BIGINT NOT NULL,
                message_thread_id INTEGER,
                day VARCHAR(10) NOT NULL,
                count INTEGER NOT NULL,
                operation_type VARCHAR(64) NOT NULL,
                decision VARCHAR(32) NOT NULL,
                remaining INTEGER,
                reason VARCHAR(128),
                error VARCHAR(128),
                timing_ms INTEGER,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "retrievable_memories": """
            CREATE TABLE retrievable_memories (
                id INTEGER NOT NULL PRIMARY KEY,
                chat_id BIGINT,
                message_thread_id INTEGER,
                user_id BIGINT,
                visibility VARCHAR(16) NOT NULL,
                memory_type VARCHAR(32) NOT NULL,
                content TEXT,
                summary TEXT,
                confidence FLOAT NOT NULL DEFAULT 1,
                source VARCHAR(32) NOT NULL DEFAULT 'manual',
                active BOOLEAN NOT NULL DEFAULT 1,
                expires_at DATETIME,
                last_used_at DATETIME,
                use_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """,
    }

    table_column_migrations: dict[str, dict[str, str]] = {
        "topic_agent_configs": {
            "main_soul_text": "ALTER TABLE topic_agent_configs ADD COLUMN main_soul_text TEXT",
            "recent_context_window_size": "ALTER TABLE topic_agent_configs ADD COLUMN recent_context_window_size INTEGER NOT NULL DEFAULT 20",
            "image_analysis_mode": "ALTER TABLE topic_agent_configs ADD COLUMN image_analysis_mode VARCHAR(16) NOT NULL DEFAULT 'inherit'",
        },
        "topic_long_memories": {
            "promotion_status": "ALTER TABLE topic_long_memories ADD COLUMN promotion_status VARCHAR(16) NOT NULL DEFAULT 'none'",
            "answer_status": "ALTER TABLE topic_long_memories ADD COLUMN answer_status VARCHAR(16) NOT NULL DEFAULT 'legacy'",
        },
        "retrievable_memories": {
            "message_thread_id": "ALTER TABLE retrievable_memories ADD COLUMN message_thread_id INTEGER",
            "summary": "ALTER TABLE retrievable_memories ADD COLUMN summary TEXT",
            "confidence": "ALTER TABLE retrievable_memories ADD COLUMN confidence FLOAT NOT NULL DEFAULT 1",
            "source": "ALTER TABLE retrievable_memories ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'manual'",
            "active": "ALTER TABLE retrievable_memories ADD COLUMN active BOOLEAN NOT NULL DEFAULT 1",
            "expires_at": "ALTER TABLE retrievable_memories ADD COLUMN expires_at DATETIME",
            "last_used_at": "ALTER TABLE retrievable_memories ADD COLUMN last_used_at DATETIME",
            "use_count": "ALTER TABLE retrievable_memories ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0",
        },
        "topic_recent_messages": {
            "telegram_message_id": "ALTER TABLE topic_recent_messages ADD COLUMN telegram_message_id BIGINT",
            "telegram_author_user_id": "ALTER TABLE topic_recent_messages ADD COLUMN telegram_author_user_id BIGINT",
            "telegram_author_username": "ALTER TABLE topic_recent_messages ADD COLUMN telegram_author_username VARCHAR(255)",
            "telegram_author_is_bot": "ALTER TABLE topic_recent_messages ADD COLUMN telegram_author_is_bot BOOLEAN NOT NULL DEFAULT 0",
            "source": "ALTER TABLE topic_recent_messages ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'user'",
        },
        "users": {
            "first_name": "ALTER TABLE users ADD COLUMN first_name VARCHAR(255)",
            "last_name": "ALTER TABLE users ADD COLUMN last_name VARCHAR(255)",
            "display_name": "ALTER TABLE users ADD COLUMN display_name VARCHAR(255)",
            "first_seen_at": "ALTER TABLE users ADD COLUMN first_seen_at DATETIME",
            "last_seen_at": "ALTER TABLE users ADD COLUMN last_seen_at DATETIME",
            "consent_status": "ALTER TABLE users ADD COLUMN consent_status VARCHAR(32) NOT NULL DEFAULT 'accepted'",
            "consent_updated_at": "ALTER TABLE users ADD COLUMN consent_updated_at DATETIME",
            "consent_prompted_at": "ALTER TABLE users ADD COLUMN consent_prompted_at DATETIME",
            "consent_prompt_count": "ALTER TABLE users ADD COLUMN consent_prompt_count INTEGER NOT NULL DEFAULT 0",
        },
        "plugins": {
            "next_run_at": "ALTER TABLE plugins ADD COLUMN next_run_at DATETIME",
            "last_run_at": "ALTER TABLE plugins ADD COLUMN last_run_at DATETIME",
            "last_status": "ALTER TABLE plugins ADD COLUMN last_status VARCHAR(32)",
            "worker_state": "ALTER TABLE plugins ADD COLUMN worker_state VARCHAR(32)",
            "worker_last_heartbeat_at": "ALTER TABLE plugins ADD COLUMN worker_last_heartbeat_at DATETIME",
            "worker_restart_count": "ALTER TABLE plugins ADD COLUMN worker_restart_count INTEGER NOT NULL DEFAULT 0",
            "worker_next_restart_at": "ALTER TABLE plugins ADD COLUMN worker_next_restart_at DATETIME",
            "worker_last_error": "ALTER TABLE plugins ADD COLUMN worker_last_error TEXT",
            "activation_status": "ALTER TABLE plugins ADD COLUMN activation_status VARCHAR(32) NOT NULL DEFAULT 'activation_pending'",
        },
        "claims": {
            "scope": "ALTER TABLE claims ADD COLUMN scope VARCHAR(128) NOT NULL DEFAULT ''",
        },
        "topic_compact_states": {
            "scope": "ALTER TABLE topic_compact_states ADD COLUMN scope VARCHAR(128) NOT NULL DEFAULT ''",
        },
    }

    with engine.begin() as connection:
        existing_tables = set(inspector.get_table_names())

        for table_name, create_sql in table_creation_migrations.items():
            if table_name not in existing_tables:
                connection.execute(text(create_sql))
                existing_tables.add(table_name)

        if "chat_user_roles" not in existing_tables:
            connection.execute(
                text(
                    """
                    CREATE TABLE chat_user_roles (
                        id INTEGER NOT NULL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        user_id INTEGER NOT NULL,
                        role_id INTEGER NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        CONSTRAINT uq_chat_user_role UNIQUE (chat_id, user_id),
                        FOREIGN KEY(chat_id) REFERENCES telegram_chats (chat_id),
                        FOREIGN KEY(user_id) REFERENCES users (id),
                        FOREIGN KEY(role_id) REFERENCES roles (id)
                    )
                    """
                )
            )

        if "chat_user_roles" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("chat_user_roles")}
            if "ix_chat_user_roles_chat_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_chat_user_roles_chat_id ON chat_user_roles (chat_id)"))
            if "ix_chat_user_roles_user_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_chat_user_roles_user_id ON chat_user_roles (user_id)"))

        if "webui_access_window" not in existing_tables:
            connection.execute(
                text(
                    """
                    CREATE TABLE webui_access_window (
                        id INTEGER NOT NULL PRIMARY KEY,
                        enabled_until DATETIME NULL,
                        updated_by_telegram_id BIGINT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
                    )
                    """
                )
            )

        if "plugin_activation_requests" not in existing_tables:
            connection.execute(
                text(
                    """
                    CREATE TABLE plugin_activation_requests (
                        id INTEGER NOT NULL PRIMARY KEY,
                        plugin_name VARCHAR(128) NOT NULL,
                        status VARCHAR(32) DEFAULT 'pending' NOT NULL,
                        requested_by_telegram_user_id BIGINT NULL,
                        resolved_by_telegram_user_id BIGINT NULL,
                        reason TEXT NULL,
                        requested_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        resolved_at DATETIME NULL
                    )
                    """
                )
            )
            existing_tables.add("plugin_activation_requests")

        if "plugin_activation_requests" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("plugin_activation_requests")}
            if "ix_plugin_activation_requests_plugin_name" not in existing_indexes:
                connection.execute(
                    text("CREATE INDEX ix_plugin_activation_requests_plugin_name ON plugin_activation_requests (plugin_name)")
                )

        if "chat_seen_users" not in existing_tables:
            connection.execute(
                text(
                    """
                    CREATE TABLE chat_seen_users (
                        id INTEGER NOT NULL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        telegram_user_id BIGINT NOT NULL,
                        first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        CONSTRAINT uq_chat_seen_user UNIQUE (chat_id, telegram_user_id),
                        FOREIGN KEY(chat_id) REFERENCES telegram_chats (chat_id)
                    )
                    """
                )
            )

        if "chat_seen_users" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("chat_seen_users")}
            if "ix_chat_seen_users_chat_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_chat_seen_users_chat_id ON chat_seen_users (chat_id)"))
            if "ix_chat_seen_users_telegram_user_id" not in existing_indexes:
                connection.execute(
                    text("CREATE INDEX ix_chat_seen_users_telegram_user_id ON chat_seen_users (telegram_user_id)")
                )



        if "plugin_policy_overrides" not in existing_tables:
            connection.execute(
                text(
                    """
                    CREATE TABLE plugin_policy_overrides (
                        id INTEGER NOT NULL PRIMARY KEY,
                        plugin_name VARCHAR(128) NOT NULL,
                        roles_mode VARCHAR(16) NOT NULL DEFAULT 'inherit',
                        required_roles_json TEXT NULL,
                        private_mode VARCHAR(16) NOT NULL DEFAULT 'inherit',
                        groups_mode VARCHAR(16) NOT NULL DEFAULT 'inherit',
                        topics_mode VARCHAR(16) NOT NULL DEFAULT 'inherit',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        CONSTRAINT uq_plugin_policy_override_plugin UNIQUE (plugin_name)
                    )
                    """
                )
            )
            existing_tables.add("plugin_policy_overrides")

        if "plugin_policy_overrides" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("plugin_policy_overrides")}
            if "ix_plugin_policy_overrides_plugin_name" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_plugin_policy_overrides_plugin_name ON plugin_policy_overrides (plugin_name)"))

        if "plugin_policy_allowed_groups" not in existing_tables:
            connection.execute(
                text(
                    """
                    CREATE TABLE plugin_policy_allowed_groups (
                        id INTEGER NOT NULL PRIMARY KEY,
                        override_id INTEGER NOT NULL,
                        chat_id BIGINT NOT NULL,
                        CONSTRAINT uq_plugin_policy_allowed_group UNIQUE (override_id, chat_id),
                        FOREIGN KEY(override_id) REFERENCES plugin_policy_overrides (id) ON DELETE CASCADE
                    )
                    """
                )
            )
            existing_tables.add("plugin_policy_allowed_groups")

        if "plugin_policy_allowed_groups" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("plugin_policy_allowed_groups")}
            if "ix_plugin_policy_allowed_groups_override_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_plugin_policy_allowed_groups_override_id ON plugin_policy_allowed_groups (override_id)"))
            if "ix_plugin_policy_allowed_groups_chat_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_plugin_policy_allowed_groups_chat_id ON plugin_policy_allowed_groups (chat_id)"))

        if "plugin_policy_allowed_topics" not in existing_tables:
            connection.execute(
                text(
                    """
                    CREATE TABLE plugin_policy_allowed_topics (
                        id INTEGER NOT NULL PRIMARY KEY,
                        override_id INTEGER NOT NULL,
                        chat_id BIGINT NOT NULL,
                        message_thread_id INTEGER NOT NULL,
                        CONSTRAINT uq_plugin_policy_allowed_topic UNIQUE (override_id, chat_id, message_thread_id),
                        FOREIGN KEY(override_id) REFERENCES plugin_policy_overrides (id) ON DELETE CASCADE
                    )
                    """
                )
            )
            existing_tables.add("plugin_policy_allowed_topics")

        if "plugin_policy_allowed_topics" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("plugin_policy_allowed_topics")}
            if "ix_plugin_policy_allowed_topics_override_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_plugin_policy_allowed_topics_override_id ON plugin_policy_allowed_topics (override_id)"))
            if "ix_plugin_policy_allowed_topics_chat_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_plugin_policy_allowed_topics_chat_id ON plugin_policy_allowed_topics (chat_id)"))

        if "image_analyze_topic_policies" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("image_analyze_topic_policies")}
            if "ix_image_analyze_topic_policies_chat_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_image_analyze_topic_policies_chat_id ON image_analyze_topic_policies (chat_id)"))

        if "image_analyze_quota_counters" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("image_analyze_quota_counters")}
            if "ix_image_analyze_quota_counters_user_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_image_analyze_quota_counters_user_id ON image_analyze_quota_counters (user_id)"))
            if "ix_image_analyze_quota_counters_chat_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_image_analyze_quota_counters_chat_id ON image_analyze_quota_counters (chat_id)"))

        if "image_analyze_audit_events" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("image_analyze_audit_events")}
            if "ix_image_analyze_audit_events_user_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_image_analyze_audit_events_user_id ON image_analyze_audit_events (user_id)"))
            if "ix_image_analyze_audit_events_chat_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_image_analyze_audit_events_chat_id ON image_analyze_audit_events (chat_id)"))

        if "image_analyze_role_quotas" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("image_analyze_role_quotas")}
            if "ix_image_analyze_role_quotas_role" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_image_analyze_role_quotas_role ON image_analyze_role_quotas (role)"))

        if "webtool_role_quotas" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("webtool_role_quotas")}
            if "ix_webtool_role_quotas_role" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_webtool_role_quotas_role ON webtool_role_quotas (role)"))

        if "webtool_quota_counters" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("webtool_quota_counters")}
            if "ix_webtool_quota_counters_user_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_webtool_quota_counters_user_id ON webtool_quota_counters (user_id)"))
            if "ix_webtool_quota_counters_chat_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_webtool_quota_counters_chat_id ON webtool_quota_counters (chat_id)"))

        if "webtool_audit_events" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("webtool_audit_events")}
            if "ix_webtool_audit_events_user_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_webtool_audit_events_user_id ON webtool_audit_events (user_id)"))
            if "ix_webtool_audit_events_chat_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_webtool_audit_events_chat_id ON webtool_audit_events (chat_id)"))

        if "bot_peers" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("bot_peers")}
            if "ix_bot_peers_telegram_bot_id" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_bot_peers_telegram_bot_id ON bot_peers (telegram_bot_id)"))

        if "claims" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("claims")}
            if "ix_claims_scope_subject" not in existing_indexes:
                connection.execute(
                    text(
                        "CREATE INDEX ix_claims_scope_subject "
                        "ON claims (scope_type, chat_id, topic_id, user_id, normalized_subject)"
                    )
                )
            if "ix_claims_source" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_claims_source ON claims (source_type, source_message_id)"))
            if "ix_claims_verification_status" not in existing_indexes:
                connection.execute(
                    text("CREATE INDEX ix_claims_verification_status ON claims (verification_status, updated_at)")
                )

        for table_name, migrations in table_column_migrations.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, ddl in migrations.items():
                if column_name not in existing_columns:
                    connection.execute(text(ddl))

        if "topic_compact_states" in existing_tables:
            rows = connection.execute(
                text(
                    """
                    SELECT id, scope, scope_type, chat_id, topic_id, user_id
                    FROM topic_compact_states
                    ORDER BY id
                    """
                )
            ).mappings()
            keep_by_scope: dict[str, int] = {}
            duplicate_ids: list[int] = []
            for row in rows:
                scope = (row["scope"] or "").strip() or _topic_compact_scope_key(
                    row_id=int(row["id"]),
                    scope_type=row["scope_type"],
                    chat_id=row["chat_id"],
                    topic_id=row["topic_id"],
                    user_id=row["user_id"],
                )
                row_id = int(row["id"])
                previous_id = keep_by_scope.get(scope)
                if previous_id is not None:
                    duplicate_ids.append(previous_id)
                keep_by_scope[scope] = row_id
                connection.execute(
                    text("UPDATE topic_compact_states SET scope = :scope WHERE id = :id"),
                    {"scope": scope, "id": row_id},
                )
            for duplicate_id in duplicate_ids:
                connection.execute(
                    text("DELETE FROM topic_compact_states WHERE id = :id"),
                    {"id": duplicate_id},
                )

            existing_indexes = {index["name"] for index in inspect(connection).get_indexes("topic_compact_states")}
            if "ux_topic_compact_states_scope" not in existing_indexes:
                connection.execute(
                    text("CREATE UNIQUE INDEX ux_topic_compact_states_scope ON topic_compact_states (scope)")
                )

        if "retrievable_memories" in existing_tables:
            existing_indexes = {index["name"] for index in inspector.get_indexes("retrievable_memories")}
            if "ix_retrievable_memories_scope_active" not in existing_indexes:
                connection.execute(
                    text(
                        "CREATE INDEX ix_retrievable_memories_scope_active "
                        "ON retrievable_memories (visibility, chat_id, message_thread_id, user_id, active)"
                    )
                )
            if "ix_retrievable_memories_type_active" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_retrievable_memories_type_active ON retrievable_memories (memory_type, active)"))
            if "ix_retrievable_memories_expires_at" not in existing_indexes:
                connection.execute(text("CREATE INDEX ix_retrievable_memories_expires_at ON retrievable_memories (expires_at)"))

        if "users" in existing_tables:
            existing_columns = {column["name"] for column in inspector.get_columns("users")}
            if "consent_status" in existing_columns:
                connection.execute(
                    text(
                        """
                        UPDATE users
                        SET consent_status = 'accepted'
                        WHERE consent_status IS NULL OR TRIM(consent_status) = ''
                        """
                    )
                )
            if "consent_prompt_count" in existing_columns:
                connection.execute(
                    text(
                        """
                        UPDATE users
                        SET consent_prompt_count = 0
                        WHERE consent_prompt_count IS NULL
                        """
                    )
                )

        if "topic_agent_configs" in existing_tables:
            existing_columns = {column["name"] for column in inspector.get_columns("topic_agent_configs")}
            if "recent_context_window_size" in existing_columns:
                connection.execute(
                    text(
                        """
                        UPDATE topic_agent_configs
                        SET recent_context_window_size = 20
                        WHERE recent_context_window_size = 0
                        """
                    )
                )
            if "image_analysis_mode" in existing_columns:
                connection.execute(
                    text(
                        """
                        UPDATE topic_agent_configs
                        SET image_analysis_mode = 'inherit'
                        WHERE image_analysis_mode IS NULL OR TRIM(image_analysis_mode) = ''
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        UPDATE topic_agent_configs
                        SET image_analysis_mode = LOWER(TRIM(image_analysis_mode))
                        WHERE image_analysis_mode IS NOT NULL
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        UPDATE topic_agent_configs
                        SET image_analysis_mode = 'inherit'
                        WHERE image_analysis_mode NOT IN ('inherit', 'enabled', 'disabled')
                        """
                    )
                )

    with session_factory() as session:
        for role, prio in DEFAULT_ROLES:
            existing = session.scalar(select(DbRole).where(DbRole.name == role.value))
            if existing is None:
                session.add(DbRole(name=role.value, priority=prio))

        offset = session.scalar(select(UpdateOffset).where(UpdateOffset.source == "telegram"))
        if offset is None:
            session.add(UpdateOffset(source="telegram", last_update_id=0))

        private_policy = session.scalar(select(PrivateChatPolicy).where(PrivateChatPolicy.id == 1))
        if private_policy is None:
            session.add(PrivateChatPolicy(id=1))

        role_quota_defaults = {
            "owner": ("unlimited", None),
            "admin": ("disabled", None),
            "vip": ("disabled", None),
            "normal": ("disabled", None),
            "ignore": ("disabled", None),
        }
        for role_name, (mode, daily_limit) in role_quota_defaults.items():
            row = session.scalar(select(ImageAnalyzeRoleQuota).where(ImageAnalyzeRoleQuota.role == role_name))
            if row is None:
                session.add(ImageAnalyzeRoleQuota(role=role_name, mode=mode, daily_limit=daily_limit))
                continue

            normalized_mode = (row.mode or "").strip().lower()
            if normalized_mode not in {"disabled", "unlimited", "limited"}:
                normalized_mode = mode

            if normalized_mode == "limited":
                if row.daily_limit is None or int(row.daily_limit) < 1:
                    normalized_mode = "disabled"
                    row.daily_limit = None
                else:
                    row.daily_limit = int(row.daily_limit)
            else:
                row.daily_limit = None

            if role_name == "ignore" and normalized_mode == "unlimited":
                normalized_mode = "disabled"
                row.daily_limit = None

            row.mode = normalized_mode

        webtool_quota_defaults = {
            "owner": ("unlimited", None),
            "admin": ("unlimited", None),
            "vip": ("unlimited", None),
            "normal": ("unlimited", None),
            "ignore": ("disabled", None),
        }
        for role_name, (mode, daily_limit) in webtool_quota_defaults.items():
            row = session.scalar(select(WebToolRoleQuota).where(WebToolRoleQuota.role == role_name))
            if row is None:
                session.add(WebToolRoleQuota(role=role_name, mode=mode, daily_limit=daily_limit))
                continue

            normalized_mode = (row.mode or "").strip().lower()
            if normalized_mode not in {"disabled", "unlimited", "limited"}:
                normalized_mode = mode

            if normalized_mode == "limited":
                if row.daily_limit is None or int(row.daily_limit) < 1:
                    normalized_mode = "disabled"
                    row.daily_limit = None
                else:
                    row.daily_limit = int(row.daily_limit)
            else:
                row.daily_limit = None

            if role_name == "ignore" and normalized_mode == "unlimited":
                normalized_mode = "disabled"
                row.daily_limit = None

            row.mode = normalized_mode

        try:
            from amo_bot.telegram.webtool_evidence import PROVIDER_REGISTRY
        except Exception:
            PROVIDER_REGISTRY = {}

        for definition in PROVIDER_REGISTRY.values():
            row = session.scalar(
                select(ResearchProvider).where(ResearchProvider.provider_name == definition.name)
            )
            if row is None:
                session.add(
                    ResearchProvider(
                        provider_name=definition.name,
                        source_name=definition.source_name,
                        domain=definition.domain,
                        enabled=definition.enabled_by_default,
                        default_priority=definition.default_priority,
                        fallback_allowed=definition.fallback_allowed,
                        min_confidence=definition.min_confidence,
                        max_age_seconds=definition.max_age_seconds,
                        metadata_json=json.dumps({"seed": "webtool_evidence_registry"}, sort_keys=True),
                    )
                )

        session.commit()
