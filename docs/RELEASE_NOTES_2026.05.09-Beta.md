# Release 2026.05.09-Beta

## English

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

## Deutsch

### Zusammenfassung

Dieses Beta-Release bringt den AMO Telegram Bot auf MVP-Status. Der Bot ist bereit für begrenztes Testing mit Fokus auf Kernfunktionalität: rollenbasierte Commands, lokale Ollama-Integration, eine leichtgewichtige WebUI und eine Plugin-Runtime-Basis. **Nicht für Produktivnutzung.**

---

### Highlights

- **Einfacher Start**: `pip install -r requirements.txt`, dann `python main.py`
- **Vereinigter Start**: Bot + WebUI laufen nun gemeinsam via `--serve`
- **Live getestet**: WebUI und Bot wurden im echten Betrieb verifiziert
- **Topic-Awareness**: Nutzer, Gruppen und Topics werden erkannt inkl. Topic-Namen; Antworten bleiben im richtigen Topic
- **Ollama-Integration**: `/ask`-Command funktioniert mit lokalem Ollama für KI-Antworten
- **Plugin-Runtime MVP**: Unterstützt Command-, Scheduled- und Worker-Runtimes plus WebUI-Betriebsoberfläche
- **Owner-Bootstrap**: Automatisches Owner-Setup und Schema-Drift-Fixes
- **Token-Redaction**: Sensitive Tokens werden aus Logs entfernt

---

### Betatest-Setup

1. **Klonen und Setup:**
   ```bash
   python3.12 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   ```

2. **`.env` konfigurieren:**
   - `BOT_TOKEN` – Dein Telegram-Bot-Token von @BotFather
   - `BOT_USERNAME` – Username deines Bots
   - `WEBUI_PASSWORD` – Sicheres lokales Passwort
   - `WEBUI_OWNER_TELEGRAM_ID` – Deine Telegram-User-ID

3. **Starten:**
   ```bash
   python main.py
   ```

   Startet sowohl den Bot (Polling) als auch die WebUI auf `http://127.0.0.1:8080`.

---

### Live Bestätigt Funktionsfähig

Getestet und funktional bestätigt:

| Feature | Status |
|---------|--------|
| `/ping` im privaten Chat | ✅ Funktioniert |
| `/ping` in Gruppen | ✅ Funktioniert |
| `/help` mit rollenbasierter Ausgabe | ✅ Funktioniert |
| `/role` Selbstcheck | ✅ Funktioniert |
| `/setrole` mit Berechtigungsprüfung | ✅ Funktioniert |
| `/ask` mit Ollama | ✅ Funktioniert |
| Topic-Erkennung & Antworten | ✅ Funktioniert |
| WebUI Login & Session-Management | ✅ Funktioniert |
| WebUI Plugin-Verwaltung | ✅ Funktioniert |
| Offset-Persistenz | ✅ Funktioniert |

---

### Security-Hinweise

- **Nur lokale WebUI**: Bindet standardmäßig an `127.0.0.1` – nicht ins Internet freigeben
- **Token-Redaction**: Bot-Tokens und sensitive Werte werden automatisch in Logs maskiert
- **Rollenbasierte Zugriffe**: Owner/Admin/VIP/Normal/Ignore-Rollen mit korrekten Berechtigungsprüfungen
- **Keine Secrets im Repo**: `.env` ist gitignored; Beispieldatei zeigt nur Struktur

---

### Bekannte Einschränkungen / Nicht Produktiv

- **MVP-Status**: Dies ist ein Beta-Release, nicht produktionsreif
- **Nur lokales Ollama**: Keine Cloud-AI-Integration
- **Stateless `/ask`**: Kein Gesprächsverlauf
- **Nur SQLite**: Noch kein PostgreSQL oder andere Datenbank-Unterstützung
- **Einfacher Owner-Login**: Das WebUI-MVP ist auf einen einfachen Owner-Login-Flow ausgelegt
- **Keine Kanäle**: Nur private Chats und Gruppen
- **Keine Medien**: Nur Textnachrichten
- **Manuelle Plugin-Installation**: Plugins müssen manuell in `AMO_PLUGIN_DIR` platziert werden

---

### Checkliste für Tester

- [ ] Setup abgeschlossen (venv, Dependencies, .env konfiguriert)
- [ ] Bot startet ohne Fehler
- [ ] WebUI erreichbar unter `http://127.0.0.1:8080`
- [ ] Privater Chat `/ping` antwortet
- [ ] Gruppen-Commands funktionieren
- [ ] Rollenverwaltung (`/setrole`) respektiert Berechtigungen
- [ ] `/ask` liefert KI-Antworten (falls Ollama konfiguriert)
- [ ] WebUI Plugin-Liste lädt
- [ ] Keine sensiblen Tokens in Logs

---

### Upgrade / Start-Hinweise

**Neustart:**
```bash
python main.py
```

**Mit Cleanup (löscht Datenbank):**
```bash
rm data/amo_bot.db
python main.py
```

Der Bot bootstrapped das Datenbank-Schema beim ersten Start automatisch.
