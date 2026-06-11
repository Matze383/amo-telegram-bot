from __future__ import annotations

from io import StringIO

from sqlalchemy import create_engine, inspect, select, text

from amo_bot.db.models import ResearchProvider, ResearchProviderHealth
from amo_bot.db.research_tables import RESEARCH_TABLE_NAMES, ensure_research_tables, main


def _sqlite_url(path) -> str:  # noqa: ANN001 - pytest tmp_path Path-like
    return f"sqlite:///{path}"


def test_research_tables_dry_run_reports_missing_without_creating(tmp_path) -> None:
    database_url = _sqlite_url(tmp_path / "dry_run.sqlite3")

    out = StringIO()
    result = ensure_research_tables(database_url, dry_run=True, out=out)

    assert result.dry_run is True
    assert result.existing_tables == ()
    assert result.created_tables == ()
    assert result.missing_tables == RESEARCH_TABLE_NAMES
    assert "mode=DRY-RUN" in out.getvalue()
    assert "missing_tables=research_providers" in out.getvalue()

    engine = create_engine(database_url, future=True)
    try:
        assert set(inspect(engine).get_table_names()).isdisjoint(RESEARCH_TABLE_NAMES)
    finally:
        engine.dispose()


def test_research_tables_migration_creates_only_target_tables(tmp_path) -> None:
    database_url = _sqlite_url(tmp_path / "research_tables.sqlite3")
    engine = create_engine(database_url, future=True)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE unrelated_table (id INTEGER NOT NULL PRIMARY KEY, value TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO unrelated_table (id, value) VALUES (1, 'keep')"))

        out = StringIO()
        result = ensure_research_tables(database_url, out=out)

        assert result.dry_run is False
        assert result.existing_tables == ()
        assert result.created_tables == RESEARCH_TABLE_NAMES
        assert result.missing_tables == ()
        assert "mode=APPLY" in out.getvalue()

        table_names = set(inspect(engine).get_table_names())
        assert set(RESEARCH_TABLE_NAMES).issubset(table_names)
        assert "unrelated_table" in table_names
        assert "roles" not in table_names
        assert "users" not in table_names

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT value FROM unrelated_table WHERE id = 1")) == "keep"
            assert connection.scalar(select(ResearchProvider.provider_name)) is None
    finally:
        engine.dispose()


def test_research_tables_migration_is_idempotent_and_preserves_existing_rows(tmp_path) -> None:
    database_url = _sqlite_url(tmp_path / "idempotent.sqlite3")
    first_result = ensure_research_tables(database_url)
    assert first_result.created_tables == RESEARCH_TABLE_NAMES

    engine = create_engine(database_url, future=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                ResearchProvider.__table__.insert().values(
                    provider_name="existing_provider",
                    source_name="Existing",
                    domain="weather",
                )
            )
            connection.execute(
                ResearchProviderHealth.__table__.insert().values(
                    provider_name="existing_provider",
                    success_count=3,
                )
            )

        out = StringIO()
        second_result = ensure_research_tables(database_url, out=out)

        assert second_result.existing_tables == RESEARCH_TABLE_NAMES
        assert second_result.created_tables == ()
        assert second_result.missing_tables == ()
        assert "created_tables=-" in out.getvalue()

        with engine.connect() as connection:
            assert connection.scalar(select(ResearchProvider.provider_name)) == "existing_provider"
            assert connection.scalar(select(ResearchProviderHealth.success_count)) == 3
    finally:
        engine.dispose()


def test_research_tables_cli_uses_targeted_migration(tmp_path) -> None:
    database_url = _sqlite_url(tmp_path / "cli.sqlite3")

    assert main(["--database-url", database_url]) == 0

    engine = create_engine(database_url, future=True)
    try:
        table_names = set(inspect(engine).get_table_names())
        assert set(RESEARCH_TABLE_NAMES).issubset(table_names)
        assert "roles" not in table_names
    finally:
        engine.dispose()
