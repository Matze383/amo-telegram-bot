# Versionshinweise 2026.06.23

---

## Übersicht

Dieses Release stellt die produktive Datenbank-Basis in Richtung PostgreSQL mit pgvector für Current-Info-Speicherung und semantisches Retrieval um. SQLite bleibt für lokale Entwicklung und Tests unterstützt; MariaDB/MySQL ist als Legacy-Migrationsquelle dokumentiert.

### Neu

- **PostgreSQL-Baseline:** Alembic-Grundstruktur und Migration für das aktuelle SQLAlchemy-Schema ergänzt.
- **pgvector-Speicher:** PostgreSQL-basierter Current-Info Vector Store mit pgvector-Tabellen-/Index-Setup ergänzt.
- **Vector-Reindex-CLI:** Neuer Current-Info Reindex-Befehl, um Vektoren aus gespeicherten PostgreSQL-Chunks neu zu erzeugen.
- **CI-Readiness:** CI-Trigger für Pull Requests, Push auf `main` und manuelle Läufe ergänzt; neuer PostgreSQL/pgvector-Readiness-Job führt Migrationen und fokussierte Tests aus.

### Upgrade-Hinweise für Admins

1. **Version:** Paketversion ist `2026.6.23`.
2. **Datenbank:** Für produktive PostgreSQL-Deployments `DATABASE_URL=postgresql+psycopg://...` setzen.
3. **Extensions:** Vor dem Cutover sicherstellen, dass `vector`, `pg_trgm` und `pgcrypto` verfügbar sind. TimescaleDB bleibt optional.
4. **Migrationen:** Vor produktiver Nutzung `alembic upgrade head` gegen die Ziel-Datenbank ausführen.
5. **Vektoren:** Current-Info-Vektoren nach der Migration aus PostgreSQL-Dokumentchunks neu indexieren. Ältere Qdrant-Punkte sind keine Restore-Quelle.
6. **Rollback:** Downgrades sind destruktiv für AMO-eigene Tabellen und dürfen nur mit geprüftem Backup- und Rollback-Plan genutzt werden.

### QA-Status

- GitHub CI auf PR #95 ist mit `test` und `postgres-readiness` grün.
- Lokale fokussierte Validierung deckte Current-Info Vector-Verhalten, Alembic-Migrationstests, Eval-Subprocess-Import, Compile-Checks und Whitespace-Diff-Checks ab.

### Bekannte Einschränkungen

- Der echte Produktions-Cutover braucht weiterhin ein umgebungsspezifisches PostgreSQL-Backup, Migration und Smoke-Test-Fenster.
- SQLite bleibt für lokale Tests/Dev sinnvoll, ist aber nicht die vorgesehene produktive Basis dieses Release-Pfads.

---

*Letzte Aktualisierung: 2026-06-23*
