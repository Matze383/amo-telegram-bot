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

# Security settings (Block 1)
# WEBUI_PUBLIC_MODE=false
# WEBUI_REQUIRE_HTTPS=false
# WEBUI_SESSION_COOKIE_SECURE=

# Security settings (Block 2 - Login Protection)
# WEBUI_LOGIN_DELAY_BASE_SECONDS=0.25
# WEBUI_LOGIN_DELAY_MAX_SECONDS=2.0

# Polling Configuration
POLL_TIMEOUT_SECONDS=30
POLL_LIMIT=100
POLL_RETRY_MAX_SECONDS=30
OFFSET_STATE_FILE=.state/offset.json
```

---

### Security Features (Block 1 + Block 2)

The WebUI now includes hardened security:

**Security Headers (always active):**
- Content-Security-Policy (CSP)
- X-Frame-Options: DENY
- X-Content-Type-Options: nosniff
- Referrer-Policy
- Permissions-Policy
- HSTS (in HTTPS contexts)

**Session Cookie Security:**
- HttpOnly flag
- SameSite=Lax
- Secure flag (auto-enabled for public/HTTPS)

**Login Protection (Block 2):**
- Progressive delay after failed login attempts (exponential backoff / brute-force protection)
- Configurable via `WEBUI_LOGIN_DELAY_BASE_SECONDS` (default: 0.25s) and `WEBUI_LOGIN_DELAY_MAX_SECONDS` (default: 2.0s)
- Delay is capped at the maximum value
- Successful login resets the delay counter
- Per-IP tracking using `remote_addr` (conservative keying)

**Configuration:**
- `WEBUI_PUBLIC_MODE=false` — Local development default
- `WEBUI_REQUIRE_HTTPS=false` — Local development default
- `WEBUI_LOGIN_DELAY_BASE_SECONDS=0.25` — Initial delay after first failure
- `WEBUI_LOGIN_DELAY_MAX_SECONDS=2.0` — Maximum delay cap

**⚠️ Production Warning:** Flask should not be exposed directly to the internet. Use a reverse proxy with HTTPS.

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

**Role Scoping Rules:**
- In **private chats (DM)**: Global role applies everywhere
- In **groups**: Global `owner` or `ignore` overrides everything; otherwise group-specific role applies; otherwise `normal`
- Group admins can only set `vip`, `normal`, `ignore` within their own group, **not** `admin` or `owner`
- An admin in Group A is **not** automatically admin in Group B

**As Owner:**
- `/setrole <user_id> admin` – User becomes Admin (global in DM, group-scoped in groups)
- `/setrole <user_id> vip` – User becomes VIP (global in DM, group-scoped in groups)
- `/setrole <user_id> normal` – User becomes Normal (global in DM, group-scoped in groups)
- `/setrole <user_id> ignore` – User is ignored (global, overrides everything)

**As Admin:**
- `/setrole <user_id> vip` – Allowed
- `/setrole <user_id> normal` – Allowed
- `/setrole <user_id> ignore` – Allowed
- `/setrole <user_id> admin` – **Not allowed**
- `/setrole <user_id> owner` – **Not allowed**

**Test Workflow:**
1. Create a second Telegram account or ask a friend
2. Get the Telegram user ID of the test account
3. In a **private chat**: Set role to `normal` → `/role` shows "normal (global)"
4. In **Group A**: Set role to `vip` → `/role` shows "vip (this group)"
5. In **Group B**: `/role` shows global role (or "normal") unless explicitly set
6. Test if account can use `/ask` (with `vip`: yes, with `normal`: no)

**Group-Scoped Role Tests:**
- [ ] `/role` in DM shows global role with source
- [ ] `/role` in Group A shows group-specific or global role
- [ ] `/setrole` in DM sets global role
- [ ] `/setrole` in Group A sets role only for Group A
- [ ] User with `vip` in Group A has `normal` permissions in Group B (if no global role)
- [ ] Group admin cannot promote to `admin`/`owner` (only `vip`/`normal`/`ignore`)

**Audit Events (optional check):**
- [ ] Group role changes via `/setrole` are auditable (check logs or database if applicable)

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

### Group Role Management via WebUI

1. Open http://127.0.0.1:8080 and log in
2. Go to "Groups" page – shows all groups/supergroups
3. Select a group – users with current role are displayed
4. Change role: `admin`, `vip`, `normal`, `ignore`

**Important:**
- `owner` cannot be set as a group role (only via `.env`)
- `normal` removes the group-scoped entry → fallback to `normal`
- Roles are group-scoped, not global
- Mutation protection: Login + CSRF token + Owner gate required

---

### WebUI Access Control via Telegram (Block 3)

The `/webui` commands allow the owner to control WebUI access via Telegram.

**Test Steps:**

1. **Test `/webui status` (closed state):**
   - Send: `/webui status`
   - Expected: "CLOSED" or similar message indicating the access window is not open

2. **Test `/webui on`:**
   - Send: `/webui on`
   - Expected: Confirmation that the WebUI access window is now OPEN for 60 minutes

3. **Test `/webui status` (open state):**
   - Send: `/webui status`
   - Expected: "OPEN" with remaining minutes displayed

4. **Test `/webui off`:**
   - Send: `/webui off`
   - Expected: Confirmation that the WebUI access window is now CLOSED

**Negative Tests:**

5. **Test in a group (should be denied):**
   - Add the bot to a test group
   - Send: `/webui status` in the group
   - Expected: Access denied, possibly with a message that this only works in private chats

6. **Test as non-owner (should be denied):**
   - Have a non-owner user send `/webui status`
   - Expected: Access denied, possibly with an authorization error

**Important Notes:**
- These commands only work in **private chats** with the owner
- The access window state is persisted in the database (survives bot restarts)
- **The HTTP request gate is NOT YET implemented** — the WebUI login page is not blocked by this mechanism yet

**Checklist:**
- [ ] `/webui status` shows CLOSED initially
- [ ] `/webui on` opens the access window
- [ ] `/webui status` shows OPEN with remaining time
- [ ] `/webui off` closes the access window
- [ ] `/webui` commands in groups are denied
- [ ] `/webui` commands for non-owners are denied

---

### Future Features (Not Yet Implemented)

The following features are planned for future releases and are **not available** in the current beta:

- HTTP request gate for WebUI access window (actual blocking of login page) — Block 3 continuation

---

### Block 2 Security Testing Notes

**Login behavior:**
- Wrong credentials return a **generic error message** — no detailed information is revealed
- Repeated failed logins are **progressively delayed** (exponential backoff, capped at max)
- Successful login after failures works normally — the delay counter resets immediately

**Audit Events (internal/optional):**
- Login attempts generate audit events: `webui_login_failure` and `webui_login_success`
- Events include IP address (`remote_addr`) only
- No passwords or sensitive data is logged
- These events are for internal logging/monitoring and do not affect user-facing behavior

**Not yet implemented:**
- HTTP request gate (actual blocking of WebUI login page based on access window state)

---

### What is NOT Tested in MVP

The following features are **not** included in the MVP:

- Channels (private chats and groups only)
- Media sending (images, videos, documents)
- Production-ready security hardening
- Chat history for `/ask`
- Multi-user WebUI (owner login only)
- Telegram-based WebUI access control (`/webui` commands) — Command path implemented; HTTP gate pending

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
- [ ] WebUI group role management: OK / Not tested
- [ ] Security headers present (check browser dev tools): OK

**Notes:**

```
Date: ___________
Tester: __________
Result: Passed / Failed / Partial
Observations: _________________________________
_________________________________________________
```
