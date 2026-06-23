from __future__ import annotations

from sqlalchemy import text

from amo_bot.config.settings import Settings
from amo_bot.db.base import _engine_kwargs_for_url, create_session_factory


def test_settings_default_database_url_remains_sqlite() -> None:
    settings = Settings(
        BOT_TOKEN="123:test",
        WEBUI_PASSWORD="test-password",
        WEBUI_SECRET_KEY="test-secret-key-0123456789-abcdefghij",
        AI_PROVIDER="ollama",
        OLLAMA_MODEL="llama3",
        PLUGIN_DIR="plugins",
    )

    assert settings.database_url == "sqlite:///./data/amo_bot.db"


def test_create_session_factory_sqlite_memory_remains_usable() -> None:
    session_factory = create_session_factory("sqlite+pysqlite:///:memory:")
    engine = session_factory.kw["bind"]

    assert engine.url.get_backend_name() == "sqlite"
    with session_factory() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1


def test_mariadb_pymysql_url_gets_mysql_pool_safety_options() -> None:
    kwargs = _engine_kwargs_for_url("mysql+pymysql://amo_user:password@example.invalid:3306/amo")

    assert kwargs == {"future": True, "pool_pre_ping": True, "pool_recycle": 3600}


def test_postgresql_psycopg_url_gets_pre_ping() -> None:
    kwargs = _engine_kwargs_for_url("postgresql+psycopg://amo_user:password@example.invalid:5432/amo")

    assert kwargs == {"future": True, "pool_pre_ping": True}


def test_create_session_factory_for_mariadb_url_passes_mysql_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeEngine:
        pass

    def fake_create_engine(database_url: str, **kwargs: object) -> FakeEngine:
        captured["database_url"] = database_url
        captured["kwargs"] = kwargs
        return FakeEngine()

    monkeypatch.setattr("amo_bot.db.base.create_engine", fake_create_engine)

    session_factory = create_session_factory("mysql+pymysql://amo_user:password@example.invalid:3306/amo")

    assert session_factory.kw["bind"].__class__ is FakeEngine
    assert captured == {
        "database_url": "mysql+pymysql://amo_user:password@example.invalid:3306/amo",
        "kwargs": {"future": True, "pool_pre_ping": True, "pool_recycle": 3600},
    }
