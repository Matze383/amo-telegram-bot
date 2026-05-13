# Plugin Contract / Plugin-Vertrag

## Deutsch

### 1. Übersicht und Ziel

Dieses Dokument definiert den verbindlichen Minimalstandard für Plugins im AMO-telegram-bot. Es dient als vertragliche Grundlage zwischen Plugin-Autoren und dem Bot-System, um sicherzustellen, dass Plugins korrekt erkannt, validiert und geladen werden können.

### 2. Manifest-Schema (YAML)

Jedes Plugin muss eine `plugin.yaml` (oder `plugin.yml`) im Plugin-Wurzelverzeichnis enthalten.

#### 2.1 Pflichtfelder

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `id` | String | Eindeutige Plugin-Identifikation. Nur Kleinbuchstaben, Zahlen, Bindestriche und Unterstriche erlaubt. Muss mit Buchstaben beginnen. |
| `name` | String | Anzeigename des Plugins (für UI und Logs) |
| `version` | String | Semantische Versionierung (MAJOR.MINOR.PATCH), z.B. "1.2.3" |
| `description` | String | Kurze Beschreibung der Plugin-Funktionalität |
| `entrypoint` | String | Relativer Pfad zur Haupt-Python-Datei (z.B. "main.py") |
| `min_bot_version` | String | Minimale Bot-Version für Kompatibilität (z.B. "2.0.0") |
| `triggers` | Array | Liste der unterstützten Trigger-Typen: `"cron"`, `"interval"`, `"user-triggered"` |

#### 2.2 Optionale Felder

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `author` | String | Name/E-Mail des Plugin-Autors |
| `homepage` | String | URL zur Plugin-Dokumentation oder Repository |
| `license` | String | Lizenzkennzeichnung (z.B. "MIT", "GPL-3.0") |

#### 2.3 Vorbereitete Felder (zukünftige Nutzung)

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `min_role` | String | Mindestrolle für Plugin-Nutzung: `"owner"`, `"admin"`, `"vip"`, `"normal"` |
| `settings_schema` | Object | JSON-Schema für Plugin-Einstellungen |
| `ai_approval_required` | Boolean | Gibt an, ob KI-Freigabe erforderlich ist |

### 3. ID- und Versionsregeln

#### 3.1 Plugin-ID-Regeln

- **Eindeutigkeit**: Eine `id` darf im gesamten System nur einmal existieren
- **Format**: `^[a-z][a-z0-9_-]*$` (Regex)
- **Länge**: 3-50 Zeichen
- **Reservierte IDs**: `core`, `system`, `internal`, `builtin` sind reserviert

#### 3.2 Versionsregeln

- **Format**: SemVer (MAJOR.MINOR.PATCH)
- **Kompatibilität**: `min_bot_version` prüft, ob das Plugin mit der aktuellen Bot-Version kompatibel ist
- **Vergleich**: Bot-Version ≥ `min_bot_version` erforderlich

#### 3.3 Kollisionsregeln (Strict/Blocking Policy)

- **Prinzip**: Kollisionen führen zu Blockierung, nicht zu Überschreibung
- **Built-in vs. User-Plugin**: Bei identischer ID wird das User-Plugin auf Status `discovery_blocked` gesetzt
- **User vs. User-Plugin**: Bei identischer ID wird das zweite Plugin auf Status `discovery_blocked` gesetzt
- **Deterministische Reihenfolge**: Alphabetische Sortierung der Verzeichnispfade garantiert reproduzierbares Verhalten
- **Logging**: Kollisionen werden als `ERROR` geloggt mit Plugin-ID und Pfadangaben

### 4. Discovery-Regeln (Plugin-Verzeichnis & Discovery)

#### 4.1 Suchpfade (Search Paths)

| Pfad | Typ | Beschreibung |
|------|-----|--------------|
| `plugins/` | User-Plugins | Root-Verzeichnis für benutzerdefinierte Plugins |
| `src/plugins/builtin/` | Built-in Plugins | Mitgelieferte System-Plugins (optional) |

#### 4.2 Discovery-Reihenfolge (Deterministic Order)

1. **Phase 1**: Built-in Plugins scannen (`src/plugins/builtin/`)
2. **Phase 2**: User-Plugins scannen (`plugins/`)
3. **Sortierung**: Alphabetisch nach Verzeichnisname (case-sensitive)
4. **Reproduzierbarkeit**: Gleiche Eingabe = Gleiche Ausgabe

#### 4.3 Discovery-Statusmodell

**Wichtige Unterscheidung / Important Distinction:**
Die Discovery-Status (`discovery_found`, `discovery_invalid`, `discovery_blocked`, `discovery_disabled`) sind **Prä-Registrierungs-Status**. Sie beschreiben das Ergebnis des Discovery-Prozesses (Block 2) **bevor** ein Plugin in das Registrierungs-Statusmodell (Block 3) übergeht. Diese Status werden nicht in der Plugin-Registry gespeichert.

| Status | Beschreibung | Ergebnis |
|--------|--------------|----------|
| `discovery_found` | Plugin entdeckt, Manifest gültig | Registrierung möglich |
| `discovery_invalid` | Manifest fehlerhaft oder unvollständig | Nicht registrierbar |
| `discovery_blocked` | ID-Kollision mit anderem Plugin | Nicht registrierbar |
| `discovery_disabled` | Plugin explizit deaktiviert (z.B. via `.disabled` Datei) | Ignoriert bei Discovery |

**Hinweis:** Die Präfixe `discovery_` dienen der klaren Trennung vom Registrierungs-Status `disabled` (Block 3), der einen **nach** der Registrierung manuell deaktivierten Zustand beschreibt.

#### 4.4 Deaktivierungsmechanismus

- **Datei-basiert**: Leere Datei `{plugin-folder}.disabled` deaktiviert das Plugin
- **Scope**: Deaktivierung erfolgt vor Manifest-Parsing (Performance)
- **Logging**: Deaktivierte Plugins werden als `INFO` geloggt

#### 4.5 Fehlerbehandlung bei Discovery

| Fehler | Verhalten | Logging |
|--------|-----------|---------|
| Fehlendes Manifest | Plugin wird übersprungen | `WARNING` |
| Ungültiges YAML | Plugin-Status: `discovery_invalid` | `ERROR` |
| ID-Kollision | Zweites Plugin: `discovery_blocked` | `ERROR` |
| Verzeichnis nicht lesbar | Überspringen | `ERROR` |

### 5. Registrierung und Statusmodell

#### 5.1 Übersicht

Die Registrierung erfolgt automatisch nach erfolgreicher Discovery (Block 2).

#### 5.2 Automatische Registrierung

| Phase | Bedingung | Ergebnis-Status |
|-------|-----------|-----------------|
| 1. Discovery | Plugin gefunden und Manifest lesbar | `discovered` |
| 2. Validierung | Pflichtfelder + Format OK | `validated` |
| 3. Registrierung | Eintrag in Plugin-Registry | `registered` (technisch/transient) |
| 4. Initial-Aktivierung | Persistierter Endzustand | `activation_pending` (normativ) |

**Wichtig / Important:** Für jedes neu gültige Plugin MUSS die automatische Registrierung im Status **`activation_pending`** enden. Der Status `registered` ist ein technischer/transienter Zwischenzustand oder ein Metadaten-Record, **kein** finaler nutzer-sichtbarer Aktivierungszustand.

**Normative Regel / Normative Rule:**
- Neue Plugins starten immer in `activation_pending`
- Built-in Plugins starten ebenfalls in `activation_pending`
- Kein Weg führt automatisch zu `active`
- Nur Owner oder Group-Admin mit Scope darf auf `active` wechseln

#### 5.3 Harte Regel: Keine Auto-Aktivierung

| Regel | Beschreibung |
|-------|--------------|
| Neue Plugins | Starten immer in `activation_pending` |
| Built-in Plugins | Starten ebenfalls in `activation_pending` |
| Kein Auto-Pfad | Kein Weg führt automatisch zu `active` |
| Aktivierung | Nur Owner oder Group-Admin mit Scope darf auf `active` wechseln |
| Persistenz | Status bleibt nach Neustart/Backup erhalten |

#### 5.4 Registrierungs-Statusmodell

| Status | Beschreibung | Sichtbar für | Nächster Schritt |
|--------|--------------|--------------|------------------|
| `discovered` | Plugin gefunden, wartet auf Validierung | Admin/Owner | → `validated` oder `error` |
| `validated` | Manifest-Format OK | Admin/Owner | → `registered` |
| `registered` | Plugin im System bekannt | Admin/Owner | → `activation_pending` |
| `activation_pending` | Wartet auf Freigabe/Aktivierung | Admin/Owner | → `active` (durch Owner/Admin) |
| `active` | Plugin läuft im definierten Scope | Admin/Owner | – (betriebsbereit) |
| `disabled` | Explizit deaktiviert | Admin/Owner | Manuelle Reaktivierung |
| `error` | Fehlerzustand | Admin/Owner | Fehlerbehebung erforderlich |

**Hinweis:** Die Status `discovery_found`, `discovery_invalid`, `discovery_blocked`, `discovery_disabled` aus dem Discovery-Status (Block 2) sind Prä-Registrierungs-Status und werden nicht in der Plugin-Registry gespeichert. Siehe Abschnitt 4.3 für die Unterscheidung zwischen Discovery-Status (Präfix `discovery_`) und Registrierungs-Status.

### 6. Validierungsfehler

| Fehler | Beschreibung | Beispiel |
|--------|--------------|----------|
| `MISSING_REQUIRED_FIELD` | Pflichtfeld fehlt | `id` nicht angegeben |
| `INVALID_TYPE` | Feld hat falschen Typ | `triggers` ist String statt Array |
| `INVALID_ID_FORMAT` | Plugin-ID entspricht nicht dem Format | `id: "123-plugin"` (beginnt mit Zahl) |
| `DUPLICATE_ID` | Plugin-ID existiert bereits | Zwei Plugins mit `id: "weather-plugin"` |
| `INVALID_VERSION` | Version nicht im SemVer-Format | `version: "v1.0"` |
| `INCOMPATIBLE_VERSION` | Bot-Version zu niedrig | Bot 1.5.0, aber `min_bot_version: "2.0.0"` |
| `INVALID_TRIGGER_TYPE` | Unbekannter Trigger-Typ | `triggers: ["unknown"]` |
| `RESERVED_ID` | ID ist reserviert | `id: "core"` |

### 7. Beispiele

#### 7.1 Gültiges Manifest

```yaml
id: weather-plugin
name: Wetter-Benachrichtigungen
version: 1.0.0
description: Zeigt aktuelle Wetterdaten für konfigurierte Standorte an
entrypoint: main.py
min_bot_version: 2.0.0
triggers:
  - cron
  - user-triggered
author: max.mustermann@example.com
homepage: https://github.com/example/weather-plugin
license: MIT
min_role: normal
settings_schema:
  location:
    type: text
    required: true
    description: Standard-Standort für Wetterabfragen
  api_key:
    type: secret
    required: true
    description: API-Schlüssel für Wetterdienst
```

#### 7.2 Ungültige Manifest-Beispiele

**Beispiel 1: Fehlendes Pflichtfeld**
```yaml
name: Fehlerhaftes Plugin
version: 1.0.0
# FEHLER: 'id' fehlt
```
*Fehler: MISSING_REQUIRED_FIELD (id)*

**Beispiel 2: Ungültiges ID-Format**
```yaml
id: 123-weather
name: Wetter Plugin
version: 1.0.0
# FEHLER: ID beginnt mit Zahl
```
*Fehler: INVALID_ID_FORMAT*

**Beispiel 3: Inkompatible Version**
```yaml
id: advanced-plugin
name: Erweitertes Plugin
version: 2.0.0
description: Benötigt neuere Bot-Version
entrypoint: main.py
min_bot_version: 3.0.0
triggers:
  - user-triggered
# FEHLER: Bot-Version ist 2.5.0, aber min_bot_version ist 3.0.0
```
*Fehler: INCOMPATIBLE_VERSION*

### 8. Nicht-Ziele dieses Contracts

Dieser Contract definiert absichtlich **nicht**:
- Implementierung des Plugin-Loaders
- Ausführungslogik für Trigger
- WebUI-Implementierung
- Speicherort der Plugin-Dateien
- Aktivierungs- und Freigabeflows (siehe separate Dokumentation)

---

## English

### 1. Overview and Purpose

This document defines the binding minimum standard for plugins in the AMO-telegram-bot. It serves as a contractual basis between plugin authors and the bot system to ensure plugins are correctly recognized, validated, and loaded.

### 2. Manifest Schema (YAML)

Each plugin must include a `plugin.yaml` (or `plugin.yml`) in the plugin root directory.

#### 2.1 Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | String | Unique plugin identifier. Only lowercase letters, numbers, hyphens, and underscores allowed. Must start with a letter. |
| `name` | String | Display name of the plugin (for UI and logs) |
| `version` | String | Semantic versioning (MAJOR.MINOR.PATCH), e.g., "1.2.3" |
| `description` | String | Short description of plugin functionality |
| `entrypoint` | String | Relative path to the main Python file (e.g., "main.py") |
| `min_bot_version` | String | Minimum bot version for compatibility (e.g., "2.0.0") |
| `triggers` | Array | List of supported trigger types: `"cron"`, `"interval"`, `"user-triggered"` |

#### 2.2 Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `author` | String | Name/email of the plugin author |
| `homepage` | String | URL to plugin documentation or repository |
| `license` | String | License identifier (e.g., "MIT", "GPL-3.0") |

#### 2.3 Reserved Fields (future use)

| Field | Type | Description |
|-------|------|-------------|
| `min_role` | String | Minimum role for plugin usage: `"owner"`, `"admin"`, `"vip"`, `"normal"` |
| `settings_schema` | Object | JSON schema for plugin settings |
| `ai_approval_required` | Boolean | Indicates if AI approval is required |

### 3. ID and Version Rules

#### 3.1 Plugin ID Rules

- **Uniqueness**: An `id` may exist only once in the entire system
- **Format**: `^[a-z][a-z0-9_-]*$` (Regex)
- **Length**: 3-50 characters
- **Reserved IDs**: `core`, `system`, `internal`, `builtin` are reserved

#### 3.2 Version Rules

- **Format**: SemVer (MAJOR.MINOR.PATCH)
- **Compatibility**: `min_bot_version` checks if the plugin is compatible with the current bot version
- **Comparison**: Bot version ≥ `min_bot_version` required

#### 3.3 Collision Rules (Strict/Blocking Policy)

- **Principle**: Collisions result in blocking, not overriding
- **Built-in vs. User Plugin**: For identical IDs, the user plugin is set to status `discovery_blocked`
- **User vs. User Plugin**: For identical IDs, the second plugin is set to status `discovery_blocked`
- **Deterministic Order**: Alphabetical sorting of directory paths guarantees reproducible behavior
- **Logging**: Collisions are logged as `ERROR` with plugin ID and path information

### 4. Discovery Rules (Plugin Directory & Discovery)

#### 4.1 Search Paths

| Path | Type | Description |
|------|------|-------------|
| `plugins/` | User Plugins | Root directory for custom plugins |
| `src/plugins/builtin/` | Built-in Plugins | Bundled system plugins (optional) |

#### 4.2 Discovery Order (Deterministic Order)

1. **Phase 1**: Scan built-in plugins (`src/plugins/builtin/`)
2. **Phase 2**: Scan user plugins (`plugins/`)
3. **Sorting**: Alphabetically by directory name (case-sensitive)
4. **Reproducibility**: Same input = Same output

#### 4.3 Discovery Status Model

**Important Distinction:**
The Discovery statuses (`discovery_found`, `discovery_invalid`, `discovery_blocked`, `discovery_disabled`) are **pre-registration statuses**. They describe the outcome of the Discovery process (Block 2) **before** a plugin transitions into the Registration status model (Block 3). These statuses are not stored in the plugin registry.

| Status | Description | Result |
|--------|-------------|--------|
| `discovery_found` | Plugin discovered, manifest valid | Registration possible |
| `discovery_invalid` | Manifest faulty or incomplete | Not registrable |
| `discovery_blocked` | ID collision with another plugin | Not registrable |
| `discovery_disabled` | Plugin explicitly disabled (e.g., via `.disabled` file) | Ignored during discovery |

**Note:** The `discovery_` prefixes provide clear separation from the Registration status `disabled` (Block 3), which describes a **post-registration** manually disabled state.

#### 4.4 Deactivation Mechanism

- **File-based**: Empty file `{plugin-folder}.disabled` disables the plugin
- **Scope**: Deactivation occurs before manifest parsing (performance)
- **Logging**: Disabled plugins logged as `INFO`

#### 4.5 Error Handling During Discovery

| Error | Behavior | Logging |
|-------|----------|---------|
| Missing manifest | Plugin skipped | `WARNING` |
| Invalid YAML | Plugin status: `discovery_invalid` | `ERROR` |
| ID collision | Second plugin: `discovery_blocked` | `ERROR` |
| Directory not readable | Skip | `ERROR` |

### 5. Registration and Status Model

#### 5.1 Overview

Registration occurs automatically after successful discovery (Block 2).

#### 5.2 Automatic Registration

| Phase | Condition | Result Status |
|-------|-----------|---------------|
| 1. Discovery | Plugin found and manifest readable | `discovered` |
| 2. Validation | Required fields + format OK | `validated` |
| 3. Registration | Entry in plugin registry | `registered` (technical/transient) |
| 4. Initial Activation | Persisted final state | `activation_pending` (normative) |

**Important:** For every newly valid plugin, automatic registration MUST end in status **`activation_pending`**. The status `registered` is a technical/transient intermediate state or a metadata record, **not** a final user-visible activation state.

**Normative Rule:**
- New plugins always start in `activation_pending`
- Built-in plugins also start in `activation_pending`
- No path leads automatically to `active`
- Only Owner or Group-Admin with scope may transition to `active`

#### 5.3 Hard Rule: No Auto-Activation

| Rule | Description |
|------|-------------|
| New plugins | Always start in `activation_pending` |
| Built-in plugins | Also start in `activation_pending` |
| No auto-path | No path leads automatically to `active` |
| Activation | Only Owner or Group-Admin with scope may transition to `active` |
| Persistence | Status preserved across restart/backup |

#### 5.4 Registration Status Model

| Status | Description | Visible to | Next Step |
|--------|-------------|------------|-----------|
| `discovered` | Plugin found, waiting for validation | Admin/Owner | → `validated` or `error` |
| `validated` | Manifest format OK | Admin/Owner | → `registered` |
| `registered` | Plugin known in system | Admin/Owner | → `activation_pending` |
| `activation_pending` | Waiting for approval/activation | Admin/Owner | → `active` (by Owner/Admin) |
| `active` | Plugin running in defined scope | Admin/Owner | – (operational) |
| `disabled` | Explicitly disabled | Admin/Owner | Manual reactivation |
| `error` | Error state | Admin/Owner | Error resolution required |

**Note:** The statuses `discovery_found`, `discovery_invalid`, `discovery_blocked`, `discovery_disabled` from the Discovery status (Block 2) are pre-registration statuses and are not stored in the plugin registry. See section 4.3 for the distinction between Discovery status (`discovery_` prefix) and Registration status.

### 6. Validation Errors

| Error | Description | Example |
|-------|-------------|---------|
| `MISSING_REQUIRED_FIELD` | Required field is missing | `id` not specified |
| `INVALID_TYPE` | Field has wrong type | `triggers` is string instead of array |
| `INVALID_ID_FORMAT` | Plugin ID doesn't match format | `id: "123-plugin"` (starts with number) |
| `DUPLICATE_ID` | Plugin ID already exists | Two plugins with `id: "weather-plugin"` |
| `INVALID_VERSION` | Version not in SemVer format | `version: "v1.0"` |
| `INCOMPATIBLE_VERSION` | Bot version too low | Bot 1.5.0, but `min_bot_version: "2.0.0"` |
| `INVALID_TRIGGER_TYPE` | Unknown trigger type | `triggers: ["unknown"]` |
| `RESERVED_ID` | ID is reserved | `id: "core"` |

### 7. Examples

#### 7.1 Valid Manifest

```yaml
id: weather-plugin
name: Weather Notifications
version: 1.0.0
description: Displays current weather data for configured locations
entrypoint: main.py
min_bot_version: 2.0.0
triggers:
  - cron
  - user-triggered
author: max.mustermann@example.com
homepage: https://github.com/example/weather-plugin
license: MIT
min_role: normal
settings_schema:
  location:
    type: text
    required: true
    description: Default location for weather queries
  api_key:
    type: secret
    required: true
    description: API key for weather service
```

#### 7.2 Invalid Manifest Examples

**Example 1: Missing Required Field**
```yaml
name: Faulty Plugin
version: 1.0.0
# ERROR: 'id' is missing
```
*Error: MISSING_REQUIRED_FIELD (id)*

**Example 2: Invalid ID Format**
```yaml
id: 123-weather
name: Weather Plugin
version: 1.0.0
# ERROR: ID starts with number
```
*Error: INVALID_ID_FORMAT*

**Example 3: Incompatible Version**
```yaml
id: advanced-plugin
name: Advanced Plugin
version: 2.0.0
description: Requires newer bot version
entrypoint: main.py
min_bot_version: 3.0.0
triggers:
  - user-triggered
# ERROR: Bot version is 2.5.0, but min_bot_version is 3.0.0
```
*Error: INCOMPATIBLE_VERSION*

### 8. Out of Scope

This contract intentionally does **not** define:
- Plugin loader implementation
- Trigger execution logic
- WebUI implementation
- Plugin file storage location
- Activation and approval flows (see separate documentation)

---

## 9. Rechte-Modell / Permission Model (Block 4)

### 9.1 Zweistufiges Rechteprinzip / Two-Stage Permission Principle

Das Rechtesystem arbeitet mit zwei Ebenen:

| Ebene / Level | Beschreibung / Description |
|---------------|----------------------------|
| **Plugin-Mindestrolle** / Plugin Minimum Role | Vom Plugin-Autor definiert via `min_role` im Manifest |
| **Owner/Admin-Einschränkung** / Owner/Admin Restriction | Vom Bot-Owner oder Group-Admin gesetzte zusätzliche Einschränkung (stricter minimum) |

**Kernregel / Core Rule:**
- Owner/Admin darf die Nutzung **einschränken** (höhere Rolle fordern)
- Owner/Admin darf die Nutzung **niemals unter** die Plugin-Mindestrolle **lockern**
- Die Admin-Einschränkung ist ein zusätzliches Minimum, keine obere Grenze

### 9.2 Rollenmodell / Role Model

| Rolle / Role | Scope | Beschreibung / Description |
|--------------|-------|----------------------------|
| `owner` | Global | Bot-Besitzer, uneingeschränkte Rechte |
| `admin` | Gruppen-scoped / Group-scoped | Gruppen-Administrator, verwaltet Plugins für seine Gruppe(n) |
| `vip` | Benutzer-scoped / User-scoped | Vereinfachte Nutzungsrechte |
| `normal` | Benutzer-scoped / User-scoped | Standardnutzer |

**Wichtig / Important:**
- `admin` ist immer **gruppenbezogen** – ein Admin hat keine globale Berechtigung
- `owner` hat implizit alle Rechte in allen Scopes

### 9.3 Scope-Regeln / Scope Rules

#### 9.3.1 Gruppen-Scope / Group Scope

| Aktion / Action | Erforderliche Rolle / Required Role |
|-----------------|-------------------------------------|
| Plugin global aktivieren | `owner` |
| Plugin für Gruppe aktivieren | `owner` oder `admin` der Gruppe |
| Plugin-Settings ändern | `owner` oder `admin` mit Scope |
| Plugin deaktivieren | `owner` oder `admin` mit Scope |

#### 9.3.2 Topic-Scope / Topic Scope

Topics sind optionale Untereinheiten innerhalb einer Gruppe:

| Konfiguration / Configuration | Verhalten / Behavior |
|-------------------------------|----------------------|
| Plugin ohne Topic-Scope | Gilt für gesamte Gruppe |
| Plugin mit Topic-Scope | Nur in definierten Topics aktiv |
| Topic-Auswahl | Erfordert `admin` oder `owner` für die Gruppe |

### 9.4 Policy-Evaluation-Order / Policy Evaluation Order

Die Berechtigungsprüfung erfolgt in dieser Reihenfolge:

```
1. Berechne effective_min_role = max(plugin_min_role, admin_restriction)
   → Falls admin_restriction < plugin_min_role: Widerspruch, plugin_min_role bleibt gültig
   
2. Ist der anfragende Nutzer im Scope berechtigt?
   → Nein: Zugriff verweigert
   
3. Ist die Nutzerrolle ≥ effective_min_role?
   → Nein: Zugriff verweigert
   
4. Zugriff erlaubt
```

**Formel / Formula:**
```
effective_min_role = max(plugin_min_role, admin_restriction)
access_granted = user_has_scope_permission AND user_role >= effective_min_role
```

**Wichtig / Important:**
- Die Admin-Einschränkung ist ein zusätzliches **Minimum** (stricter floor), keine obere Grenze
- Wenn `admin_restriction < plugin_min_role`, ist die Einschränkung ungültig (Lockern versucht) – Plugin-Minimum bleibt wirksam

### 9.5 Truth-Table / Wahrheitstabelle

#### Typische Fälle / Typical Cases

| Plugin-Mindestrolle | Admin-Einschränkung | Anfragende Rolle | Ergebnis | Begründung |
|---------------------|---------------------|------------------|----------|------------|
| `normal` | - | `normal` | ✅ Erlaubt | Minimum erfüllt |
| `normal` | - | `vip` | ✅ Erlaubt | Über Minimum |
| `vip` | - | `normal` | ❌ Verweigert | Unter Minimum |
| `vip` | - | `vip` | ✅ Erlaubt | Minimum erfüllt |
| `admin` | - | `normal` | ❌ Verweigert | Unter Minimum |
| `admin` | - | `admin`* | ✅ Erlaubt | Minimum erfüllt (*mit Gruppen-Scope) |
| `owner` | - | `admin` | ❌ Verweigert | Unter Minimum |
| `owner` | - | `owner` | ✅ Erlaubt | Minimum erfüllt |

#### Edge Cases / Grenzfälle

| Plugin-Mindestrolle | Admin-Einschränkung | Anfragende Rolle | Ergebnis | Begründung |
|---------------------|---------------------|------------------|----------|------------|
| `normal` | `vip` | `normal` | ❌ Verweigert | Unter effective_minimum (vip) |
| `normal` | `vip` | `vip` | ✅ Erlaubt | Einschränkung erfüllt |
| `vip` | `vip` | `vip` | ✅ Erlaubt | Beide Bedingungen erfüllt |
| `vip` | `normal` | `normal` | ❌ Verweigert | Unter plugin_minimum (vip) |
| `vip` | `normal` | `vip` | ✅ Erlaubt | Plugin-Minimum erfüllt, Admin-Einschränkung ignoriert (Widerspruch: Lockern versucht) |
| `admin` | `vip` | `admin`* | ✅ Erlaubt | Plugin-Minimum erfüllt, Admin-Einschränkung ignoriert (Widerspruch: Lockern versucht) |
| `admin` | `vip` | `vip` | ❌ Verweigert | Unter plugin_minimum (admin) |

**Hinweis:** Wenn Admin-Einschränkung < Plugin-Mindestrolle, ist die Einschränkung ungültig (versucht Lockern) und Plugin-Mindestrolle bleibt als wirksames Minimum erhalten.

### 9.6 Policy-Verletzungen / Policy Violations

| Verletzung / Violation | Verhalten / Behavior | Logging |
|------------------------|----------------------|---------|
| Lockern unter Minimum | Abgelehnt, Admin wird informiert | `WARNING` |
| Scope-Verletzung | Abgelehnt | `INFO` |
| Berechtigungsprüfung fehlgeschlagen | Abgelehnt | `INFO` |

### 9.7 QA-Verifizierung / QA Verification

QA kann anhand folgender Kriterien prüfen:

| Testfall / Test Case | Erwartetes Verhalten / Expected Behavior |
|----------------------|------------------------------------------|
| Plugin mit `min_role: vip` | `normal` Nutzer können Plugin nicht nutzen |
| Admin setzt Einschränkung `vip` | `normal` Nutzer können Plugin nicht nutzen, obwohl Minimum `normal` wäre |
| Plugin mit `min_role: owner` | Nur Owner kann Plugin nutzen/aktivieren |
| Versuch, unter Minimum zu lockern | System lehnt ab, behält Plugin-Minimum bei |
| Admin ohne Gruppen-Scope | Keine Aktivierungsmöglichkeit für diese Gruppe |

**Kern-Garantie / Core Guarantee:**
> Das System verhindert, dass ein Plugin jemals mit weniger Rechten ausgeführt wird, als der Plugin-Autor in `min_role` definiert hat.

### 9.8 Block-Übergang / Block Transition

Block 4 (Rechte-Modell) ist Voraussetzung für Block 5. Die Policy-Evaluation aus 9.4 wird bei jeder Aktivierungsanfrage angewendet.

---

## 10. Aktivierungs-/Freigabe-Flow (Block 5) / Activation and Approval Flow

### 10.1 Übersicht / Overview

Der Aktivierungsflow definiert, wie Plugins aus dem Zustand `activation_pending` (Block 3) in den Zustand `active` überführt werden können. Dieser Flow ist zentral für die Sicherheit des Plugin-Systems, da er verhindert, dass ungeprüfte oder nicht autorisierte Plugins automatisch aktiv werden.

### 10.2 Aktivierungsobjekt / Activation Object

| Feld / Field | Typ / Type | Beschreibung / Description |
|--------------|------------|----------------------------|
| `plugin_id` | String | Eindeutige Plugin-ID / Unique plugin identifier |
| `scope` | Object | Scope-Definition (Gruppe/Topic) / Scope definition (group/topic) |
| `scope.group_id` | Integer | Telegram-Gruppen-ID / Telegram group ID |
| `scope.topic_ids` | Array[Integer] | Optional: Topic-IDs innerhalb der Gruppe / Optional: Topic IDs within group |
| `configuration` | Object | Plugin-spezifische Einstellungen / Plugin-specific settings |
| `requested_by` | Integer | User-ID des Anfragenden / User ID of requester |
| `requested_at` | Timestamp | Zeitpunkt der Anfrage / Request timestamp |
| `approval_status` | Enum | `pending`, `approved`, `rejected`, `blocked` |
| `approved_by` | Integer | User-ID des Genehmigenden / User ID of approver |
| `approved_at` | Timestamp | Zeitpunkt der Genehmigung / Approval timestamp |
| `activation_status` | Enum | `inactive`, `activating`, `active`, `deactivating`, `error` |

**Hinweis:** Scope kann global (Owner-only), gruppen-basiert oder topic-spezifisch sein.

### 10.3 Berechtigungen für Aktivierung/Deaktivierung / Permissions for Activation/Deactivation

#### 10.3.1 Wer darf was? / Who Can Do What?

| Aktion / Action | Erforderliche Rolle / Required Role | Scope-Anforderung / Scope Requirement |
|-----------------|-------------------------------------|---------------------------------------|
| Plugin global aktivieren | `owner` | Global |
| Plugin für Gruppe aktivieren | `owner` oder `admin` | Gruppen-Admin muss für diese Gruppe Admin sein |
| Plugin für Topic aktivieren | `owner` oder `admin` | Admin benötigt Scope für die übergeordnete Gruppe |
| Plugin deaktivieren | `owner` oder `admin` mit Scope | Same as activation |
| Plugin-Settings ändern | `owner` oder `admin` mit Scope | Same as activation |
| Konfiguration ändern | `owner` oder `admin` mit Scope | Same as activation |

#### 10.3.2 Berechtigungsprüfung / Permission Check

Die Berechtigungsprüfung erfolgt in zwei Ebenen:

**Ebene 1: Scope-Berechtigung / Level 1: Scope Permission**
- Ist der Anfragende Owner? → Globaler Zugriff gewährt
- Ist der Anfragende Admin für die Zielgruppe? → Gruppen-Zugriff gewährt
- Keine der beiden? → Zugriff verweigert

**Ebene 2: Policy-Prüfung (Block 4) / Level 2: Policy Check (Block 4)**
```
effective_min_role = max(plugin_min_role, admin_restriction)
access_granted = user_has_scope_permission AND user_role >= effective_min_role
```

### 10.4 Pflicht-Settings-Blockade / Required Settings Blocking

| Bedingung / Condition | Verhalten / Behavior |
|-----------------------|----------------------|
| Alle Pflicht-Settings vorhanden | Aktivierung möglich / Activation allowed |
| Pflicht-Settings fehlen | Aktivierung blockiert / Activation blocked |
| Ungültige Konfiguration | Aktivierung blockiert / Activation blocked |

**Regeln / Rules:**
- Ein Plugin mit `settings_schema` kann nicht aktiviert werden, wenn Pflichtfelder (`required: true`) nicht konfiguriert sind.
- Das System muss vor der Aktivierung validieren, dass alle `required` Felder in `configuration` vorhanden sind.
- Secrets müssen gesetzt sein (nicht leer), wenn sie als Pflicht markiert sind.

### 10.5 Zustandsmodell für Aktivierungs-Entscheidungen / Activation Decision State Model

**Wichtige Unterscheidung / Important Distinction:**

| Aspekt / Aspect | Block 3 (Registrierung/Runtime) | Block 5 (Aktivierungs-Entscheidung) |
|-----------------|----------------------------------|-------------------------------------|
| **Feldname** | `status` (Plugin-Status) | `approval_status` (Entscheidungs-Status) |
| **Gültige Werte** | `discovered`, `validated`, `registered`, `activation_pending`, `active`, `disabled`, `error` | `pending`, `approved`, `rejected`, `blocked` |
| **Lebenszyklus** | Lebensdauer des Plugins | Lebensdauer einer Aktivierungsanfrage |
| **Persistenz** | Plugin-Registry | Aktivierungsanfrage-Objekt |

Die folgenden Status (`pending`, `approved`, `rejected`, `blocked`) gehören zum **Aktivierungs-Entscheidungs-Subflow** (`approval_status` auf dem Aktivierungsanfrage-Objekt), NICHT zum Block-3-Registrierungsstatus. Ein Plugin im Block-3-Status `activation_pending` durchläuft bei einer Aktivierungsanfrage einen separaten Entscheidungsprozess mit eigenen Zuständen.

**Block 3 Registrierungsstatus-Modell** (unverändert): `discovered` → `validated` → `registered` → `activation_pending` → `active` ←→ `disabled`, `error` möglich von überall

**Block 5 Aktivierungs-Entscheidungsmodell** (`approval_status` auf Aktivierungsanfrage):
- `pending` → `approved` → Plugin-Status wechselt zu `active` (System aktiviert)
- `pending` → `rejected` (Ablehnung, Plugin-Status bleibt `activation_pending`)
- Direkt `blocked` (durch Systempolitik blockiert)

Das folgende Diagramm zeigt den **Aktivierungs-Entscheidungsfluss**. Rechte Seite = Block 3 Plugin-Status (`status`), linke Seite = Block 5 Aktivierungsanfrage-Status (`approval_status`):

```
BLOCK 5 (Aktivierungsanfrage / Activation Request)    BLOCK 3 (Plugin-Registry)

┌─────────────────────────┐
│  KEINE ANFRAGE          │
│  (No request exists)    │                         ┌─────────────────┐
└───────────┬─────────────┘                         │                 │
            │ Anfrage erstellt                        │   activation_   │
            ▼ (Create request with                    │    pending      │
┌─────────────────────────┐    approval_status)       │   (Block 3)     │
│    approval_status:     │──────────────────────────▶│                 │
│       PENDING           │                           └─────────────────┘
│  (Anfrage ausstehend)   │                                    │
└───────────┬─────────────┘                                    │
            │ Genehmigung                                        │
            ▼ erteilt                                            │
┌─────────────────────────┐                           ┌────────▼────────┐
│    approval_status:     │                           │                 │
│      APPROVED           │──────────────────────────▶│     ACTIVE      │
│  (Genehmigt)            │   System aktiviert Plugin │   (Plugin läuft)│
└─────────────────────────┘                           └────────┬────────┘
                                                               │
            ┌──────────────────────────────────────────────────┘
            │ Deaktivierung durch Admin/Owner
            ▼
┌─────────────────────────┐                           ┌─────────────────┐
│   (Anfrage kann neu     │                           │                 │
│    erstellt werden)     │                           │    DISABLED     │
│                         │◀─────────────────────────│   (Block 3)     │
└─────────────────────────┘   Neustart von vorne       │                 │
                                                        └─────────────────┘
                                                               │
            ┌──────────────────────────────────────────────────┘
            │ Reaktivierungsanfrage
            ▼
┌─────────────────────────┐                           ┌─────────────────┐
│    approval_status:     │──────────────────────────▶│   activation_   │
│       PENDING           │                           │    pending      │
│  (Neue Anfrage)         │                           │   (Block 3)     │
└─────────────────────────┘                           └─────────────────┘

LEGENDE:
─────────▶  Block 3 Status-Transition (Plugin-Registry)
──────────▶ Block 5 Approval-Status-Transition (Anfrage-Objekt)
```

**Status-Trennung klargestellt / Status Separation Clarified:**

| Status-Typ | Zugehörigkeit | Beschreibung |
|------------|---------------|--------------|
| `activation_pending` | Block 3 (Plugin) | Plugin wartet auf erste Aktivierung |
| `active` | Block 3 (Plugin) | Plugin läuft aktuell |
| `disabled` | Block 3 (Plugin) | Plugin wurde deaktiviert |
| `error` | Block 3 (Plugin) | Plugin hat Fehlerzustand |
| `pending` | Block 5 (Anfrage) | Aktivierungsanfrage wartet auf Genehmigung |
| `approved` | Block 5 (Anfrage) | Aktivierungsanfrage genehmigt, Plugin wird aktiviert |
| `rejected` | Block 5 (Anfrage) | Aktivierungsanfrage abgelehnt, Plugin bleibt `activation_pending` |
| `blocked` | Block 5 (Anfrage) | Aktivierungsanfrage durch System blockiert |

**Zustandsübergänge / State Transitions:**

### Block 3: Plugin-Status-Übergänge (Status-Feld `status`)

| Von / From | Nach / To | Auslöser / Trigger | Berechtigung / Permission |
|------------|-----------|--------------------|---------------------------|
| `discovered` | `validated` | Manifest-Validierung OK | System (automatisch) |
| `validated` | `registered` | Eintrag in Registry | System (automatisch) |
| `registered` | `activation_pending` | Registrierung abgeschlossen | System (automatisch) |
| `activation_pending` | `active` | `approval_status=approved` → System aktiviert | System (automatisch) |
| `active` | `disabled` | Deaktivierungsanfrage | Owner/Admin mit Scope |
| `disabled` | `activation_pending` | Reaktivierungsanfrage | Owner/Admin mit Scope |
| `active` | `error` | Laufzeitfehler | System (automatisch) |
| `error` | `disabled` | Fehler behoben | Owner/Admin mit Scope |

### Block 5: Aktivierungsanfrage-Status-Übergänge (Status-Feld `approval_status`)

| Von / From | Nach / To | Auslöser / Trigger | Berechtigung / Permission | Folge auf Plugin-Status |
|------------|-----------|--------------------|---------------------------|-------------------------|
| *(kein Anfrage-Objekt)* | `pending` | Aktivierungsanfrage erstellt | Owner/Admin mit Scope | Plugin bleibt `activation_pending` |
| `pending` | `approved` | Genehmigung erteilt | Owner/Admin mit Scope | System wechselt Plugin zu `active` |
| `pending` | `rejected` | Ablehnung durch Admin | Owner/Admin mit Scope | Plugin bleibt `activation_pending` |
| `pending` | `blocked` | Systempolitik blockiert | System (automatisch) | Plugin bleibt `activation_pending` |

**Hinweis zu rejected / Note on rejected:**
Ein `rejected` Status im Aktivierungs-Entscheidungsmodell (Block 5 `approval_status`) bedeutet, dass die Aktivierungsanfrage abgelehnt wurde. Das Plugin verbleibt im Block-3-Registrierungsstatus `activation_pending` und kann eine neue Aktivierungsanfrage stellen. `rejected` ist **kein** Block-3-Registrierungsstatus.

### 10.6 Audit-Trail / Audit Trail

#### 10.6.1 Was wird protokolliert? / What Gets Logged?

Jede Zustandsänderung ist vollständig auditierbar:

| Ereignis / Event | Protokollierte Daten / Logged Data |
|------------------|------------------------------------|
| Aktivierungsanfrage | plugin_id, scope (group_id, topic_ids), requested_by, requested_at, configuration_summary |
| Genehmigung | approved_by, approved_at, approval_status |
| Ablehnung | rejected_by, rejected_at, reason (optional) |
| Aktivierung | plugin_id, scope, configuration_hash, activated_at |
| Deaktivierung | plugin_id, scope, deactivated_by, deactivated_at, reason |
| Fehler | error_code, error_message (ohne Secrets), timestamp |
| Konfigurationsänderung | changed_by, changed_at, changed_fields (keine Secrets im Klartext) |

#### 10.6.2 Scope-/Konfigurations-Zusammenfassung / Scope/Config Summary

- **Scope-Info:** Gruppen-ID, Topic-IDs (falls zutreffend)
- **Konfigurations-Summary:** Hash oder strukturierte Zusammenfassung ohne Secret-Werte
- **Keine Secrets:** Passwörter, API-Keys und andere Secrets werden nie im Audit-Trail im Klartext gespeichert

### 10.7 Zustände und Transitionen für QA / States and Transitions for QA

#### 10.7.1 Vollständiger Status-Graph

**Wichtige Unterscheidung / Important Distinction:**

| Status-Feld | Zugehörigkeit | Gültige Werte |
|-------------|---------------|---------------|
| `status` | Block 3 (Plugin-Registry) | `discovered`, `validated`, `registered`, `activation_pending`, `active`, `disabled`, `error` |
| `approval_status` | Block 5 (Aktivierungsanfrage-Objekt) | `pending`, `approved`, `rejected`, `blocked` |

Die Zustände `pending`, `approved`, `rejected`, `blocked` gehören zum Aktivierungs-Entscheidungsmodell (Block 5 `approval_status`), nicht zum Block-3-Registrierungsmodell. Ein Plugin im Block-3-Status `activation_pending` kann eine Aktivierungsanfrage stellen, die einen separaten Aktivierungs-Entscheidungsprozess mit eigenen Zuständen durchläuft. `rejected` ist eine Aktivierungs-Entscheidung, kein Block-3-Registrierungsstatus.

**Note:** The states `pending`, `approved`, `rejected`, `blocked` belong to the activation decision model (Block 5 `approval_status`), not the Block 3 registration model. A plugin in Block 3 status `activation_pending` may submit an activation request that undergoes a separate activation decision process with its own states. `rejected` is an activation decision outcome, not a Block 3 registration state.

**Block 3 Registrierungsstatus-Modell (`status` Feld) / Block 3 Registration Status Model (`status` field):**
```
discovered → validated → registered → activation_pending → active
                                          ↑              │
                                          └──────────────┤
                                    disabled ←─────────────┘
                                    error (von überall möglich)
```

**Block 5 Aktivierungs-Entscheidungsmodell (`approval_status` Feld) / Block 5 Activation Decision Model (`approval_status` field):**
```
(Keine Anfrage) → pending → approved
                     ↓
                  rejected
                     ↓
                  blocked
```

**Zusammenspiel beider Modelle / Interaction of both models:**

| Schritt | Block 3 (`status`) | Block 5 (`approval_status`) | Aktion |
|---------|-------------------|----------------------------|--------|
| 1 | `activation_pending` | *(keine Anfrage)* | Plugin wartet auf Aktivierung |
| 2 | `activation_pending` | `pending` | Anfrage erstellt, wartet auf Genehmigung |
| 3 | `activation_pending` | `approved` | Genehmigt, System aktiviert Plugin |
| 4 | `active` | *(Anfrage abgeschlossen)* | Plugin läuft |

**Korrektur der Zustandsübergänge / Corrected State Transitions:**

### Block 3: Plugin-Registry Status (`status` Feld)

| Aktueller Status / Current State | Gültige nächste Status / Valid Next States |
|----------------------------------|--------------------------------------------|
| `discovered` | `validated`, `error` |
| `validated` | `registered`, `error` |
| `registered` | `activation_pending`, `error` |
| `activation_pending` | `active` (nach `approval_status=approved`), `error` |
| `active` | `disabled`, `error` |
| `disabled` | `activation_pending` (bei Reaktivierung) |
| `error` | `disabled`, `activation_pending` (nach Behebung) |

### Block 5: Aktivierungsanfrage (`approval_status` Feld)

| Aktueller Status / Current State | Gültige nächste Status / Valid Next States |
|----------------------------------|--------------------------------------------|
| *(keine Anfrage)* | `pending` (Anfrage erstellt) |
| `pending` | `approved`, `rejected`, `blocked` |
| `approved` | *(Endzustand - Anfrage abgeschlossen)* |
| `rejected` | `pending` (neue Anfrage möglich) |
| `blocked` | `pending` (neue Anfrage möglich) |

#### 10.7.2 Fehlerbehandlung / Error Handling

| Fehlerbedingung / Error Condition | Resultierender Status / Resulting Status |
|-----------------------------------|------------------------------------------|
| Pflicht-Settings fehlen | Plugin-Registrierung bleibt in `activation_pending`; separate Aktivierungsanfrage kann `approval_status=rejected` erhalten / Plugin registration stays in `activation_pending`; separate activation request may receive `approval_status=rejected` |
| Berechtigung fehlt | Anfrage abgelehnt, Status unverändert |
| Laufzeitfehler | `error`, Plugin wird gestoppt |
| Konfigurationsfehler | `error`, zur manuellen Prüfung |

### 10.8 Interaktion mit Block 4 (Policy-Engine) / Interaction with Block 4

Der Aktivierungsflow nutzt das Rechtemodell aus Block 4:

1. **Vor Aktivierung:** Policy-Evaluation mit `effective_min_role`
2. **Während Aktivierung:** Scope-Prüfung (Owner global, Admin gruppenbasiert)
3. **Nach Aktivierung:** Laufende Berechtigungsprüfungen bei Settings-Änderungen

**Wichtig:** Ein Admin kann die Nutzung einschränken (höhere Rolle fordern), aber niemals unter das Plugin-Minimum lockern.

---

## 11. Settings-Schema & Validierung (Block 6) / Settings Schema & Validation

### 11.1 Übersicht / Overview

Dieser Abschnitt definiert den vereinheitlichten Settings-Schema-Vertrag für Plugins. Plugins müssen ihre Konfigurationsfelder über `settings_schema` deklarieren, das UI-fähige Metadaten für jedes Feld enthält.

This section defines the unified settings schema contract for plugins. Plugins must declare their configuration fields via `settings_schema`, which includes UI-capable metadata for each field.

**Abhängigkeiten / Dependencies:** Block 1 (Plugin-Contract), Block 5 (Aktivierungs-Flow)

### 11.2 Schema-Struktur / Schema Structure

```yaml
settings_schema:
  field_name:
    type: text|number|bool|select|secret
    required: true|false
    default: <value>
    description: "Human-readable description"
    # Typ-spezifische Validierung / Type-specific validation:
    min: <number>          # Nur für type: number
    max: <number>          # Nur für type: number
    options: [<values>]    # Nur für type: select
    pattern: <regex>         # Optional für type: text
```

### 11.3 Unterstützte Typen / Supported Types

| Typ / Type | Beschreibung / Description | UI-Rendering |
|------------|---------------------------|--------------|
| `text` | Freitext / Free text | Textfeld / Text input |
| `number` | Numerischer Wert / Numeric value | Zahleneingabe / Number input |
| `bool` | Boolean / Boolean | Checkbox / Toggle / Checkbox / Toggle |
| `select` | Auswahl aus Optionen / Selection from options | Dropdown / Dropdown |
| `secret` | Sensitive Daten / Sensitive data | Passwortfeld (maskiert) / Password field (masked) |

### 11.4 Validierungsregeln / Validation Rules

#### Basis-Validierung / Base Validation

| Regel / Rule | Beschreibung / Description | Anwendbar / Applicable |
|--------------|---------------------------|------------------------|
| `required` | Feld muss Wert haben / Field must have value | Alle / All |
| `range` (min/max) | Wertebereich / Value range | `number` |
| `options` / `enum` | Erlaubte Werte / Allowed values | `select` |
| `pattern` | Regex-Pattern | `text` (optional) |

#### Leerwert-Handling & Defaults / Empty Value Handling & Defaults

| Konfiguration / Configuration | Verhalten / Behavior |
|------------------------------|----------------------|
| `required: true` + kein Default | Validierung fehlschlägt bei leer / Validation fails if empty |
| `required: true` + Default | Default übernehmen bei leer / Apply default if empty |
| `required: false` + kein Default | `null` oder `undefined` erlaubt / `null` or `undefined` allowed |
| `required: false` + Default | Default bei leerem Wert / Default when empty |

**Beispiel / Example:**
```yaml
settings_schema:
  timeout:
    type: number
    required: true
    default: 30
    description: "Timeout in Sekunden / Timeout in seconds"
    min: 5
    max: 300
```

### 11.5 Secret-Handling / Secret Handling

Secrets erfordern besondere Behandlung in allen Ausgabekanälen:

Secrets require special treatment in all output channels:

#### Maskierung / Masking

- In UI-Formularen als `••••••••` anzeigen / Display as `••••••••` in UI forms
- Nur erster Buchstabe oder Hash-Präfix sichtbar / Only first character or hash prefix visible
- Keine Vollanzeige in Input-Feldern / No full display in input fields

#### Ausgabegrenzen / Output Boundaries

| Kontext / Context | Verhalten / Behavior |
|-------------------|---------------------|
| UI-Formular / UI Form | Maskiert anzeigen / Display masked |
| Status-API / Status API | `null` oder maskiert zurückgeben / Return `null` or masked |
| Logs | **Nie im Klartext** / **Never in cleartext** |
| Audit-Trail | Referenz-ID statt Wert / Reference ID instead of value |

#### Code-Beispiele / Code Examples

```python
# ❌ VERBOTEN / FORBIDDEN:
logger.info(f"API Key: {config.api_key}")
response.send({"api_key": plugin_config.secret_value})
audit_log.write({"secret": settings.webhook_token})

# ✅ ERLAUBT / ALLOWED:
logger.info("API Key configured")  # Kein Wert / No value
response.send({"api_key": "***MASKED***"})
audit_log.write({"api_key_ref": "key_abc123", "is_configured": True})
```

### 11.6 QA-Gate Testfälle / QA Gate Test Cases

#### Testfall-Matrix / Test Case Matrix

| Typ / Type | Gültig / Valid | Ungültig / Invalid | Grund / Reason |
|------------|----------------|-------------------|----------------|
| `text` | `"hello"` | `null` (wenn required) | Pflichtfeld leer / Required empty |
| `text` | `"hello"` | `123` | Typ-Fehler / Type error |
| `number` | `42` | `"abc"` | Typ-Fehler / Type error |
| `number` | `10` | `-5` (min: 0) | Unter Minimum / Below min |
| `number` | `50` | `500` (max: 100) | Über Maximum / Above max |
| `bool` | `true` | `"yes"` | Typ-Fehler / Type error |
| `select` | `"option_a"` | `"invalid"` | Nicht in Optionen / Not in options |
| `secret` | `"sk-12345"` | `null` (wenn required) | Pflichtfeld leer / Required empty |

#### QA-Status-Tabelle / QA Status Table

| Typ / Type | Gültiger Test / Valid Test | Ungültiger Test / Invalid Test | Status |
|------------|------------------------------|-------------------------------|--------|
| text | ✅ `"hello"` | ❌ `null` (req) | ⬜ |
| text | ✅ `"hello"` | ❌ `123` | ⬜ |
| number | ✅ `42` | ❌ `"abc"` | ⬜ |
| number | ✅ `10` | ❌ `-5` (min) | ⬜ |
| bool | ✅ `true` | ❌ `"yes"` | ⬜ |
| select | ✅ `"option_a"` | ❌ `"invalid"` | ⬜ |
| secret | ✅ `"sk-12345"` | ❌ `null` (req) | ⬜ |

**QA kann direkt aus dieser Dokumentation pass/fail markieren.**

**QA can mark pass/fail directly from this documentation.**

### 11.7 Integration mit Block 5 / Integration with Block 5

Das Settings-Schema wird bei der Aktivierung validiert (Block 5, Abschnitt 10.4):

The settings schema is validated during activation (Block 5, section 10.4):

| Bedingung / Condition | Verhalten / Behavior |
|------------------------|----------------------|
| Alle `required` Felder vorhanden | Aktivierung möglich / Activation allowed |
| Pflichtfeld fehlt / Required missing | Blockiert, Fehlermeldung / Blocked, error message |
| Validierung fehlschlägt / Validation fails | Blockiert, Status bleibt `activation_pending` / Blocked, stays `activation_pending` |

### 11.8 Zusammenfassung Block 6 / Block 6 Summary

| Aspekt / Aspect | Definition |
|-----------------|------------|
| Schema-Location / Schema Location | `settings_schema` in `plugin.yaml` |
| Unterstützte Typen / Supported Types | `text`, `number`, `bool`, `select`, `secret` |
| Validierung / Validation | `required`, `min/max`, `options`, `pattern` |
| Secret-Sicherheit / Secret Security | Maskiert in UI, nie im Klartext loggen / Masked in UI, never log cleartext |
| QA-Gate | Mindestens 1 gültig + 1 ungültig pro Typ / At least 1 valid + 1 invalid per type |

---

## 12. Trigger-Baseline (Block 7) / Trigger Baseline

### 12.1 Übersicht / Overview

Dieser Abschnitt definiert den verbindlichen Trigger-Contract für das Plugin-System. Trigger bestimmen, wann und wie ein Plugin ausgeführt wird. Für den MVP werden `cron`, `interval` und `user-triggered` unterstützt; `ki-triggered` ist reserviert für Post-MVP.

This section defines the binding trigger contract for the plugin system. Triggers determine when and how a plugin executes. For the MVP, `cron`, `interval`, and `user-triggered` are supported; `ki-triggered` is reserved for Post-MVP.

**Abhängigkeiten / Dependencies:** Block 5 (Aktivierungs-Flow), Block 6 (Settings-Schema)

### 12.2 Unterstützte Trigger-Typen / Supported Trigger Types

| Trigger-Typ / Trigger Type | Beschreibung / Description | MVP-Status |
|---------------------------|---------------------------|------------|
| `cron` | Zeitbasierte Ausführung via Cron-Ausdruck / Time-based execution via cron expression | ✅ Aktiv / Active |
| `interval` | Periodische Ausführung in festem Intervall / Periodic execution at fixed interval | ✅ Aktiv / Active |
| `user-triggered` | Manuelle Ausführung durch Benutzerbefehl / Manual execution via user command | ✅ Aktiv / Active |
| `ki-triggered` | KI-gesteuerte Ausführung / AI-controlled execution | ⛔ Reserviert / Reserved |

### 12.3 Trigger-Contract / Trigger Contract

#### 12.3.1 Cron-Trigger / Cron Trigger

| Attribut / Attribute | Typ / Type | Beschreibung / Description | Validierung / Validation |
|---------------------|-----------|---------------------------|-------------------------|
| `expression` | String | Standard-Cron-Ausdruck (5 Felder: min hour day month weekday) | Syntax-Prüfung / Syntax check |
| `timezone` | String | Optional: Zeitzone (default: UTC) | IANA-Zeitzonen-Name / IANA timezone name |
| `enabled` | Boolean | Aktiviert/Deaktiviert | Boolean-Prüfung / Boolean check |

**Cron-Format / Cron Format:**
```
* * * * *
│ │ │ │ └─ Wochentag (0-7, 0 und 7 = Sonntag) / Weekday (0-7, 0 and 7 = Sunday)
│ │ │ └── Monat (1-12) / Month (1-12)
│ │ └──── Tag (1-31) / Day (1-31)
│ └────── Stunde (0-23) / Hour (0-23)
└──────── Minute (0-59) / Minute (0-59)
```

**Beispiele / Examples:**
```yaml
triggers:
  - type: cron
    expression: "0 9 * * 1"      # Jeden Montag um 9:00 / Every Monday at 9:00
    timezone: "Europe/Berlin"
  - type: cron
    expression: "*/15 * * * *"   # Alle 15 Minuten / Every 15 minutes
```

#### 12.3.2 Interval-Trigger / Interval Trigger

| Attribut / Attribute | Typ / Type | Beschreibung / Description | Validierung / Validation |
|---------------------|-----------|---------------------------|-------------------------|
| `seconds` | Integer | Intervall in Sekunden / Interval in seconds | ≥ Mindestwert / ≥ Minimum value |
| `enabled` | Boolean | Aktiviert/Deaktiviert | Boolean-Prüfung / Boolean check |

**Mindestwerte / Minimum Values:**

| Umgebung / Environment | Minimum | Hinweis / Note |
|------------------------|---------|----------------|
| Produktion / Production | 60 Sekunden / 60 seconds | Schutz vor Überlastung / Flood protection |
| Entwicklung / Development | 10 Sekunden / 10 seconds | Kürzer für Tests / Shorter for testing |

**Beispiele / Examples:**
```yaml
triggers:
  - type: interval
    seconds: 300    # Alle 5 Minuten / Every 5 minutes
  - type: interval
    seconds: 3600   # Jede Stunde / Every hour
```

#### 12.3.3 User-Triggered / User Triggered

| Attribut / Attribute | Typ / Type | Beschreibung / Description | Validierung / Validation |
|---------------------|-----------|---------------------------|-------------------------|
| `command` | String | Befehlsname für den Nutzer / Command name for users | Eindeutig pro Scope / Unique per scope |
| `description` | String | Hilfetext für den Befehl / Help text for command | Max. 200 Zeichen / Max. 200 chars |
| `cooldown_seconds` | Integer | Cooldown zwischen Aufrufen / Cooldown between calls | ≥ 0 (default: 0) |

**Beispiele / Examples:**
```yaml
triggers:
  - type: user-triggered
    command: "weather"
    description: "Zeigt aktuelles Wetter an / Shows current weather"
    cooldown_seconds: 30
```

#### 12.3.4 KI-Triggered (Reserviert) / AI-Triggered (Reserved)

`ki-triggered` ist im **MVP nicht aktiv** und kann nicht konfiguriert werden.

`ki-triggered` is **not active in MVP** and cannot be configured.

```yaml
# ⛔ Dies ist im MVP UNGÜLTIG / This is INVALID in MVP:
triggers:
  - type: ki-triggered
```

### 12.4 Validierungs- und Konfliktregeln / Validation and Conflict Rules

#### 12.4.1 Ungültiger Cron-Ausdruck / Invalid Cron Expression

| Fehler / Error | Verhalten / Behavior | Logging |
|----------------|----------------------|---------|
| Syntaxfehler / Syntax error | Trigger ungültig / Trigger invalid | `ERROR` |
| Nicht-existente Zeit / Non-existent time | Ignoriert (z.B. 31. Feb.) / Ignored (e.g., Feb 31) | `WARNING` |
| Zu häufig (< 1 min) / Too frequent (< 1 min) | Abgelehnt / Rejected | `ERROR` |

**Cron-Validierungsregeln / Cron Validation Rules:**

| Regel / Rule | Beschreibung / Description |
|--------------|---------------------------|
| 5 Felder erforderlich / 5 fields required | Minute, Stunde, Tag, Monat, Wochentag / Minute, hour, day, month, weekday |
| Gültige Werte / Valid values | Numerisch oder Wildcard `*` / Numeric or wildcard `*` |
| Schritte erlaubt / Steps allowed | `*/5` für alle 5 Einheiten / `*/5` for every 5 units |
| Bereiche erlaubt / Ranges allowed | `9-17` für 9 bis 17 Uhr / `9-17` for 9 to 17 hours |
| Listen erlaubt / Lists allowed | `1,15` für 1. und 15. / `1,15` for 1st and 15th |

#### 12.4.2 Zu kurzes Intervall / Too Short Interval

| Intervall / Interval | Verhalten / Behavior | Hinweis / Note |
|---------------------|----------------------|---------------|
| `< 10s` | ❌ Abgelehnt / Rejected | Sicherheitsgrenze / Safety limit |
| `10-59s` | ⚠️ Nur in Dev erlaubt / Dev only | Warnung in Logs / Warning in logs |
| `≥ 60s` | ✅ Erlaubt / Allowed | Standard-Minimum / Standard minimum |

#### 12.4.3 Konfliktregeln / Conflict Rules

| Konflikt / Conflict | Regel / Rule | Beispiel / Example |
|--------------------|--------------|------------------|
| Gleicher Befehl / Same command | Abgelehnt in gleichem Scope / Rejected in same scope | Zwei Plugins mit `/weather` in Gruppe X / Two plugins with `/weather` in group X |
| Cron + Interval gleichzeitig / Cron + Interval together | Erlaubt, unabhängig ausgeführt / Allowed, executed independently | Plugin hat beide Trigger-Typen / Plugin has both trigger types |
| Überlappende Zeiten / Overlapping times | Jeweils separat ausgeführt / Executed separately | Keine Deduplizierung / No deduplication |

### 12.5 Ausführungsgrenzen und Schutzmaßnahmen / Execution Limits and Protection

#### 12.5.1 Flood-Protection / Flood Protection

| Schutzmaßnahme / Protection | Grenze / Limit | Verhalten / Behavior |
|----------------------------|---------------|---------------------|
| Globaler Rate-Limit / Global rate limit | 100 Ausführungen/Minute / 100 execs/minute | Überlauf in Queue / Overflow to queue |
| Pro-Plugin-Limit / Per-plugin limit | 20 Ausführungen/Minute / 20 execs/minute | Trigger verzögert / Trigger delayed |
| Pro-Nutzer-Limit / Per-user limit | 10 Befehle/Minute / 10 commands/minute | Fehlermeldung / Error message |

#### 12.5.2 Cooldown-Mechanismen / Cooldown Mechanisms

| Cooldown-Typ / Cooldown Type | Standard / Default | Beschreibung / Description |
|-----------------------------|-------------------|---------------------------|
| Globaler Plugin-Cooldown / Global plugin cooldown | 1 Sekunde / 1 second | Zeit zwischen Plugin-Ausführungen / Time between plugin executions |
| Pro-Trigger-Cooldown / Per-trigger cooldown | Je nach Konfig / Per config | `cooldown_seconds` im Trigger / `cooldown_seconds` in trigger |
| Nutzer-spezifisch / User-specific | 0 Sekunden / 0 seconds | Für `user-triggered` / For `user-triggered` |

#### 12.5.3 Ausführungslimits / Execution Limits

| Limit / Limit | Wert / Value | Beschreibung / Description |
|---------------|--------------|---------------------------|
| Maximale Laufzeit / Max execution time | 300 Sekunden / 300 seconds | Plugin wird danach abgebrochen / Plugin terminated after |
| Maximale parallele Ausführungen / Max parallel executions | 5 pro Plugin / 5 per plugin | Neue Anfragen queued / New requests queued |
| Timeout-Grace-Period / Timeout grace period | 30 Sekunden / 30 seconds | Warnung vor hartem Abbruch / Warning before hard kill |

### 12.6 Statusmodell für Trigger / Trigger Status Model

| Status / Status | Beschreibung / Description | Nächster Schritt / Next Step |
|-----------------|---------------------------|------------------------------|
| `configured` | Trigger im Schema definiert / Trigger defined in schema | Validierung / Validation |
| `valid` | Validierung bestanden / Validation passed | Aktivierung möglich / Activation possible |
| `invalid` | Validierung fehlgeschlagen / Validation failed | Fehlerbehebung / Error resolution |
| `active` | Trigger läuft / Trigger running | – (betriebsbereit / operational) |
| `paused` | Temporär pausiert / Temporarily paused | Manual resume |
| `error` | Laufzeitfehler / Runtime error | Manuelle Prüfung / Manual check |

**Zustandsübergänge / State Transitions:**
```
configured → valid → active
              ↓
           invalid (Fehlerbehebung / fix required)

active ↔ paused (Owner/Admin)
active → error (System)
error → configured (Owner/Admin nach Fix / after fix)
```

### 12.7 QA-Gate Testfälle / QA Gate Test Cases

#### 12.7.1 Trigger-Validierung / Trigger Validation

| Testfall / Test Case | Erwartetes Verhalten / Expected Behavior | MVP-Relevant |
|---------------------|------------------------------------------|--------------|
| Gültiger Cron: `0 9 * * 1` | ✅ Akzeptiert, Status `valid` | ✅ Ja |
| Ungültiger Cron: `99 99 * * *` | ❌ Abgelehnt, Status `invalid` | ✅ Ja |
| Intervall 300s | ✅ Akzeptiont, Status `valid` | ✅ Ja |
| Intervall 5s (Produktion) | ❌ Abgelehnt, Fehler: Zu kurz | ✅ Ja |
| Intervall 5s (Entwicklung) | ⚠️ Akzeptiert mit Warnung | ✅ Ja |
| User-Trigger ohne Befehlskonflikt | ✅ Akzeptiert | ✅ Ja |
| User-Trigger mit Befehlskonflikt | ❌ Abgelehnt | ✅ Ja |
| `ki-triggered` in Konfiguration | ❌ Abgelehnt (reserviert) | ✅ Ja |

#### 12.7.2 Flood-Protection Tests

| Testfall / Test Case | Auslöser / Trigger | Erwartetes Verhalten / Expected Behavior |
|---------------------|-------------------|------------------------------------------|
| 101 globale Ausführungen/Min | Hohe Last / High load | 101. in Queue / 101st queued |
| 21 Ausführungen/Plugin/Min | Schneller Cron / Fast cron | 21. verzögert / 21st delayed |
| 11 Nutzerbefehle/Min | Spam / Spam | Fehlermeldung an Nutzer / Error to user |
| Cooldown-Überschreitung | Wiederholter Aufruf / Repeated call | Blockiert bis Cooldown abgelaufen / Blocked until cooldown |

#### 12.7.3 Ausführungslimit-Tests

| Testfall / Test Case | Grenze / Limit | Erwartetes Verhalten / Expected Behavior |
|---------------------|---------------|------------------------------------------|
| Plugin läuft > 300s | Timeout | Plugin abgebrochen / Plugin terminated |
| 6 parallele Ausführungen | Parallel-Limit / Parallel limit | 6. in Queue / 6th queued |
| Timeout-Annäherung / Timeout approaching | 270s erreicht / 270s reached | Warnung in Logs / Warning in logs |

#### 12.7.4 Status-Übergang-Tests

| Von / From | Nach / To | Auslöser / Trigger | Erwartet / Expected |
|------------|-----------|-------------------|-------------------|
| `configured` | `valid` | Gültige Konfig / Valid config | ✅ Ja |
| `configured` | `invalid` | Ungültiger Cron / Invalid cron | ✅ Ja |
| `valid` | `active` | Plugin-Aktivierung / Plugin activation | ✅ Ja |
| `active` | `paused` | Admin-Stop / Admin stop | ✅ Ja |
| `paused` | `active` | Admin-Resume / Admin resume | ✅ Ja |
| `active` | `error` | Laufzeitfehler / Runtime error | ✅ Ja |

#### 12.7.5 Reservierte Funktionen / Reserved Functions

| Funktion / Function | MVP-Verhalten / MVP Behavior | Erwartet / Expected |
|--------------------|------------------------------|---------------------|
| `ki-triggered` Typ | Abgelehnt mit Fehler / Rejected with error | ✅ Ja |
| Konfiguration KI-Trigger | Ignoriert bei Discovery / Ignored at discovery | ✅ Ja |
| Mischbetrieb MVP + KI | Nur MVP-Trigger aktiv / Only MVP triggers active | ✅ Ja |

### 12.8 Integration mit anderen Blöcken / Integration with Other Blocks

#### 12.8.1 Block 5 (Aktivierung) / Block 5 (Activation)

Trigger werden bei der Plugin-Aktivierung validiert:

| Bedingung / Condition | Ergebnis / Result |
|-----------------------|-------------------|
| Alle Trigger gültig / All triggers valid | Aktivierung möglich / Activation allowed |
| Mindestens ein Trigger ungültig / At least one invalid | Blockiert, Status bleibt `activation_pending` / Blocked, stays `activation_pending` |
| Trigger-Konflikt / Trigger conflict | Blockiert mit Fehlermeldung / Blocked with error message |

#### 12.8.2 Block 6 (Settings) / Block 6 (Settings)

Trigger-Konfigurationen können Settings referenzieren:

```yaml
settings_schema:
  check_interval:
    type: number
    default: 300

triggers:
  - type: interval
    seconds: "{{settings.check_interval}}"  # Referenz auf Setting / Reference to setting
```

### 12.9 Zusammenfassung Block 7 / Block 7 Summary

| Aspekt / Aspect | Definition |
|-----------------|------------|
| Aktive Trigger-Typen / Active trigger types | `cron`, `interval`, `user-triggered` |
| Reserviert / Reserved | `ki-triggered` (Post-MVP) |
| Mindest-Intervall / Minimum interval | 60s (Prod), 10s (Dev) |
| Cron-Validierung / Cron validation | 5 Felder, Standard-Syntax / 5 fields, standard syntax |
| Flood-Protection / Flood protection | Global: 100/min, Pro-Plugin: 20/min, Pro-Nutzer: 10/min |
| Max Laufzeit / Max runtime | 300 Sekunden / 300 seconds |
| Max parallel / Max parallel | 5 pro Plugin / 5 per plugin |
| QA-Gate | Trigger-Validierung, Flood-Tests, Limit-Tests / Trigger validation, flood tests, limit tests |

---

## 13. Daten- & DB-Sicherheitsgrenzen (Block 8) / Data & DB Security Boundaries

### 13.1 Übersicht / Overview

Dieser Abschnitt definiert die Sicherheitsgrenzen für den Datenzugriff und die Datenbanknutzung durch Plugins. Das Ziel ist die strikte Trennung von Core-Daten und Plugin-Daten sowie die Verhinderung von Sicherheitsrisiken wie SQL-Injection.

This section defines security boundaries for data access and database usage by plugins. The goal is strict separation of core data and plugin data, as well as prevention of security risks such as SQL injection.

**Abhängigkeiten / Dependencies:** Block 1, Block 4

### 13.2 Grundprinzipien / Core Principles

#### 13.2.1 Core-DB ist read-only für Plugins / Core DB is Read-Only for Plugins

| Aspekt / Aspect | Regel / Rule |
|-----------------|--------------|
| Lesezugriff / Read access | ✅ Erlaubt via API / Allowed via API |
| Schreibzugriff / Write access | ❌ Verboten / Forbidden |
| Betroffene Tabellen / Affected tables | users, groups, messages, configuration |
| Konsequenz bei Verstoß / Consequence | Plugin wird abgelehnt / Plugin rejected |

- Plugins können Core-Datenbanken **nur lesen**, niemals schreiben
- Schreiboperationen auf Core-DBs werden **rigoros abgelehnt**
- Core-DB enthält: Benutzer, Gruppen, Nachrichten, Konfiguration

---

- Plugins can **only read** core databases, never write
- Write operations on core DBs are **rigorously rejected**
- Core DB contains: users, groups, messages, configuration

#### 13.2.2 Plugin-spezifische SQL getrennt von Core-DB / Plugin-Specific SQL Separated from Core DB

| Regel / Rule | Beschreibung / Description |
|--------------|---------------------------|
| Eigene Datenbank / Own database | Jedes Plugin erhält eigene Datenbank oder Schema / Each plugin receives own database or schema |
| Keine direkten Referenzen / No direct references | Plugin-Daten dürfen Core-Tabellen niemals direkt referenzieren / Plugin data must never directly reference core tables |
| Keine Fremdschlüssel / No foreign keys | Keine FK-Constraints von Plugin zu Core / No FK constraints from plugin to core |
| Datenkonsistenz / Data consistency | Über API-Calls, nicht DB-Constraints / Via API calls, not DB constraints |

#### 13.2.3 Externe DB-Bindung mit separaten DB-Benutzern/Rollen / External DB Binding with Separate DB Users/Roles

| Aspekt / Aspect | Anforderung / Requirement |
|-----------------|---------------------------|
| Dedizierte DB-Benutzer / Dedicated DB users | Pro Plugin separater DB-User / Separate DB user per plugin |
| Least-Privilege / Least-privilege | Datenbankrollen mit minimalen Rechten / Database roles with minimal rights |
| Keine Admin-Rechte / No admin rights | Keine Superuser-Rechte für Plugins / No superuser rights for plugins |
| Credentials / Credentials | Nie im Plugin-Code, nur über sichere Konfiguration / Never in plugin code, only via secure config |

#### 13.2.4 Parameterisierte Queries zwingend erforderlich / Parameterized Queries Mandatory

| Erlaubt / Allowed | Verboten / Forbidden |
|-------------------|---------------------|
| `SELECT * FROM users WHERE id = ?` | `SELECT * FROM users WHERE id = ${userId}` |
| Prepared Statements | String-Interpolation / String interpolation |
| Query-Parameter / Query parameters | Dynamische SQL-Generierung / Dynamic SQL generation |

**Beispiele / Examples:**

```sql
-- ✅ KORREKT / CORRECT: Parametrisierte Query / Parameterized query
SELECT * FROM users WHERE id = ? AND group_id = ?

-- ❌ VERBOTEN / FORBIDDEN: String-Interpolation / String interpolation
SELECT * FROM users WHERE id = ${userId} AND group_id = ${groupId}
```

#### 13.2.5 SQL-Injection-unsichere Pfade werden abgelehnt / SQL Injection Unsafe Paths Rejected

| Maßnahme / Measure | Beschreibung / Description |
|--------------------|---------------------------|
| Statische Analyse / Static analysis | Erkennt unsichere SQL-Muster / Detects unsafe SQL patterns |
| Validierungsablehnung / Validation rejection | Plugins mit unparametrisierten Queries werden abgelehnt / Plugins with non-parameterized queries rejected |
| Runtime-Überwachung / Runtime monitoring | Blockiert verdächtige Query-Muster / Blocks suspicious query patterns |
| Fehlermeldung / Error message | "Security violation: Non-parameterized query detected" |

#### 13.2.6 Least-Privilege für alle Datenbankoperationen / Least-Privilege for All Database Operations

| Rechte-Typ / Right Type | Plugin-DB-Benutzer / Plugin DB User |
|-------------------------|-------------------------------------|
| Eigene Tabellen / Own tables | SELECT, INSERT, UPDATE, DELETE |
| Eigene Schema-Operationen / Own schema ops | CREATE, DROP, ALTER (optional) |
| Systemtabellen / System tables | ❌ Kein Zugriff / No access |
| Andere Plugin-Schemas / Other plugin schemas | ❌ Kein Zugriff / No access |
| Core-DB / Core DB | SELECT nur auf explizit erlaubte Tabellen / SELECT only on explicitly allowed tables |

### 13.3 Verbotene Operationen / Forbidden Operations

| Operation | Grund / Reason | Konsequenz / Consequence |
|-----------|----------------|--------------------------|
| `INSERT/UPDATE/DELETE` auf Core-DB / on Core DB | Datenintegrität / Data integrity | Plugin wird abgelehnt / Plugin rejected |
| `DROP TABLE` ohne Berechtigung / without permission | Datenschutz / Data protection | Plugin wird abgelehnt / Plugin rejected |
| `SELECT * FROM pg_authid` (oder ähnlich) / (or similar) | Sicherheit / Security | Plugin wird abgelehnt / Plugin rejected |
| Dynamische SQL-Generierung / Dynamic SQL generation | Injection-Risiko / Injection risk | Plugin wird abgelehnt / Plugin rejected |
| Stored Procedures ohne Review / without review | Code-Injection / Code injection | Manuelle Prüfung erforderlich / Manual review required |

### 13.4 Sichere Datenbankarchitektur / Secure Database Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Core Database                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐     │
│  │   users     │  │   groups    │  │   messages      │     │
│  │   (RO)      │  │   (RO)      │  │   (RO)          │     │
│  └─────────────┘  └─────────────┘  └─────────────────┘     │
│                                                             │
│  Plugin-Zugriff: READ-ONLY via API oder eingeschränkter    │
│                  DB-User mit SELECT-Rechten nur auf          │
│                  explizit freigegebene Tabellen            │
│                                                             │
│  Plugin access: READ-ONLY via API or restricted             │
│                 DB user with SELECT rights only on          │
│                 explicitly approved tables                  │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼ (nur lesend / read-only)
┌─────────────────────────────────────────────────────────────┐
│                    Plugin Database(s)                       │
│  ┌─────────────────┐  ┌─────────────────┐                    │
│  │  plugin_data    │  │  plugin_cache    │                    │
│  │  (Read+Write)   │  │  (Read+Write)    │                    │
│  └─────────────────┘  └─────────────────┘                    │
│                                                             │
│  Dedizierter DB-User pro Plugin mit minimalen Rechten      │
│  Keine Referenzen zu Core-Tabellen (keine FK-Constraints)  │
│                                                             │
│  Dedicated DB user per plugin with minimal rights           │
│  No references to core tables (no FK constraints)          │
└─────────────────────────────────────────────────────────────┘
```

### 13.5 API-Endpunkte für Datenzugriff / API Endpoints for Data Access

#### 13.5.1 Sicherer Core-Datenzugriff (read-only) / Secure Core Data Access (Read-Only)

```
POST /api/v1/plugins/{id}/core-data/query
{
  "table": "users",
  "columns": ["id", "username"],
  "filters": {
    "group_id": "uuid"  // Wird als Parameter gebunden / Bound as parameter
  },
  "limit": 100
}

Response: {
  "rows": [...],
  "total_count": 50
}
```

**Regeln / Rules:**
- Nur erlaubte Core-Tabellen können abgefragt werden / Only allowed core tables can be queried
- `filters` werden immer parametrisiert / `filters` are always parameterized
- `limit` ist Pflicht (max. 1000) / `limit` is required (max. 1000)

#### 13.5.2 Plugin-Datenbankzugriff (mit Parametrisierung) / Plugin Database Access (With Parameterization)

```
POST /api/v1/plugins/{id}/db/query
{
  "query": "SELECT * FROM events WHERE user_id = ? AND timestamp > ?",
  "params": ["uuid", "2024-01-01T00:00:00Z"]
}

Response: {
  "rows": [...]
}
```

**Regeln / Rules:**
- Nur auf eigenes Plugin-Schema / Only on own plugin schema
- `params` sind Pflicht für alle Variablen / `params` required for all variables
- Keine String-Konkatenation in `query` / No string concatenation in `query`

#### 13.5.3 Unsichere Anfragen werden abgelehnt / Unsafe Requests Rejected

```
POST /api/v1/plugins/{id}/db/query
{
  "query": "SELECT * FROM users WHERE id = '" + userId + "'"  // ❌ VERBOTEN / FORBIDDEN
}

Response: {
  "error": "Security violation: Non-parameterized query detected",
  "status": "rejected"
}
```

### 13.6 QA-Verifizierungskriterien / QA Verification Criteria

#### 13.6.1 Testfälle für Datenbanksicherheit / Test Cases for Database Security

| Testfall / Test Case | Erwartetes Verhalten / Expected Behavior | Status |
|---------------------|------------------------------------------|--------|
| Plugin versucht Core-DB zu schreiben / Plugin attempts to write to core DB | ❌ Abgelehnt mit klarer Fehlermeldung / Rejected with clear error message | ⬜ |
| Plugin verwendet parametrisierte Queries / Plugin uses parameterized queries | ✅ Akzeptiert / Accepted | ⬜ |
| Plugin verwendet String-Interpolation / Plugin uses string interpolation | ❌ Abgelehnt bei Validierung / Rejected during validation | ⬜ |
| Plugin-DB-User hat nur Rechte auf eigenes Schema / Plugin DB user only has rights on own schema | ✅ Verifiziert / Verified | ⬜ |
| Keine Foreign Keys von Plugin- zu Core-Tabellen / No foreign keys from plugin to core tables | ✅ Schema-Check / Schema check | ⬜ |
| SQL-Injection-Versuche werden blockiert / SQL injection attempts are blocked | ✅ Security-Test / Security test | ⬜ |
| Plugin kann Core-Daten über API lesen / Plugin can read core data via API | ✅ Funktioniert / Works | ⬜ |
| Plugin kann eigene Daten lesen/schreiben / Plugin can read/write own data | ✅ Funktioniert / Works | ⬜ |

#### 13.6.2 Checkliste für Plugin-Validierung / Plugin Validation Checklist

- [ ] Alle Datenbankabfragen sind parametrisiert / All database queries are parameterized
- [ ] Keine direkten Core-DB-Schreiboperationen / No direct core DB write operations
- [ ] Eigene Plugin-DB/Schema vorhanden / Own plugin DB/schema exists
- [ ] DB-Benutzer hat minimal notwendige Rechte / DB user has minimal necessary rights
- [ ] Keine dynamischen SQL-Generierungen / No dynamic SQL generations
- [ ] Stored Procedures (falls vorhanden) geprüft / Stored procedures (if any) reviewed

### 13.7 Nicht-Ziele (Non-Goals) / Non-Goals

| Nicht-Ziel / Non-Goal | Begründung / Reason |
|----------------------|---------------------|
| Keine vollständige Datenbank-Migrationsverwaltung / No complete DB migration management | Außerhalb des MVP-Scopes / Outside MVP scope |
| Keine anbieterspezifische Datenbank-Tuning-Optimierung / No vendor-specific DB tuning optimization | Nicht MVP-relevant / Not MVP relevant |
| Keine automatische Schema-Evolution für Plugin-Datenbanken / No automatic schema evolution | Manuelle Migration erlaubt / Manual migration allowed |
| Keine Unterstützung für NoSQL-Datenbanken / No support for NoSQL databases | Zukünftiger Block / Future block |

### 13.8 Block-Zusammenfassung / Block Summary

| Aspekt / Aspect | Definition |
|-----------------|------------|
| Core-DB-Zugriff / Core DB access | READ-ONLY via API |
| Plugin-DB-Trennung / Plugin DB separation | Eigenes Schema pro Plugin / Own schema per plugin |
| DB-Benutzer / DB users | Dediziert, Least-Privilege / Dedicated, least-privilege |
| Query-Sicherheit / Query security | Parameterisierte Queries Pflicht / Parameterized queries mandatory |
| Injection-Schutz / Injection protection | Statische Analyse + Runtime-Überwachung / Static analysis + runtime monitoring |
| QA-Gate | 8 Testfälle + Validierungs-Checkliste / 8 test cases + validation checklist |

---

## 14. Dateisystem-Sandbox & Zugriffsbeschränkung (Block 9) / Filesystem Sandbox & Access Restrictions

### 14.1 Übersicht / Overview

Dieser Abschnitt definiert den verbindlichen Dateisystem-Contract für Plugins. Ziel ist, Dateizugriffe strikt auf freigegebene Plugin-Bereiche zu begrenzen, Missbrauch zu verhindern und Verstöße auditierbar zu machen.

This section defines the binding filesystem contract for plugins. The goal is to strictly limit file access to approved plugin areas, prevent abuse, and make violations auditable.

**Abhängigkeiten / Dependencies:** Block 5 (Aktivierungs-/Rechtemodell), Block 8 (Daten-/DB-Sicherheitsgrenzen)

### 14.2 Sicherheitsprinzip: Default-Deny + Whitelist

| Prinzip / Principle | Regel / Rule |
|---------------------|--------------|
| Default Deny | Jeder Dateizugriff ist standardmäßig verboten, außer explizit freigegeben |
| Whitelist | Freigaben erfolgen nur über feste, kanonische Basis-Pfade |
| Pfad-Kanonisierung | Vor jeder Prüfung: `realpath`/kanonischer Pfad, danach Prefix-Match gegen erlaubte Roots |
| Symlink-Schutz | Symlinks dürfen nicht aus erlaubten Roots herausführen |
| Kontextbindung | Zugriff wird immer gegen `plugin_id` + Scope + Operation geprüft |

### 14.3 Erlaubte Pfade und Operationen / Allowed Paths and Operations

#### 14.3.1 Erlaubte Basis-Pfade pro Plugin

| Bereich / Area | Beispielpfad / Example Path | Zugriff / Access |
|----------------|-----------------------------|------------------|
| Plugin-Code (read-only) | `plugins/{plugin_id}/` | `read`, `list` |
| Plugin-Daten (persistiert) | `data/plugins/{plugin_id}/` | `read`, `list`, `write`, `create`, `delete` |
| Plugin-Temp | `tmp/plugins/{plugin_id}/` | `read`, `list`, `write`, `create`, `delete` |

**Regeln / Rules:**
- `plugin_id` muss exakt zur laufenden Plugin-Identität passen.
- Pfadzugriff außerhalb dieser drei Roots ist verboten.
- Schreibzugriff auf `plugins/{plugin_id}/` ist im MVP verboten (Code-Integrität).

#### 14.3.2 Erlaubte Operationen (MVP)

| Operation | `plugins/{id}` | `data/plugins/{id}` | `tmp/plugins/{id}` |
|----------|-----------------|---------------------|--------------------|
| `read` | ✅ | ✅ | ✅ |
| `list` | ✅ | ✅ | ✅ |
| `create` | ❌ | ✅ | ✅ |
| `write/overwrite` | ❌ | ✅ | ✅ |
| `delete` | ❌ | ✅ | ✅ |
| `rename/move` | ❌ | ✅ (nur innerhalb gleicher Root) | ✅ (nur innerhalb gleicher Root) |
| `chmod/chown/exec` | ❌ | ❌ | ❌ |

### 14.4 Verbotene Zugriffe / Forbidden Access

#### 14.4.1 Verbotene Pfadbereiche

| Verboten / Forbidden | Beispiele |
|----------------------|-----------|
| Core-Code & Core-Konfig | `src/`, `config/`, `docs/`, `venv/` |
| Secrets & Credentials | `.env`, `secrets/`, `*.pem`, `*.key`, Token-Dateien |
| System-/Host-Bereiche | `/etc`, `/proc`, `/sys`, `/var/lib`, Home anderer Nutzer |
| Andere Plugin-Bereiche | `plugins/{other_plugin_id}/`, `data/plugins/{other_plugin_id}/` |

#### 14.4.2 Verbotene Zugriffsmuster

| Muster / Pattern | Verhalten / Behavior |
|------------------|----------------------|
| Path Traversal (`../`) | Blockieren + Audit-Event |
| Symlink-Escape | Blockieren + Audit-Event |
| Absolutpfad außerhalb Whitelist | Blockieren + Audit-Event |
| Wildcard-Massenzugriffe außerhalb Root | Blockieren + Audit-Event |

### 14.5 Temporäre Dateien, Quotas und Cleanup

#### 14.5.1 Temp-Nutzung

| Regel | Wert |
|------|------|
| Temp-Root | `tmp/plugins/{plugin_id}/` |
| Max Dateigröße (MVP Default) | 10 MB pro Datei |
| Max Temp-Quota pro Plugin | 100 MB |
| Max Anzahl Temp-Dateien | 1.000 |

#### 14.5.2 Persistente Plugin-Daten (`data/plugins/{plugin_id}`)

| Regel | Wert |
|------|------|
| Max Dateigröße (MVP Default) | 50 MB pro Datei |
| Max Persistenz-Quota pro Plugin | 500 MB |
| Überschreitung | Schreibvorgang abweisen (`QUOTA_EXCEEDED`) |

#### 14.5.3 Cleanup-Regeln

| Bereich | Cleanup-Policy |
|---------|----------------|
| Temp-Dateien | TTL-basiert, Standard: 24h |
| Stale Temp-Dateien | Bei Plugin-Start + periodisch (z. B. stündlich) löschen |
| Persistente Daten | Kein automatisches Löschen ohne explizite Plugin-/Admin-Aktion |

**Nicht-Ziel:** Keine komplexe Lifecycle-Orchestrierung im MVP; deterministischer Basis-Cleanup reicht.

### 14.6 Fehlercodes und API/Runtime-Verhalten bei Verstößen

| Fehlercode | Bedeutung | HTTP/Runtime-Verhalten |
|------------|-----------|------------------------|
| `FS_ACCESS_DENIED` | Pfad/Operation nicht erlaubt | Request ablehnen, keine Dateiveränderung |
| `FS_PATH_TRAVERSAL` | Traversal-Muster erkannt | Request ablehnen, Security-Event loggen |
| `FS_SYMLINK_ESCAPE` | Symlink verlässt erlaubten Root | Request ablehnen, Security-Event loggen |
| `FS_QUOTA_EXCEEDED` | Quota-/Size-Grenze überschritten | Request ablehnen, Hinweis auf aktuelle Nutzung |
| `FS_INVALID_OPERATION` | Operation im Kontext unzulässig | Request ablehnen |

**Anforderung:** Fail-closed – bei unklarer Bewertung immer ablehnen.

### 14.7 Audit- und Logging-Verhalten

#### 14.7.1 Pflichtfelder pro Verstoß-Event

| Feld | Beschreibung |
|------|--------------|
| `event_type` | z. B. `plugin.fs.denied`, `plugin.fs.quota_exceeded` |
| `plugin_id` | Betroffenes Plugin |
| `scope` | Group/Topic falls vorhanden |
| `actor` | System/Benutzerkontext |
| `operation` | `read/write/delete/...` |
| `requested_path` | Angefragter Pfad (raw) |
| `resolved_path` | Kanonisierter Pfad |
| `reason_code` | Einer der Fehlercodes aus 14.6 |
| `timestamp` | UTC Zeitstempel |
| `request_id` | Korrelations-ID |

#### 14.7.2 Logging-Regeln

- Verstöße mindestens als `WARNING`, sicherheitskritische Muster (`traversal`, `symlink escape`) als `ERROR`.
- Keine Secret-Inhalte oder Dateiinhalte in Logs/Audit.
- Optionales Rate-Limiting für identische Events erlaubt, aber erste N Ereignisse müssen vollständig auditierbar bleiben.

### 14.8 QA-Verifizierung (Block-9-Gate)

| Testfall | Erwartetes Verhalten | Status |
|----------|---------------------|--------|
| Zugriff auf `data/plugins/{id}/file.txt` | ✅ erlaubt | ⬜ |
| Schreibversuch in `plugins/{id}/main.py` | ❌ `FS_ACCESS_DENIED` | ⬜ |
| Zugriff auf `.env` | ❌ `FS_ACCESS_DENIED` + Audit | ⬜ |
| Path Traversal `../../.env` | ❌ `FS_PATH_TRAVERSAL` + `ERROR` Audit | ⬜ |
| Symlink in erlaubtem Root zeigt nach `/etc/passwd` | ❌ `FS_SYMLINK_ESCAPE` + Audit | ⬜ |
| Temp-Datei >10MB | ❌ `FS_QUOTA_EXCEEDED` | ⬜ |
| Temp-Quota >100MB | ❌ `FS_QUOTA_EXCEEDED` | ⬜ |
| Violation-Event enthält Pflichtfelder | ✅ vollständig auditierbar | ⬜ |

### 14.9 Nicht-Ziele (Block 9)

| Nicht-Ziel | Begründung |
|-----------|------------|
| Vollständige OS-Sandboxing-Isolation (Namespaces/Container pro Plugin) | Außerhalb MVP |
| Plattformweiter Security-Refactor | Nicht Bestandteil dieses Blocks |
| Forensik-/SIEM-Vollintegration | Post-MVP (siehe Observability Block 12) |

### 14.10 Block-Zusammenfassung / Block Summary

| Aspekt | Definition |
|--------|------------|
| Zugriffsprinzip | Default-Deny + Whitelist |
| Erlaubte Roots | `plugins/{id}` (RO), `data/plugins/{id}`, `tmp/plugins/{id}` |
| Kritische Verbote | Core-Dateien, Secrets, andere Plugins, Systempfade |
| Temp-Regeln | 10MB/File, 100MB/Plugin, TTL 24h |
| Persistenz-Regeln | 50MB/File, 500MB/Plugin |
| Verstöße | Blockieren + standardisierte Fehlercodes + Audit |
| QA-Gate | Pfadblockierung + Auditnachweis |

---

## 15. Observability & Audit-Trail (Block 12) / Observability & Audit Trail

### 15.1 Übersicht / Overview

Dieser Abschnitt definiert den verbindlichen Audit- und Run-Log-Contract für Aktivierung, Deaktivierung, Settings-Änderungen, Policy-Verweigerungen und Trigger-Ausführungen.

This section defines the binding audit and run-log contract for activation, deactivation, settings changes, policy denials, and trigger executions.

**Abhängigkeiten / Dependencies:** Block 5 (Aktivierungsflow), Block 6 (Settings), Block 7 (Trigger), Block 11 (WebUI Detail)

### 15.2 Pflicht-Audit-Events / Mandatory Audit Events

#### 15.2.1 Event-Typen (MUSS) / Event Types (MUST)

| Event-Typ / Event Type | Muss bei / Must be emitted on |
|------------------------|-------------------------------|
| `plugin.activation` | erfolgreicher Aktivierung pro Scope / successful activation per scope |
| `plugin.deactivation` | erfolgreicher Deaktivierung pro Scope / successful deactivation per scope |
| `plugin.settings_updated` | jeder persistierten Settings-Änderung / each persisted settings update |
| `plugin.policy_denied` | jeder abgelehnten Policy-/RBAC-Entscheidung / each denied policy/RBAC decision |
| `plugin.trigger_run` | jeder Trigger-Ausführung (Erfolg/Fehler) / each trigger execution (success/failure) |

#### 15.2.2 Audit-Event-Basisschema / Audit Event Base Schema

| Feld / Field | Typ / Type | Pflicht / Required | Beschreibung / Description |
|--------------|-----------|--------------------|----------------------------|
| `event_id` | String (UUID) | ✅ | Eindeutige Event-ID / unique event id |
| `event_type` | String | ✅ | Typ aus 15.2.1 |
| `plugin_id` | String | ✅ | Plugin-ID |
| `scope` | Object | ✅ | `{scope_type, group_id?, topic_id?}` |
| `actor` | Object | ✅ | `{actor_type: user|system, actor_id}` |
| `timestamp` | RFC3339 UTC | ✅ | Ereigniszeit |
| `request_id` | String | ◇ | HTTP/API Request-ID (falls vorhanden) |
| `correlation_id` | String | ◇ | Korrelations-ID für zusammenhängende Events |
| `result` | String | ✅ | `success` / `failure` / `denied` |
| `error_code` | String | ◇ | Nur bei Fehler/denied |
| `error_message` | String | ◇ | Gekürzt/sanitized, keine Secrets |
| `details` | Object | ◇ | Ereignisspezifische, sanitizte Metadaten |

**Hinweis / Note:** `request_id` und `correlation_id` sind verpflichtend, wenn der auslösende Kanal/API diese IDs bereitstellt.

### 15.3 Minimales Run-Log-Schema / Minimal Run Log Schema

#### 15.3.1 Run-Log-Record (MUSS) / Run Log Record (MUST)

| Feld / Field | Typ / Type | Pflicht / Required | Beschreibung / Description |
|--------------|-----------|--------------------|----------------------------|
| `run_id` | String (UUID) | ✅ | Eindeutige Lauf-ID / unique run id |
| `plugin_id` | String | ✅ | Plugin-ID |
| `scope` | Object | ✅ | `{scope_type, group_id?, topic_id?}` |
| `trigger_type` | Enum | ✅ | `cron` \| `interval` \| `user-triggered` |
| `trigger_id` | String | ✅ | Eindeutige Trigger-Referenz im Plugin |
| `status` | Enum | ✅ | `started` \| `completed` \| `failed` \| `timeout` \| `cancelled` |
| `result` | Enum | ✅ | `success` \| `failure` |
| `started_at` | RFC3339 UTC | ✅ | Startzeit |
| `finished_at` | RFC3339 UTC | ◇ | Endzeit (bei Abschluss/Fehler) |
| `duration_ms` | Integer | ◇ | Laufzeit in Millisekunden |
| `error_code` | String | ◇ | Standardisierter Fehlercode |
| `error_message` | String | ◇ | Sanitized, max 512 Zeichen |
| `request_id` | String | ◇ | Request-ID (falls vorhanden) |
| `correlation_id` | String | ◇ | Korrelations-ID |

#### 15.3.2 Feldgrenzen / Field Boundaries

- `error_message` max. 512 Zeichen; darüber abschneiden und mit `…<truncated>` markieren.
- Keine Payload-Dumps in `error_message` oder `details`.
- `duration_ms` wird aus `finished_at - started_at` abgeleitet und muss ≥ 0 sein.

### 15.4 Logging- und Redaction-Grenzen / Logging and Redaction Boundaries

#### 15.4.1 Verbotene Inhalte (MUSS NIE) / Forbidden Content (MUST NEVER)

- Secrets im Klartext (API-Keys, Tokens, Passwörter, private Schlüssel)
- Unmaskierte sensitive Konfigurationswerte
- Datei-Inhalte oder private Nutzlasten (z. B. Message-Body-Dumps, Attachments)
- Vollständige Request/Response-Dumps mit potentiell sensitiven Daten

#### 15.4.2 Erlaubte Maskierung / Allowed Masking

| Datentyp | Regel |
|----------|-------|
| `secret` Settings | Nur Marker wie `<SECRET_CHANGED>` oder `***MASKED***` |
| IDs/Tokens in Freitext | Teilmaskierung, z. B. `tok_****abcd` |
| Fehlertexte | Sanitized + gekürzt; keine Rohpayload |

#### 15.4.3 Stack-Traces

- Stack-Traces dürfen referenziert werden, aber nur **Admin/Owner** sichtbar.
- Für nicht privilegierte Rollen nur Fehlercode + kurze sanitized Message.
- Exporte für Group-Admin enthalten keine owner-only Stack-Trace-Details außerhalb des eigenen Scopes.

### 15.5 Audit-Ansicht, Export und RBAC-Sichtbarkeit / Audit View, Export, and RBAC Visibility

#### 15.5.1 Sichtbarkeitsregeln / Visibility Rules

| Rolle | Audit/Run-Log Sicht |
|-------|----------------------|
| `owner` | Global: alle Scopes, alle Plugins |
| `admin` (Group-Admin) | Nur eigene Gruppen/Topics |
| `vip` | Kein Zugriff |
| `normal` | Kein Zugriff |

#### 15.5.2 Export-Anforderungen (MVP-Basis)

- Exportformate: `jsonl` (Pflicht), `csv` (optional).
- Export muss Scope-Filter respektieren (RBAC serverseitig erzwingen).
- Export enthält denselben Redaction-Stand wie UI/API (keine unmaskierten Secrets).
- Export-Metadaten: `exported_at`, `exported_by`, `scope_filter`, `record_count`.

### 15.6 QA-Verifizierung (Block-12-Gate) / QA Verification (Block 12 Gate)

| Testfall | Erwartetes Verhalten | Status |
|----------|----------------------|--------|
| Aktivierung erzeugt Audit-Event | `plugin.activation` mit `plugin_id`, `scope`, `actor`, `timestamp`, `result=success` | ⬜ |
| Deaktivierung erzeugt Audit-Event | `plugin.deactivation` vollständig | ⬜ |
| Settings-Update auditierbar | `plugin.settings_updated` mit `changed_fields`, ohne Secret-Werte | ⬜ |
| Policy-Denial auditierbar | `plugin.policy_denied` mit `error_code` + Scope + Actor | ⬜ |
| Trigger-Run Erfolg | `plugin.trigger_run` + Run-Log `result=success`, Zeitfelder gesetzt | ⬜ |
| Trigger-Run Fehler | `plugin.trigger_run` + Run-Log `result=failure`, `error_code` gesetzt | ⬜ |
| Secret-Leak-Stichprobe | Keine Klartext-Secrets in Audit, Run-Log, Export | ⬜ |
| RBAC Owner/Admin | Owner global sichtbar, Group-Admin nur eigene Scopes | ⬜ |
| RBAC VIP/Normal | Zugriff auf Audit/Run-Logs verweigert (403/hidden) | ⬜ |
| Export-Redaction | Export enthält keine privaten Payload- oder Secret-Dumps | ⬜ |

### 15.7 Nicht-Ziele (Block 12) / Non-Goals (Block 12)

- Kein SIEM-/SOC-Integrationsdesign.
- Keine Langzeit-Analytics-/BI-Spezifikation.
- Keine Alerting-Policy-Engine über Basisfehler hinaus.

---

*Dokument Version / Document Version: 1.7.0*
