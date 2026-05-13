# WebUI Plugin-Hauptseite (Übersicht) / WebUI Plugin Overview Page

## Block 10: Post-MVP – Plugin-Übersicht / Block 10: Post-MVP – Plugin Overview

**Last Updated:** 2026-05-13

---

## Deutsch

### Übersicht

Die Plugin-Hauptseite im WebUI bietet eine zentrale Übersicht über alle installierten und verfügbaren Plugins. Diese Seite ist das primäre Interface für Admins und Owner zur Verwaltung von Plugins.

### Liste der angezeigten Felder

Jedes Plugin in der Übersicht zeigt folgende Informationen:

| Feld | Beschreibung | Beispiel |
|------|-------------|----------|
| **Name** | Anzeigename des Plugins | "Wetter-Plugin" |
| **Version** | Semantische Version des Plugins | "1.2.3" |
| **Status** | Aktueller Plugin-Status | "aktiv", "ausstehend", "deaktiviert", "Fehler" |
| **Scope** | Geltungsbereich des Plugins | "global", "Gruppe: XYZ", "Topic: ABC" |
| **Error** | Fehlermeldung (nur bei Status "error") | "Verbindung fehlgeschlagen" |

### Status-Anzeige

Die Status-Anzeige verwendet Farbcodes und Icons:

| Status | Farbe | Icon | Sichtbarkeit |
|--------|-------|------|--------------|
| `active` | 🟢 Grün | ✓ | Alle berechtigten Benutzer |
| `activation_pending` | 🟡 Gelb | ⏳ | Nur Admin/Owner |
| `registered` | ⚪ Grau | ⏸ | Nur Admin/Owner |
| `disabled` | ⚫ Dunkelgrau | ✗ | Nur Admin/Owner |
| `error` | 🔴 Rot | ⚠ | Nur Admin/Owner + Fehlertext |

**Hinweis:** Konsistent mit Block 3 – Nicht aktive Plugins sind nur für Admin/Owner sichtbar.

### Filter-Optionen

Benutzer können die Plugin-Liste filtern nach:

#### Status-Filter

| Filter | Beschreibung |
|--------|-------------|
| Alle | Zeigt alle Plugins (rollenabhängig) |
| Aktiv | Nur `active` Plugins |
| Ausstehend | Nur `activation_pending` Plugins |
| Deaktiviert | Nur `disabled` Plugins |
| Mit Fehler | Nur `error` Plugins |

#### Quellen-Filter (Source)

| Filter | Beschreibung |
|--------|-------------|
| Alle Quellen | Plugins aus allen Quellen |
| Builtin | Integrierte Plugins |
| Extern | Extern installierte Plugins |
| Marketplace | Aus dem Marketplace installiert |

### Sortier-Optionen

Die Liste kann sortiert werden nach:

| Sortierung | Richtung |
|------------|----------|
| Name (A-Z) | Aufsteigend |
| Name (Z-A) | Absteigend |
| Version (neueste zuerst) | Absteigend |
| Version (älteste zuerst) | Aufsteigend |
| Status | Nach Status-Reihenfolge |
| Installationsdatum (neueste zuerst) | Absteigend |
| Letzte Aktivität | Absteigend |

### Rollenabhängige Aktionen

Die verfügbaren Aktionen hängen von der Benutzerrolle ab:

#### Für Owner

| Aktion | Beschreibung | Verfügbar bei Status |
|--------|-------------|-------------------|
| Details anzeigen | Öffnet Plugin-Detailseite | Alle |
| Aktivieren | Setzt Status auf `active` | `activation_pending`, `disabled` |
| Deaktivieren | Setzt Status auf `disabled` | `active` |
| Konfigurieren | Öffnet Konfigurationsdialog | `active`, `disabled` |
| Löschen | Entfernt Plugin permanent | `registered`, `disabled`, `error` |
| Berechtigungen | Öffnet Rechte-Management | Alle |

#### Für Group-Admin

| Aktion | Beschreibung | Verfügbar bei Status |
|--------|-------------|-------------------|
| Details anzeigen | Öffnet Plugin-Detailseite | Alle (eigene Gruppe) |
| Aktivieren | Setzt Status auf `active` | `activation_pending` (eigene Gruppe) |
| Deaktivieren | Setzt Status auf `disabled` | `active` (eigene Gruppe) |
| Konfigurieren | Öffnet Konfigurationsdialog | `active`, `disabled` (eigene Gruppe) |

#### Für VIP / Normal

| Aktion | Beschreibung | Verfügbar bei Status |
|--------|-------------|-------------------|
| Details anzeigen | Öffnet Plugin-Detailseite (nur lesend) | `active` (sichtbare Plugins) |
| Verwenden | Plugin-Funktionalität nutzen | `active` (sichtbare Plugins) |

**Hinweis:** Konsistent mit Block 4 – Rechte-Modell. Effektive Berechtigung = MAX(plugin_minimum, admin_setting).

### Basis-Aktionen

Jede Plugin-Zeile bietet folgende Basis-Aktionen:

```
┌─────────────────────────────────────────────────────────────┐
│ 🌤️ Wetter-Plugin    v1.2.3    🟢 aktiv    global    [Details]│
└─────────────────────────────────────────────────────────────┘
```

**Details-Button:** Öffnet die Plugin-Detailseite mit umfassenden Informationen:
- Vollständige Metadaten
- Konfigurationsoptionen
- Nutzungsstatistiken
- Fehlerhistorie (bei `error`)
- Berechtigungseinstellungen

**Schnell-Aktionen (Hover/Mehr-Optionen):**
- Aktivieren / Deaktivieren (je nach aktuellem Status)
- Konfigurieren
- Duplizieren (für Template-Nutzung)

### Leer-Zustände

#### Keine Plugins installiert

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│              📦 Keine Plugins installiert                   │
│                                                             │
│   Installieren Sie Ihr erstes Plugin aus dem Marketplace    │
│   oder laden Sie ein eigenes Plugin hoch.                   │
│                                                             │
│              [Marketplace öffnen]  [Plugin hochladen]       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### Filter ergibt keine Treffer

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│              🔍 Keine Plugins gefunden                      │
│                                                             │
│   Ihre aktuellen Filtereinstellungen zeigen keine Plugins.  │
│   Passen Sie die Filter an oder setzen Sie sie zurück.      │
│                                                             │
│              [Filter zurücksetzen]                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### Keine Berechtigung

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│              🔒 Zugriff eingeschränkt                       │
│                                                             │
│   Sie haben keine Berechtigung, Plugins zu sehen.           │
│   Kontaktieren Sie einen Administrator.                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Fehler-Zustände

#### Plugin-Fehler (Status `error`)

```
┌─────────────────────────────────────────────────────────────┐
│ ⚠️ Wetter-Plugin    v1.2.3    🔴 Fehler    global             │
│    └─ Fehler: Verbindung zu API fehlgeschlagen (Timeout)    │
│       [Details anzeigen]  [Erneut versuchen]  [Deaktivieren] │
└─────────────────────────────────────────────────────────────┘
```

**Anzeige bei `error`:**
- Rotes Warn-Icon
- Kurze Fehlerzusammenfassung
- Tooltip mit vollständigem `error_text`
- Aktions-Buttons für Fehlerbehebung

**Verfügbare Fehler-Aktionen:**
- Details anzeigen (mit vollständigem Stack-Trace)
- Erneut versuchen (Trigger Re-Validation)
- Deaktivieren (Setzt auf `disabled`)
- Protokoll anzeigen (Log-Einträge)

#### System-Fehler

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│              ⚠️ Fehler beim Laden der Plugins               │
│                                                             │
│   Die Plugin-Liste konnte nicht geladen werden.             │
│   Fehler: {error_message}                                   │
│                                                             │
│              [Erneut versuchen]  [Support kontaktieren]     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Konsistenz mit Backend-Status

Die WebUI-Status entsprechen 1:1 den Backend-Status aus Block 3:

| Backend-Status | WebUI-Anzeige | Farbe |
|----------------|---------------|-------|
| `discovered` | "Entdeckt" | 🔵 Blau |
| `validated` | "Validiert" | 🟣 Lila |
| `registered` | "Registriert" | ⚪ Grau |
| `activation_pending` | "Ausstehend" | 🟡 Gelb |
| `active` | "Aktiv" | 🟢 Grün |
| `disabled` | "Deaktiviert" | ⚫ Dunkelgrau |
| `error` | "Fehler" | 🔴 Rot |

**Block 5 Aktivierungs-Flow:**
- Button "Aktivieren" nur bei `activation_pending` oder `disabled`
- Nach Aktivierung: Status-Wechsel zu `active`
- Sofortige Aktualisierung der Liste

### API-Integration

**Liste laden:**
```
GET /api/v1/plugins?status=&source=&sort=name&order=asc
Headers: Authorization: Bearer {token}

Response: {
  "plugins": [
    {
      "id": "uuid",
      "name": "Wetter-Plugin",
      "version": "1.2.3",
      "status": "active",
      "scope": "global",
      "error_text": null,
      "source": "marketplace",
      "installed_at": "2024-01-15T10:30:00Z",
      "last_activity": "2024-01-20T14:22:00Z"
    }
  ],
  "total": 5,
  "filters_applied": { ... }
}
```

**Status-Übergänge:**
```
POST /api/v1/plugins/{id}/activate
POST /api/v1/plugins/{id}/deactivate
POST /api/v1/plugins/{id}/retry  // Bei error
```

---

## English

### Overview

The Plugin Overview page in the WebUI provides a central view of all installed and available plugins. This page is the primary interface for Admins and Owners to manage plugins.

### List of Displayed Fields

Each plugin in the overview displays the following information:

| Field | Description | Example |
|-------|-------------|---------|
| **Name** | Display name of the plugin | "Weather Plugin" |
| **Version** | Semantic version of the plugin | "1.2.3" |
| **Status** | Current plugin status | "active", "pending", "disabled", "error" |
| **Scope** | Scope of the plugin | "global", "Group: XYZ", "Topic: ABC" |
| **Error** | Error message (only when status is "error") | "Connection failed" |

### Status Display

The status display uses color codes and icons:

| Status | Color | Icon | Visibility |
|--------|-------|------|------------|
| `active` | 🟢 Green | ✓ | All authorized users |
| `activation_pending` | 🟡 Yellow | ⏳ | Admin/Owner only |
| `registered` | ⚪ Gray | ⏸ | Admin/Owner only |
| `disabled` | ⚫ Dark Gray | ✗ | Admin/Owner only |
| `error` | 🔴 Red | ⚠ | Admin/Owner + error text |

**Note:** Consistent with Block 3 – Non-active plugins are only visible to Admin/Owner.

### Filter Options

Users can filter the plugin list by:

#### Status Filter

| Filter | Description |
|--------|-------------|
| All | Shows all plugins (role-dependent) |
| Active | Only `active` plugins |
| Pending | Only `activation_pending` plugins |
| Disabled | Only `disabled` plugins |
| With Error | Only `error` plugins |

#### Source Filter

| Filter | Description |
|--------|-------------|
| All Sources | Plugins from all sources |
| Builtin | Built-in plugins |
| External | Externally installed plugins |
| Marketplace | Installed from marketplace |

### Sorting Options

The list can be sorted by:

| Sort | Direction |
|------|-----------|
| Name (A-Z) | Ascending |
| Name (Z-A) | Descending |
| Version (newest first) | Descending |
| Version (oldest first) | Ascending |
| Status | By status order |
| Installation date (newest first) | Descending |
| Last activity | Descending |

### Role-Dependent Actions

Available actions depend on the user role:

#### For Owner

| Action | Description | Available for Status |
|--------|-------------|---------------------|
| View Details | Opens plugin detail page | All |
| Activate | Sets status to `active` | `activation_pending`, `disabled` |
| Deactivate | Sets status to `disabled` | `active` |
| Configure | Opens configuration dialog | `active`, `disabled` |
| Delete | Permanently removes plugin | `registered`, `disabled`, `error` |
| Permissions | Opens permission management | All |

#### For Group-Admin

| Action | Description | Available for Status |
|--------|-------------|---------------------|
| View Details | Opens plugin detail page | All (own group) |
| Activate | Sets status to `active` | `activation_pending` (own group) |
| Deactivate | Sets status to `disabled` | `active` (own group) |
| Configure | Opens configuration dialog | `active`, `disabled` (own group) |

#### For VIP / Normal

| Action | Description | Available for Status |
|--------|-------------|---------------------|
| View Details | Opens plugin detail page (read-only) | `active` (visible plugins) |
| Use | Use plugin functionality | `active` (visible plugins) |

**Note:** Consistent with Block 4 – Rights Model. Effective permission = MAX(plugin_minimum, admin_setting).

### Base Actions

Each plugin row provides the following base actions:

```
┌─────────────────────────────────────────────────────────────┐
│ 🌤️ Weather Plugin    v1.2.3    🟢 active    global [Details]│
└─────────────────────────────────────────────────────────────┘
```

**Details Button:** Opens the plugin detail page with comprehensive information:
- Complete metadata
- Configuration options
- Usage statistics
- Error history (for `error`)
- Permission settings

**Quick Actions (Hover/More Options):**
- Activate / Deactivate (depending on current status)
- Configure
- Duplicate (for template use)

### Empty States

#### No Plugins Installed

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│              📦 No Plugins Installed                        │
│                                                             │
│   Install your first plugin from the Marketplace          │
│   or upload your own plugin.                                │
│                                                             │
│              [Open Marketplace]  [Upload Plugin]          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### Filter Returns No Results

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│              🔍 No Plugins Found                          │
│                                                             │
│   Your current filter settings show no plugins.             │
│   Adjust filters or reset them.                           │
│                                                             │
│              [Reset Filters]                              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### No Permission

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│              🔒 Access Restricted                           │
│                                                             │
│   You do not have permission to view plugins.               │
│   Contact an administrator.                               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Error States

#### Plugin Error (Status `error`)

```
┌─────────────────────────────────────────────────────────────┐
│ ⚠️ Weather Plugin    v1.2.3    🔴 Error    global             │
│    └─ Error: API connection failed (Timeout)              │
│       [View Details]  [Retry]  [Deactivate]               │
└─────────────────────────────────────────────────────────────┘
```

**Display for `error`:**
- Red warning icon
- Short error summary
- Tooltip with full `error_text`
- Action buttons for error resolution

**Available Error Actions:**
- View Details (with full stack trace)
- Retry (Trigger Re-Validation)
- Deactivate (Sets to `disabled`)
- View Log (Log entries)

#### System Error

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│              ⚠️ Error Loading Plugins                       │
│                                                             │
│   The plugin list could not be loaded.                    │
│   Error: {error_message}                                    │
│                                                             │
│              [Retry]  [Contact Support]                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Consistency with Backend States

WebUI statuses correspond 1:1 with backend statuses from Block 3:

| Backend Status | WebUI Display | Color |
|----------------|---------------|-------|
| `discovered` | "Discovered" | 🔵 Blue |
| `validated` | "Validated" | 🟣 Purple |
| `registered` | "Registered" | ⚪ Gray |
| `activation_pending` | "Pending" | 🟡 Yellow |
| `active` | "Active" | 🟢 Green |
| `disabled` | "Disabled" | ⚫ Dark Gray |
| `error` | "Error" | 🔴 Red |

**Block 5 Activation Flow:**
- "Activate" button only for `activation_pending` or `disabled`
- After activation: Status change to `active`
- Immediate list refresh

### API Integration

**Load List:**
```
GET /api/v1/plugins?status=&source=&sort=name&order=asc
Headers: Authorization: Bearer {token}

Response: {
  "plugins": [
    {
      "id": "uuid",
      "name": "Weather Plugin",
      "version": "1.2.3",
      "status": "active",
      "scope": "global",
      "error_text": null,
      "source": "marketplace",
      "installed_at": "2024-01-15T10:30:00Z",
      "last_activity": "2024-01-20T14:22:00Z"
    }
  ],
  "total": 5,
  "filters_applied": { ... }
}
```

**Status Transitions:**
```
POST /api/v1/plugins/{id}/activate
POST /api/v1/plugins/{id}/deactivate
POST /api/v1/plugins/{id}/retry  // For error
```

---

## QA-Testbare Akzeptanzkriterien / QA-Testable Acceptance Criteria

### Deutsch

| ID | Kriterium | Test-Methode |
|----|-----------|--------------|
| QA-10.1 | Liste zeigt alle 5 Felder (Name, Version, Status, Scope, Error) | Visuelle Inspektion |
| QA-10.2 | Farbcodierung der Status ist korrekt | Visuelle Inspektion |
| QA-10.3 | Filter nach Status funktioniert | Funktionaler Test |
| QA-10.4 | Filter nach Quelle funktioniert | Funktionaler Test |
| QA-10.5 | Sortierung funktioniert für alle Optionen | Funktionaler Test |
| QA-10.6 | Nur Admin/Owner sieht nicht-aktive Plugins | Rollen-Test |
| QA-10.7 | Aktivieren-Button nur bei `activation_pending`/`disabled` | Zustands-Test |
| QA-10.8 | Deaktivieren-Button nur bei `active` | Zustands-Test |
| QA-10.9 | Fehler-Zustand zeigt `error_text` korrekt an | Fehler-Test |
| QA-10.10 | Leer-Zustand wird korrekt angezeigt | Zustands-Test |
| QA-10.11 | Details-Button öffnet Detailseite | Funktionaler Test |
| QA-10.12 | Aktionen sind rollenabhängig korrekt eingeschränkt | RBAC-Test |
| QA-10.13 | Konsistenz mit Block 3 Status-Modell | Dokumenten-Review |
| QA-10.14 | Konsistenz mit Block 4 Rechte-Modell | Dokumenten-Review |
| QA-10.15 | Konsistenz mit Block 5 Aktivierungs-Flow | Dokumenten-Review |

### English

| ID | Criterion | Test Method |
|----|-----------|-------------|
| QA-10.1 | List displays all 5 fields (Name, Version, Status, Scope, Error) | Visual inspection |
| QA-10.2 | Status color coding is correct | Visual inspection |
| QA-10.3 | Filter by status works | Functional test |
| QA-10.4 | Filter by source works | Functional test |
| QA-10.5 | Sorting works for all options | Functional test |
| QA-10.6 | Only Admin/Owner sees non-active plugins | Role test |
| QA-10.7 | Activate button only for `activation_pending`/`disabled` | State test |
| QA-10.8 | Deactivate button only for `active` | State test |
| QA-10.9 | Error state displays `error_text` correctly | Error test |
| QA-10.10 | Empty state displays correctly | State test |
| QA-10.11 | Details button opens detail page | Functional test |
| QA-10.12 | Actions are correctly restricted by role | RBAC test |
| QA-10.13 | Consistency with Block 3 status model | Document review |
| QA-10.14 | Consistency with Block 4 rights model | Document review |
| QA-10.15 | Consistency with Block 5 activation flow | Document review |

---

## Zusammenfassung / Summary

Dieses Dokument beschreibt die WebUI Plugin-Hauptseite (Übersicht) für Block 10 (Post-MVP). Es deckt alle wichtigen Aspekte ab:

- **Liste der Felder:** Name, Version, Status, Scope, Error
- **Filter:** Status-Filter, Quellen-Filter
- **Sortierung:** Name, Version, Status, Datum, Aktivität
- **Rollenbasierte Aktionen:** Owner, Group-Admin, VIP/Normal
- **Leer-Zustände:** Keine Plugins, Keine Treffer, Keine Berechtigung
- **Fehler-Zustände:** Plugin-Fehler, System-Fehler
- **Konsistenz:** Block 3 (Status), Block 4 (Rechte), Block 5 (Aktivierung)

---

**Dokument-Version:** 1.0.0  
**Gilt für:** Block 10 – WebUI Plugin-Hauptseite (Übersicht)  
**Konsistent mit:** PLUGIN_CONTRACT.md v1.2.0
