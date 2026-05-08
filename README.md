# AMO-telegram-bot (MVP Skeleton)

Defensives initiales Python-Codegeruest fuer einen Telegram-Bot mit eigener Telegram Bot API-Integration (ohne externe Telegram-Bot-Library).

## Scope (MVP)
- Long Polling via `getUpdates`
- `sendMessage` Skeleton
- MVP Command-Dispatcher (Update-Parser + Registry + Routing, rollenabhaengige Hilfe)
- Builtin-Commands: `/ping`, `/help`, `/role`, `/setrole`, `/ask`
- Rollenbasis: `owner`, `admin`, `vip`, `normal`, `ignore`
- Defensiver Plugin-Unterbau (Manifest + Loader + kontrollierte API)
- Ollama-Client + `/ask` MVP (stateless, ohne Verlauf)
- Lokale WebUI-MVP (Flask) mit Login-Session + CSRF

## Nicht im MVP
- Produktivbetrieb
- Echte Secrets im Repo
- Kanal-Features
- Erweiterter Dispatcher fuer komplexe Routing-Faelle (z. B. Multi-Step/Stateful Flows)

## Setup
```bash
cd /path/to/local/workspace
python3.12 -m venv venv
source venv/bin/activate
pip install -e .[dev]
cp .env.example .env
```

Danach `.env` mit lokalen Werten fuellen (keine echten Secrets committen).

Wichtig zur Config-Prioritaet:
- Beim lokalen Start wird `.env` standardmaessig **ueber** bereits exportierte Shell-Variablen gelegt.
- Damit gewinnen Projektwerte aus `.env` (z. B. `WEBUI_HOST=0.0.0.0`) gegen alte Shell-Altwerte.
- Fuer einen bewussten Runtime-Override per Shell: `AMO_ENV_OVERRIDE=0` setzen.

## Start
Bot-Polling:
```bash
python -m amo_bot.main
```

WebUI (lokal):
```bash
python -m amo_bot.main --webui
```

## Betatest

Eine vollständige Betatest-Anleitung mit Checkliste und Protokoll findest du in:

- **[docs/BETATEST.md](docs/BETATEST.md)** – Schritt-für-Schritt-Tests für MVP-Features

## Lokaler Preflight / Smoke (ohne echte Secrets)
Vor dem ersten echten Telegram-Test:

```bash
source venv/bin/activate
pytest -q
python -m amo_bot.smoke
```

Der Smoke nutzt nur lokale Fakes (kein echter Telegram- oder Ollama-Call) und prueft bewusst nur einen leichten Preflight (Bootstrap + Basis-Commands `/ping`, `/help`, `/role`).
Der vollstaendigere Command-Flow (inkl. Rollenrechte, `/setrole` und `/ask`) liegt in den pytest-E2E-Tests.

## Sicherheitsregeln
> WebUI ist im MVP nur lokal gedacht (Default `127.0.0.1`) und nicht produktiv.
> Nicht ins Internet exponieren.

- Secrets nur in `.env`, nie in Code/Repo/Chat.
- Admin darf per Telegram nur `vip|normal|ignore` setzen.
- Owner bleibt exklusiv.
- Plugin-Aktivierung im MVP nur ueber WebUI erlaubt (technisch per Policy abgesichert; Telegram bleibt blockiert).
- Telegram-Eingaben als untrusted behandeln.

## Offset-Entscheidung
Offset wird im MVP defensiv in einer lokalen State-Datei (`.state/offset.json`) gespeichert. Grund: weniger Komplexitaet im Start, robuste atomare Dateischreibweise. DB-Offset ist spaeter problemlos migrierbar.


## Rollen-Commands (MVP)
- `/setrole <telegram_user_id> <role>`
  - Erlaubt fuer: `owner`, `admin`
  - `admin` darf nur `vip|normal|ignore` setzen
  - `admin` darf **nicht** `admin|owner` setzen
  - `owner` kann `admin|vip|normal|ignore` setzen
  - Defensiv im MVP: `owner -> owner` Zuweisung via Telegram ist deaktiviert (WebUI-Pfad spaeter)

## Ask-Command (Ollama MVP)
- `/ask <frage>`
  - Erlaubt fuer: `owner`, `admin`, `vip`
  - `normal` und `ignore` duerfen `/ask` nicht nutzen
  - Ohne Argumente: `usage: /ask <question>`
- Implementierung ist **stateless**: kein Chat-Verlauf, keine History-Speicherung
- Fehlerantworten bleiben freundlich und geben keine internen Details preis

### .env fuer echten Telegram-Test
Pflichtwerte:
- `TELEGRAM_BOT_TOKEN`
- `BOT_USERNAME`
- `WEBUI_PASSWORD`
- `WEBUI_OWNER_TELEGRAM_ID`

Optional fuer `/ask` mit lokalem Ollama:
- `OLLAMA_URL` (Default: `http://127.0.0.1:11434`)
- `OLLAMA_MODEL` (Default: `llama3.1`)
- `OLLAMA_TIMEOUT_SECONDS` (Default: `20`)
- `OLLAMA_MAX_RESPONSE_CHARS` (Default: `1500`)

## WebUI-Routen (MVP)
- `GET /health` offen
- `GET /login`
- `POST /login`
- `POST /logout`
- `GET /dashboard` (Login erforderlich)
- `GET /users` (Login erforderlich, HTML-Liste)
- `POST /users/<telegram_user_id>/role` (Login erforderlich, CSRF, mutierend)

Wichtige Hinweise:
- Rollenmutation ist deaktiviert, wenn `WEBUI_OWNER_TELEGRAM_ID` nicht gesetzt ist.
- Audit-Actor wird serverseitig aus `WEBUI_OWNER_TELEGRAM_ID` gesetzt.

### Zusaetzliche WebUI-.env Variablen
- `WEBUI_OWNER_TELEGRAM_ID` (required fuer mutierende WebUI-Routen inkl. `POST /users/set-role`, `POST /plugins/activate`, `POST /plugins/deactivate`)
- `WEBUI_SESSION_TTL_SECONDS` (Default: `3600`)

## Aktueller MVP-Stand (dieser Block)
- DB-basierter RoleResolver vorhanden (`DBRoleResolver`)
- Rollenpersistenz inkl. Audit-Event fuer Rollenwechsel vorhanden
- Rollenverwaltung via Telegram-Command `/setrole` umgesetzt
- Hilfeausgabe ist rollenabhaengig und zeigt `/setrole` nur fuer `owner/admin`
- `/ask` mit Ollama als stateless MVP umgesetzt (ohne Verlauf)
- Lokale Owner-WebUI MVP mit minimaler Auth, Token-TTL/Logout und geschuetzten Rollen-/Plugin-Routen umgesetzt

## Plugin Manifest (MVP)
- Kein Code-Import/Exec im MVP.
- Feld `entrypoint` ist explizit nicht erlaubt.
