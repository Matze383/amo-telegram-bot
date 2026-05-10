# Release 2026.05.09-Beta

[Deutsche Version](RELEASE_NOTES_2026.05.09-Beta_DE.md)

### Summary

This Beta release brings the AMO Telegram Bot to MVP status. The bot is ready for limited testing with a focus on core functionality: role-based commands, local Ollama integration, a lightweight WebUI, and a plugin runtime foundation. **Not for production use.**

---

### Highlights

- **Simple Start**: `pip install -r requirements.txt`, then `python main.py`
- **Unified Launch**: Bot + WebUI now run together via `--serve`
- **Live Tested**: Both WebUI and Bot have been verified in real usage
- **Topic Aware**: Users, groups, and topics are recognized including topic names; replies stay in the correct topic
- **Ollama Integration**: `/ask` command works with local Ollama for AI responses
- **Plugin Runtime MVP**: Supports Command, Scheduled, and Worker runtimes plus WebUI management interface
- **Owner Bootstrap**: Automatic owner setup and schema drift fixes
- **Token Redaction**: Sensitive tokens are redacted from logs

---

### Beta Test Setup

1. **Clone and setup:**
   ```bash
   python3.12 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   ```

2. **Configure `.env`:**
   - `BOT_TOKEN` – Your Telegram bot token from @BotFather
   - `BOT_USERNAME` – Your bot's username
   - `WEBUI_PASSWORD` – Secure local password
   - `WEBUI_OWNER_TELEGRAM_ID` – Your Telegram user ID

3. **Start:**
   ```bash
   python main.py
   ```

   This launches both the bot (polling) and WebUI on `http://127.0.0.1:8080`.

---

### Verified Working

Tested and confirmed functional:

| Feature | Status |
|---------|--------|
| `/ping` in private chats | ✅ Working |
| `/ping` in groups | ✅ Working |
| `/help` with role-based output | ✅ Working |
| `/role` self-check | ✅ Working |
| `/setrole` with permission checks | ✅ Working |
| `/ask` with Ollama | ✅ Working |
| Topic recognition & replies | ✅ Working |
| WebUI login & session management | ✅ Working |
| WebUI plugin management | ✅ Working |
| Offset persistence | ✅ Working |

---

### Security Notes

- **Local WebUI only**: Binds to `127.0.0.1` by default – do not expose to the internet
- **Token redaction**: Bot tokens and sensitive values are automatically redacted in logs
- **Role-based access**: Owner/Admin/VIP/Normal/Ignore roles with proper permission checks
- **No secrets in repo**: `.env` is gitignored; example file shows structure only

---

### Known Limitations / Not Production

- **MVP status**: This is a beta release, not production-ready
- **Local Ollama only**: No cloud AI integration
- **Stateless `/ask`**: No conversation history
- **SQLite only**: No PostgreSQL or other database support yet
- **Simple owner login**: The WebUI MVP is designed around a simple owner login flow
- **No channels**: Private chats and groups only
- **No media**: Text messages only
- **Manual plugin install**: Plugins must be placed in `AMO_PLUGIN_DIR` manually

---

### Checklist for Testers

- [ ] Setup complete (venv, dependencies, .env configured)
- [ ] Bot starts without errors
- [ ] WebUI accessible at `http://127.0.0.1:8080`
- [ ] Private chat `/ping` responds
- [ ] Group chat commands work
- [ ] Role management (`/setrole`) respects permissions
- [ ] `/ask` returns AI responses (if Ollama configured)
- [ ] WebUI plugin list loads
- [ ] No sensitive tokens in logs

---

### Upgrade / Start Notes

**Fresh start:**
```bash
python main.py
```

**With cleanup (removes database):**
```bash
rm data/amo_bot.db
python main.py
```

The bot will auto-bootstrap the database schema on first run.

---

---
