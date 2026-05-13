# WebUI Plugin-Detailseite / WebUI Plugin Detail Page

## Block 11: Post-MVP – Plugin-Details mit Settings & Status / Block 11: Post-MVP – Plugin Details with Settings & Status

### Status: Dokumentation vollständig / Documentation complete

**Last Updated:** 2026-05-13

---

## Deutsch

### Übersicht

Die Plugin-Detailseite im WebUI bietet eine umfassende Ansicht eines einzelnen Plugins mit allen relevanten Informationen, Einstellungsmöglichkeiten und Status-Details. Diese Seite ist über die Plugin-Übersicht (Block 10) erreichbar und bildet das zentrale Interface für die Plugin-Konfiguration.

### Zugriff

**Navigation:** Plugin-Übersicht → [Details] → Detailseite

**URL-Pattern:** `/plugins/{plugin_id}`

**Zugriffsberechtigung:**
- **Owner:** Vollzugriff auf alle Tabs und Aktionen für alle Plugins und alle Scopes
- **Group-Admin:** Zugriff auf Plugins in eigenen Gruppen/Topics (Tabs: alle, Logs-Löschung nur Owner)
- **VIP/Normal:** Kein Zugriff auf die Management-Detailseite; stattdessen: Lesender Zugriff auf begrenzte öffentliche Plugin-Info (Name, Beschreibung, Version, Author, Lizenz, Homepage – ohne Settings, internen Status, Logs, Berechtigungen, Aktionen)

### Seitenaufbau

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🌤️ Wetter-Plugin                                          v1.2.3  🟢 Aktiv │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ TABS: [Übersicht] [Einstellungen] [Status] [Berechtigungen] [Logs]      ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ ÜBERSICHT                                                               ││
│  │ ─────────────                                                           ││
│  │ Beschreibung: Zeigt aktuelle Wetterdaten für konfigurierte Standorte    ││
│  │ Autor: max.mustermann@example.com                                       ││
│  │ Lizenz: MIT                                                             ││
│  │ Homepage: https://github.com/example/weather-plugin                       ││
│  │                                                                         ││
│  │ Metadaten:                                                              ││
│  │ • ID: weather-plugin                                                    ││
│  │ • Min. Bot-Version: 2.0.0                                               ││
│  │ • Trigger: cron, user-triggered                                         ││
│  │ • Installiert: 2024-01-15                                               ││
│  │ • Letzte Aktivität: 2024-01-20 14:22                                    ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Tab: Übersicht

**Anzeige-Elemente:**

| Feld | Beschreibung | Quelle (Block) |
|------|--------------|----------------|
| Name | Anzeigename des Plugins | Block 1: Manifest |
| Version | Semantische Version | Block 1: Manifest |
| Status | Aktueller Zustand | Block 3: Statusmodell |
| Beschreibung | Kurzbeschreibung | Block 1: Manifest |
| Autor | Plugin-Autor | Block 1: Manifest (optional) |
| Lizenz | Lizenzkennung | Block 1: Manifest (optional) |
| Homepage | URL zur Dokumentation | Block 1: Manifest (optional) |
| Plugin-ID | Eindeutige ID | Block 1: Manifest |
| Min. Bot-Version | Kompatibilitätsversion | Block 1: Manifest |
| Unterstützte Trigger | Liste der Trigger-Typen | Block 1: Manifest, Block 7 |
| Installationsdatum | Zeitpunkt der Registrierung | Block 3: Registry |
| Letzte Aktivität | Zeitpunkt letzter Ausführung | Block 12: Observability |

**Konsistenzprüfung:**
- Status-Anzeige konsistent mit Block 3 (`discovered`, `registered`, `activation_pending`, `active`, `disabled`, `error`)
- Farbcodierung identisch zu Block 10 (Overview)

### Tab: Einstellungen

**Anzeige der Plugin-Konfiguration gemäß Block 6 (Settings-Schema).**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ EINSTELLUNGEN                                                             │
│ ─────────────                                                             │
│                                                                           │
│  Standort                                  [Berlin              ]  [?]   │
│  API-Schlüssel                             [••••••••••••••        ]  [?]   │
│  Aktualisierungsintervall (Minuten)        [15                   ]  [?]   │
│  Benachrichtigungen aktivieren             [✓] Ein                    [?] │
│  Benachrichtigungsmodus                    [Privatnachricht ▼   ]  [?]   │
│                                                                           │
│  [Auf Standard zurücksetzen]    [Änderungen speichern]                    │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Unterstützte Feldtypen (Block 6):**

| Typ | UI-Komponente | Beispiel | Validierung |
|-----|---------------|----------|-------------|
| `text` | Text-Input | Standort, API-Key | `required`, `min_length`, `max_length`, `pattern` |
| `number` | Number-Input | Intervall, Port | `required`, `min`, `max` |
| `bool` | Checkbox/Toggle | Aktiviert/Deaktiviert | Boolean |
| `select` | Dropdown | Modus, Region | `options` (Enum) |
| `secret` | Maskiertes Passwort-Input | API-Schlüssel, Token | Maskierung, nie im Klartext anzeigen |

**Secret-Handling (Block 6, Abschnitt 11.5):**
- Anzeige: `••••••••` oder `***MASKED***`
- Keine Wert-Übertragung im Klartext
- Änderung nur über "Neuen Wert eingeben"
- Im Audit-Trail: `<SECRET_CHANGED>` statt Wert

**Validierungs-Feedback:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ⚠️ Fehler beim Speichern                                                  │
│ ─────────────────────────                                                 │
│ • Feld "API-Schlüssel" ist erforderlich                                   │
│ • Feld "Aktualisierungsintervall" muss mindestens 5 sein                  │
│                                                                           │
│  [Fehler beheben]                                                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Validierungsregeln (Block 6):**
- Client-seitige Validierung (sofortiges Feedback)
- Server-seitige Validierung (bei Submit)
- Blockierung bei `required`-Fehlern (Block 5, Abschnitt 10.4)

### Tab: Status

**Detaillierte Status-Informationen, Health-Metriken und Historie.**

Die Status-Historie entspricht dem Audit-Trail-Schema aus `PLUGIN_CONTRACT.md` Abschnitt 15.2. Unterstützte Status-Werte: `discovered`, `registered`, `activation_pending`, `active`, `disabled`, `error`.

**Detailed status information, health metrics, and history.**

The status history follows the audit-trail schema from `PLUGIN_CONTRACT.md` Section 15.2. Supported status values: `discovered`, `registered`, `activation_pending`, `active`, `disabled`, `error`.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ STATUS                                                                    │
│ ────────                                                                  │
│                                                                           │
│  Aktueller Status:    🟢 Aktiv                                            │
│  Scope:               Global                                              │
│  Aktiviert von:       admin_user (2024-01-15 10:45)                       │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ HEALTH (Plugin-Health-Status)                                       │  │
│  │ ───────────────────────────                                         │  │
│  │ Letzter Lauf:         2024-01-20 14:22                             │  │
│  │ Letztes Ergebnis:     ✅ Erfolgreich                               │  │
│  │                                                       │  │
│  │ Letzter Fehler:       2024-01-20 13:45                             │  │
│  │ Fehlercode:           PLUGIN_EXECUTION_ERROR                       │  │
│  │ Fehlermeldung:        Verbindung zu API fehlgeschlagen (Timeout)  │  │
│  │ Letzter Erfolg:       2024-01-20 12:00                             │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  Status-Historie:                                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ Zeitpunkt          │ Aktion              │ Benutzer      │ Status   │  │
│  ├────────────────────┼─────────────────────┼───────────────┼──────────┤  │
│  │ 2024-01-20 14:00   │ Konfiguration geänd.│ admin_user    │ active   │  │
│  │ 2024-01-15 10:45   │ Aktiviert           │ admin_user    │ active   │  │
│  │ 2024-01-15 10:30   │ Registriert         │ system        │ registered│  │
│  │ 2024-01-15 10:00   │ Entdeckt            │ system        │ discovered│  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  Aktuelle Aktionen:                                                       │
│  [Deaktivieren]  [Konfiguration ändern]  [Logs anzeigen]                  │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Health-Bereich (expliziter Abschnitt):**

| Feld | Beschreibung | Sichtbarkeit |
|------|--------------|--------------|
| Letzter Lauf | Zeitstempel der letzten Ausführung | Alle |
| Letztes Ergebnis | Erfolg/Misserfolg der letzten Ausführung | Alle |
| Letzter Fehler | Zeitstempel des letzten Fehlers (wenn vorhanden) | Admin/Owner |
| Fehlercode | Standardisierter Fehlercode | Admin/Owner |
| Fehlermeldung | Lesbare Fehlerbeschreibung | Admin/Owner |
| Letzter Erfolg | Zeitstempel des letzten erfolgreichen Laufs | Admin/Owner |

**Status-Übergänge (Block 3 + Block 5):**

| Von | Nach | Trigger | Wer |
|-----|------|---------|-----|
| `activation_pending` | `active` | Aktivierung | Owner, Group-Admin (mit Scope) |
| `active` | `disabled` | Deaktivierung | Owner, Group-Admin (mit Scope) |
| `disabled` | `activation_pending` | Reaktivierung | Owner, Group-Admin (mit Scope) |
| `active` | `error` | Fehler bei Ausführung | System |
| `error` | `active` | Fehler behoben | Owner, Group-Admin |
| `error` | `disabled` | Deaktivierung nach Fehler | Owner, Group-Admin |

**Berechtigungsprüfungen für Aktionen:**
- **Owner:** Alle mutierenden Aktionen (Aktivieren, Deaktivieren, Konfiguration ändern)
- **Group-Admin:** Mutierende Aktionen nur in eigenen Gruppen/Topics; bei unautorisiertem Zugriff: Steuerung deaktiviert/verborgen, Server liefert 403
- **VIP/Normal:** Keine mutierenden Aktionen; Steuerung deaktiviert/verborgen

**Fehler-Anzeige (bei Status `error`):**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ⚠️ FEHLERZUSTAND                                                          │
│ ─────────────────                                                         │
│  Fehlercode: PLUGIN_EXECUTION_ERROR                                       │
│  Zeitpunkt: 2024-01-20 14:22:15                                           │
│  Nachricht: Verbindung zu API fehlgeschlagen (Timeout nach 30s)           │
│                                                                           │
│  [Erneut versuchen]  [Fehlerdetails anzeigen]  [Deaktivieren]             │
│                                                                           │
│  Stack-Trace (Admin/Owner only):                                          │
│  ───────────────────────────────                                          │
│  Traceback (most recent call last):                                       │
│    File "...", line 45, in fetch_weather                                  │
│      response = requests.get(url, timeout=30)                             │
│  requests.exceptions.Timeout: Request timeout                             │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Tab: Berechtigungen

**Konfiguration des Rechte-Modells (Block 4).**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ BERECHTIGUNGEN                                                            │
│ ──────────────                                                            │
│                                                                           │
│  Plugin-Mindestrolle (hart, nicht editierbar)                             │
│  ────────────────────────────────                                         │
│  Dieses Plugin erfordert mindestens Rolle: **Normal** (festgelegt im Manifest) │
│                                                                           │
│  Hinweis: Die Plugin-Mindestrolle ist ein Vertragswert aus dem Manifest   │
│  und kann nicht geändert werden. Nur administrative Einschränkungen       │
│  können bearbeitet werden.                                                │
│                                                                           │
│  Aktuelle Einschränkungen (optional)                                      │
│  ────────────────────────────────                                         │
│  [✓] Für Gruppe "AMO-Support" auf VIP beschränken                         │
│  [ ] Für Topic "Wetter-Updates" auf Admin beschränken                     │
│                                                                           │
│  ─────────────────────────────────────────────────────────────────────  │
│                                                                           │
│  Effektive Berechtigungen:                                                │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ Gruppe/Topic           │ Plugin-Min. │ Admin-Einschr. │ Effektiv    │  │
│  ├────────────────────────┼─────────────┼────────────────┼─────────────┤  │
│  │ Global                 │ Normal      │ -              │ Normal      │  │
│  │ AMO-Support            │ Normal      │ VIP            │ VIP         │  │
│  │ Wetter-Updates         │ Normal      │ Admin          │ Admin       │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  [Speichern]                                                              │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Kernregel (Block 4, Abschnitt 9.1):**
- `Effektive Berechtigung = MAX(Plugin-Minimum, Admin-Einschränkung)`
- Admin kann niemals unter das Plugin-Minimum lockern
- **Plugin-Minimum ist Manifest-Vertrag und nicht editierbar**

**Scope-Regeln (Block 4, Abschnitt 9.3):**
- Owner: Alle Scopes (global)
- Group-Admin: Nur eigene Gruppen/Topics
- Admin-Einschränkung wirkt zusätzlich zum Plugin-Minimum

### Tab: Logs

**Zugriff auf Plugin-spezifische Logs (Block 12, Observability).**

Für das detaillierte Audit-Trail-Schema und Run-Log-Spezifikation siehe `PLUGIN_CONTRACT.md` Abschnitt 15 (Observability & Audit-Trail). Dieser Abschnitt definiert Pflicht-Audit-Events, das minimale Run-Log-Schema sowie Logging-/Redaction-Grenzen.

**Access to plugin-specific logs (Block 12, Observability).**

For detailed audit-trail schema and run-log specifications, see `PLUGIN_CONTRACT.md` Section 15 (Observability & Audit Trail). This section defines mandatory audit events, minimal run-log schema, and logging/redaction boundaries.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ LOGS                                                                      │
│ ────                                                                      │
│                                                                           │
│  Filter: [Alle Level ▼] [Letzte 24h ▼]  [Aktualisieren]                    │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ Zeit                │ Level   │ Nachricht                            │  │
│  ├────────────────────┼─────────┼──────────────────────────────────────┤  │
│  │ 2024-01-20 14:22   │ INFO    │ Wetter aktualisiert: Berlin, 12°C    │  │
│  │ 2024-01-20 14:22   │ DEBUG   │ API-Antwort erhalten (234ms)         │  │
│  │ 2024-01-20 14:15   │ INFO    │ Wetter aktualisiert: Berlin, 13°C    │  │
│  │ 2024-01-20 14:00   │ WARNING │ API-Antwort langsam (2.1s)           │  │
│  │ 2024-01-20 13:45   │ ERROR   │ Verbindung fehlgeschlagen (Timeout)  │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  [⬇️ Herunterladen]  [Alle Logs löschen]                                  │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Log-Level:** DEBUG, INFO, WARNING, ERROR, CRITICAL

---

## English

### Overview

The Plugin Detail page in the WebUI provides a comprehensive view of a single plugin with all relevant information, configuration options, and status details. This page is accessible from the Plugin Overview (Block 10) and serves as the central interface for plugin configuration.

### Access

**Navigation:** Plugin Overview → [Details] → Detail Page

**URL Pattern:** `/plugins/{plugin_id}`

**Access Permissions:**
- **Owner:** Full access to all tabs and actions for all plugins and all scopes
- **Group-Admin:** Access to plugins within own groups/topics (all tabs except log deletion, which is Owner-only)
- **VIP/Normal:** No access to management detail page; instead: read-only access to limited public plugin info (Name, Description, Version, Author, License, Homepage – without Settings, internal Status, Logs, Permissions, actions)

### Page Layout

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🌤️ Weather Plugin                                          v1.2.3  🟢 Active│
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ TABS: [Overview] [Settings] [Status] [Permissions] [Logs]               ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │ OVERVIEW                                                                ││
│  │ ────────                                                                ││
│  │ Description: Shows current weather data for configured locations        ││
│  │ Author: max.mustermann@example.com                                      ││
│  │ License: MIT                                                            ││
│  │ Homepage: https://github.com/example/weather-plugin                     ││
│  │                                                                         │
│  │ Metadata:                                                               ││
│  │ • ID: weather-plugin                                                    ││
│  │ • Min. Bot Version: 2.0.0                                               ││
│  │ • Triggers: cron, user-triggered                                        ││
│  │ • Installed: 2024-01-15                                                 ││
│  │ • Last Activity: 2024-01-20 14:22                                      ││
│  └─────────────────────────────────────────────────────────────────────────┘│
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Tab: Overview

**Display Elements:**

| Field | Description | Source (Block) |
|-------|-------------|----------------|
| Name | Display name of the plugin | Block 1: Manifest |
| Version | Semantic version | Block 1: Manifest |
| Status | Current state | Block 3: Status Model |
| Description | Short description | Block 1: Manifest |
| Author | Plugin author | Block 1: Manifest (optional) |
| License | License identifier | Block 1: Manifest (optional) |
| Homepage | URL to documentation | Block 1: Manifest (optional) |
| Plugin ID | Unique identifier | Block 1: Manifest |
| Min. Bot Version | Compatibility version | Block 1: Manifest |
| Supported Triggers | List of trigger types | Block 1: Manifest, Block 7 |
| Installation Date | Registration timestamp | Block 3: Registry |
| Last Activity | Last execution timestamp | Block 12: Observability |

**Consistency Check:**
- Status display consistent with Block 3 (`discovered`, `registered`, `activation_pending`, `active`, `disabled`, `error`)
- Color coding identical to Block 10 (Overview)

### Tab: Settings

**Display of plugin configuration according to Block 6 (Settings Schema).**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ SETTINGS                                                                  │
│ ────────                                                                  │
│                                                                           │
│  Location                                  [Berlin              ]  [?]   │
│  API Key                                   [••••••••••••••        ]  [?]   │
│  Refresh Interval (minutes)                [15                   ]  [?]   │
│  Enable Notifications                      [✓] On                       [?] │
│  Notification Mode                         [Private Message ▼     ]  [?]   │
│                                                                           │
│  [Reset to Defaults]            [Save Changes]                            │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Supported Field Types (Block 6):**

| Type | UI Component | Example | Validation |
|------|--------------|---------|------------|
| `text` | Text Input | Location, API Key | `required`, `min_length`, `max_length`, `pattern` |
| `number` | Number Input | Interval, Port | `required`, `min`, `max` |
| `bool` | Checkbox/Toggle | Enabled/Disabled | Boolean |
| `select` | Dropdown | Mode, Region | `options` (Enum) |
| `secret` | Masked Password Input | API Key, Token | Masking, never display in plain text |

**Secret Handling (Block 6, Section 11.5):**
- Display: `••••••••` or `***MASKED***`
- No plain text value transmission
- Changes only via "Enter new value"
- In Audit Trail: `<SECRET_CHANGED>` instead of value

**Validation Feedback:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ⚠️ Error Saving                                                           │
│ ─────────────                                                             │
│ • Field "API Key" is required                                             │
│ • Field "Refresh Interval" must be at least 5                             │
│                                                                           │
│  [Fix Errors]                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Validation Rules (Block 6):**
- Client-side validation (immediate feedback)
- Server-side validation (on submit)
- Blocking on `required` errors (Block 5, Section 10.4)

### Tab: Status

**Detailed status information, health metrics, and history.**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ STATUS                                                                    │
│ ──────                                                                    │
│                                                                           │
│  Current Status:      🟢 Active                                             │
│  Scope:               Global                                              │
│  Activated by:        admin_user (2024-01-15 10:45)                       │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ HEALTH (Plugin Health Status)                                       │  │
│  │ ───────────────────────────                                         │  │
│  │ Last Run:             2024-01-20 14:22                              │  │
│  │ Last Result:          ✅ Success                                    │  │
│  │                                                       │  │
│  │ Last Error:           2024-01-20 13:45                              │  │
│  │ Error Code:           PLUGIN_EXECUTION_ERROR                        │  │
│  │ Error Message:        Connection to API failed (Timeout)            │  │
│  │ Last Success:         2024-01-20 12:00                              │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  Status History:                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ Timestamp          │ Action             │ User         │ Status      │  │
│  ├────────────────────┼────────────────────┼──────────────┼─────────────┤  │
│  │ 2024-01-20 14:00   │ Config changed     │ admin_user   │ active      │  │
│  │ 2024-01-15 10:45   │ Activated          │ admin_user   │ active      │  │
│  │ 2024-01-15 10:30   │ Registered         │ system       │ registered  │  │
│  │ 2024-01-15 10:00   │ Discovered         │ system       │ discovered  │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  Current Actions:                                                         │
│  [Deactivate]  [Change Configuration]  [View Logs]                        │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Health Section (explicit subsection):**

| Field | Description | Visibility |
|-------|-------------|------------|
| Last Run | Timestamp of last execution | All |
| Last Result | Success/failure of last execution | All |
| Last Error | Timestamp of last error (if any) | Admin/Owner |
| Error Code | Standardized error code | Admin/Owner |
| Error Message | Human-readable error description | Admin/Owner |
| Last Success | Timestamp of last successful run | Admin/Owner |

**Status Transitions (Block 3 + Block 5):**

| From | To | Trigger | Who |
|------|------|---------|-----|
| `activation_pending` | `active` | Activation | Owner, Group-Admin (with scope) |
| `active` | `disabled` | Deactivation | Owner, Group-Admin (with scope) |
| `disabled` | `activation_pending` | Reactivation | Owner, Group-Admin (with scope) |
| `active` | `error` | Execution error | System |
| `error` | `active` | Error resolved | Owner, Group-Admin |
| `error` | `disabled` | Deactivation after error | Owner, Group-Admin |

**Permission Checks for Actions:**
- **Owner:** All mutating actions (Activate, Deactivate, Change Configuration)
- **Group-Admin:** Mutating actions only in own groups/topics; unauthorized access: controls disabled/hidden, server returns 403
- **VIP/Normal:** No mutating actions; controls disabled/hidden

**Error Display (for status `error`):**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ⚠️ ERROR STATE                                                            │
│ ─────────────                                                             │
│  Error Code: PLUGIN_EXECUTION_ERROR                                       │
│  Timestamp: 2024-01-20 14:22:15                                           │
│  Message: Connection to API failed (Timeout after 30s)                   │
│                                                                           │
│  [Retry]  [Show Error Details]  [Deactivate]                                │
│                                                                           │
│  Stack Trace (Admin/Owner only):                                          │
│  ─────────────────────────────                                            │
│  Traceback (most recent call last):                                       │
│    File "...", line 45, in fetch_weather                                  │
│      response = requests.get(url, timeout=30)                             │
│  requests.exceptions.Timeout: Request timeout                             │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Tab: Permissions

**Configuration of the permission model (Block 4).**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ PERMISSIONS                                                               │
│ ───────────                                                               │
│                                                                           │
│  Plugin Minimum Role (hard, read-only)                                    │
│  ─────────────────────────────────────                                    │
│  This plugin requires at least role: **Normal** (defined in manifest)     │
│                                                                           │
│  Note: Plugin minimum role is a manifest contract value and cannot be     │
│  changed. Only administrative restrictions can be edited.                   │
│                                                                           │
│  Current Restrictions (optional)                                        │
│  ───────────────────────────────                                        │
│  [✓] Restrict to VIP for group "AMO-Support"                              │
│  [ ] Restrict to Admin for topic "Weather-Updates"                        │
│                                                                           │
│  ──────────────────────────────────────────────────────────────────────── │
│                                                                           │
│  Effective Permissions:                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ Group/Topic            │ Plugin Min. │ Admin Restr. │ Effective     │  │
│  ├────────────────────────┼─────────────┼────────────────┼───────────────┤  │
│  │ Global                 │ Normal      │ -              │ Normal        │  │
│  │ AMO-Support            │ Normal      │ VIP            │ VIP           │  │
│  │ Weather-Updates        │ Normal      │ Admin          │ Admin         │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  [Save]                                                                   │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Core Rule (Block 4, Section 9.1):**
- `Effective Permission = MAX(Plugin Minimum, Admin Restriction)`
- Admin can never relax below plugin minimum
- **Plugin minimum is a manifest contract value and is read-only**

**Scope Rules (Block 4, Section 9.3):**
- Owner: All scopes (global)
- Group-Admin: Only own groups/topics
- Admin restriction applies in addition to plugin minimum

### Tab: Logs

**Access to plugin-specific logs (Block 12, Observability).**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ LOGS                                                                      │
│ ────                                                                      │
│                                                                           │
│  Filter: [All Levels ▼] [Last 24h ▼]  [Refresh]                             │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ Time                │ Level   │ Message                            │  │
│  ├────────────────────┼─────────┼──────────────────────────────────────┤  │
│  │ 2024-01-20 14:22   │ INFO    │ Weather updated: Berlin, 12°C      │  │
│  │ 2024-01-20 14:22   │ DEBUG   │ API response received (234ms)      │  │
│  │ 2024-01-20 14:15   │ INFO    │ Weather updated: Berlin, 13°C      │  │
│  │ 2024-01-20 14:00   │ WARNING │ API response slow (2.1s)           │  │
│  │ 2024-01-20 13:45   │ ERROR   │ Connection failed (Timeout)        │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                           │
│  [⬇️ Download]  [Delete All Logs]                                         │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Log Levels:** DEBUG, INFO, WARNING, ERROR, CRITICAL

---

## Abhängigkeiten / Dependencies

### Von Block 11 Abhängig / Depends On:

| Block | Dokument / Document | Relevanz |
|-------|---------------------|----------|
| Block 1 | `PLUGIN_CONTRACT.md` – Manifest-Schema | Plugin-Metadaten (Name, Version, Beschreibung) |
| Block 3 | `PLUGIN_CONTRACT.md` – Statusmodell | Status-Anzeige und Übergänge |
| Block 4 | `PLUGIN_CONTRACT.md` – Rechte-Modell | Berechtigungs-Tab |
| Block 5 | `PLUGIN_CONTRACT.md` – Aktivierungs-Flow | Status-Übergänge, Aktivierung/Deaktivierung |
| Block 6 | `PLUGIN_CONTRACT.md` – Settings-Schema | Einstellungs-Tab, Feldtypen, Validierung |
| Block 7 | `PLUGIN_CONTRACT.md` – Trigger-Baseline | Unterstützte Trigger-Anzeige |
| Block 10 | `WEBUI_PLUGIN_OVERVIEW.md` | Navigation, Konsistenz der Status-Anzeige |
| Block 12 | `PLUGIN_CONTRACT.md` – Observability | Logs-Tab, Status-Historie |

### Konsistenz mit / Consistency With:

- **Block 10 (Overview):** Gleiche Farbcodierung, gleiche Status-Namen
- **Block 6 (Settings):** Identische Feldtypen und Validierungsregeln
- **Block 4 (Permissions):** Identische Berechnung effektiver Berechtigungen

---

## QA-Testbare Akzeptanzkriterien / QA-Testable Acceptance Criteria

### Deutsch

| ID | Kriterium | Test-Methode |
|----|-----------|--------------|
| QA-11.1 | Detailseite zeigt alle 5 Tabs | Visuelle Inspektion |
| QA-11.2 | Übersicht-Tab zeigt alle Metadaten aus Block 1 | Datenvergleich |
| QA-11.3 | Einstellungen-Tab zeigt alle Schema-Felder (Block 6) | Funktionaler Test |
| QA-11.4 | Secret-Felder sind maskiert | Visuelle Inspektion |
| QA-11.5 | Validierungsfehler zeigen lesbare Meldungen | Fehler-Test |
| QA-11.6 | Status-Tab zeigt aktuellen Status korrekt | Zustands-Test |
| QA-11.7 | Status-Tab enthält Health-Bereich mit Last Run/Result/Error | Daten-Test |
| QA-11.8 | Status-Historie zeigt korrekte Übergänge (keine `found`/`pending`) | Historien-Test |
| QA-11.9 | Fehler-Zustand zeigt Stack-Trace (nur Admin/Owner) | Rollen-Test |
| QA-11.10 | Berechtigungs-Tab zeigt effektive Berechtigungen | Berechnungs-Test |
| QA-11.11 | Plugin-Mindestrolle ist nicht editierbar (Anzeige nur) | UI-Test |
| QA-11.12 | Logs-Tab zeigt Plugin-spezifische Logs | Funktionaler Test |
| QA-11.13 | Zugriffsbeschränkung: VIP/Normal kein Management-Zugriff | RBAC-Test |
| QA-11.14 | Group-Admin nur in eigenen Scopes autorisiert; 403 bei Verstoß | Autorisierungs-Test |
| QA-11.15 | Aktivieren/Deaktivieren aktualisiert Status sofort | Zustands-Test |
| QA-11.16 | Änderungen speichern mit Validierung | Funktionaler Test |

### English

| ID | Criterion | Test Method |
|----|-----------|-------------|
| QA-11.1 | Detail page shows all 5 tabs | Visual inspection |
| QA-11.2 | Overview tab shows all metadata from Block 1 | Data comparison |
| QA-11.3 | Settings tab shows all schema fields (Block 6) | Functional test |
| QA-11.4 | Secret fields are masked | Visual inspection |
| QA-11.5 | Validation errors show readable messages | Error test |
| QA-11.6 | Status tab shows current status correctly | State test |
| QA-11.7 | Status tab contains Health section with Last Run/Result/Error | Data test |
| QA-11.8 | Status history shows correct transitions (no `found`/`pending`) | History test |
| QA-11.9 | Error state shows stack trace (Admin/Owner only) | Role test |
| QA-11.10 | Permissions tab shows effective permissions | Calculation test |
| QA-11.11 | Plugin minimum role is not editable (display only) | UI test |
| QA-11.12 | Logs tab shows plugin-specific logs | Functional test |
| QA-11.13 | Access restriction: VIP/Normal no management access | RBAC test |
| QA-11.14 | Group-Admin only authorized in own scopes; 403 on violation | Authorization test |
| QA-11.15 | Activate/Deactivate updates status immediately | State test |
| QA-11.16 | Save changes with validation | Functional test |

---

## Zusammenfassung / Summary

Dieses Dokument beschreibt die WebUI Plugin-Detailseite für Block 11 (Post-MVP). Es deckt alle wichtigen Aspekte ab:

- **5 Tabs:** Übersicht/Overview, Einstellungen/Settings, Status/Status, Berechtigungen/Permissions, Logs/Logs
- **Settings-Integration:** Vollständige Unterstützung des Block 6 Settings-Schema mit allen Feldtypen
- **Status-Management:** Anzeige und Steuerung aller Status-Übergänge aus Block 3 und Block 5
- **Health-Sektion:** Expliziter Health-Bereich mit letztem Lauf, Ergebnis, Fehlerinformationen
- **Rechte-Integration:** Berechnung und Anzeige effektiver Berechtigungen gemäß Block 4
- **Plugin-Mindestrolle:** Als Manifest-Vertrag markiert, nicht editierbar
- **Secret-Handling:** Sichere Maskierung und Audit-konforme Behandlung
- **Fehleranzeige:** Detaillierte Fehlerinformationen mit Stack-Trace für Admin/Owner
- **Zugriffskontrolle:** Management nur Owner/Group-Admin; VIP/Normal begrenzte öffentliche Info
- **Konsistenz:** Vollständige Übereinstimmung mit Block 10 (Overview) und allen zugrundeliegenden Contracts

---

**Dokument-Version / Document Version:** 1.1.0 (QA-Fixes applied)  
**Gilt für / Applies to:** Block 11 – WebUI Plugin-Detailseite (Settings/Status)  
**Konsistent mit / Consistent with:** PLUGIN_CONTRACT.md v1.6.0, WEBUI_PLUGIN_OVERVIEW.md v1.0.0
