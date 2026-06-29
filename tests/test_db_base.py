from __future__ import annotations

from sqlalchemy import text

from amo_bot.config.settings import Settings
from amo_bot.db.base import _engine_kwargs_for_url, clear_session_factory_cache, create_session_factory


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

    assert kwargs == {"future": True, "pool_pre_ping": True, "pool_size": 1, "max_overflow": 1}


def test_postgresql_pool_limits_can_be_configured(monkeypatch) -> None:
    monkeypatch.setenv("AMO_DB_POOL_SIZE", "3")
    monkeypatch.setenv("AMO_DB_MAX_OVERFLOW", "0")

    kwargs = _engine_kwargs_for_url("postgresql+psycopg://amo_user:password@example.invalid:5432/amo")

    assert kwargs == {"future": True, "pool_pre_ping": True, "pool_size": 3, "max_overflow": 0}


def test_create_session_factory_keeps_sqlite_memory_fresh() -> None:
    first = create_session_factory("sqlite+pysqlite:///:memory:")
    second = create_session_factory("sqlite+pysqlite:///:memory:")

    assert second is not first
    assert second.kw["bind"] is not first.kw["bind"]


def test_create_session_factory_reuses_engine_per_process_for_persistent_url(tmp_path) -> None:
    clear_session_factory_cache()
    database_url = f"sqlite+pysqlite:///{tmp_path / 'cache.db'}"
    try:
        first = create_session_factory(database_url)
        second = create_session_factory(database_url)

        assert second is first
        assert second.kw["bind"] is first.kw["bind"]
    finally:
        clear_session_factory_cache()


def test_create_session_factory_for_mariadb_url_passes_mysql_options(monkeypatch) -> None:
    captured: dict[str, object] = {}
    clear_session_factory_cache()

    class FakeEngine:
        pass

    def fake_create_engine(database_url: str, **kwargs: object) -> FakeEngine:
        captured["database_url"] = database_url
        captured["kwargs"] = kwargs
        return FakeEngine()

    monkeypatch.setattr("amo_bot.db.base.create_engine", fake_create_engine)

    try:
        session_factory = create_session_factory("mysql+pymysql://amo_user:password@example.invalid:3306/amo")
    finally:
        clear_session_factory_cache()

    assert session_factory.kw["bind"].__class__ is FakeEngine
    assert captured == {
        "database_url": "mysql+pymysql://amo_user:password@example.invalid:3306/amo",
        "kwargs": {"future": True, "pool_pre_ping": True, "pool_recycle": 3600},
    }
