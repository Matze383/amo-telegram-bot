# Versionshinweise 2026.06.03

---

## Übersicht

Dieses Release bringt erhebliche Verbesserungen bei der Web-Recherche-Zuverlässigkeit, Datenbank-Skalierbarkeit und Memory-Verwaltung. Besonders hervorzuheben sind die neue Learning-Feedback-Memory-Funktion und verbesserte Scoped-Memory-Recall-Fähigkeiten.

### Neu

- **Auto-Web-Research/Zuverlässigkeit:** Klarere Unterscheidung zwischen erfolgreicher Websuche und tatsächlich verifizierbaren aktuellen Werten; Fail-Closed-Verhalten bei nicht verfügbaren Web-Ergebnissen.
- **Bounded Web-Extraktion:** Automatische Web-Recherche begrenzt auf konfigurierte Limits (SearXNG → statische Extraktion → optionaler Browser-Fallback).
- **Feedback-gesteuerte Follow-up-Recherche:** Nutzer können mit Phrasen wie "such weiter", "andere Quellen", "öffne/prüfe die Quellen" eine weitere Recherche-Runde auslösen.
- **Erweiterte Web-Research-Trigger:** Mehr Intent-Typen lösen automatische Web-Recherche aus (Sport-Ergebnisse, aktuelle externe Fakten, generische Aktualitäts-Klassifizierung).
- **Retry bei leeren Ergebnissen:** Einmaliger Retry bei leerer Auto-Websearch.

### Datenbank

- **MariaDB/MySQL-Support:** Vollständige und robuste MariaDB/MySQL-Unterstützung neben SQLite. Für zukünftige Multi-Instance-/Produktions-Deployments nach Abschluss von Backup-, Security- und Load-Testing-Gates vorbereitet/empfohlen.
- **Migrations-Tooling:** Dry-Run-Migration mit Tabellennamen, Zeilenzahlen und Status-Übersicht (keine Memory-Inhalte).
- **Legacy Null-Handling:** Robuste Behandlung von Legacy-Null-Werten während der Migration.
- **Source-of-Truth:** SQLite bleibt Standard für lokale Instanzen; MariaDB für zukünftige Multi-Instance-/Produktions-Deployments nach Abschluss der Härtungs-Gates empfohlen.

### Memory

- **Scoped Retrievable Memory:** Memory-Antworten werden strikt nach Scope (Topic/Gruppe/Privat) isoliert.
- **Backfill aus Daily Summaries:** Migration bestehender täglicher Zusammenfassungen in abrufbares Memory.
- **Explicit `/remember` Command:** Manuelles Speichern wichtiger Präferenzen und Fakten.
- **Global Manual Memory v1 deaktiviert:** In v1 sind globale manuelle Memories deaktiviert; Speicherung nur nach explizitem Wunsch.

**Wichtige Einschränkung:** Memories sind **scoped/untrusted context** und können **nicht** die Anforderung an aktuelle Live-Web-Daten überschreiben.

### Learning Feedback Memory v1

- **Explizites Lernen aus Feedback:** Quellen-Präferenzen, Korrekturen zu Chart-Analysen und Ergebnissen, Ansatz-Präferenzen.
- **Scope-begrenzt:** Lernen ist auf Topic/Chat/User begrenzt — kein globales Lernen in v1.
- **Emoji-Reaktionen als schwache Signale:** Telegram-Reaktionen/Smileys werden als schwache Engagement-/Feedback-Signale verstanden — niedrige Konfidenz, scope-begrenzt.

**Opt-out:** Wer keine Reaktions-Feedback-Lernung wünscht, sollte Bot-Nachrichten nicht mit Emoji reagieren oder explizit korrigierenden Text senden.

### Auto-Web-Research: Provider-Registry & Quality-Gates

- **Source/Provider Registry:** Interne Registry für Weather- und Crypto-Provider mit definierten Default-Kandidaten.
- **Weather-Provider:** Open-Meteo (primär) + wttr.in (Fallback) für Wetterabfragen.
- **Crypto-Provider:** CoinGecko (primär) + Binance public ticker (Fallback), bewusst eng auf BTC/ETH in USD/USDT begrenzt; unbekannte Assets oder EUR-Paare führen zu Fail-Closed-Verhalten.
- **Health-Monitoring:** Provider-Health wird über den Prozesslauf geteilt; DB Session/Quota Repository wird pro Execute frisch initialisiert.
- **Fail-Closed:** Stock/Sports-Daten bleiben ohne strukturierte Provider fail-closed (keine unsicheren Annahmen).
- **Quota/Audit:** Metadata-only Persistenz für Audit-Zwecke; keine sensitiven Daten in Logs.

### Sicherheit & Privacy

- Keine Secrets in Release-Dokumentation.
- Memory-Scope-Isolation: Kein Cross-Scope-Zugriff.
- Daily Memory und Dreaming teilen das gleiche Nachtfenster (02:00–05:00 Europe/Berlin).

### Upgrade-Hinweise für Admins

1. **MariaDB-Migration (optional):**
   ```bash
   pip install pymysql
   python -m amo_bot.db.migrate \
     --source-url sqlite:///./data/amo_bot.db \
     --target-url 'mysql+pymysql://amo_bot:<pass>@<host>:3306/amo_bot?charset=utf8mb4' \
     --dry-run
   ```
   Nach Backup-Prüfung: `--dry-run` entfernen.

2. **Retrievable Memory Backfill (nach Migration):**
   ```bash
   python -m amo_bot.db.retrievable_memory_backfill --dry-run
   # Nach Prüfung:
   python -m amo_bot.db.retrievable_memory_backfill --apply
   ```

3. **SearXNG für aktuelle Daten:**
   - `SEARXNG_BASE_URL` konfigurieren für Auto-Web-Research.
   - Nur HTTPS-URLs für öffentliche Endpunkte erlaubt.

4. **Learning Feedback Memory:**
   - Emoji-Reaktionen sind schwache, scope-begrenzte Signale.
   - Für wichtige Präferenzen: `/remember` verwenden.

### Bekannte Einschränkungen

- Memories können Live-Web-Evidenz nicht ersetzen (Fail-Closed für aktuelle Daten).
- Kein globales Memory-Lernen in v1 (nur scoped).
- Daily Memory und Dreaming teilen das Nachtfenster — Ressourcen-Konflikte möglich bei gleichzeitiger Aktivierung.

### Betriebsnotizen

- SQLite bleibt empfohlener Standard für lokale Instanzen.
- MariaDB ist für zukünftige Multi-Instance-/Produktions-Deployments nach Backup-, Security- und Load-Gates vorbereitet.
- Keine Breaking Changes für Endnutzer erwartet.

---

*Letzte Aktualisierung: 2026-06-03*
