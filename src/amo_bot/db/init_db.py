from __future__ import annotations

from sqlalchemy import select

from amo_bot.db.base import Base, create_session_factory
from amo_bot.db.models import DEFAULT_ROLES, DbRole, UpdateOffset


def init_db(database_url: str) -> None:
    session_factory = create_session_factory(database_url)
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(bind=engine)

    with session_factory() as session:
        for role, prio in DEFAULT_ROLES:
            existing = session.scalar(select(DbRole).where(DbRole.name == role.value))
            if existing is None:
                session.add(DbRole(name=role.value, priority=prio))

        offset = session.scalar(select(UpdateOffset).where(UpdateOffset.source == "telegram"))
        if offset is None:
            session.add(UpdateOffset(source="telegram", last_update_id=0))

        session.commit()
