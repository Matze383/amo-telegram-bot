"""PostgreSQL pgvector baseline

Revision ID: 20260623_0001
Revises:
Create Date: 2026-06-23 00:00:00 UTC
"""
from __future__ import annotations

from alembic import op

from amo_bot.db.base import Base
from amo_bot.db import models  # noqa: F401 - imported for metadata registration
from amo_bot.db.init_db import _init_postgresql_extensions_and_indexes


revision = "20260623_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name == "postgresql":
        _init_postgresql_extensions_and_indexes(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TABLE IF EXISTS current_info_chunk_vectors")
    Base.metadata.drop_all(bind=bind)
