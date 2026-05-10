# Release 2026.05.10-Beta

**Status:** Beta / MVP — Private Beta — Nicht Produktionsreif  
**Tag:** `2026.05.10-Beta` auf `8bef4c1`

---

## Zusammenfassung

Dieses Release baut auf `2026.05.09-Beta` auf mit verbesserter gruppenspezifischer Rollenverwaltung, vollständiger WebUI-Integration für Gruppenrollen, verbesserter Audit-Trail-Abdeckung und aufgeräumter bilingualer Dokumentation. Der Fokus liegt weiterhin auf Stabilität und Nachvollziehbarkeit für Multi-Group-Deployments.

---

## Highlights

- **Gruppenspezifische Rollen**: Vollständige Trennung zwischen globalen/privaten Rollen und Gruppenrollen
- **WebUI-Gruppenverwaltung**: Vollständiges CRUD für Gruppenrollen über die Weboberfläche
- **Audit-Events**: Alle Gruppenrollen-Änderungen sind jetzt mit Quellenangabe nachvollziehbar
- **Dokumentation**: Umstrukturierte bilingual öffentliche Dokumentation für bessere Klarheit

---

## Was ist neu

### Gruppenspezifische Rollen

Das Rollensystem trennt jetzt ordnungsgemäß zwischen globalen Rollen und gruppen-spezifischen Rollen:

- **Globale Rollen** (`owner`, `admin`, `vip`, `normal`, `ignore`) gelten in privaten Chats
- In Gruppen überschreiben globaler `owner` und globales `ignore` immer; sonst gilt eine gruppenspezifische Rolle oder der Nutzer fällt auf `normal (default)` zurück
- **`/role`-Befehl** ist jetzt gruppenbewusst:
  - In DMs: Zeigt globale Rolle
  - In Gruppen: Zeigt effektive Rolle und deren Quelle (global vs. diese Gruppe)
- **`/setrole`-Befehl** respektiert den Gruppenkontext:
  - In DMs: Setzt globale Rolle
  - In Gruppen: Setzt Rolle nur für diese spezifische Gruppe
- **Berechtigungsgrenzen**: Gruppen-Admins dürfen nur `vip`, `normal`, `ignore` in ihrer eigenen Gruppe setzen; `admin` und `owner` erfordern globale Berechtigungen
- **Gruppenübergreifende Isolation**: Admin-Status in Gruppe A verleiht keinen Admin-Status in Gruppe B

### WebUI Gruppenrollenverwaltung

Die WebUI enthält jetzt eine vollständige Gruppenrollenverwaltung:

- **Gruppen-Übersichtsseite**: Listet alle Gruppen/Supergruppen auf, in denen der Bot präsent ist
- **Nutzer-Rollen-Anzeige**: Zeigt aktuelle Gruppenrolle für jedes Mitglied oder `normal (default)` falls keine gesetzt
- **Rollenzuweisung**: `admin`, `vip`, `normal`, `ignore` können via Dropdown zugewiesen werden
- **Owner-Schutz**: `owner`-Rolle kann nicht als Gruppenrolle vergeben werden (bleibt `.env`-only)
- **Rollen löschen**: Setzen auf `normal` entfernt den gruppenspezifischen Eintrag, Fallback auf globale Rolle
- **Sicherheit**: Alle Mutationen erfordern Login + CSRF-Token + Owner-Gate
- **Live getestet**: Verifiziert in echten Telegram-Gruppen und -Supergruppen

### Audit-Events & Nachvollziehbarkeit

Gruppenrollen-Änderungen sind jetzt vollständig auditierbar:

- **Neue Audit-Event-Typen**:
  - `group_role_set` — Wenn eine Gruppenrolle zugewiesen oder geändert wird
  - `group_role_clear` — Wenn eine Gruppenrolle entfernt wird (Fallback auf global)
- **Quellen-Tracking**: Unterscheidet zwischen `telegram_command` (Telegram-Bot-Befehle) und `webui` (Web-Oberfläche)
- **Vollständiger Payload**:
  - `chat_id` — Zielgruppe
  - `target_telegram_user_id` — Nutzer, dessen Rolle geändert wurde
  - `previous_role` — Rolle vor der Änderung (korrekt gemeldet auch bei Löschungen)
  - `new_role` — Rolle nach der Änderung
  - `source` — Ursprung der Änderung
- **Bugfix**: Vorherige Rolle wird jetzt korrekt in der Antwort gemeldet, wenn Gruppenrollen gelöscht werden

### Datenbank-Performance

- **Bulk Loading** für Gruppenrollen-Abfragen in der WebUI
- **Datenbank-Indizes** auf `chat_user_roles` für schnellere Lookups
- **`updated_at`-Härtung** stellt ordnungsgemäße Zeitstempel-Tracking bei Rollen-Mutationen sicher

### Dokumentation

- **Bilinguale Aufräumarbeiten**: Öffentliche Dokumentation umstrukturiert und gekürzt für bessere Lesbarkeit
- **Deutsch + Englisch**: Alle Release-Notes werden in beiden Sprachen gepflegt
- **Klarere Struktur**: Setup, Testing und Release-Dokumentation getrennt

---

## Tests

Letzter Testlauf: **141 passed**

---

## Bekannte Einschränkungen

- **Beta / MVP-Status**: Nicht produktionsreif; Sicherheitshärtung läuft
- **Nur SQLite**: Noch keine PostgreSQL oder andere Datenbank-Backends
- **Nur lokales Ollama**: Keine Cloud-KI-Anbieter integriert
- **Stateless `/ask`**: Kein Gesprächsverlauf wird gespeichert
- **Nur Text**: Keine Medienverarbeitung (Bilder, Dateien, Sprache)
- **Manuelle Plugin-Installation**: Plugins müssen manuell in `AMO_PLUGIN_DIR` platziert werden
- **Keine Kanäle**: Nur private Chats und Gruppen; Kanal-Support nicht implementiert

---

## Upgrade-Hinweise

### Von 2026.05.09-Beta

1. **Datenbank**: Schema migriert automatisch beim ersten Start (`group_roles`-Tabelle, Indizes)
2. **Keine Breaking Changes**: Bestehende globale Rollen bleiben gültig
3. **WebUI**: Neuer "Groups"-Menüpunkt erscheint automatisch

### Neustart

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env mit deinen Werten bearbeiten
python main.py
```

### Mit Cleanup (löscht Datenbank)

```bash
rm data/amo_bot.db
python main.py
```

---

## Checkliste für Tester

- [ ] Gruppenrollen-Befehle (`/role`, `/setrole`) funktionieren in DMs und Gruppen
- [ ] WebUI Groups-Seite lädt und zeigt aktive Gruppen
- [ ] Gruppenrollen-Änderungen via WebUI werden in Telegram übernommen
- [ ] Audit-Events erscheinen für alle Gruppenrollen-Mutationen
- [ ] Vorherige Rolle wird korrekt angezeigt beim Löschen von Gruppenrollen
- [ ] `owner`-Rolle kann nicht über Gruppenrollen-Verwaltung gesetzt werden
- [ ] Keine sensiblen Daten in Logs oder Antworten

---

## Vorheriges Release

Siehe [2026.05.09-Beta Release Notes](RELEASE_NOTES_2026.05.09-Beta_DE.md) für frühere Änderungen.
