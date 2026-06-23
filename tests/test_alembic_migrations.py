from __future__ import annotations

import runpy
import sys
from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType, SimpleNamespace

from sqlalchemy import Column, Integer, MetaData, Table, create_engine, inspect

from amo_bot.db.base import Base
from amo_bot.db import models  # noqa: F401 - imported for metadata registration


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = PROJECT_ROOT / "migrations" / "versions" / "20260623_0001_postgres_pgvector_baseline.py"


class _FakeAlembicConfig:
    config_file_name = None
    config_ini_section = "alembic"

    def get_main_option(self, name: str) -> str | None:
        assert name == "sqlalchemy.url"
        return None

    def get_section(self, name: str, default: dict[str, str] | None = None) -> dict[str, str]:
        assert name == "alembic"
        return dict(default or {})


class _BeginTransaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001 - context-manager protocol
        return None


def test_alembic_env_uses_database_url_without_full_app_settings(monkeypatch) -> None:
    configured_urls: list[str] = []
    fake_context = SimpleNamespace(
        config=_FakeAlembicConfig(),
        is_offline_mode=lambda: True,
        configure=lambda **kwargs: configured_urls.append(kwargs["url"]),
        begin_transaction=lambda: _BeginTransaction(),
        run_migrations=lambda: None,
    )
    fake_settings = ModuleType("amo_bot.config.settings")

    def _unexpected_get_settings() -> object:
        raise AssertionError("get_settings must not be called when DATABASE_URL is present")

    fake_settings.get_settings = _unexpected_get_settings  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "amo_bot.config.settings", fake_settings)
    monkeypatch.setitem(sys.modules, "alembic", SimpleNamespace(context=fake_context))
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db/amo")

    runpy.run_path(str(PROJECT_ROOT / "migrations" / "env.py"))

    assert configured_urls == ["postgresql+psycopg://user:pass@db/amo"]


def test_baseline_downgrade_drops_only_revision_owned_tables(monkeypatch) -> None:
    fake_op = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "alembic", SimpleNamespace(op=fake_op))
    spec = importlib_util.spec_from_file_location("pgvector_baseline_under_test", BASELINE_PATH)
    assert spec is not None
    assert spec.loader is not None
    baseline = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(baseline)

    engine = create_engine("sqlite:///:memory:", future=True)
    extra_metadata = MetaData()
    future_table = Table("future_table_not_owned_by_baseline", extra_metadata, Column("id", Integer, primary_key=True))

    with engine.begin() as connection:
        Base.metadata.create_all(bind=connection)
        future_table.create(bind=connection)
        monkeypatch.setattr(baseline.op, "get_bind", lambda: connection, raising=False)

        baseline.downgrade()

        table_names = set(inspect(connection).get_table_names())

    assert "users" not in table_names
    assert "current_info_documents" not in table_names
    assert "future_table_not_owned_by_baseline" in table_names
