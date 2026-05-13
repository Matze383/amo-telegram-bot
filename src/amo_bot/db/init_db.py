from __future__ import annotations

from sqlalchemy import inspect, select, text

from amo_bot.db.base import Base, create_session_factory
from amo_bot.db.models import DEFAULT_ROLES, DbRole, UpdateOffset


def init_db(database_url: str) -> None:
    session_factory = create_session_factory(database_url)
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)

    table_column_migrations: dict[str, dict[str, str]] = {
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

        for table_name, migrations in table_column_migrations.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, ddl in migrations.items():
                if column_name not in existing_columns:
                    connection.execute(text(ddl))

        if "users" in existing_tables:
            connection.execute(
                text(
                    """
                    UPDATE users
                    SET consent_status = 'accepted'
                    WHERE consent_status IS NULL OR TRIM(consent_status) = ''
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE users
                    SET consent_prompt_count = 0
                    WHERE consent_prompt_count IS NULL
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

        session.commit()
