# AMO Telegram Bot — Setup Guide

Complete setup instructions for running the bot locally.

---

## Prerequisites

- Python 3.12 or higher
- Linux or macOS development environment
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Optional: Local [Ollama](https://ollama.com/) instance for AI features

---

## Installation

### 1. Clone and Setup

```bash
git clone <repository-url>
cd AMO-telegram-bot
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment Configuration

Copy the example file and edit:

```bash
cp .env.example .env
```

Edit `.env` with your values:

```ini
# Required: Telegram
BOT_TOKEN=your_bot_token_here
BOT_USERNAME=your_bot_username

# Required: WebUI
WEBUI_PASSWORD=your_secure_password
WEBUI_OWNER_TELEGRAM_ID=your_telegram_user_id

# Optional: Ollama (for /ask command)
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_MAX_RESPONSE_CHARS=1500

# Optional: Database (defaults to SQLite)
DATABASE_URL=sqlite:///./data/amo_bot.db

# Optional: Plugin directory
AMO_PLUGIN_DIR=./plugins

# Optional: WebUI settings
WEBUI_HOST=127.0.0.1
WEBUI_PORT=8080
WEBUI_SESSION_TTL_SECONDS=3600

# Security settings (Block 1)
# WEBUI_PUBLIC_MODE=false
# WEBUI_REQUIRE_HTTPS=false
# WEBUI_SESSION_COOKIE_SECURE=

# Security settings (Block 2 - Login Protection)
# WEBUI_LOGIN_DELAY_BASE_SECONDS=0.25
# WEBUI_LOGIN_DELAY_MAX_SECONDS=2.0
```

> **Config Priority:** When starting locally, `.env` overrides shell environment variables. Set `AMO_ENV_OVERRIDE=0` to disable this behavior.

---

## Security Settings (Block 1 + Block 2)

The WebUI includes configurable security features:

## Environment Variables

### Block 1: Session Security

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBUI_PUBLIC_MODE` | `false` | Enable for public/internet-facing deployments. Enforces stricter security checks. |
| `WEBUI_REQUIRE_HTTPS` | `false` | Require HTTPS. Should be `true` when public. |
| `WEBUI_SESSION_COOKIE_SECURE` | *(auto)* | Override cookie Secure flag. Empty = auto (true if public OR require_https). |

### Block 2: Login Protection

| Variable | Default | Constraints | Description |
|----------|---------|-------------|-------------|
| `WEBUI_LOGIN_DELAY_BASE_SECONDS` | `0.25` | non-negative | Base delay after first failed login (seconds) |
| `WEBUI_LOGIN_DELAY_MAX_SECONDS` | `2.0` | non-negative, must be >= base | Maximum delay cap (seconds) |

---

## Security Headers

The WebUI sets the following HTTP security headers:

- **Content-Security-Policy (CSP):** Restricts resource loading
- **X-Frame-Options: DENY:** Prevents clickjacking
- **X-Content-Type-Options: nosniff:** Prevents MIME sniffing
- **Referrer-Policy: strict-origin-when-cross-origin:** Limits referrer leakage
- **Permissions-Policy:** Restricts browser features
- **HSTS:** Only in HTTPS/secure contexts

---

## Session Cookie Security

Session cookies use:
- **HttpOnly:** Prevents JavaScript access
- **SameSite=Lax:** CSRF protection
- **Secure:** Auto-enabled for public mode or HTTPS; override via `WEBUI_SESSION_COOKIE_SECURE`

---

## Login Protection (Block 2)

To prevent brute-force attacks, the WebUI implements **progressive delays** after failed login attempts (exponential backoff).

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBUI_LOGIN_DELAY_BASE_SECONDS` | `0.25` | Base delay after first failure (seconds) |
| `WEBUI_LOGIN_DELAY_MAX_SECONDS` | `2.0` | Maximum delay cap (seconds). Must be >= base. |

**Behavior:**
- Delay increases progressively after each failed login attempt (exponential backoff)
- Delay is capped at `WEBUI_LOGIN_DELAY_MAX_SECONDS`
- Successful login resets the counter
- Delays are per IP address (`remote_addr`)
- `LoginAttemptTracker` is in-memory per process with `max_keys` limit and oldest eviction
- Multi-process/shared state remains a future enhancement

**Audit Events:**
- `webui_login_failure` — Logged on failed login attempt
- `webui_login_success` — Logged on successful login

Both events include `remote_addr` only. No passwords or other sensitive data is logged.

> **Reverse Proxy Note:** When running behind a reverse proxy, ensure `remote_addr` is set correctly and trustworthily by your infrastructure. The WebUI uses `remote_addr` directly without parsing `X-Forwarded-For`. Never expose Flask directly to the public internet.

---

## Local Development Defaults

For local testing, keep defaults:

```ini
WEBUI_PUBLIC_MODE=false
WEBUI_REQUIRE_HTTPS=false
# WEBUI_SESSION_COOKIE_SECURE=  # leave empty for auto
```

---

## Production/Internet Deployment

**⚠️ Warning:** Do not expose Flask directly to the internet. Use a reverse proxy (nginx, Caddy, Traefik) with HTTPS.

Recommended production configuration:

```ini
WEBUI_PUBLIC_MODE=true
WEBUI_REQUIRE_HTTPS=true
# WEBUI_SESSION_COOKIE_SECURE=  # auto-enabled
```

The WebUI will fail fast with a clear error if an unsafe configuration is detected in public mode.

---

## Running the Bot

### Bot Only (Polling)

```bash
source venv/bin/activate
python main.py
```

### WebUI Only

```bash
source venv/bin/activate
python main.py --webui
```

### Bot + WebUI Together

```bash
source venv/bin/activate
python main.py
```

---

## Telegram Bot Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Create a new bot: `/newbot`
3. Copy the token provided
4. Set your bot username in `.env`

---

## Preflight Tests

Before connecting to real Telegram APIs:

```bash
source venv/bin/activate
pytest -q
python -m amo_bot.smoke
```

Expected results:
- pytest: All tests pass
- smoke: Bootstrap and basic commands OK

---

## Troubleshooting

### Bot does not respond
- Check terminal: Is `python main.py` running?
- Verify `.env`: Is `BOT_TOKEN` correct?
- Check Telegram: Did you click "Start" in the bot chat?

### Database/SQLite errors
- Does the `data/` directory exist?
- Are write permissions available?
- For testing only: `rm data/amo_bot.db` and restart

### Ollama not reachable
- Is Ollama running? `curl http://127.0.0.1:11434/api/tags`
- Is the URL in `.env` correct?
- Firewall blocking port 11434?

### WebUI login fails
- Is `WEBUI_PASSWORD` set in `.env`?
- Is the value not empty or "change_me"?
- Are you accessing `http://127.0.0.1:8080`?

---

## WebUI: KI Topic-Agent Status (Read-Only)

The WebUI Dashboard displays the current KI Topic-Agent configuration status:

- **Scope:** Shows whether the configuration applies to a topic (`topic`) or private chat (`private`)
- **Chat ID:** Telegram chat identifier (for topic scopes)
- **Topic ID:** Topic/thread identifier within the chat (for topic scopes)
- **User ID:** User identifier (for private scopes)
- **AI Status:** Shows `active` or `inactive` — whether AI auto-reply is enabled for this scope
- **Response Mode:** Current response mode (e.g., `command` for explicit commands only, or other configured modes)

This is a **read-only** view. Editing topic-agent configuration via WebUI requires future implementation (KI-F2).

---

## Next Steps

- See [BETATEST_EN.md](BETATEST_EN.md) for detailed testing instructions
- See [RELEASE_NOTES_2026.05.09-Beta_EN.md](RELEASE_NOTES_2026.05.09-Beta_EN.md) for changelog

## WebUI Security — Access Window (Block 3)

The WebUI access can be controlled via Telegram commands. This allows the owner to open or close access to the WebUI from anywhere.

### Telegram Commands

| Command | Description | Requirements |
|---------|-------------|--------------|
| `/webui status` | Shows whether the WebUI access window is OPEN or CLOSED, and the remaining time if open | Private chat, Owner only |
| `/webui on` | Opens the WebUI access window for 60 minutes (extends if already open) | Private chat, Owner only |
| `/webui off` | Closes the WebUI access window immediately | Private chat, Owner only |

**Important:** These commands only work in **private chats** (not in groups) and only for the **owner**.

### Access Denied Reasons

If access is denied, an audit event is logged with one of these reasons:
- `not_private` — Command was used in a group or channel
- `not_owner` — User is not the configured owner

### Audit Events

The following audit events are generated:

| Event | Description |
|-------|-------------|
| `webui_access_enabled` | WebUI access window opened via `/webui on` |
| `webui_access_disabled` | WebUI access window closed via `/webui off` |
| `webui_access_status` | Status checked via `/webui status` |
| `webui_access_denied` | Access denied (wrong chat type or unauthorized user) |

### Status Information

When running `/webui status`, you receive:
- **OPEN** with remaining minutes if the access window is active
- **CLOSED** if no access window is currently open

The access window is stored persistently in the database, so it survives bot restarts.

---

## WebUI Security — HTTP Request Gate (Block 3C)

When `WEBUI_PUBLIC_MODE=true`, the WebUI uses an **HTTP Request Gate** that blocks access to protected pages when the access window is closed.

### How It Works

| Scenario | Behavior |
|----------|----------|
| `WEBUI_PUBLIC_MODE=false` | Gate is inactive; local/LAN usage unchanged |
| `WEBUI_PUBLIC_MODE=true` + Access Window **closed** | `/login` and protected pages return **403 Forbidden** |
| `WEBUI_PUBLIC_MODE=true` + Access Window **open** | Normal password login works; access granted |

### Whitelisted Paths

The following paths are always accessible (gate does not block):
- `/health` — Health check endpoint
- `/static/*` — Static assets (CSS, JS, images)
- `/logout` — Logout endpoint

### 403 Responses

When access is blocked, the gate returns:

**HTML/Plain text requests:**
```
403 Forbidden
```

**JSON/API requests:**
```json
{"error":"forbidden","status":403}
```

### Configuration

```ini
# Enable public mode to activate the gate
WEBUI_PUBLIC_MODE=true

# Access window controlled via Telegram commands:
# /webui on  - opens the window for 60 minutes
# /webui off - closes the window immediately
# /webui status - shows current state
```

> **Note:** When the access window is open, normal password authentication is still required. The gate only controls *whether* the login page is reachable, not the login itself.

---

## WebUI: Group Role Management

After logging in, group roles can be managed under "Groups":

- View users and their current roles
- Set roles: `admin`, `vip`, `normal`, `ignore`
- `owner` cannot be assigned as a group role (only via `.env`)
- `normal` removes the group-scoped entry → fallback to `normal`
- Roles are group-scoped, not global

**Note on user list:** The WebUI only shows users the bot has seen in that group. Existing role assignments remain visible and are marked `[assigned/not seen]` if the user has not yet been active in the group.
