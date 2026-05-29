from __future__ import annotations

from sqlalchemy import inspect, select, text

from amo_bot.db.base import Base, create_session_factory
from amo_bot.db.models import (
    DEFAULT_ROLES,
    DbRole,
    ImageAnalyzeRoleQuota,
    PrivateChatPolicy,
    UpdateOffset,
    WebToolRoleQuota,
)


def init_db(database_url: str) -> None:
    session_factory = create_session_factory(database_url)
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)


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
    }

    with engine.begin() as connection:
        existing_tables = set(inspector.get_table_names())

        for table_name, create_sql in table_creation_migrations.items():
            if table_name not in existing_tables:
                connection.execute(text(create_sql))

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

        for table_name, migrations in table_column_migrations.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, ddl in migrations.items():
                if column_name not in existing_columns:
                    connection.execute(text(ddl))

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

        session.commit()
