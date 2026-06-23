from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import TextIO

from sqlalchemy import select

from amo_bot.config.settings import get_settings
from amo_bot.current_info.vector import build_current_info_vector_components_from_settings
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import CurrentInfoDocumentChunk


@dataclass(frozen=True)
class ReindexResult:
    scanned_count: int
    indexed_count: int


def reindex_current_info_vectors(
    *,
    database_url: str | None = None,
    batch_size: int = 100,
    limit: int | None = None,
    out: TextIO | None = None,
) -> ReindexResult:
    settings = get_settings()
    target_url = database_url or settings.database_url
    init_db(target_url)
    session_factory = create_session_factory(target_url)
    components = build_current_info_vector_components_from_settings(settings, session_factory=session_factory)
    if components is None:
        raise RuntimeError("AMO_VECTOR_ENABLED=true and AMO_VECTOR_PROVIDER=postgres are required for reindexing")
    indexer = components[0]

    safe_batch_size = max(1, min(int(batch_size or 100), 1000))
    max_rows = None if limit is None else max(0, int(limit))
    last_id = 0
    scanned = 0
    indexed = 0

    while True:
        remaining = None if max_rows is None else max_rows - scanned
        if remaining is not None and remaining <= 0:
            break
        current_batch_size = safe_batch_size if remaining is None else min(safe_batch_size, remaining)
        with session_factory() as session:
            rows = tuple(
                session.scalars(
                    select(CurrentInfoDocumentChunk)
                    .where(
                        CurrentInfoDocumentChunk.id > last_id,
                        CurrentInfoDocumentChunk.text_excerpt != "",
                    )
                    .order_by(CurrentInfoDocumentChunk.id.asc())
                    .limit(current_batch_size)
                ).all()
            )
        if not rows:
            break
        scanned += len(rows)
        last_id = int(rows[-1].id)
        indexer.upsert_chunks(rows)
        indexed += len(rows)
        if out is not None:
            out.write(f"indexed={indexed} last_chunk_id={last_id}\n")

    return ReindexResult(scanned_count=scanned, indexed_count=indexed)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild Current-Info pgvector embeddings from stored DB chunks")
    parser.add_argument("--database-url", default=None, help="SQLAlchemy DATABASE_URL override; defaults to configured DATABASE_URL")
    parser.add_argument("--batch-size", type=int, default=100, help="Chunks embedded per batch")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of chunks to process")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = reindex_current_info_vectors(
            database_url=args.database_url,
            batch_size=args.batch_size,
            limit=args.limit,
            out=sys.stdout,
        )
    except Exception as exc:
        parser.exit(1, f"error: {exc.__class__.__name__}: {exc}\n")
    sys.stdout.write(f"done scanned={result.scanned_count} indexed={result.indexed_count}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
