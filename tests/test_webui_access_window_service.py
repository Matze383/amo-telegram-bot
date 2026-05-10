from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from amo_bot.db.base import Base
from amo_bot.db.init_db import init_db
from amo_bot.webui.access_window import WebuiAccessWindowService


def _mk_service(db_url: str) -> WebuiAccessWindowService:
    engine = create_engine(db_url)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return WebuiAccessWindowService(factory)


def test_enable_sets_exactly_one_hour(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'db.sqlite3'}"
    init_db(db_url)
    service = _mk_service(db_url)

    now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    enabled_until = service.enable_for_one_hour(actor_id=1, now_utc=now)

    assert enabled_until == now + timedelta(hours=1)


def test_reenable_extends_from_new_now(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'db.sqlite3'}"
    init_db(db_url)
    service = _mk_service(db_url)

    first_now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    second_now = datetime(2026, 1, 1, 10, 30, 0, tzinfo=UTC)

    service.enable_for_one_hour(actor_id=1, now_utc=first_now)
    enabled_until = service.enable_for_one_hour(actor_id=1, now_utc=second_now)

    assert enabled_until == second_now + timedelta(hours=1)


def test_disable_closes_window(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'db.sqlite3'}"
    init_db(db_url)
    service = _mk_service(db_url)

    now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    service.enable_for_one_hour(actor_id=1, now_utc=now)
    service.disable(actor_id=1, now_utc=now + timedelta(minutes=1))

    status = service.get_status(now_utc=now + timedelta(minutes=2))
    assert status.open is False
    assert status.remaining_seconds == 0


def test_expired_window_is_closed(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'db.sqlite3'}"
    init_db(db_url)
    service = _mk_service(db_url)

    now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    service.enable_for_one_hour(actor_id=1, now_utc=now)

    status = service.get_status(now_utc=now + timedelta(hours=1, seconds=1))
    assert status.open is False
    assert service.is_open(now_utc=now + timedelta(hours=1, seconds=1)) is False


def test_remaining_seconds_plausible(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'db.sqlite3'}"
    init_db(db_url)
    service = _mk_service(db_url)

    now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    service.enable_for_one_hour(actor_id=1, now_utc=now)

    status = service.get_status(now_utc=now + timedelta(minutes=15))
    assert status.open is True
    assert status.remaining_seconds == 45 * 60


def test_init_db_idempotent_schema_contains_access_window(tmp_path) -> None:
    db_path = tmp_path / "db.sqlite3"
    db_url = f"sqlite:///{db_path}"

    init_db(db_url)
    init_db(db_url)

    engine = create_engine(db_url)
    table_names = set(inspect(engine).get_table_names())
    assert "webui_access_window" in table_names
