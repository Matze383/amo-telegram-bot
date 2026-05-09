# Plugin-Integration Plan (AMO-telegram-bot)

## 1) Zielbild des Plugin-Systems

Ziel ist ein pragmatisches, sicheres Plugin-System für den AMO-telegram-bot, bei dem Plugins als einzelne Python-Skripte betrieben werden können. Das System soll drei Ausführungsarten unterstützen: per Telegram-Command, zeitgesteuert und dauerhaft laufend (Worker).

MVP-Ziel bis zur ersten Kundenvorlage:
- Plugins zuverlässig entdecken, validieren, aktivieren/deaktivieren
- Command-Plugins funktionsfähig machen
- Grundlegende, defensive Plugin-API bereitstellen (kein Rohzugriff)
- Sichtbarkeit im WebUI (Status, Typ, letzte Fehler)

Nicht-Ziel im MVP:
- Vollständige Sandbox mit Prozessisolation auf OS-Niveau
- Freie, ungeprüfte Python-Ausführung mit vollen Rechten
- Komplexe verteilte Scheduling-/Queue-Architektur

---

## 2) Plugin-Typen

### a) command plugin
Wird durch Telegram-Befehle ausgelöst (z. B. `/report`, `/digest`).

Eigenschaften:
- registriert einen oder mehrere Commands
- bekommt Command-Context (User, Chat, Argumente)
- Rückgabe über definierte Bot-Aktionen (z. B. `send_message`)

### b) scheduled plugin
Wird nach Zeitplan ausgeführt (Intervall oder feste Zeiten).

Eigenschaften:
- läuft ohne direkten User-Trigger
- erzeugt Logs/Audit-Einträge pro Lauf
- kann Nachrichten/Reports in definierte Ziele senden

### c) long-running / worker plugin
Startet dauerhaft und verarbeitet wiederkehrende Aufgaben.

Eigenschaften:
- eigener Lifecycle (start/stop/restart)
- Health-/Heartbeat-Status erforderlich
- kontrolliertes Shutdown-Verhalten

---

## 3) Plugin-Datei-/Ordnerstruktur (Python-Skripte)

Empfehlung:
- ein Plugin = ein Ordner
- mindestens ein Entry-Skript
- Manifest je Plugin

Beispielstruktur:
- `plugins/`
  - `weather_alert/`
    - `plugin.json` (Manifest)
    - `main.py` (Entrypoint)
    - `README.md` (optional, intern)
    - `config.schema.json` (optional)
  - `daily_digest/`
    - `plugin.json`
    - `main.py`

Konventionen:
- Entrypoint-Datei im Manifest explizit angeben (kein stilles Raten)
- pro Plugin eigene ID (Ordnername + Manifest-ID konsistent)
- keine Secrets im Plugin-Ordner; Konfig via kontrollierte Host-Config

---

## 4) Manifest-Schema Erweiterung

Pflichtfelder (MVP):
- `id`
- `name`
- `version`
- `description`
- `entrypoint` (z. B. `main.py`)
- `types` (Liste aus `command`, `scheduled`, `worker`)

Optionale/typabhängige Felder:
- `commands`:
  - Liste der Commands inkl. Name, Kurzbeschreibung, optional Argument-Schema
- `schedule`:
  - Intervall (z. B. Sekunden/Minuten) oder Cron-ähnliche Angabe (vereinfachtes Format im MVP)
- `roles_required`:
  - erlaubte Rollen je Plugin oder je Command
- `capabilities`:
  - benötigte Bot-API-Fähigkeiten (z. B. `send_message`, `delete_message`, `read_group_meta`)
- `config_keys`:
  - explizit erlaubte Konfig-Schlüssel für das Plugin

Validierungsregeln:
- unbekannte Felder warnen (nicht sofort hart brechen im MVP)
- fehlende Pflichtfelder = Plugin nicht ladbar
- Typ-spezifische Pflichtfelder prüfen (z. B. `commands` bei command plugin)

---

## 5) Plugin-Schnittstelle / API

Plugins bekommen **kein** direktes Vollobjekt auf Bot, DB oder OS, sondern ein kontrolliertes Kontextobjekt.

### Kontextobjekt (MVP)
Enthält mindestens:
- `plugin_id`, `run_id`, `trigger_type`
- User-/Chat-Kontext (bei Command-Trigger)
- read-only Runtime-Metadaten
- Zugriff auf erlaubte Host-Funktionen

### erlaubte Bot-Aktionen (defensive API)
- `send_message(chat_id, text, ...)`
- `reply(message_ref, text, ...)`
- optionale weitere Actions nur über Capability-Freigabe

### DB/Storage-Zugriff kontrolliert
- kein direkter SQL/ORM-Zugriff aus Plugins
- stattdessen Namespaced Key-Value/Document-Storage API pro Plugin
- harte Trennung der Plugin-Daten (Namespace by plugin_id)

### Logging/Audit
- strukturierte Logs pro Plugin-Run
- Audit-Events für:
  - enable/disable
  - command execution
  - schedule run
  - worker start/stop/crash
- Fehlertrace intern sichtbar, aber gegenüber Endnutzern reduziert ausgeben

---

## 6) Rechte-/Rollenmodell

Rollenbasis laut Projekt:
- Owner
- Admin
- VIP
- Normal
- Ignore

Regeln:
- Plugin aktivieren/deaktivieren: standardmäßig nur Owner/Admin
- Plugin-Ausführung per Command: abhängig von `roles_required` (Plugin- oder Command-spezifisch)
- `Ignore` darf nie Plugins triggern
- pro Command dürfen strengere Regeln gelten als globales Plugin-Level

Empfohlene Priorität:
1. Harte Systemregeln (z. B. Ignore-Block)
2. Plugin-Status (aktiv/inaktiv)
3. Rollenregel pro Command
4. Plugin-default Rollenregel

---

## 7) Ausführung / Lifecycle

### Discovery
- Scannen von `plugins/` auf Plugin-Ordner mit Manifest

### Validation
- Manifest-Schema prüfen
- Entry-Skript prüfen (existiert, importierbar)
- Typabhängige Felder prüfen

### Enable/Disable
- Status persistent speichern
- Disable stoppt laufende Worker und verhindert neue Dispatches

### Load/Unload
- kontrolliertes Laden des Entrypoints
- Unload räumt Runtime-Referenzen auf

### Command Dispatch
- Command-Mapping aus Manifest
- Rollenprüfung vor Ausführung
- Ausführung mit Timeout + Fehlerisolation

### Scheduler Dispatch
- fällige Jobs ermitteln
- Run starten, Ergebnis/Audit speichern
- nächste Ausführung persistieren

### Worker Start/Stop/Restart
- dedizierter Worker-Manager
- Restart mit Backoff bei Crash-Schleifen
- Zustand im UI sichtbar machen

---

## 8) Sicherheit

MVP-Sicherheitsprinzipien:
- keine ungeprüfte Vollzugriff-Ausführung
- nur defensive Host-API statt Rohobjekte
- Zeitlimits pro Command-/Scheduled-Run
- Rate-Limits pro Plugin/Command
- Fehlerisolation: Plugin-Fehler darf Bot-Kern nicht reißen
- Secrets nicht global injizieren; nur explizit freigegebene Config-Keys
- Validierung beim Laden + Logging bei Policy-Verstößen

Hinweis:
Für das MVP reicht Prozess-isolierte Ausführung optional als späterer Schritt; zunächst klare API-Grenzen und harte Runtime-Checks.

---

## 9) Scheduling-Konzept

### MVP: interne Scheduler-Variante
- einfacher interner Scheduler im Bot-Prozess
- unterstützt Intervall + optional vereinfachte Zeitfenster
- persistiert `next_run_at`, `last_run_at`, `last_status`

Vorteile:
- schnell umsetzbar bis Montag
- keine externe Infrastruktur nötig

Nachteile:
- geringere Robustheit bei Prozessneustarts ohne saubere Recovery-Logik

### Später: Ausbauoptionen
- APScheduler für robustere Triggerlogik
- alternativ systemd timer / cron für externe Taktung
- langfristig evtl. Queue-basiertes Job-System

---

## 10) WebUI-Konzept

MVP-Screens/Funktionen:
- Plugin-Liste:
  - Name, Version, Typ(en), Status (aktiv/inaktiv), Health
- Manifest-Detailansicht:
  - Commands, Schedule, Rollen, Capabilities
- Aktionen:
  - aktivieren/deaktivieren
  - bei Worker: start/stop/restart
- Laufhistorie (light):
  - letzte Läufe, letzter Fehler, letzter Erfolg

UX-Prinzip:
- erst Transparenz und Bedienbarkeit, dann tiefe Betriebsmetriken

---

## 11) Umsetzungsblöcke bis Montag (Reihenfolge)

### Block 1 – Read-only Plan/Manifest UI (sofort)
- Discovery + Manifest-Validierung read-only
- WebUI-Liste und Detailansicht ohne aktive Ausführung
- Ziel: Kundensichtbarkeit und frühes Feedback

### Block 2 – Command-Plugin-Skeleton
- Laden/Ausführen von Command-Plugins
- minimaler Context + 1-2 sichere Bot-Aktionen
- Rollenprüfung + Timeout + Audit

### Block 3 – Sandbox API minimal
- defensive API konsolidieren
- kontrollierter Storage-Zugriff
- Capability-Checks pro Action

### Block 4 – QA Gates
- Validierungsfälle für Manifeste
- negative Tests (unerlaubte Rolle, Timeout, fehlende Capability)
- Smoke-Test für Enable/Disable + Command Dispatch

Optional nach Montag:
- Scheduler-Dispatch produktiv
- Worker-Manager mit restart/backoff

---

## 12) Risiken & offene Fragen (max. 5)

1. **Rollenmodell-Feinheiten unklar**
   - Gilt `roles_required` chat-spezifisch, global oder pro Gruppe unterschiedlich?

2. **Scheduling-Format noch offen**
   - Intervall-only im MVP oder direkt Cron-ähnlich? (Komplexität vs. Tempo)

3. **Fehlerisolationstiefe**
   - Reicht In-Process-Isolation mit Guards, oder braucht es kurzfristig Subprozess-Isolation?

4. **Capability-Granularität**
   - Welche Bot-Aktionen müssen im MVP wirklich freigebbar sein, um nicht zu überladen?

5. **Persistenzmodell für Plugin-State**
   - Wo werden Enable-Status, nächste Runs und Worker-Health zuverlässig abgelegt (bestehende DB-Struktur?)

---

## Annahmen

- Bestehendes MVP-Plugin-Gerüst (Manifest/Status/Enable-Disable) ist bereits im Projekt vorhanden.
- Secrets bleiben ausschließlich in `.env` bzw. Host-Konfiguration und werden nicht pauschal an Plugins weitergereicht.
- Priorität ist ein vorzeigbarer, kontrollierter MVP bis Montag (nicht Vollausbau).