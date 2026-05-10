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
```

> **Config Priority:** When starting locally, `.env` overrides shell environment variables. Set `AMO_ENV_OVERRIDE=0` to disable this behavior.

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

## Next Steps

- See [BETATEST_EN.md](BETATEST_EN.md) for detailed testing instructions
- See [RELEASE_NOTES_2026.05.09-Beta_EN.md](RELEASE_NOTES_2026.05.09-Beta_EN.md) for changelog

## WebUI: Group Role Management

After logging in, group roles can be managed under "Groups":

- View users and their current roles
- Set roles: `admin`, `vip`, `normal`, `ignore`
- `owner` cannot be assigned as a group role (only via `.env`)
- `normal` removes the group-scoped entry → fallback to `normal`
- Roles are group-scoped, not global
