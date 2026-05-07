# AMO-telegram-bot – Betatest-Anleitung

## Deutsch

### Ziel des Betatests

Diese Anleitung unterstützt dich beim Testen des MVP-Status des Bots:

- Commands funktionieren in privaten Chats und Gruppen
- Rollenverwaltung via Telegram und WebUI
- Plugin-Aktivierung über die WebUI
- Ollama-Integration für `/ask`

---

### Voraussetzungen

- Python 3.12 oder höher
- Linux/macOS-Entwicklungsumgebung
- Ein Telegram-Bot-Token (von @BotFather)
- Optional: Lokale Ollama-Instanz für `/ask`

---

### .env Konfiguration

Kopiere die Beispieldatei:

```bash
cp .env.example .env
```

Bearbeite `.env` mit deinen Werten:

```
# Telegram (Pflicht)
BOT_TOKEN=dein_bot_token_hier
BOT_USERNAME=dein_bot_username
TELEGRAM_API_BASE=https://api.telegram.org

# Ollama (optional für /ask)
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_MAX_RESPONSE_CHARS=1500

# Datenbank
DATABASE_URL=sqlite:///./data/amo_bot.db

# Plugins
AMO_PLUGIN_DIR=./plugins

# WebUI (Pflicht für Betatest)
WEBUI_HOST=127.0.0.1
WEBUI_PORT=8080
WEBUI_PASSWORD=dein_sicheres_passwort
WEBUI_OWNER_TELEGRAM_ID=deine_telegram_user_id
WEBUI_SESSION_TTL_SECONDS=3600

# Polling-Konfiguration
POLL_TIMEOUT_SECONDS=30
POLL_LIMIT=100
POLL_RETRY_MAX_SECONDS=30
OFFSET_STATE_FILE=.state/offset.json
```

---

### Setup-Schritte

1. **Virtuelle Umgebung erstellen:**

```bash
cd /path/to/local/workspace
python3.12 -m venv venv
source venv/bin/activate
```

2. **Abhängigkeiten installieren:**

```bash
pip install -e .[dev]
```

3. **Datenbank-Ordner erstellen:**

```bash
mkdir -p data
mkdir -p .state
mkdir -p plugins
```

---

### Lokaler Preflight

Vor dem ersten Telegram-Test:

```bash
source venv/bin/activate

# Unit-Tests ausführen
pytest -q

# Smoke-Test (lokale Checks ohne echte API-Calls)
python -m amo_bot.smoke
```

**Erwartete Ergebnisse:**
- pytest: Alle Tests bestanden
- smoke: Bootstrap und Basis-Commands OK

---

### Bot starten

```bash
source venv/bin/activate
python -m amo_bot.main
```

**Erfolgsindikatoren:**
- Bot meldet "Bot started"
- Polling beginnt ohne Fehler
- Offset wird aus `.state/offset.json` geladen oder neu erstellt

---

### WebUI starten

In einem **zweiten Terminal**:

```bash
cd /path/to/local/workspace
source venv/bin/activate
uvicorn amo_bot.webui.app:app --host 127.0.0.1 --port 8080
```

**Wichtig:** WebUI läuft nur lokal (`127.0.0.1`). Nicht ins Internet freigeben.

**Erreichbarkeit:** http://127.0.0.1:8080

---

### Private Chat Tests

Starte einen privaten Chat mit deinem Bot:

**Test 1: /ping**
- Sende: `/ping`
- Erwartet: `Pong!`

**Test 2: /help**
- Sende: `/help`
- Erwartet: Liste der verfügbaren Commands (abhängig von deiner Rolle)

**Test 3: /role**
- Sende: `/role`
- Erwartet: Deine aktuelle Rolle (z.B. "owner")

---

### Gruppen-Test

1. Füge den Bot zu einer Testgruppe hinzu
2. Stelle sicher, dass der Bot Admin-Rechte hat (zum Lesen aller Nachrichten)
3. Teste Commands:
   - `/ping`
   - `/help`
   - `/role`

**Wichtig:** Commands in Gruppen beginnen oft mit dem Bot-Username: `/ping@dein_bot_username`

---

### Rollen-Test mit /setrole

**Voraussetzung:** Du bist Owner oder Admin

**Als Owner:**
- `/setrole <user_id> admin` – User wird Admin
- `/setrole <user_id> vip` – User wird VIP
- `/setrole <user_id> normal` – User wird Normal
- `/setrole <user_id> ignore` – User wird ignoriert

**Als Admin:**
- `/setrole <user_id> vip` – Erlaubt
- `/setrole <user_id> normal` – Erlaubt
- `/setrole <user_id> ignore` – Erlaubt
- `/setrole <user_id> admin` – **Nicht erlaubt**
- `/setrole <user_id> owner` – **Nicht erlaubt**

**Test-Workflow:**
1. Erstelle einen zweiten Telegram-Account oder frage einen Freund
2. Ermittle die Telegram-User-ID des Test-Accounts
3. Setze die Rolle auf `normal`
4. Teste, ob der Account `/ask` nutzen kann (bei `normal`: nein)
5. Setze die Rolle auf `vip`
6. Teste erneut `/ask` (bei `vip`: ja)

---

### /ask-Test mit Ollama (optional)

**Voraussetzung:** Ollama läuft lokal

```bash
# Prüfe Ollama-Status
curl http://127.0.0.1:11434/api/tags
```

**Test:**
- Sende: `/ask Was ist Python?`
- Erwartet: Eine kurze Antwort vom KI-Modell

**Einschränkungen im MVP:**
- Stateless (kein Chat-Verlauf)
- Timeout nach 20 Sekunden
- Maximale Antwortlänge: 1500 Zeichen

---

### Plugin-Test über WebUI

1. Öffne http://127.0.0.1:8080 im Browser
2. Melde dich an:
   - POST /auth/login mit Body: `{"password": "dein_webui_passwort"}`
   - Oder nutze die HTML-Oberfläche falls vorhanden
3. Rufe `GET /plugins` auf – zeigt alle Plugins
4. Aktiviere ein Plugin:
   - `POST /plugins/activate` mit Body: `{"plugin_name": "demo_plugin"}`
5. Deaktiviere ein Plugin:
   - `POST /plugins/deactivate` mit Body: `{"plugin_name": "demo_plugin"}`

**Hinweis:** Plugins müssen zuerst im `AMO_PLUGIN_DIR` liegen.

---

### Was NICHT getestet wird im MVP

Folgende Features sind **nicht** im MVP enthalten:

- Kanäle (nur private Chats und Gruppen)
- Medienversand (Bilder, Videos, Dokumente)
- Echte Plugin-Code-Ausführung (nur Manifest-Verwaltung)
- Produktivbetrieb-Sicherheitsfeatures
- Chat-Verlauf für `/ask`
- Multi-User-WebUI (nur Owner-Login)

---

### Sicherheitsregeln

- **Token niemals posten:** Dein `BOT_TOKEN` gehört niemals in Chats, Logs oder Git
- **WebUI nur lokal:** Nicht auf `0.0.0.0` oder öffentliche IPs binden
- **Owner-ID prüfen:** `WEBUI_OWNER_TELEGRAM_ID` muss korrekt gesetzt sein
- **Starke Passwörter:** `WEBUI_PASSWORD` sollte nicht "password123" sein
- **Keine Secrets im Repo:** `.env` steht in `.gitignore`

---

### Fehlerdiagnose

**Bot antwortet nicht:**
- Prüfe Terminal: Läuft `python -m amo_bot.main`?
- Prüfe `.env`: Ist `BOT_TOKEN` korrekt?
- Prüfe Telegram: Hast du den Bot gestartet (im Chat auf "Start" geklickt)?

**DB/SQLite Fehler:**
- Existiert der `data/`-Ordner?
- Schreibrechte vorhanden?
- Löschen der DB-Datei (nur im Test!): `rm data/amo_bot.db`

**Ollama nicht erreichbar:**
- Läuft Ollama? `curl http://127.0.0.1:11434/api/tags`
- Korrekte URL in `.env`?
- Firewall blockt Port 11434?

**WebUI Login geht nicht:**
- Ist `WEBUI_PASSWORD` in `.env` gesetzt?
- Ist der Wert nicht auf "change_me" oder leer?
- Rufst du `http://127.0.0.1:8080` auf?

---

### Betatest-Protokoll

Nutze diese Checkliste für deinen Test:

- [ ] Setup abgeschlossen (venv, pip install)
- [ ] .env korrekt konfiguriert
- [ ] pytest: Alle Tests bestanden
- [ ] Smoke-Test: OK
- [ ] Bot startet ohne Fehler
- [ ] WebUI startet ohne Fehler
- [ ] Privater Chat /ping: OK
- [ ] Privater Chat /help: OK
- [ ] Privater Chat /role: OK
- [ ] Gruppen-Test /ping: OK
- [ ] Gruppen-Test /help: OK
- [ ] Rollen-Test /setrole normal: OK
- [ ] Rollen-Test /setrole vip: OK
- [ ] Rollen-Test Einschränkung Admin/Owner: OK
- [ ] /ask-Test (optional): OK / Nicht getestet
- [ ] WebUI Login: OK
- [ ] WebUI Plugin-Liste: OK
- [ ] WebUI Plugin aktivieren/deaktivieren: OK / Nicht getestet

**Notizen:**

```
Datum: ___________
Tester: __________
Ergebnis: Bestanden / Fehlgeschlagen / Teilweise
Auffälligkeiten: _________________________________
_________________________________________________
```

---

---

## English

### Beta Test Goal

This guide helps you test the MVP status of the bot:

- Commands work in private chats and groups
- Role management via Telegram and WebUI
- Plugin activation through WebUI
- Ollama integration for `/ask`

---

### Prerequisites

- Python 3.12 or higher
- Linux/macOS development environment
- A Telegram bot token (from @BotFather)
- Optional: Local Ollama instance for `/ask`

---

### .env Configuration

Copy the example file:

```bash
cp .env.example .env
```

Edit `.env` with your values:

```
# Telegram (Required)
BOT_TOKEN=your_bot_token_here
BOT_USERNAME=your_bot_username
TELEGRAM_API_BASE=https://api.telegram.org

# Ollama (optional for /ask)
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_MAX_RESPONSE_CHARS=1500

# Database
DATABASE_URL=sqlite:///./data/amo_bot.db

# Plugins
AMO_PLUGIN_DIR=./plugins

# WebUI (Required for beta test)
WEBUI_HOST=127.0.0.1
WEBUI_PORT=8080
WEBUI_PASSWORD=your_secure_password
WEBUI_OWNER_TELEGRAM_ID=your_telegram_user_id
WEBUI_SESSION_TTL_SECONDS=3600

# Polling Configuration
POLL_TIMEOUT_SECONDS=30
POLL_LIMIT=100
POLL_RETRY_MAX_SECONDS=30
OFFSET_STATE_FILE=.state/offset.json
```

---

### Setup Steps

1. **Create virtual environment:**

```bash
cd /path/to/local/workspace
python3.12 -m venv venv
source venv/bin/activate
```

2. **Install dependencies:**

```bash
pip install -e .[dev]
```

3. **Create directories:**

```bash
mkdir -p data
mkdir -p .state
mkdir -p plugins
```

---

### Local Preflight

Before the first Telegram test:

```bash
source venv/bin/activate

# Run unit tests
pytest -q

# Smoke test (local checks without real API calls)
python -m amo_bot.smoke
```

**Expected Results:**
- pytest: All tests passed
- smoke: Bootstrap and basic commands OK

---

### Start Bot

```bash
source venv/bin/activate
python -m amo_bot.main
```

**Success indicators:**
- Bot reports "Bot started"
- Polling begins without errors
- Offset loaded from `.state/offset.json` or newly created

---

### Start WebUI

In a **second terminal**:

```bash
cd /path/to/local/workspace
source venv/bin/activate
uvicorn amo_bot.webui.app:app --host 127.0.0.1 --port 8080
```

**Important:** WebUI runs locally only (`127.0.0.1`). Do not expose to the internet.

**URL:** http://127.0.0.1:8080

---

### Private Chat Tests

Start a private chat with your bot:

**Test 1: /ping**
- Send: `/ping`
- Expected: `Pong!`

**Test 2: /help**
- Send: `/help`
- Expected: List of available commands (depends on your role)

**Test 3: /role**
- Send: `/role`
- Expected: Your current role (e.g., "owner")

---

### Group Test

1. Add the bot to a test group
2. Ensure the bot has admin rights (to read all messages)
3. Test commands:
   - `/ping`
   - `/help`
   - `/role`

**Important:** Commands in groups often need the bot username: `/ping@your_bot_username`

---

### Role Test with /setrole

**Prerequisite:** You are Owner or Admin

**As Owner:**
- `/setrole <user_id> admin` – User becomes Admin
- `/setrole <user_id> vip` – User becomes VIP
- `/setrole <user_id> normal` – User becomes Normal
- `/setrole <user_id> ignore` – User is ignored

**As Admin:**
- `/setrole <user_id> vip` – Allowed
- `/setrole <user_id> normal` – Allowed
- `/setrole <user_id> ignore` – Allowed
- `/setrole <user_id> admin` – **Not allowed**
- `/setrole <user_id> owner` – **Not allowed**

**Test Workflow:**
1. Create a second Telegram account or ask a friend
2. Get the Telegram user ID of the test account
3. Set role to `normal`
4. Test if account can use `/ask` (with `normal`: no)
5. Set role to `vip`
6. Test `/ask` again (with `vip`: yes)

---

### /ask Test with Ollama (optional)

**Prerequisite:** Ollama running locally

```bash
# Check Ollama status
curl http://127.0.0.1:11434/api/tags
```

**Test:**
- Send: `/ask What is Python?`
- Expected: A short response from the AI model

**MVP Limitations:**
- Stateless (no chat history)
- Timeout after 20 seconds
- Maximum response length: 1500 characters

---

### Plugin Test via WebUI

1. Open http://127.0.0.1:8080 in browser
2. Log in:
   - POST /auth/login with body: `{"password": "your_webui_password"}`
   - Or use HTML interface if available
3. Call `GET /plugins` – shows all plugins
4. Activate a plugin:
   - `POST /plugins/activate` with body: `{"plugin_name": "demo_plugin"}`
5. Deactivate a plugin:
   - `POST /plugins/deactivate` with body: `{"plugin_name": "demo_plugin"}`

**Note:** Plugins must first exist in `AMO_PLUGIN_DIR`.

---

### What is NOT Tested in MVP

The following features are **not** included in the MVP:

- Channels (private chats and groups only)
- Media sending (images, videos, documents)
- Real plugin code execution (manifest management only)
- Production security features
- Chat history for `/ask`
- Multi-user WebUI (owner login only)

---

### Security Rules

- **Never post your token:** Your `BOT_TOKEN` never belongs in chats, logs, or Git
- **WebUI local only:** Do not bind to `0.0.0.0` or public IPs
- **Check owner ID:** `WEBUI_OWNER_TELEGRAM_ID` must be set correctly
- **Strong passwords:** `WEBUI_PASSWORD` should not be "password123"
- **No secrets in repo:** `.env` is in `.gitignore`

---

### Troubleshooting

**Bot does not respond:**
- Check terminal: Is `python -m amo_bot.main` running?
- Check `.env`: Is `BOT_TOKEN` correct?
- Check Telegram: Did you start the bot (clicked "Start" in chat)?

**DB/SQLite errors:**
- Does the `data/` folder exist?
- Write permissions available?
- Delete DB file (test only!): `rm data/amo_bot.db`

**Ollama not reachable:**
- Is Ollama running? `curl http://127.0.0.1:11434/api/tags`
- Correct URL in `.env`?
- Firewall blocking port 11434?

**WebUI login fails:**
- Is `WEBUI_PASSWORD` set in `.env`?
- Is the value not "change_me" or empty?
- Are you calling `http://127.0.0.1:8080`?

---

### Beta Test Protocol

Use this checklist for your test:

- [ ] Setup complete (venv, pip install)
- [ ] .env configured correctly
- [ ] pytest: All tests passed
- [ ] Smoke test: OK
- [ ] Bot starts without errors
- [ ] WebUI starts without errors
- [ ] Private chat /ping: OK
- [ ] Private chat /help: OK
- [ ] Private chat /role: OK
- [ ] Group test /ping: OK
- [ ] Group test /help: OK
- [ ] Role test /setrole normal: OK
- [ ] Role test /setrole vip: OK
- [ ] Role test restriction admin/owner: OK
- [ ] /ask test (optional): OK / Not tested
- [ ] WebUI login: OK
- [ ] WebUI plugin list: OK
- [ ] WebUI plugin activate/deactivate: OK / Not tested

**Notes:**

```
Date: ___________
Tester: __________
Result: Passed / Failed / Partial
Observations: _________________________________
_________________________________________________
```
