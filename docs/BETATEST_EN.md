# AMO Telegram Bot — Beta Test Guide

[Deutsche Version](BETATEST_DE.md)

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
cd AMO-telegram-bot
python3.12 -m venv venv
source venv/bin/activate
```

2. **Install dependencies:**

```bash
pip install -r requirements.txt
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
python main.py
```

**Success indicators:**
- Bot reports "Bot started"
- Polling begins without errors
- Offset loaded from `.state/offset.json` or newly created

---

### Start WebUI

In a **second terminal**:

```bash
source venv/bin/activate
python main.py --webui
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
2. Log in with your `WEBUI_PASSWORD`
3. Go to plugin overview – shows all plugins
4. Activate/Deactivate plugins via the management interface

**Note:** Plugins must first exist in `AMO_PLUGIN_DIR`. The plugin system supports Command, Scheduled, and Worker runtimes (MVP).

---

### What is NOT Tested in MVP

The following features are **not** included in the MVP:

- Channels (private chats and groups only)
- Media sending (images, videos, documents)
- Production-ready security hardening
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
- Check terminal: Is `python main.py` running?
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
