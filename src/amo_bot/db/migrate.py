from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from sqlalchemy import MetaData, create_engine, func, inspect, select
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from amo_bot.db.base import Base, _engine_kwargs_for_url
from amo_bot.db.init_db import init_db


class MigrationSafetyError(RuntimeError):
    """Raised when migration safety preconditions are not met."""


@dataclass(frozen=True)
class TableMigrationStatus:
    table_name: str
    source_count: int
    target_count_before: int
    copied_count: int = 0
    skipped: bool = False


@dataclass(frozen=True)
class MigrationResult:
    dry_run: bool
    statuses: tuple[TableMigrationStatus, ...]

    @property
    def total_source_count(self) -> int:
        return sum(status.source_count for status in self.statuses)

    @property
    def total_copied_count(self) -> int:
        return sum(status.copied_count for status in self.statuses)


def create_migration_engine(database_url: str) -> Engine:
    return create_engine(database_url, **_engine_kwargs_for_url(database_url))


def safe_url_for_display(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)


def _sqlite_database_identity(url: URL) -> str | None:
    if url.get_backend_name() != "sqlite":
        return None

    database = url.database
    if not database:
        return ":memory:"
    if database == ":memory:":
        return ":memory:"
    return str(Path(database).expanduser().resolve())


def _same_database(source_url: str, target_url: str) -> bool:
    source = make_url(source_url)
    target = make_url(target_url)

    source_sqlite_identity = _sqlite_database_identity(source)
    target_sqlite_identity = _sqlite_database_identity(target)
    if source_sqlite_identity is not None or target_sqlite_identity is not None:
        return source.get_backend_name() == target.get_backend_name() and source_sqlite_identity == target_sqlite_identity

    return (
        source.get_backend_name() == target.get_backend_name()
        and (source.host or "") == (target.host or "")
        and (source.port or None) == (target.port or None)
        and (source.database or "") == (target.database or "")
        and (source.username or "") == (target.username or "")
    )


def _metadata_for_engine(engine: Engine) -> MetaData:
    metadata = MetaData()
    metadata.reflect(bind=engine, only=[table.name for table in Base.metadata.sorted_tables])
    return metadata


def _count_table(engine: Engine, table) -> int:  # noqa: ANN001 - SQLAlchemy Table is runtime-typed
    with engine.connect() as connection:
        return int(connection.scalar(select(func.count()).select_from(table)) or 0)


def _target_counts(engine: Engine, tables) -> dict[str, int]:  # noqa: ANN001 - SQLAlchemy Table collection is runtime-typed
    return {table.name: _count_table(engine, table) for table in tables}


def _existing_model_tables(engine: Engine) -> list[str]:
    existing = set(inspect(engine).get_table_names())
    return [table.name for table in Base.metadata.sorted_tables if table.name in existing]


def _clear_target_tables(engine: Engine, metadata: MetaData, table_names: list[str]) -> None:
    tables_by_name = {table.name: table for table in metadata.sorted_tables if table.name in table_names}
    with engine.begin() as connection:
        for table in reversed(metadata.sorted_tables):
            if table.name in tables_by_name:
                connection.execute(table.delete())


def _format_status_line(status: TableMigrationStatus) -> str:
    if status.skipped:
        action = "skipped"
    elif status.copied_count:
        action = f"copied={status.copied_count}"
    else:
        action = "copied=0"
    return (
        f"table={status.table_name} "
        f"source_count={status.source_count} "
        f"target_count_before={status.target_count_before} "
        f"{action}"
    )


def migrate_database(
    *,
    source_url: str,
    target_url: str,
    dry_run: bool = False,
    allow_nonempty_target: bool = False,
    out: TextIO | None = None,
) -> MigrationResult:
    """Prepare/copy model-table data from one SQLAlchemy database URL to another.

    Output is intentionally metadata-only: table names, counts, and status.
    """

    if _same_database(source_url, target_url):
        raise MigrationSafetyError("source and target resolve to the same database; refusing to continue")

    source_engine = create_migration_engine(source_url)
    target_engine = create_migration_engine(target_url)

    try:
        source_inspector = inspect(source_engine)
        source_existing_tables = set(source_inspector.get_table_names())
        expected_tables = [table for table in Base.metadata.sorted_tables if table.name in source_existing_tables]
        missing_source_tables = [table.name for table in Base.metadata.sorted_tables if table.name not in source_existing_tables]

        target_preinit_table_names = _existing_model_tables(target_engine)
        target_preinit_metadata = MetaData()
        if target_preinit_table_names:
            target_preinit_metadata.reflect(bind=target_engine, only=target_preinit_table_names)
        target_preinit_counts = _target_counts(
            target_engine,
            [target_preinit_metadata.tables[name] for name in target_preinit_table_names],
        )
        preinit_nonempty_tables = {name: count for name, count in target_preinit_counts.items() if count > 0}
        if preinit_nonempty_tables and not allow_nonempty_target:
            table_summary = ", ".join(f"{name}={count}" for name, count in sorted(preinit_nonempty_tables.items()))
            raise MigrationSafetyError(
                "target database is not empty; refusing to copy without --allow-nonempty-target "
                f"({table_summary})"
            )

        init_db(target_url)
        target_metadata = _metadata_for_engine(target_engine)

        source_metadata = MetaData()
        source_metadata.reflect(bind=source_engine, only=[table.name for table in expected_tables])
        source_tables = [source_metadata.tables[table.name] for table in expected_tables]
        target_tables = [target_metadata.tables[table.name] for table in expected_tables]

        target_counts = _target_counts(target_engine, target_tables)

        statuses: list[TableMigrationStatus] = []
        for source_table, target_table in zip(source_tables, target_tables, strict=True):
            source_count = _count_table(source_engine, source_table)
            target_count = target_counts[source_table.name]
            statuses.append(
                TableMigrationStatus(
                    table_name=source_table.name,
                    source_count=source_count,
                    target_count_before=target_count,
                    skipped=dry_run,
                )
            )

        if out is not None:
            mode = "DRY-RUN" if dry_run else "COPY"
            out.write(f"mode={mode} tables={len(statuses)}\n")
            if missing_source_tables:
                out.write(f"missing_source_tables={','.join(missing_source_tables)}\n")
            for status in statuses:
                out.write(_format_status_line(status) + "\n")

        if dry_run:
            return MigrationResult(dry_run=True, statuses=tuple(statuses))

        if not preinit_nonempty_tables:
            # init_db seeds default rows (roles, offsets, policies/quotas). For a
            # brand-new target, clear those seed rows so the copied source keeps
            # original primary keys and unique values. Existing non-empty targets
            # are refused above unless explicitly allowed, and are never cleared.
            _clear_target_tables(target_engine, target_metadata, [table.name for table in target_tables])

        copied_statuses: list[TableMigrationStatus] = []
        with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
            for source_table, target_table, status in zip(source_tables, target_tables, statuses, strict=True):
                copied_count = 0
                result = source_connection.execute(select(source_table))
                mappings = result.mappings()
                while True:
                    batch = [dict(row) for row in mappings.fetchmany(500)]
                    if not batch:
                        break
                    target_connection.execute(target_table.insert(), batch)
                    copied_count += len(batch)
                copied_status = TableMigrationStatus(
                    table_name=status.table_name,
                    source_count=status.source_count,
                    target_count_before=status.target_count_before,
                    copied_count=copied_count,
                    skipped=False,
                )
                copied_statuses.append(copied_status)
                if out is not None:
                    out.write(_format_status_line(copied_status) + "\n")

        return MigrationResult(dry_run=False, statuses=tuple(copied_statuses))
    finally:
        source_engine.dispose()
        target_engine.dispose()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely prepare/copy AMO DB data between SQLAlchemy URLs")
    parser.add_argument("--source-url", required=True, help="Source SQLAlchemy URL, e.g. sqlite:///./data/amo_bot.db")
    parser.add_argument("--target-url", required=True, help="Target SQLAlchemy URL; use placeholder docs, not secrets in shell history")
    parser.add_argument("--dry-run", action="store_true", help="Report source/target table counts only; do not copy data")
    parser.add_argument(
        "--allow-nonempty-target",
        action="store_true",
        help="Allow copy into a target that already has rows; default refuses for safety",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        migrate_database(
            source_url=args.source_url,
            target_url=args.target_url,
            dry_run=args.dry_run,
            allow_nonempty_target=args.allow_nonempty_target,
            out=sys.stdout,
        )
    except MigrationSafetyError as exc:
        parser.exit(2, f"error: {exc}\n")
    except SQLAlchemyError as exc:
        parser.exit(1, f"database error: {exc.__class__.__name__}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
