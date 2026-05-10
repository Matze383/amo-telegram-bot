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

        for table_name, migrations in table_column_migrations.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, ddl in migrations.items():
                if column_name not in existing_columns:
                    connection.execute(text(ddl))

    with session_factory() as session:
        for role, prio in DEFAULT_ROLES:
            existing = session.scalar(select(DbRole).where(DbRole.name == role.value))
            if existing is None:
                session.add(DbRole(name=role.value, priority=prio))

        offset = session.scalar(select(UpdateOffset).where(UpdateOffset.source == "telegram"))
        if offset is None:
            session.add(UpdateOffset(source="telegram", last_update_id=0))

        session.commit()
