# Release Notes 2026.05.26-Beta / Versionshinweise 2026.05.26-Beta

---

## 🇩🇪 Deutsch

### Übersicht

Dieses Release härtet den YouTube-RSS-Plugin-Stack und die Sandbox-Runtime ab. Neue Kommandos ergänzen die bestehenden Varianten, der Handle-/Channel-ID-Resolver wurde robuster gemacht, und die Diagnose-Ausgaben bleiben privacy-safe.

### Neu

- **YT-RSS Commands:** `/addyt` und `/delyt` zum Hinzufügen und Entfernen von YouTube-RSS-Feeds.
- **YouTube Handle/Channel-ID Resolver Härtung:** Verbesserte Auflösung von YouTube-Handles und Channel-IDs mit robusterer Fehlerbehandlung.
- **Scheduler Cursor & Backlog:** Verbessertes Cursor-Verhalten und Backlog-Verarbeitung für zuverlässigere Feed-Updates.
- **Legacy Handle Migration & Deduplizierung:** Automatische Migration und Deduplizierung von Legacy-Handles.

### Sicherheit & Privacy

- **Safe Diagnostics & Log Redaction:** Diagnose-Ausgaben enthalten keine sensiblen Daten; automatische Redaktion von Tokens und persönlichen Identifikatoren.
- **No Callback/UI Reintro:** Keine Re-Introduktion von Callback- oder UI-Code; alle Interaktionen über Sandbox-Runtime geregelt.
- **Sandbox/Runtime RSS Support:** RSS-Fetching läuft vollständig innerhalb der Sandbox mit Capability-Gating.

### Architektur / Interna

- **Sandbox Runtime Tests:** Erweiterte Tests für die Sandbox-Runtime mit RSS-Feed-Handling.
- **Capability-Gating:** Alle RSS-Operationen unterliegen strikter Capability-Prüfung (`rss.fetch`).

### Betriebsnotizen

- Keine Breaking Changes für Endnutzer.
- Alle RSS-Operationen durchlaufen jetzt die Sandbox-Runtime.
- Legacy-Handles werden automatisch migriert und dedupliziert.

---

*Letzte Aktualisierung: 2026-05-26*
