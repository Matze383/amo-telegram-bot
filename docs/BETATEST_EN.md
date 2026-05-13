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

**Test 3: /consent**
- Send: `/consent`
- Expected: Shows your current consent status and available commands
  - **Private chat**: Full status and details shown
  - **Groups**: Privacy notice only (no status details shown in groups for data protection)

**Test 4: /accept**
- Send: `/accept`
- Expected: Consent accepted confirmation
- Note: If you previously declined, you can use `/accept` again to re-consent

**Test 5: /decline**
- Send: `/decline`
- Expected: Consent declined confirmation
- Note: You can use `/accept` later if you change your mind

**Test 6: /role**
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

### AI Auto-Reply (Mention/Reply)

The bot can auto-respond via AI when mentioned or replied to in **active scopes** (topics or private chats with AI enabled).

**How it works:**
- **Mention:** Type `@YourBotName` in an active topic or private chat
- **Reply:** Reply to one of the bot's messages in an active scope
- The bot sends the message text to the configured AI and returns the response

**Requirements:**
- User must have role `vip`, `admin`, or `owner`
- User must have accepted consent (`/accept`)
- The scope (topic or private chat) must have AI enabled in the configuration
- The AI service must be configured (Ollama)

**Audit Events:**
- `ai_autoreply_sent` — Response successfully sent
- `ai_autoreply_denied` — Blocked (role or consent)
- `ai_autoreply_error` — AI service error

**Note:** This is separate from the `/ask` command. Auto-reply is triggered implicitly by mentions/replies; `/ask` is an explicit command.

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

**Checklist:**
- [ ] `/webui status` shows CLOSED initially
- [ ] `/webui on` opens the access window
- [ ] `/webui status` shows OPEN with remaining time
- [ ] `/webui off` closes the access window
- [ ] `/webui` commands in groups are denied
- [ ] `/webui` commands for non-owners are denied

---

### WebUI HTTP Request Gate (Block 3C)

When `WEBUI_PUBLIC_MODE=true`, the HTTP Request Gate blocks access to protected WebUI pages when the access window is closed.

**Prerequisites:**
- Set `WEBUI_PUBLIC_MODE=true` in `.env`
- Restart the bot/WebUI

**Test Steps:**

1. **With access window CLOSED:**
   - Ensure window is closed: `/webui off` in Owner DM
   - Try to access: `http://127.0.0.1:8080/login`
   - Expected: **403 Forbidden** page

2. **Protected routes also blocked:**
   - Try to access: `http://127.0.0.1:8080/groups`
   - Expected: **403 Forbidden**

3. **Open access window:**
   - Send: `/webui on` in Owner DM
   - Try to access: `http://127.0.0.1:8080/login`
   - Expected: Login page loads normally
   - Log in with password: Should work as usual

4. **Access window expires or closed:**
   - Wait for expiration (or send `/webui off`)
   - Try to access protected pages again
   - Expected: **403 Forbidden** returned

5. **Whitelisted paths remain accessible:**
   - With window CLOSED, test:
     - `http://127.0.0.1:8080/health` — Expected: Health check response (not 403)
     - `http://127.0.0.1:8080/static/css/style.css` — Expected: Static file served
     - `/logout` — Expected: Logout works (if logged in before window closed)

6. **JSON/API responses:**
   - Send request with `Accept: application/json` header to blocked path
   - Expected: `{"error":"forbidden","status":403}`

**Public Mode Off (default):**

7. **With `WEBUI_PUBLIC_MODE=false`:**
   - Close access window: `/webui off`
   - Access `http://127.0.0.1:8080/login`
   - Expected: Login page loads normally (gate is inactive in non-public mode)

**Checklist:**
- [ ] Public mode + closed window → `/login` returns 403
- [ ] Public mode + closed window → `/groups` returns 403
- [ ] `/webui on` in Owner DM → `/login` becomes reachable
- [ ] Normal password login works when window is open
- [ ] `/webui off` or expiration → 403 returned again
- [ ] `/health` remains reachable when window is closed
- [ ] Static assets remain reachable when window is closed
- [ ] JSON requests receive `{"error":"forbidden","status":403}`
- [ ] Non-public mode (`WEBUI_PUBLIC_MODE=false`) → gate inactive, local usage unchanged

**Important Notes:**
- The gate is **only active when `WEBUI_PUBLIC_MODE=true`**
- When the window is open, normal password authentication is still required
- The gate controls whether the login page is *reachable*, not the login itself
- For production/internet deployment, a reverse proxy (nginx, Caddy, Traefik) with HTTPS is still required — Flask should not be exposed directly to the internet

**⚠️ Deployment Note:** This feature enables access control for public deployments, but does not replace proper reverse proxy and HTTPS setup. Nginx/internet deployment configuration is outside the scope of this beta.

---

### Consent Commands (Block 1)

The bot now includes user consent management via Telegram commands.

**Test Steps:**

1. **Test `/consent` in private chat:**
   - Send: `/consent`
   - Expected: Shows current consent status, details, and available commands

2. **Test `/accept`:**
   - Send: `/accept`
   - Expected: Confirmation that consent has been accepted

3. **Test `/decline`:**
   - Send: `/decline`
   - Expected: Confirmation that consent has been declined

4. **Test `/consent` after declining:**
   - Send: `/consent`
   - Expected: Shows declined status and reminds that you can `/accept` again

5. **Test re-accepting:**
   - Send: `/accept` (after previously declining)
   - Expected: Consent accepted again successfully

6. **Test `/consent` in groups:**
   - Send: `/consent` in a group where the bot is present
   - Expected: Privacy notice only — no consent status details shown in groups for data protection reasons

**Checklist:**
- [ ] `/consent` in private chat shows full status
- [ ] `/accept` confirms consent accepted
- [ ] `/decline` confirms consent declined
- [ ] `/consent` shows declined status correctly
- [ ] `/accept` works after previous decline
- [ ] `/consent` in groups shows only privacy hint (no details)

---

### Automatic Private Consent DM Prompt (Block 2)

The bot automatically sends a private consent prompt to users who are in "pending" status (not yet accepted or declined).

**How it works:**
- When a pending user is seen in a group, the bot automatically sends them a private DM with a consent notice
- The DM includes **inline buttons** (✅ Accept / ❌ Decline) for quick consent, plus fallback commands: `/accept`, `/decline`, `/consent`
- **One-shot policy:** Exactly 1 automatic DM per user — only sent if `consent_prompt_count == 0`. After successful delivery, `prompt_count` is set to 1 and no further automatic DMs are sent.
- **Unreachable users:** If the bot cannot initiate a private conversation (user hasn't started the bot), the user is marked as `unreachable` and won't receive prompts. The user must start the bot privately and use `/accept` (or the Accept button) to consent.

**Test Steps:**

1. **Test automatic prompt:**
   - Have a new user join a group where the bot is present
   - User should receive exactly one private DM from the bot with the consent notice

2. **Test one-shot policy:**
   - After receiving the first (and only) automatic prompt, the user will not receive any further automatic DMs
   - The `consent_prompt_count` is set to 1 after successful delivery

3. **Test unreachable handling:**
   - If the user hasn't started a private chat with the bot, the DM cannot be delivered
   - User is marked as `unreachable` in the system
   - To become reachable and consent, the user must start the bot privately first and use `/accept`

**Checklist:**
- [ ] Pending users receive exactly one automatic DM prompt when first seen in groups
- [ ] DM contains **inline buttons** (Accept/Decline) and `/accept`, `/decline`, `/consent` fallback commands
- [ ] Inline buttons work: Accept button sets consent to accepted, Decline button sets consent to declined
- [ ] Fallback commands remain usable alongside buttons
- [ ] One-shot policy enforced: only 1 automatic prompt per user (when `consent_prompt_count == 0`)
- [ ] No automatic retries after successful delivery or failure
- [ ] Unreachable users are marked appropriately and must start the bot privately to consent
- [ ] Runtime gate blocks normal usage for `pending`/`declined`/`unreachable` users
- [ ] Allowed commands work despite gate: `/accept`, `/decline`, `/consent`, `/start`
- [ ] `accepted` users can use all commands normally
- [ ] Owner bypass works for consent (owner can always use the bot)
- [ ] Global `ignore` role remains blocking regardless of consent

**Runtime Consent Gate:** The runtime gate is **now active**. Users with status `pending`, `declined`, or `unreachable` cannot use normal bot functions until they `/accept`.

**Allowed commands despite gate:** `/accept`, `/decline`, `/consent`, `/start` — these always work.

**Group behavior:** In groups, only a privacy-preserving notice is shown. No status details are revealed.

**Private block message:** Blocked users in private chats are told to use `/accept` or `/consent`. For `unreachable` users: Start the bot privately first, then `/accept`.

---

### Future Features (Not Yet Implemented)

The following features are planned for future releases and are **not available** in the current beta:

- Additional security enhancements — future blocks

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
- [ ] Private chat /consent: OK
- [ ] Private chat /accept: OK
- [ ] Private chat /decline: OK
- [ ] Private chat /role: OK
- [ ] Group test /ping: OK
- [ ] Group test /help: OK
- [ ] Role test /setrole normal: OK
- [ ] Role test /setrole vip: OK
- [ ] Role test restriction admin/owner: OK
- [ ] /ask test (optional): OK / Not tested
- [ ] AI auto-reply via mention in active scope (optional): OK / Not tested
- [ ] AI auto-reply via reply in active scope (optional): OK / Not tested
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
