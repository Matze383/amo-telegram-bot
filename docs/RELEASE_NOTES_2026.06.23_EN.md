# Release Notes 2026.06.23

---

## Overview

This release moves the production database baseline toward PostgreSQL with pgvector for Current-Info storage and semantic retrieval. SQLite remains supported for local development and tests; MariaDB/MySQL is documented as a legacy migration source.

### New

- **PostgreSQL baseline:** Added Alembic scaffolding and migration support for the current SQLAlchemy schema.
- **pgvector storage:** Added PostgreSQL-backed Current-Info vector storage with pgvector table/index setup.
- **Vector reindex CLI:** Added a Current-Info vector reindex command so vectors can be regenerated from stored PostgreSQL chunks.
- **CI readiness:** Added pull request, `main` push, manual workflow triggers, and a PostgreSQL/pgvector readiness job that runs migrations and focused tests.

### Upgrade Notes for Admins

1. **Version:** Package version is `2026.6.23`.
2. **Database:** Set `DATABASE_URL=postgresql+psycopg://...` for production PostgreSQL deployments.
3. **Extensions:** Ensure `vector`, `pg_trgm`, and `pgcrypto` are available before cutover. TimescaleDB remains optional.
4. **Migrations:** Run `alembic upgrade head` against the target database before production use.
5. **Vectors:** Reindex Current-Info vectors from PostgreSQL document chunks after migration. Older Qdrant points are not a restore source.
6. **Rollback:** Downgrades are destructive for AMO-owned tables and must only be used with a verified backup and rollback plan.

### QA Status

- GitHub CI passed on PR #95 with both `test` and `postgres-readiness`.
- Local focused validation covered Current-Info vector behavior, Alembic migration tests, eval subprocess import behavior, compile checks, and diff whitespace checks.

### Known Limitations

- Live production cutover still needs an environment-specific PostgreSQL backup, migration, and smoke-test window.
- SQLite remains useful for local tests/dev but is not the intended production baseline for this release path.

---

*Last updated: 2026-06-23*
