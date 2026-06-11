# Userplugin Development Guide / Entwicklungsanleitung für Userplugins

> **Language Policy / Sprachpolitik:**
> This document is **bilingual (DE/EN)**. Normative sections are provided in both languages.
> Manifest field names, runtime signatures, and canonical terms remain **identical** across languages.
> When updating this document, maintain parity between DE and EN sections.
> See synchronization note at end of document.

> **DE:** Anleitung für Entwickler, die eigene Userplugins für AMO schreiben möchten.
> **EN:** Guide for developers who want to write custom userplugins for AMO.

**Zielgruppe / Target Audience:**
- Menschliche Entwickler, die Userplugins erstellen / Human developers building userplugins
- KI-Agenten/Subagenten, die Userplugins aus dieser Anleitung generieren / AI agents/subagents generating userplugins from this guide

**Version:** 2026.05.23
**Erforderliche AMO-Version / Required AMO Version:** 2026.05.22 or later

---

## 📋 Quick Reference / Kurzübersicht (Language-Agnostic)

The following are **canonical technical terms** used identically in all language versions:

| Concept | Manifest Field | Runtime Signature | Notes |
|---------|---------------|-------------------|-------|
| Plugin name | `name` | `plugin_id` in context | Lowercase, alphanumeric + `_` `-`, 3-50 chars |
| Version | `version` | — | Any format |
| Commands | `commands` | `command_name` in context | Array of strings |
| Schedule | `schedule` | `trigger_type: "schedule"` | `interval_seconds` or `cron` |
| Worker | `worker` | — | `restart_backoff_seconds` |
| Required roles | `required_roles` | `role` in context | `ignore`, `normal`, `vip`, `admin`, `owner` |
| Required permissions | `required_permissions` | Runtime validation | `rss.fetch`, `send_message` |
| Settings schema | `settings_schema` | — | Types: `text`, `number`, `bool`, `select`, `secret` |
| Entry point | — | `handle_command()` or `handle_schedule()` | Async function |
| Host API | — | `host_api` parameter | `send_message()`, `reply()`, `send_photo()`, `send_document()` |

---

## 🇩🇪 Deutsch

### Inhaltsverzeichnis

1. [Übersicht](#übersicht-de)
2. [Dateistruktur](#dateistruktur-de)
3. [Manifest-Spezifikation](#manifest-spezifikation-de)
4. [Dos und Don'ts](#dos-und-donts-de)
5. [Minimales funktionierendes Beispiel](#minimales-funktionierendes-beispiel-de)
6. [Host-API-Referenz](#host-api-referenz-de)
7. [Sicherheitsregeln](#sicherheitsregeln-de)
8. [UI/Konfigurations-Integration](#uikonfigurations-integration-de)
9. [Testanforderungen](#testanforderungen-de)
10. [KI-spezifische Richtlinien](#ki-spezifische-richtlinien-de)

---

### Übersicht {#übersicht-de}

**Was ist ein Userplugin?**

Ein Userplugin erweitert die Funktionalität von AMO, ohne den Core-Code zu verändern. Es läuft in einer sandboxed Umgebung mit explizit deklarierten Fähigkeiten.

**Verantwortlichkeiten: Core vs. Userplugin**

| Aspekt | Core (AMO) | Userplugin |
|--------|------------|------------|
| Telegram-API-Zugriff | ✅ Direkt | ❌ Nur via host_api |
| Datenbankzugriff | ✅ Direkt | ❌ Kein direkter Zugriff |
| HTTP-Anfragen | ✅ Direkt | ❌ Via Berechtigungen (rss.fetch) |
| Dateisystem | ✅ Direkt | ❌ Kein direkter Zugriff |
| Secrets-Management | ✅ Nur Core | ❌ Nie Secrets behandeln |
| Logging | ✅ Strukturiert | ✅ Nur via logging-Modul |

---

### Dateistruktur {#dateistruktur-de}

Ein gültiges Userplugin benötigt genau diese Dateien:

```
my_plugin/
├── plugin.yaml          # ERFORDERLICH: Manifest (YAML oder JSON)
└── main.py              # ERFORDERLICH: Implementierung (muss main.py heißen)
```

**Unterstützte Manifest-Formate:** `plugin.yaml`, `plugin.yml`, `plugin.json`, oder `manifest.json`

**Optionale Dateien:**

```
my_plugin/
├── plugin.yaml
├── main.py
└── README.md            # OPTIONAL: Plugin-Dokumentation
```

**Plugin-Verzeichnis-Standort:**
- Konfiguriert via `AMO_PLUGIN_DIR` in `.env` (Standard: `./plugins`)
- Jedes Plugin liegt in seinem eigenen Unterverzeichnis

---

### Manifest-Spezifikation {#manifest-spezifikation-de}

Das Manifest ist **verpflichtend** und muss gültiges YAML oder JSON sein.

#### Erforderliche Felder

```yaml
name: my_plugin
version: "1.0.0"
commands:
  - mycommand
required_roles:
  - normal
required_permissions:
  - send_message
```

ODER mit Schedule:

```yaml
name: my_plugin
version: "1.0.0"
schedule:
  interval_seconds: 60
required_roles:
  - normal
required_permissions: []
```

ODER mit Worker:

```yaml
name: my_plugin
version: "1.0.0"
worker:
  restart_backoff_seconds: 60
required_roles:
  - normal
required_permissions:
  - send_message
```

#### Feld-Referenz

| Feld | Typ | Erforderlich | Beschreibung |
|------|-----|--------------|--------------|
| `name` | string | ✅ | Eindeutiger Plugin-Name: Kleinbuchstaben, alphanumerisch + Unterstrich/Bindestrich, 3-50 Zeichen. Nicht reserviert: `core`, `system`, `internal`, `builtin` |
| `version` | string | ✅ | Plugin-Versionsstring (beliebiges Format) |
| `description` | string | ❌ | Kurze Beschreibung (Standard: leerer String) |
| `commands` | array | ❌* | Liste von Befehlsnamen (z.B. `["rss", "weather"]`). Erforderlich, wenn kein schedule oder worker |
| `schedule` | object | ❌* | Schedule-Konfig mit `interval_seconds` (≥10) ODER `cron` (5-Feld Cron-Ausdruck). Erforderlich, wenn keine commands oder worker |
| `worker` | object | ❌* | Worker-Konfig mit `restart_backoff_seconds` (Standard: 60). Erforderlich, wenn keine commands oder schedule |
| `required_roles` | array | ✅* | Liste erlaubter Rollen: `ignore`, `normal`, `vip`, `admin`, `owner`. Mindestens `normal` empfohlen. Bedingt: Mindestens eines von `required_roles` oder `required_permissions` muss definiert sein; wenn nicht vorhanden, standardmäßig `[]`. |
| `required_permissions` | array | ✅* | Liste erforderlicher Capability-Berechtigungen. Bedingt: Mindestens eines von `required_roles` oder `required_permissions` muss definiert sein; wenn nicht vorhanden, standardmäßig `[]`. |
| `settings_schema` | object | ❌ | Schema für WebUI-Konfigurationseinstellungen |

*Hinweis: Mindestens eine von `commands`, `schedule`, oder `worker` muss definiert sein.*

*Hinweis: Mindestens eines der Felder `required_roles` oder `required_permissions` muss definiert sein. Jedes Feld wird zu einem leeren Array `[]` standardmäßig, wenn es nicht vorhanden ist, aber ein Manifest ohne beide Felder ist ungültig.*

#### Berechtigungsdeklaration

**Regel:** Deklariere NUR Berechtigungen, die du tatsächlich im Plugin-Code verwendest.

```yaml
required_permissions:
  - rss.fetch
  - send_message
```

**Verfügbare Berechtigungen:**

| Berechtigung | Zweck |
|--------------|-------|
| `rss.fetch` | RSS-Feeds abrufen und parsen |
| `send_message` | Telegram-Nachrichten senden/antworten; auch erforderlich für `send_photo`/`send_document` in Sandbox |

**Hinweis:** Die Runtime validiert, dass Plugins mit RSS-bezogenen Einstellungen die `rss.fetch`-Berechtigung deklariert haben. Siehe `service.py` `_uses_rss_behavior()` für Details.

**Sandbox-only Operationen:** `send_photo` und `send_document` sind nur in der Sandbox-Command-Host-API verfügbar und werden durch die `send_message`-Berechtigung geregelt. Sie sind nicht in der Non-Sandbox-Runtime verfügbar.

**NICHT** Berechtigungen "für alle Fälle" deklarieren. Nicht deklarierte Berechtigungsaufrufe schlagen zur Laufzeit fehl.

---

### Dos und Don'ts {#dos-und-donts-de}

#### ✅ DO (Empfohlen)

- ✅ **Async/await** für alle Host-API-Aufrufe verwenden
- ✅ Alle Eingaben vor der Verarbeitung validieren
- ✅ Timeouts und Netzwerkfehler elegant behandeln
- ✅ Strukturiertes Logging via `logging`-Modul verwenden
- ✅ Exakte Berechtigungen in `required_permissions` deklarieren
- ✅ Zuerst mit minimalen Berechtigungen testen
- ✅ Klare Fehlermeldungen an User zurückgeben
- ✅ Einstellungen in `settings_schema` dokumentieren
- ✅ Temporäre Ressourcen in `finally`-Blöcken aufräumen
- ✅ Type Hints im Python-Code verwenden

#### ❌ DON'T (Verboten)

- ❌ **Nie** Secrets, Token oder User-Daten loggen
- ❌ **Nie** private Artefakte ins Repo committen
- ❌ **Nie** Sandbox via direktem Netzwerk/FS-Zugriff umgehen
- ❌ **Nie** relative Pfade wie `./data` oder `../config` verwenden
- ❌ **Nie** Berechtigungen deklarieren, die du nicht nutzt
- ❌ **Nie** Secrets im Plugin-Code oder Config speichern
- ❌ **Nie** blockierende Aufrufe ohne Timeouts machen
- ❌ **Nie** auf Dateien außerhalb des Plugin-Verzeichnisses zugreifen
- ❌ **Nie** User-provided Code/Strings ausführen
- ❌ **Nie** annehmen, dass das Plugin-Verzeichnis beschreibbar ist

---

### Minimales funktionierendes Beispiel {#minimales-funktionierendes-beispiel-de}

#### `plugin.yaml`

```yaml
name: rss_notifier
version: "1.0.0"
description: Überwacht RSS-Feeds und sendet neue Einträge an Telegram
commands:
  - rss
required_roles:
  - normal
  - vip
  - admin
  - owner
required_permissions:
  - rss.fetch
  - send_message
settings_schema:
  feed_url:
    type: text
    required: true
    description: Zu überwachende RSS-Feed-URL
  check_interval_minutes:
    type: number
    required: false
    default: 60
    min: 5
    max: 1440
    description: Überprüfungsintervall in Minuten
```

#### `main.py`

```python
"""
RSS Notifier Plugin für AMO Telegram Bot.

Dies ist ein minimales funktionierendes Beispiel eines Userplugins, das:
1. Befehlsaufrufe vom Core empfängt
2. Die Host-API zum Senden von Nachrichten nutzt
3. Attachments für Bildanalyse verwendet
"""

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


async def handle_command(context: Dict[str, Any], host_api: Any) -> None:
    """
    Haupt-Einstiegspunkt für Befehlsausführung.

    Args:
        context: Befehlskontext mit Schlüsseln:
            - plugin_id: Plugin-Name
            - run_id: Eindeutige Ausführungs-ID
            - chat_id: Telegram Chat-ID
            - message_id: Nachrichten-ID
            - message_thread_id: Thread-ID (optional)
            - user_id: Telegram User-ID
            - role: User-Rolle (ignore/normal/vip/admin/owner)
            - command_name: Aufgerufener Befehl
            - argument: Befehlsargument-Text
            - attachments: Liste von Attachment-Dicts
            - reply_to_image: Bildkontext für Analyse
        host_api: API-Objekt für Interaktion mit Telegram

    Raises:
        Jede Ausnahme wird von der Runtime abgefangen und geloggt.
    """
    command = context.get("command_name", "")
    argument = context.get("argument", "")
    chat_id = context.get("chat_id")
    message_id = context.get("message_id")

    if command == "rss":
        if not argument:
            await host_api.reply(chat_id, message_id, "Verwendung: /rss <feed_url>")
            return
        await host_api.reply(chat_id, message_id, f"Wird überprüft: {argument}")
    else:
        await host_api.reply(chat_id, message_id, "Unbekannter Befehl")
```

#### Schedule-Plugin Beispiel

Für Plugins mit `schedule` statt `commands`:

**`plugin.yaml`**

```yaml
name: daily_report
version: "1.0.0"
description: Sendet tägliche Zusammenfassung
schedule:
  interval_seconds: 86400
required_roles:
  - admin
required_permissions:
  - send_message
```

**`main.py`**

```python
"""
Daily Report Plugin für AMO Telegram Bot.

Ein Schedule-Plugin, das regelmäßig ausgeführt wird.
"""

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


async def handle_schedule(context: Dict[str, Any], host_api: Any) -> None:
    """
    Haupt-Einstiegspunkt für Schedule-Ausführung.

    Args:
        context: Schedule-Kontext mit Schlüsseln:
            - plugin_id: Plugin-Name
            - run_id: Eindeutige Ausführungs-ID
            - trigger_type: "schedule"
            - scheduled_at: ISO-Format Zeitstempel
        host_api: API-Objekt für Interaktion mit Telegram
            (Sandbox-Runtime: verwendet ops-Liste für Sendeoperationen)

    Raises:
        Jede Ausnahme wird von der Runtime abgefangen und geloggt.
    """
    logger.info(f"Schedule ausgeführt: {context.get('run_id')}")
    # In Sandbox-Runtime werden Sendeoperationen über die ops-Liste zurückgegeben
    # Beispiel: return {"ops": [{"op": "send_message", "chat_id": 123, "text": "Report"}]}
```

**Hinweis:** Schedule-Plugins laufen in der Sandbox-Runtime. Sendeoperationen werden
als ops-Liste im Rückgabewert übergeben, nicht direkt via host_api aufgerufen.

#### Host-API-Methoden

Das `host_api`-Objekt bietet diese async-Methoden:

**`await host_api.send_message(chat_id: int, text: str)`**
- Sendet eine Nachricht an den angegebenen Chat
- Benötigt `send_message`-Berechtigung
- Lange Texte werden automatisch in mehrere Nachrichten (≤4000 Zeichen) aufgeteilt
- Wirft `PluginCapabilityError`, wenn Berechtigung nicht gewährt

**`await host_api.reply(chat_id: int, message_id: int, text: str)`**
- Antwortet auf eine spezifische Nachricht
- Benötigt `send_message`-Berechtigung
- Lange Texte werden automatisch in mehrere Nachrichten (≤4000 Zeichen) aufgeteilt; nur der erste Chunk antwortet auf die Originalnachricht

**`await host_api.send_photo(chat_id: int, file_path: str, caption: str = "", *, message_thread_id: int | None = None, mime_type: str | None = None)`** (Nur Sandbox)
- Sendet ein Foto an den angegebenen Chat
- Benötigt `send_message`-Berechtigung (geregelt durch `send_message`)
- Nur in Sandbox-Command-Runtime verfügbar

**`await host_api.send_document(chat_id: int, file_path: str, caption: str = "", *, message_thread_id: int | None = None, mime_type: str | None = None)`** (Nur Sandbox)
- Sendet ein Dokument an den angegebenen Chat
- Benötigt `send_message`-Berechtigung (geregelt durch `send_message`)
- Nur in Sandbox-Command-Runtime verfügbar

**Context-Attachments** (`context["attachments"]`):
Liste von Attachment-Dicts mit Schlüsseln:
- `source_kind`: Quelltyp
- `type_hint`: "image" oder "image_document"
- `file_id`: Telegram file ID
- `file_unique_id`: Eindeutiger Datei-Identifier
- `width`, `height`: Bildabmessungen
- `size`: Dateigröße in Bytes
- `mime_type`: MIME-Typ
- `media_ref`: Heruntergeladene Media-Referenz (falls verfügbar)

**Kontext für Bildanalyse** (`context["reply_to_image"]`):
Dict mit Bildkontext beim Antworten auf ein Bild, mit Schlüsseln:
- `ok`: Boolean, ob Bild gültig ist
- `media_ref`: Media-Referenz für Analyse
- `reason_code`: Fehlercode, falls nicht gültig

**Hinweis:** Plugins laufen in einem sandboxed Subprozess. Operationen sind limitiert und überwacht.

---

### Sicherheitsregeln {#sicherheitsregeln-de}

#### 1. Secret-Handling

**ABSOLUT VERBOTEN:**

```python
# ❌ NIE MACHEN
token = "my-secret-token"  # Hardcoded
logger.info(f"Token: {user_token}")  # Secrets loggen
return {"error": f"Fehlgeschlagen mit Token {api_key}"}  # Secrets zurückgeben
```

**Korrekter Ansatz:**

Secrets werden via `settings_schema` mit `type: secret` konfiguriert. Der Core handhabt Secret-Speicherung und -Validierung. Plugins sehen nie die tatsächlichen Secret-Werte.

#### 2. Private Artefakte

**NICHT commiten:**
- `.env` Dateien
- `__pycache__/` Verzeichnisse
- `.pyc` Dateien
- Testdaten mit echten User-Infos
- Debug-Logs
- IDE-Konfigurationsdateien

**Immer in `.gitignore` aufnehmen:**

```gitignore
# Plugin-spezifisch
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/
.env
*.log
```

#### 3. Netzwerkzugriff

**VERBOTEN:** Direkter Netzwerkzugriff

```python
# ❌ NIE MACHEN
import requests
response = requests.get("https://api.example.com")  # BYPASST SANDBOX
```

**Hinweis:** RSS/Netzwerk-Operationen müssen von der Runtime gehandhabt werden. Plugins mit RSS-bezogenen Einstellungen müssen `rss.fetch` in `required_permissions` deklarieren.

#### 4. Dateisystemzugriff

**VERBOTEN:** Direkter Dateisystemzugriff außerhalb Plugin-Dir

```python
# ❌ NIE MACHEN
open("/etc/passwd")  # Absolute Escape
open("../../config.json")  # Relativer Escape
open(os.environ["HOME"] + "/.secrets")  # Home-Directory-Escape
```

**ERLAUBT:** Nur Plugin-Verzeichnis (read-only)

```python
# ✅ KORREKT: Nur aus Plugin-Verzeichnis lesen
import os
plugin_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(plugin_dir, "data.json")
```

---

### UI/Konfigurations-Integration {#uikonfigurations-integration-de}

#### Settings Schema

Das `settings_schema`-Feld in `plugin.yaml` definiert WebUI-Konfigurationsoptionen.

**Unterstützte Einstellungstypen:** `text`, `number`, `bool`, `select`, `secret`

**YAML-Format:**

```yaml
settings_schema:
  enabled:
    type: bool
    required: false
    default: true
    description: Dieses Plugin aktivieren
  api_key:
    type: secret
    required: false
    description: API-Key (sicher vom Core gespeichert)
  interval:
    type: number
    required: false
    default: 5
    min: 1
    max: 60
    description: Überprüfungsintervall in Minuten
  mode:
    type: select
    required: false
    default: basic
    options:
      - basic
      - advanced
      - expert
    description: Plugin-Modus
  webhook_url:
    type: text
    required: false
    pattern: "^https?://.*"
    description: Webhook-URL (muss HTTP/HTTPS sein)
```

**Einstellungstyp-Constraints:**
- `number`: `default`, `min`, `max` müssen numerisch sein; `min` muss ≤ `max` sein
- `bool`: `default` muss true/false sein
- `select`: `options` müssen nicht-leere Strings sein; `default` muss in options sein
- `text`/`secret`: `default` muss String sein; `pattern` muss gültiger Regex sein

#### UI-Verhalten

- Einstellungen werden in WebUI bearbeitet und vom Core persistiert
- Plugin empfängt Einstellungen via `settings_schema`-Validierung
- Schema-Validierung passiert, bevor das Plugin die Daten sieht
- Ungültige Einstellungen erreichen das Plugin nie
- Plugins können nicht in ihr Verzeichnis schreiben; aller State muss via Host-API verwaltet werden

#### Was gehört in UI-Config

| Gehört in UI | Gehört NICHT in UI |
|--------------|-------------------|
| Feature-Toggles | Secrets (verwende secret type in settings_schema) |
| Numerische Schwellenwerte | Dateipfade |
| Kanal-Listen | Rohe Credentials |
| Boolean-Flags | Interner State |
| Text-Templates | Abgeleitete/berechnete Werte |

**Einstellungen werden in `settings_schema` mit Typen definiert:** `text`, `number`, `bool`, `select`, `secret`

---

### Testanforderungen {#testanforderungen-de}

#### Minimale Testabdeckung

Jedes Userplugin MUSS haben:

1. **Manifest-Validierungs-Test**
   ```python
   def test_manifest_valid_yaml():
       import yaml
       with open("plugin.yaml") as f:
           manifest = yaml.safe_load(f)
       assert "name" in manifest
       assert "version" in manifest
       assert "required_permissions" in manifest or "required_roles" in manifest
   ```

2. **Plugin lädt ohne Fehler**
   ```python
   def test_plugin_imports():
       # main.py muss handle_command definieren
       import importlib.util
       spec = importlib.util.spec_from_file_location("main", "main.py")
       module = importlib.util.module_from_spec(spec)
       spec.loader.exec_module(module)
       assert callable(getattr(module, "handle_command", None))
   ```

3. **Keine direkten Netzwerkaufrufe**
   ```python
   def test_no_direct_requests():
       import ast
       with open("main.py") as f:
           tree = ast.parse(f.read())
       for node in ast.walk(tree):
           if isinstance(node, ast.Import):
               for alias in node.names:
                   assert alias.name != "requests"
   ```

4. **Keine Secrets im Code**
   ```python
   def test_no_hardcoded_secrets():
       import re
       with open("main.py") as f:
           code = f.read()
       # Auf gängige Secret-Patterns prüfen
       assert not re.search(r'[a-zA-Z0-9]{32,}', code)  # API-Keys
   ```

#### QA-Checkliste vor Submission

- [ ] Plugin lädt ohne Fehler in AMO
- [ ] Alle deklarierten Berechtigungen werden tatsächlich verwendet
- [ ] Es werden keine nicht-deklarierten Berechtigungen verwendet
- [ ] Settings-Schema validiert korrekt
- [ ] Umgang mit fehlenden/ungültigen Einstellungen
- [ ] Keine Secrets im Code oder Logs
- [ ] Kein direkter Netzwerk/FS-Zugriff
- [ ] Nur absolute Pfade verwendet
- [ ] Ordentliches Error-Handling
- [ ] Tests bestehen mit `pytest -q`

---

### KI-spezifische Richtlinien {#ki-spezifische-richtlinien-de}

**Für KI-Agenten, die Userplugins generieren:**

#### Nur absolute Pfade

```python
# ❌ NIE
data_file = "./data/items.json"
config = "../config.json"

# ✅ IMMER
import os
plugin_dir = os.path.dirname(os.path.abspath(__file__))
data_file = os.path.join(plugin_dir, "data", "items.json")
```

#### Keine OpenClaw-Artefakte

**ENTFERNEN vor dem Generieren:**
- Referenzen zu OpenClaw-Pfaden (z.B. `/home/claw/.openclaw/`)
- Sitzungsspezifische Daten
- Interne Tooling-Referenzen
- Debug-Prints aus der Generierung

#### Kein Scope Creep

**Bleibe bei der Anfrage:**
- Wenn nach RSS-Plugin gefragt, füge keine Weather-API hinzu
- Wenn nach 3 Befehlen gefragt, füge nicht 10 hinzu
- Minimale viable Implementierung zuerst

#### Keine öffentlichen Aktionen ohne Genehmigung

**VERBOTEN:**
- In Repository pushen
- Öffentliche Issues erstellen
- In soziale Medien posten
- Jede externe Netzwerkaktion außerhalb von Capability-Aufrufen

**ERFORDERLICH:**
- Generierten Code zum Review an User zurückgeben
- User über Submission/Deployment entscheiden lassen
- Nie auto-commit oder auto-push

#### Code-Generierungs-Template

Wenn ein Plugin generiert werden soll, verwende diese Struktur:

**plugin.yaml:**

```yaml
name: my_plugin
version: "1.0.0"
description: Kurze Beschreibung, was dieses Plugin tut
commands:
  - mycommand
required_roles:
  - normal
required_permissions:
  - send_message
```

**main.py:**

```python
"""
[Plugin-Name] für AMO Telegram Bot.

Generiert: [Datum]
Zweck: [Kurzbeschreibung]
Erforderliche Berechtigungen: [Liste deklarierter Berechtigungen]
"""

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


async def handle_command(context: Dict[str, Any], host_api: Any) -> None:
    """Haupt-Einstiegspunkt für Befehlsausführung."""
    command = context.get("command_name", "")
    argument = context.get("argument", "")
    chat_id = context.get("chat_id")
    message_id = context.get("message_id")

    # Befehlslogik hier
    await host_api.reply(chat_id, message_id, "Hallo von meinem Plugin!")
```

---

### Schnell-Referenz-Karte (DE)

```
MANIFEST (plugin.yaml)
├── name: Kleinbuchstaben, alphanumerisch+Unterstrich/Bindestrich, 3-50 Zeichen
├── version: Beliebiges Format
├── commands: [] ← Befehlsnamen
├── schedule: {interval_seconds: N} oder {cron: "..."}
├── worker: {restart_backoff_seconds: N}
├── required_roles: ["normal", "vip", "admin", "owner"]
└── required_permissions: ["rss.fetch", "send_message"]

CODE (main.py)
├── async def handle_command(context, host_api)
├── context: dict mit chat_id, message_id, command_name, argument, attachments, etc.
├── host_api.send_message(chat_id, text)
└── host_api.reply(chat_id, message_id, text)

SETTINGS_SCHEMA
├── text: String mit optionalem Pattern-Regex
├── number: Integer mit min/max
├── bool: true/false
├── select: Liste von Optionen
└── secret: String (sicher gespeichert)

SICHERHEIT
├── KEINE Secrets im Code/Logs
├── KEIN direktes Netzwerk (verwende Capabilities)
├── KEIN Filesystem-Escape
└── KEINE öffentlichen Aktionen ohne Genehmigung
```

---

## 🇬🇧 English

### Table of Contents

1. [Quick Overview](#quick-overview-en)
2. [File Structure](#file-structure-en)
3. [Manifest Specification](#manifest-specification-en)
4. [Do's and Don'ts](#dos-and-donts-en)
5. [Minimal Working Example](#minimal-working-example-en)
6. [Host API Reference](#host-api-reference-en)
7. [Security Rules](#security-rules-en)
8. [UI/Config Integration](#uiconfig-integration-en)
9. [Testing Requirements](#testing-requirements-en)
10. [AI-Specific Guidelines](#ai-specific-guidelines-en)

---

### Quick Overview {#quick-overview-en}

**What is a Userplugin?**

A userplugin extends AMO's functionality without modifying core code. It runs in a sandboxed environment with explicitly declared permissions.

**Core vs. Userplugin Responsibility:**

| Aspect | Core (AMO) | Userplugin |
|--------|------------|------------|
| Telegram API access | ✅ Direct | ❌ Via host_api only |
| Database access | ✅ Direct | ❌ No direct access |
| HTTP requests | ✅ Direct | ❌ Via permissions (rss.fetch) |
| File system | ✅ Direct | ❌ No direct access |
| Secrets management | ✅ Core only | ❌ Never handle secrets |
| Logging | ✅ Structured | ✅ Via logging module only |

---

### File Structure {#file-structure-en}

A valid userplugin requires exactly these files:

```
my_plugin/
├── plugin.yaml          # REQUIRED: Manifest (YAML or JSON)
└── main.py              # REQUIRED: Implementation (must be named main.py)
```

**Supported manifest formats:** `plugin.yaml`, `plugin.yml`, `plugin.json`, or `manifest.json`

**Optional files:**

```
my_plugin/
├── plugin.yaml
├── main.py
└── README.md            # OPTIONAL: Plugin documentation
```

**Plugin directory location:**
- Set via `AMO_PLUGIN_DIR` in `.env` (default: `./plugins`)
- Each plugin lives in its own subdirectory

---

### Manifest Specification {#manifest-specification-en}

The manifest is **mandatory** and must be valid YAML or JSON.

#### Required Fields

```yaml
name: my_plugin
version: "1.0.0"
commands:
  - mycommand
required_roles:
  - normal
required_permissions:
  - send_message
```

OR with schedule:

```yaml
name: my_plugin
version: "1.0.0"
schedule:
  interval_seconds: 60
required_roles:
  - normal
required_permissions: []
```

OR with worker:

```yaml
name: my_plugin
version: "1.0.0"
worker:
  restart_backoff_seconds: 60
required_roles:
  - normal
required_permissions:
  - send_message
```

#### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Unique plugin name: lowercase, alphanumeric + underscore/hyphen, 3-50 chars. Cannot be reserved: `core`, `system`, `internal`, `builtin` |
| `version` | string | ✅ | Plugin version string (any format) |
| `description` | string | ❌ | Short description (defaults to empty string) |
| `commands` | array | ❌* | List of command names (e.g., `["rss", "weather"]`). Required if no schedule or worker |
| `schedule` | object | ❌* | Schedule config with `interval_seconds` (≥10) OR `cron` (5-field cron expression). Required if no commands or worker |
| `worker` | object | ❌* | Worker config with `restart_backoff_seconds` (default: 60). Required if no commands or schedule |
| `required_roles` | array | ✅* | List of allowed roles: `ignore`, `normal`, `vip`, `admin`, `owner`. At least `normal` recommended. Conditional: at least one of `required_roles` or `required_permissions` must be defined; if absent, defaults to `[]`. |
| `required_permissions` | array | ✅* | List of required capability permissions. Conditional: at least one of `required_roles` or `required_permissions` must be defined; if absent, defaults to `[]`. |
| `settings_schema` | object | ❌ | Schema for WebUI configuration settings |

*Note: At least one of `commands`, `schedule`, or `worker` must be defined.*

*Note: At least one of `required_roles` or `required_permissions` must be defined. Each defaults to an empty array `[]` when absent, but a manifest with neither field is invalid.*

#### Permission Declaration

**Rule:** Declare ONLY permissions you actually use in your plugin code.

```yaml
required_permissions:
  - rss.fetch
  - send_message
```

**Available permissions:**

| Permission | Purpose |
|------------|---------|
| `rss.fetch` | Fetch and parse RSS feeds |
| `send_message` | Send/reply to Telegram messages; also required for `send_photo`/`send_document` in sandbox |

**Note:** The runtime validates that plugins using RSS-related settings have `rss.fetch` permission declared. See `service.py` `_uses_rss_behavior()` for details.

**Sandbox-only operations:** `send_photo` and `send_document` are available only in the sandbox command host API and are gated by the `send_message` permission. They are not available in the non-sandbox runtime.

**DO NOT** declare permissions "just in case." Undeclared permission calls will fail at runtime.

---

### Do's and Don'ts {#dos-and-donts-en}

#### ✅ DO

- ✅ Use **async/await** for all host API calls
- ✅ Validate all inputs before processing
- ✅ Handle timeouts and network failures gracefully
- ✅ Use structured logging via the `logging` module
- ✅ Declare exact permissions in `required_permissions`
- ✅ Test with minimal permissions first
- ✅ Return clear error messages to users
- ✅ Document settings in `settings_schema`
- ✅ Clean up temporary resources in `finally` blocks
- ✅ Use type hints in Python code

#### ❌ DON'T

- ❌ **Never** log secrets, tokens, or user data
- ❌ **Never** commit private artifacts to the repo
- ❌ **Never** bypass sandbox via direct network/FS access
- ❌ **Never** use relative paths like `./data` or `../config`
- ❌ **Never** declare permissions you don't use
- ❌ **Never** store secrets in plugin code or config
- ❌ **Never** make blocking calls without timeouts
- ❌ **Never** access files outside plugin directory
- ❌ **Never** execute user-provided code/strings
- ❌ **Never** assume the plugin directory is writeable

---

### Minimal Working Example {#minimal-working-example-en}

#### `plugin.yaml`

```yaml
name: rss_notifier
version: "1.0.0"
description: Monitors RSS feeds and sends new items to Telegram
commands:
  - rss
required_roles:
  - normal
  - vip
  - admin
  - owner
required_permissions:
  - rss.fetch
  - send_message
settings_schema:
  feed_url:
    type: text
    required: true
    description: RSS feed URL to monitor
  check_interval_minutes:
    type: number
    required: false
    default: 60
    min: 5
    max: 1440
    description: Check interval in minutes
```

#### `main.py`

```python
"""
RSS Notifier Plugin for AMO Telegram Bot.

This is a minimal working example of a userplugin that:
1. Receives command invocations from the core
2. Uses the host API to send messages
3. Uses attachments for image analysis
"""

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


async def handle_command(context: Dict[str, Any], host_api: Any) -> None:
    """
    Main entry point for command execution.

    Args:
        context: Command context with keys:
            - plugin_id: Plugin name
            - run_id: Unique execution ID
            - chat_id: Telegram chat ID
            - message_id: Message ID
            - message_thread_id: Thread ID (optional)
            - user_id: Telegram user ID
            - role: User role (ignore/normal/vip/admin/owner)
            - command_name: Command that was invoked
            - argument: Command argument text
            - attachments: List of attachment dicts
            - reply_to_image: Image context for analysis
        host_api: API object for interacting with Telegram

    Raises:
        Any exception is caught and logged by the runtime.
    """
    command = context.get("command_name", "")
    argument = context.get("argument", "")
    chat_id = context.get("chat_id")
    message_id = context.get("message_id")

    if command == "rss":
        if not argument:
            await host_api.reply(chat_id, message_id, "Usage: /rss <feed_url>")
            return
        await host_api.reply(chat_id, message_id, f"Will check: {argument}")
    else:
        await host_api.reply(chat_id, message_id, "Unknown command")
```

#### Schedule Plugin Example

For plugins with `schedule` instead of `commands`:

**`plugin.yaml`**

```yaml
name: daily_report
version: "1.0.0"
description: Sends daily summary
schedule:
  interval_seconds: 86400
required_roles:
  - admin
required_permissions:
  - send_message
```

**`main.py`**

```python
"""
Daily Report Plugin for AMO Telegram Bot.

A schedule plugin that runs periodically.
"""

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


async def handle_schedule(context: Dict[str, Any], host_api: Any) -> None:
    """
    Main entry point for schedule execution.

    Args:
        context: Schedule context with keys:
            - plugin_id: Plugin name
            - run_id: Unique execution ID
            - trigger_type: "schedule"
            - scheduled_at: ISO format timestamp
        host_api: API object for interacting with Telegram
            (Sandbox runtime: uses ops list for send operations)

    Raises:
        Any exception is caught and logged by the runtime.
    """
    logger.info(f"Schedule executed: {context.get('run_id')}")
    # In Sandbox runtime, send operations are returned as ops list
    # Example: return {"ops": [{"op": "send_message", "chat_id": 123, "text": "Report"}]}
```

**Note:** Schedule plugins run in the sandbox runtime. Send operations are
passed as an ops list in the return value, not called directly via host_api.

---

### Host API Reference {#host-api-reference-en}

#### Host API Methods

The `host_api` object provides these async methods:

**`await host_api.send_message(chat_id: int, text: str)`**
- Sends a message to the specified chat
- Requires `send_message` permission
- Long texts are automatically split into multiple messages (≤4000 chars each)
- Raises `PluginCapabilityError` if permission not granted

**`await host_api.reply(chat_id: int, message_id: int, text: str)`**
- Replies to a specific message
- Requires `send_message` permission
- Long texts are automatically split into multiple messages (≤4000 chars each); only the first chunk replies to the original message

**`await host_api.send_photo(chat_id: int, file_path: str, caption: str = "", *, message_thread_id: int | None = None, mime_type: str | None = None)`** (Sandbox-only)
- Sends a photo to the specified chat
- Requires `send_message` permission (gated by `send_message`)
- Only available in sandbox command runtime

**`await host_api.send_document(chat_id: int, file_path: str, caption: str = "", *, message_thread_id: int | None = None, mime_type: str | None = None)`** (Sandbox-only)
- Sends a document to the specified chat
- Requires `send_message` permission (gated by `send_message`)
- Only available in sandbox command runtime

**Context attachments** (`context["attachments"]`):
List of attachment dicts with keys:
- `source_kind`: Source type
- `type_hint`: "image" or "image_document"
- `file_id`: Telegram file ID
- `file_unique_id`: Unique file identifier
- `width`, `height`: Image dimensions
- `size`: File size in bytes
- `mime_type`: MIME type
- `media_ref`: Downloaded media reference (if available)

**Context for image analysis** (`context["reply_to_image"]`):
Dict with image context when replying to an image, with keys:
- `ok`: Boolean indicating if image is valid
- `media_ref`: Media reference for analysis
- `reason_code`: Error code if not valid

**Note:** Plugins run in a sandboxed subprocess. Operations are limited and monitored.

---

### Security Rules {#security-rules-en}

#### 1. Secret Handling

**ABSOLUTELY FORBIDDEN:**

```python
# ❌ NEVER DO THIS
token = "my-secret-token"  # Hardcoded
logger.info(f"Token: {user_token}")  # Logging secrets
return {"error": f"Failed with token {api_key}"}  # Returning secrets
```

**Correct approach:**

Secrets are configured via `settings_schema` with `type: secret`. The core handles secret storage and validation. Plugins never see the actual secret values.

#### 2. Private Artifacts

**DO NOT commit:**
- `.env` files
- `__pycache__/` directories
- `.pyc` files
- Test data with real user info
- Debug logs
- IDE configuration files

**Always include in `.gitignore`:**

```gitignore
# Plugin-specific
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/
.env
*.log
```

#### 3. Network Access

**FORBIDDEN:** Direct network access

```python
# ❌ NEVER DO THIS
import requests
response = requests.get("https://api.example.com")  # BYPASSES SANDBOX
```

**Note:** RSS/network operations must be handled by the runtime. Plugins with RSS-related settings must declare `rss.fetch` in `required_permissions`.

#### 4. Filesystem Access

**FORBIDDEN:** Direct filesystem access outside plugin dir

```python
# ❌ NEVER DO THIS
open("/etc/passwd")  # Absolute escape
open("../../config.json")  # Relative escape
open(os.environ["HOME"] + "/.secrets")  # Home directory escape
```

**ALLOWED:** Plugin directory only (read-only)

```python
# ✅ CORRECT: Read from plugin directory only
import os
plugin_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(plugin_dir, "data.json")
```

---

### UI/Config Integration {#uiconfig-integration-en}

#### Settings Schema

The `settings_schema` field in `plugin.yaml` defines WebUI configuration options.

**Supported setting types:** `text`, `number`, `bool`, `select`, `secret`

**YAML format:**

```yaml
settings_schema:
  enabled:
    type: bool
    required: false
    default: true
    description: Enable this plugin
  api_key:
    type: secret
    required: false
    description: API key (stored securely by core)
  interval:
    type: number
    required: false
    default: 5
    min: 1
    max: 60
    description: Check interval in minutes
  mode:
    type: select
    required: false
    default: basic
    options:
      - basic
      - advanced
      - expert
    description: Plugin mode
  webhook_url:
    type: text
    required: false
    pattern: "^https?://.*"
    description: Webhook URL (must be HTTP/HTTPS)
```

**Setting type constraints:**
- `number`: `default`, `min`, `max` must be numeric; `min` must be ≤ `max`
- `bool`: `default` must be true/false
- `select`: `options` must be non-empty strings; `default` must be in options
- `text`/`secret`: `default` must be string; `pattern` must be valid regex

#### UI Behavior

- Settings are edited in WebUI and persisted by core
- Plugin receives settings via the `settings_schema` validation
- Schema validation happens before plugin sees the data
- Invalid settings never reach the plugin
- Plugins cannot write to their directory; all state must be managed via host API

#### What Belongs in UI Config

| Belongs in UI | Does NOT Belong |
|--------------|-----------------|
| Feature toggles | Secrets (use secret type in settings_schema) |
| Numeric thresholds | File paths |
| List of channels | Raw credentials |
| Boolean flags | Internal state |
| Text templates | Derived/calculated values |

**Settings are defined in `settings_schema` with types:** `text`, `number`, `bool`, `select`, `secret`

---

### Testing Requirements {#testing-requirements-en}

#### Minimum Test Coverage

Every userplugin MUST have:

1. **Manifest validation test**
   ```python
   def test_manifest_valid_yaml():
       import yaml
       with open("plugin.yaml") as f:
           manifest = yaml.safe_load(f)
       assert "name" in manifest
       assert "version" in manifest
       assert "required_permissions" in manifest or "required_roles" in manifest
   ```

2. **Plugin loads without errors**
   ```python
   def test_plugin_imports():
       # main.py must define handle_command
       import importlib.util
       spec = importlib.util.spec_from_file_location("main", "main.py")
       module = importlib.util.module_from_spec(spec)
       spec.loader.exec_module(module)
       assert callable(getattr(module, "handle_command", None))
   ```

3. **No direct network calls**
   ```python
   def test_no_direct_requests():
       import ast
       with open("main.py") as f:
           tree = ast.parse(f.read())
       for node in ast.walk(tree):
           if isinstance(node, ast.Import):
               for alias in node.names:
                   assert alias.name != "requests"
   ```

4. **No secrets in code**
   ```python
   def test_no_hardcoded_secrets():
       import re
       with open("main.py") as f:
           code = f.read()
       # Check for common secret patterns
       assert not re.search(r'[a-zA-Z0-9]{32,}', code)  # API keys
   ```

#### QA Checklist Before Submission

- [ ] Plugin loads without errors in AMO
- [ ] All declared permissions are actually used
- [ ] No permissions are used that aren't declared
- [ ] Settings schema validates correctly
- [ ] Handles missing/invalid settings gracefully
- [ ] No secrets in code or logs
- [ ] No direct network/FS access
- [ ] Uses absolute paths only
- [ ] Has proper error handling
- [ ] Tests pass with `pytest -q`

---

### AI-Specific Guidelines {#ai-specific-guidelines-en}

**For AI agents generating userplugins:**

#### Absolute Paths Only

```python
# ❌ NEVER
data_file = "./data/items.json"
config = "../config.json"

# ✅ ALWAYS
import os
plugin_dir = os.path.dirname(os.path.abspath(__file__))
data_file = os.path.join(plugin_dir, "data", "items.json")
```

#### No OpenClaw Artifacts

**REMOVE before generating:**
- References to OpenClaw paths (e.g., `/home/claw/.openclaw/`)
- Session-specific data
- Internal tooling references
- Debug prints from generation

#### No Scope Creep

**Stick to the request:**
- If asked for RSS plugin, don't add weather API
- If asked for 3 commands, don't add 10
- Minimal viable implementation first

#### No Public Actions Without Approval

**FORBIDDEN:**
- Pushing to repository
- Creating public issues
- Posting to social media
- Any external network action beyond capability calls

**REQUIRED:**
- Return generated code to user for review
- Let user decide on submission/deployment
- Never auto-commit or auto-push

#### Code Generation Template

When asked to generate a plugin, use this structure:

**plugin.yaml:**

```yaml
name: my_plugin
version: "1.0.0"
description: Brief description of what this plugin does
commands:
  - mycommand
required_roles:
  - normal
required_permissions:
  - send_message
```

**main.py:**

```python
"""
[Plugin Name] for AMO Telegram Bot.

Generated: [Date]
Purpose: [Brief description]
Required permissions: [List of declared permissions]
"""

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


async def handle_command(context: Dict[str, Any], host_api: Any) -> None:
    """Main entry point for command execution."""
    command = context.get("command_name", "")
    argument = context.get("argument", "")
    chat_id = context.get("chat_id")
    message_id = context.get("message_id")

    # Handle your command logic here
    await host_api.reply(chat_id, message_id, "Hello from my plugin!")
```

---

### Quick Reference Card (EN)

```
MANIFEST (plugin.yaml)
├── name: lowercase, alphanumeric+underscore/hyphen, 3-50 chars
├── version: any format
├── commands: [] ← command names
├── schedule: {interval_seconds: N} or {cron: "..."}
├── worker: {restart_backoff_seconds: N}
├── required_roles: ["normal", "vip", "admin", "owner"]
└── required_permissions: ["rss.fetch", "send_message"]

CODE (main.py)
├── async def handle_command(context, host_api)
├── context: dict with chat_id, message_id, command_name, argument, attachments, etc.
├── host_api.send_message(chat_id, text)
└── host_api.reply(chat_id, message_id, text)

SETTINGS_SCHEMA
├── text: string with optional pattern regex
├── number: integer with min/max
├── bool: true/false
├── select: list of options
└── secret: string (stored securely)

SECURITY
├── NO secrets in code/logs
├── NO direct network (use host-provided permissions such as rss.fetch)
├── NO filesystem escape
└── NO public actions without approval
```

---

## 🔄 Document Synchronization / Dokumenten-Synchronisation

> **Sync Marker:** `USERPLUGINS-DE-EN-PARITY-2026.05`

### Language Parity Policy / Sprachparitäts-Regel

This document follows the **single bilingual file** pattern:
- Normative technical content is provided in **both DE and EN** sections
- **Canonical technical terms** (manifest field names, runtime signatures, API methods) remain **identical** across languages
- Code examples use English identifiers per Python conventions
- Section anchors use language suffixes: `-de` and `-en`

### Synchronization Process / Synchronisationsprozess

When updating this document:
1. **Check both language sections** for equivalent coverage
2. **Preserve canonical term identity** — never translate field names, API methods, or runtime signatures
3. **Update the sync marker** in both sections when making significant changes
4. **Run parity check:** Compare the DE and EN table-of-contents headings manually, or verify that `grep -c '^## .*🇩🇪' docs/USERPLUGINS.md` and `grep -c '^## .*🇬🇧' docs/USERPLUGINS.md` both return 1 (one German block, one English block)

### Canonical Terms Reference (Non-Translated) / Kanonische Begriffe (Nicht übersetzt)

| Category | Terms that stay identical |
|----------|---------------------------|
| Manifest fields | `name`, `version`, `commands`, `schedule`, `worker`, `required_roles`, `required_permissions`, `settings_schema` |
| Runtime context keys | `plugin_id`, `run_id`, `chat_id`, `message_id`, `message_thread_id`, `user_id`, `role`, `command_name`, `argument`, `attachments`, `reply_to_image`, `trigger_type`, `scheduled_at` |
| API methods | `handle_command`, `handle_schedule`, `send_message`, `reply`, `send_photo`, `send_document` |
| Permission values | `rss.fetch`, `send_message` |
| Role values | `ignore`, `normal`, `vip`, `admin`, `owner` |
| Settings types | `text`, `number`, `bool`, `select`, `secret` |

### Drift Prevention / Abdrift-Verhinderung

- DE and EN sections have **matching structure**: Overview → File Structure → Manifest → Do/Don't → Examples → API → Security → UI → Testing → AI Guidelines → Quick Ref
- When adding a new subsection, add it to **both** language blocks
- Use the **Quick Reference** tables at the start of each language section as parity checkpoints

---

## Related Documentation / Verwandte Dokumentation

- [CONTRIBUTING.md](../CONTRIBUTING.md) — Allgemeine Mitwirkungsrichtlinien / General contribution guidelines
- [SETUP_EN.md](SETUP_EN.md) — Setup instructions (EN)
- [CHANGELOG.md](../CHANGELOG.md) — Versionshistorie / Version history

---

<p align="center">
  <sub>AMO Userplugin Guide — Version 2026.05.23</sub>
</p>
