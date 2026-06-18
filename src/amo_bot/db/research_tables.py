from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import TextIO

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import SQLAlchemyError

from amo_bot.db.base import _engine_kwargs_for_url
from amo_bot.db.models import (
    ResearchEvalCase,
    ResearchProvider,
    ResearchProviderHealth,
    ResearchSourceObservation,
    ResearchSourcePreference,
)


RESEARCH_TABLES = (
    ResearchProvider.__table__,
    ResearchProviderHealth.__table__,
    ResearchSourceObservation.__table__,
    ResearchSourcePreference.__table__,
    ResearchEvalCase.__table__,
)
RESEARCH_TABLE_NAMES = tuple(table.name for table in RESEARCH_TABLES)


@dataclass(frozen=True)
class ResearchTablesMigrationResult:
    dry_run: bool
    existing_tables: tuple[str, ...]
    created_tables: tuple[str, ...]
    missing_tables: tuple[str, ...]


def _write_result(result: ResearchTablesMigrationResult, out: TextIO) -> None:
    mode = "DRY-RUN" if result.dry_run else "APPLY"
    out.write(f"mode={mode} targeted_tables={len(RESEARCH_TABLE_NAMES)}\n")
    out.write(f"existing_tables={','.join(result.existing_tables) or '-'}\n")
    if result.dry_run:
        out.write(f"missing_tables={','.join(result.missing_tables) or '-'}\n")
    else:
        out.write(f"created_tables={','.join(result.created_tables) or '-'}\n")


def ensure_research_tables(
    database_url: str,
    *,
    dry_run: bool = False,
    out: TextIO | None = None,
) -> ResearchTablesMigrationResult:
    """Create only the research provider/eval tables, leaving all other tables untouched."""

    engine = create_engine(database_url, **_engine_kwargs_for_url(database_url))
    try:
        inspector = inspect(engine)
        before = set(inspector.get_table_names())
        existing = tuple(table_name for table_name in RESEARCH_TABLE_NAMES if table_name in before)
        missing = tuple(table_name for table_name in RESEARCH_TABLE_NAMES if table_name not in before)

        if dry_run:
            result = ResearchTablesMigrationResult(
                dry_run=True,
                existing_tables=existing,
                created_tables=(),
                missing_tables=missing,
            )
            if out is not None:
                _write_result(result, out)
            return result

        if missing:
            ResearchProvider.metadata.create_all(bind=engine, tables=RESEARCH_TABLES, checkfirst=True)

        after = set(inspect(engine).get_table_names())
        created = tuple(table_name for table_name in RESEARCH_TABLE_NAMES if table_name not in before and table_name in after)
        still_missing = tuple(table_name for table_name in RESEARCH_TABLE_NAMES if table_name not in after)
        result = ResearchTablesMigrationResult(
            dry_run=False,
            existing_tables=existing,
            created_tables=created,
            missing_tables=still_missing,
        )
        if out is not None:
            _write_result(result, out)
        return result
    finally:
        engine.dispose()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create only the AMO research provider/eval tables if they are missing"
    )
    parser.add_argument("--database-url", required=True, help="Target SQLAlchemy URL")
    parser.add_argument("--dry-run", action="store_true", help="Report existing/missing tables without creating them")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        result = ensure_research_tables(args.database_url, dry_run=args.dry_run, out=sys.stdout)
    except SQLAlchemyError as exc:
        parser.exit(1, f"database error: {exc.__class__.__name__}\n")

    if result.missing_tables and not result.dry_run:
        parser.exit(1, f"error: tables still missing after migration: {','.join(result.missing_tables)}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
