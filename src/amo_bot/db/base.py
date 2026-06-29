from __future__ import annotations

from functools import lru_cache
import os
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def _pool_int_from_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _engine_kwargs_for_url(database_url: str) -> dict[str, Any]:
    """Return SQLAlchemy engine options that are safe for the configured backend."""

    backend = make_url(database_url).get_backend_name()
    kwargs: dict[str, Any] = {"future": True}

    if backend in {"mysql", "mariadb"}:
        # MariaDB/MySQL servers commonly close idle TCP connections. pre_ping
        # avoids handing stale pooled connections to callers; recycle keeps long
        # lived bot processes below typical server wait_timeout defaults.
        kwargs.update(pool_pre_ping=True, pool_recycle=3600)
    elif backend == "postgresql":
        kwargs.update(
            pool_pre_ping=True,
            pool_size=_pool_int_from_env("AMO_DB_POOL_SIZE", 1, minimum=1),
            max_overflow=_pool_int_from_env("AMO_DB_MAX_OVERFLOW", 1, minimum=0),
        )

    return kwargs


@lru_cache(maxsize=32)
def _cached_session_factory(database_url: str) -> sessionmaker:
    engine = create_engine(database_url, **_engine_kwargs_for_url(database_url))
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _is_sqlite_memory_url(database_url: str) -> bool:
    url = make_url(database_url)
    return url.get_backend_name() == "sqlite" and url.database in {None, "", ":memory:"}


def clear_session_factory_cache() -> None:
    _cached_session_factory.cache_clear()


def create_session_factory(database_url: str) -> sessionmaker:
    if _is_sqlite_memory_url(database_url):
        engine = create_engine(database_url, **_engine_kwargs_for_url(database_url))
        return sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return _cached_session_factory(database_url)
