from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def _engine_kwargs_for_url(database_url: str) -> dict[str, Any]:
    """Return SQLAlchemy engine options that are safe for the configured backend."""

    backend = make_url(database_url).get_backend_name()
    kwargs: dict[str, Any] = {"future": True}

    if backend in {"mysql", "mariadb"}:
        # MariaDB/MySQL servers commonly close idle TCP connections. pre_ping
        # avoids handing stale pooled connections to callers; recycle keeps long
        # lived bot processes below typical server wait_timeout defaults.
        kwargs.update(pool_pre_ping=True, pool_recycle=3600)

    return kwargs


def create_session_factory(database_url: str) -> sessionmaker:
    engine = create_engine(database_url, **_engine_kwargs_for_url(database_url))
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
