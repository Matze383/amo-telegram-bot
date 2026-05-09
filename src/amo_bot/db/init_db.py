from __future__ import annotations

from sqlalchemy import inspect, select, text

from amo_bot.db.base import Base, create_session_factory
from amo_bot.db.models import DEFAULT_ROLES, DbRole, UpdateOffset


def init_db(database_url: str) -> None:
    session_factory = create_session_factory(database_url)
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    if "plugins" in inspector.get_table_names():
        existing_columns = {column["name"] for column in inspector.get_columns("plugins")}
        with engine.begin() as connection:
            if "next_run_at" not in existing_columns:
                connection.execute(text("ALTER TABLE plugins ADD COLUMN next_run_at DATETIME"))
            if "last_run_at" not in existing_columns:
                connection.execute(text("ALTER TABLE plugins ADD COLUMN last_run_at DATETIME"))
            if "last_status" not in existing_columns:
                connection.execute(text("ALTER TABLE plugins ADD COLUMN last_status VARCHAR(32)"))
            if "worker_state" not in existing_columns:
                connection.execute(text("ALTER TABLE plugins ADD COLUMN worker_state VARCHAR(32)"))
            if "worker_last_heartbeat_at" not in existing_columns:
                connection.execute(text("ALTER TABLE plugins ADD COLUMN worker_last_heartbeat_at DATETIME"))
            if "worker_restart_count" not in existing_columns:
                connection.execute(text("ALTER TABLE plugins ADD COLUMN worker_restart_count INTEGER NOT NULL DEFAULT 0"))
            if "worker_next_restart_at" not in existing_columns:
                connection.execute(text("ALTER TABLE plugins ADD COLUMN worker_next_restart_at DATETIME"))
            if "worker_last_error" not in existing_columns:
                connection.execute(text("ALTER TABLE plugins ADD COLUMN worker_last_error TEXT"))

    with session_factory() as session:
        for role, prio in DEFAULT_ROLES:
            existing = session.scalar(select(DbRole).where(DbRole.name == role.value))
            if existing is None:
                session.add(DbRole(name=role.value, priority=prio))

        offset = session.scalar(select(UpdateOffset).where(UpdateOffset.source == "telegram"))
        if offset is None:
            session.add(UpdateOffset(source="telegram", last_update_id=0))

        session.commit()
