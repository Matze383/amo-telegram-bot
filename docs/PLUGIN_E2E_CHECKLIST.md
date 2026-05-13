# Plugin E2E Checklist / Plugin End-to-End Checkliste

## Deutsch

### Übersicht

Diese Checkliste dient als Referenz für die manuelle und automatisierte Verifizierung von Plugin-Funktionalität im AMO-System.

### Zwingende E2E-Gate-Sequenz

Alle E2E-Tests MÜSSEN dieser nummerierten Sequenz folgen:

1. **Discovery** – Plugin wird vom Discovery-Service erkannt
2. **Registrierung** – Plugin erscheint im WebUI mit korrekten Metadaten
3. **Freigabe** – Berechtigungsprüfung bestanden, Aktivierung genehmigt
4. **Aktivierung** – Plugin-Status wechselt zu `active`
5. **Triggerlauf** – Plugin wird ausgeführt (user-triggered, cron oder interval)
6. **Audit** – Ausführung wird im Audit-Trail protokolliert

### Pre-Flight Checks

- [ ] Plugin-Metadaten validiert (plugin.yaml Schema per PLUGIN_CONTRACT.md Abschnitt 2)
- [ ] Plugin-Code syntaktisch korrekt (Python)
- [ ] Erforderliche Felder vorhanden (id, name, version, description, entrypoint, min_bot_version, triggers)
- [ ] Keine verbotenen Imports oder Operationen

### Discovery & Registrierung

- [ ] Plugin wird vom Discovery-Service erkannt
- [ ] Plugin wird im WebUI angezeigt (Admin/Owner)
- [ ] Status korrekt: `registered` oder `activation_pending`
- [ ] Plugin-Metadaten korrekt angezeigt

### Aktivierung

- [ ] Aktivierungs-Button verfügbar (Admin/Owner)
- [ ] Berechtigungsprüfung erfolgreich
- [ ] Status-Wechsel zu `active`
- [ ] Plugin erscheint in aktiver Plugin-Liste
- [ ] Bestätigungsnachricht angezeigt

### Deaktivierung

- [ ] Deaktivierungs-Button verfügbar
- [ ] Status-Wechsel zu `disabled`
- [ ] Plugin nicht mehr aktiv, aber in Liste sichtbar
- [ ] Bestätigungsnachricht angezeigt

### Settings-Konfiguration

- [ ] Settings-Formular aus `settings_schema` generiert
- [ ] Textfelder korrekt gerendert
- [ ] Secret-Felder maskiert (****)
- [ ] Select/Dropdown-Felder funktionieren
- [ ] Validierung bei Fehleingaben
- [ ] Speichern persistiert Werte korrekt
- [ ] Change-Feedback angezeigt

### Scope & Berechtigungen

- [ ] Globale Scope-Einstellungen verfügbar (Owner)
- [ ] Gruppen-Scope-Einstellungen verfügbar (Group-Admin)
- [ ] Topic-Scope-Einstellungen verfügbar
- [ ] Plugin-Minimum-Role wird respektiert
- [ ] Einschränkungen unter Minimum werden blockiert

### Command-Plugins

- [ ] Command wird in der Befehlsliste angezeigt
- [ ] Hilfe-Text korrekt
- [ ] Befehl wird ausgeführt bei Trigger
- [ ] Rückgabewert wird verarbeitet
- [ ] Fehler werden abgefangen und geloggt

### Scheduled-Plugins (cron, interval)

- [ ] Trigger-Typ korrekt erkannt (cron, interval oder user-triggered per PLUGIN_CONTRACT.md Abschnitt 12)
- [ ] Cron-Ausdruck bzw. Intervall korrekt interpretiert
- [ ] Ausführung zum definierten Zeitpunkt (cron) oder im definierten Intervall
- [ ] Log-Eintrag bei Ausführung
- [ ] Fehlerbehandlung bei Ausführungsfehlern
- [ ] Nächste Ausführung korrekt berechnet

### Secret-Verwaltung

- [ ] Secrets werden verschlüsselt gespeichert
- [ ] Secrets werden entschlüsselt bei Plugin-Ausführung
- [ ] Secrets sind nicht im Klartext sichtbar
- [ ] Secret-Referenzen (`$SECRET:name`) werden aufgelöst

### Health & Monitoring

- [ ] Letzte Ausführung wird angezeigt
- [ ] Fehler werden im Status-Bereich angezeigt
- [ ] Fehler-Details einsehbar für Admin/Owner
- [ ] Health-Status korrekt berechnet

### Cleanup

- [ ] Plugin-Daten werden bei Deinstallation gelöscht
- [ ] Plugin-Tabellen/Collections werden entfernt
- [ ] Keine verwaisten Datensätze

---

## English

### Overview

This checklist serves as a reference for manual and automated verification of plugin functionality in the AMO system.

### Mandatory E2E Gate Sequence

All E2E tests MUST follow this numbered sequence:

1. **Discovery** – Plugin is recognized by discovery service
2. **Registration** – Plugin appears in WebUI with correct metadata
3. **Release** – Permission check passed, activation approved
4. **Activation** – Plugin status transitions to `active`
5. **Trigger Run** – Plugin executes (user-triggered, cron, or interval)
6. **Audit** – Execution is logged in audit trail

### Pre-Flight Checks

- [ ] Plugin metadata validated (plugin.yaml schema per PLUGIN_CONTRACT.md section 2)
- [ ] Plugin code syntactically correct (Python)
- [ ] Required fields present (id, name, version, description, entrypoint, min_bot_version, triggers)
- [ ] No forbidden imports or operations

### Discovery & Registration

- [ ] Plugin recognized by discovery service
- [ ] Plugin displayed in WebUI (Admin/Owner)
- [ ] Status correct: `registered` or `activation_pending`
- [ ] Plugin metadata displayed correctly

### Activation

- [ ] Activation button available (Admin/Owner)
- [ ] Permission check successful
- [ ] Status transition to `active`
- [ ] Plugin appears in active plugin list
- [ ] Confirmation message displayed

### Deactivation

- [ ] Deactivation button available
- [ ] Status transition to `disabled`
- [ ] Plugin no longer active but visible in list
- [ ] Confirmation message displayed

### Settings Configuration

- [ ] Settings form generated from `settings_schema`
- [ ] Text fields rendered correctly
- [ ] Secret fields masked (****)
- [ ] Select/dropdown fields work
- [ ] Validation on invalid input
- [ ] Save persists values correctly
- [ ] Change feedback displayed

### Scope & Permissions

- [ ] Global scope settings available (Owner)
- [ ] Group scope settings available (Group-Admin)
- [ ] Topic scope settings available
- [ ] Plugin minimum role respected
- [ ] Restrictions below minimum are blocked

### Command Plugins

- [ ] Command displayed in command list
- [ ] Help text correct
- [ ] Command executes on trigger
- [ ] Return value processed
- [ ] Errors caught and logged

### Scheduled Plugins (cron, interval)

- [ ] Trigger type correctly recognized (cron, interval, or user-triggered per PLUGIN_CONTRACT.md section 12)
- [ ] Cron expression or interval correctly interpreted
- [ ] Execution at defined time (cron) or in defined interval
- [ ] Log entry on execution
- [ ] Error handling on execution failures
- [ ] Next execution calculated correctly

### Secret Management

- [ ] Secrets stored encrypted
- [ ] Secrets decrypted on plugin execution
- [ ] Secrets not visible in plaintext
- [ ] Secret references (`$SECRET:name`) resolved

### Health & Monitoring

- [ ] Last execution displayed
- [ ] Errors shown in status area
- [ ] Error details visible to Admin/Owner
- [ ] Health status calculated correctly

### Cleanup

- [ ] Plugin data deleted on uninstall
- [ ] Plugin tables/collections removed
- [ ] No orphaned records

---

## Negative Test Cases / Negative Testfälle

### Deutsch

| Testfall | Erwartetes Verhalten |
|----------|---------------------|
| Ungültiges Manifest | Plugin-Status `invalid`, kein Registrierungsversuch |
| Rechteverletzung (Normal-Nutzer Aktivierung) | 403 Forbidden, Audit-Event `policy_denied` |
| DB-Policy-Verstoß (Schreiben in Core-DB) | Blockiert, Fehler `DB_ACCESS_DENIED` |
| FS-Policy-Verstoß (Zugriff auf `.env`) | Blockiert, Fehler `FS_ACCESS_DENIED` |
| Path Traversal (`../../etc/passwd`) | Blockiert, Fehler `FS_PATH_TRAVERSAL` |
| Ungültiger Cron-Ausdruck | Status `invalid`, Aktivierung blockiert |
| Zu kurzes Intervall (< 60s Prod) | Validierungsfehler, Aktivierung blockiert |
| Fehlende Pflicht-Settings | Aktivierung blockiert, Fehlermeldung angezeigt |
| Secret im Klartext loggen | Blockiert, Audit-Event `logging.violation` |
| Plugin mit ID-Kollision | Status `blocked`, ERROR-Log |

### English

| Test Case | Expected Behavior |
|-----------|-------------------|
| Invalid manifest | Plugin status `invalid`, no registration attempt |
| Rights violation (Normal user activation) | 403 Forbidden, audit event `policy_denied` |
| DB policy violation (write to core DB) | Blocked, error `DB_ACCESS_DENIED` |
| FS policy violation (access `.env`) | Blocked, error `FS_ACCESS_DENIED` |
| Path traversal (`../../etc/passwd`) | Blocked, error `FS_PATH_TRAVERSAL` |
| Invalid cron expression | Status `invalid`, activation blocked |
| Too short interval (< 60s prod) | Validation error, activation blocked |
| Missing required settings | Activation blocked, error message shown |
| Logging secret in plaintext | Blocked, audit event `logging.violation` |
| Plugin with ID collision | Status `blocked`, ERROR log |

---

## Version

**Version:** 1.0.0  
**Created:** 2026-05-13  
**Applies to:** Block 13 – Plugin E2E Testing
