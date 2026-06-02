from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import inspect

from amo_bot.config.settings import get_settings
from amo_bot.db.base import create_session_factory
from amo_bot.db.models import RetrievableMemory, TopicDailyMemory, TopicLongMemory
from amo_bot.db.repositories import RetrievableMemoryBackfillResult, RetrievableMemoryBackfillStats, RetrievableMemoryRepository


@dataclass(frozen=True, slots=True)
class RetrievableMemoryBackfillSchemaStatus:
    missing_tables: tuple[str, ...]

    @property
    def schema_ready(self) -> bool:
        return not self.missing_tables


class RetrievableMemoryBackfillSchemaError(RuntimeError):
    def __init__(self, status: RetrievableMemoryBackfillSchemaStatus) -> None:
        self.status = status
        super().__init__(format_schema_status(status))


def check_schema_ready(
    *,
    database_url: str,
    include_daily: bool = True,
    include_long: bool = True,
) -> RetrievableMemoryBackfillSchemaStatus:
    session_factory = create_session_factory(database_url)
    bind = session_factory.kw["bind"]
    required_tables = [RetrievableMemory.__tablename__]
    if include_daily:
        required_tables.append(TopicDailyMemory.__tablename__)
    if include_long:
        required_tables.append(TopicLongMemory.__tablename__)

    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    missing = tuple(table_name for table_name in required_tables if table_name not in existing_tables)
    return RetrievableMemoryBackfillSchemaStatus(missing_tables=missing)


def format_schema_status(status: RetrievableMemoryBackfillSchemaStatus) -> str:
    missing_tables = ",".join(status.missing_tables) if status.missing_tables else "none"
    return f"schema_ready={str(status.schema_ready).lower()} missing_tables={missing_tables}"


def _add_stats_lines(lines: list[str], stats: RetrievableMemoryBackfillStats) -> None:
    visibility = stats.by_visibility or {}
    visibility_text = ",".join(f"{key}:{visibility[key]}" for key in sorted(visibility)) or "none"
    lines.append(
        " ".join(
            [
                f"source={stats.source}",
                f"source_rows={stats.source_rows}",
                f"created={stats.created}",
                f"updated={stats.updated}",
                f"unchanged={stats.unchanged}",
                f"skipped={stats.skipped}",
                f"by_visibility={visibility_text}",
            ]
        )
    )


def format_result(result: RetrievableMemoryBackfillResult) -> str:
    lines = [
        "retrievable_memory_backfill",
        f"mode={'dry_run' if result.dry_run else 'apply'}",
        (
            f"total source_rows={result.total_source_rows} created={result.total_created} "
            f"updated={result.total_updated} skipped={result.total_skipped}"
        ),
    ]
    _add_stats_lines(lines, result.daily_memory)
    _add_stats_lines(lines, result.long_memory)
    return "\n".join(lines)


def run_backfill(
    *,
    database_url: str,
    dry_run: bool,
    include_daily: bool = True,
    include_long: bool = True,
) -> RetrievableMemoryBackfillResult:
    status = check_schema_ready(database_url=database_url, include_daily=include_daily, include_long=include_long)
    if not status.schema_ready:
        raise RetrievableMemoryBackfillSchemaError(status)

    session_factory = create_session_factory(database_url)
    with session_factory() as session:
        return RetrievableMemoryRepository(session).backfill_from_summarized_memories(
            dry_run=dry_run,
            include_daily=include_daily,
            include_long=include_long,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill retrievable_memories from summarized daily/long memories only."
    )
    parser.add_argument("--database-url", default=None, help="SQLAlchemy DATABASE_URL override; defaults to configured DATABASE_URL")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Write created/updated retrievable memory rows")
    mode.add_argument("--dry-run", action="store_true", help="Preview counts only; this is the default")
    parser.add_argument("--daily-only", action="store_true", help="Backfill only topic_daily_memories")
    parser.add_argument("--long-only", action="store_true", help="Backfill only topic_long_memories")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.daily_only and args.long_only:
        parser.error("--daily-only and --long-only are mutually exclusive")

    database_url = args.database_url or get_settings().database_url
    dry_run = not bool(args.apply)
    include_daily = not bool(args.long_only)
    include_long = not bool(args.daily_only)
    try:
        result = run_backfill(
            database_url=database_url,
            dry_run=dry_run,
            include_daily=include_daily,
            include_long=include_long,
        )
    except RetrievableMemoryBackfillSchemaError as exc:
        prefix = "retrievable_memory_backfill"
        mode = f"mode={'dry_run' if dry_run else 'apply'}"
        print(f"{prefix}\n{mode}\n{format_schema_status(exc.status)}")
        return 0 if dry_run else 2
    print(format_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
