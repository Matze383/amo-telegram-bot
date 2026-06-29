# AMO Telegram Bot

> **DE:** Ein modularer, rollenbasierter Telegram-Bot mit Plugin-Unterstützung, WebUI-Verwaltung, optionaler KI-Integration — jetzt mit erweitertem Speicher-Management und sicherem Plugin-Sandboxing.
> **EN:** A modular, role-based Telegram bot with plugin support, WebUI management, optional AI integration — now with extended memory management and secure plugin sandboxing.

---

## Sprachauswahl / Language Selection

| Sprache | Verfügbare Dokumente |
|---------|---------------------|
| 🇩🇪 Deutsch | Diese Seite, [Setup-Anleitung](docs/SETUP_DE.md), [Testanleitung](docs/BETATEST_DE.md) |
| 🇬🇧 English | This page, [Setup Guide](docs/SETUP_EN.md), [Test Guide](docs/BETATEST_EN.md) |

---

## Deutsch 🇩🇪

### Übersicht

AMO ist ein erweiterbarer Telegram-Bot für Gruppen und private Chats. Er bietet ein rollenbasiertes Berechtigungssystem, eine lokale WebUI zur Verwaltung und optionale KI-Funktionen über Ollama oder OpenAI.

**Status:** MVP Complete / Stable — Funktionsumfang ist stabil; Produktions-Härtung (Load, Security-Audit) nicht abgeschlossen.

### Unterstützte Plattformen

- ✅ Linux
- ✅ macOS
- ✅ Windows (PowerShell / Eingabeaufforderung)

### Funktionen

| Feature | Beschreibung |
|---------|--------------|
| 🤖 **Modularer Aufbau** | Plugin-System für eigene Erweiterungen |
| 🔐 **Rollen-System** | Owner, Admin, VIP, Normal, Ignore — gruppenspezifisch und berechtigungsbasiert |
| ✅ **Rollenbasierte Rechte** | Automatische Nutzung für Human-User, Rollen (owner/admin/vip/normal/ignore) steuern Berechtigungen. Bot-to-Bot-Kommunikation erfordert explizite Freigabe |
| 🌐 **WebUI** | Lokale Flask-Oberfläche für Verwaltung und Konfiguration |
| 🤖 **KI-Integration** | Optionales `/ask`-Kommando mit gescopten Sessions, `/new` und `/reset` für Session-Management, Auto-Antworten via Ollama, OpenAI, Anthropic, Google, OpenRouter, Groq, Mistral, xAI, DeepSeek, Together AI, Fireworks AI, Amazon Bedrock, LiteLLM, LM Studio, vLLM oder SGLang |
| 🧠 **Memory-System** | Tägliche Langzeitgedächtnis-Kuratierung mit Datenschutz-Defaults; **Neu:** Scoped Memory mit C2-Review-Service für verbesserte Datenschutzkontrolle; **Neu:** Scoped User Profile Memory mit Scope-Isolation (keine Cross-Scope-Leaks, nur aktuelle Teilnehmer) |
| 🖼️ **Bildanalyse & -sendung** | Image-Analysis-Interface (IMG) mit send_photo/send_document Wrappern; WebUI pro-Topic Bilderkennungs-Toggle (inherit/enabled/disabled); WebUI Rollen-Quotas für Bildanalyse (IMG-B7); Runtime Quota-Enforcement mit Rolling-24h-Fenster (IMG-B8); **Neu:** Follow-up Bildanalyse für Kontext-Fortführung |
| 🔍 **Webtool-Quotas** | Rollenbasierte Nutzungsquotas für Websearch/Webscraping über `/webtoolquota` und WebUI; Metadata-only Audit-Logging (keine Queries/URLs/Prompts) |
| 🔒 **Sandbox-Runtime** | **Neu:** Plugin-Ausführung über Sandbox-Worker mit Capability-Gating für Commands, Scheduled- und Worker-Plugins (Command/Scheduled/Worker-Runtime isoliert) |
| 📺 **YT-RSS Plugin** | YouTube-Kanal-Abos via RSS mit verbesserter Handle/Channel-ID-Auflösung und robusterem Scheduler |

### Schnellstart

**Linux / macOS:**

```bash
# 1. Repository klonen
git clone <repository-url>
cd AMO-telegram-bot

# 2. Virtuelle Umgebung erstellen
python3.12 -m venv venv
source venv/bin/activate

# 3. Abhängigkeiten installieren
pip install -r requirements.txt

# 4. Konfiguration anlegen
cp .env.example .env
# .env mit eigenen Werten bearbeiten (siehe Dokumentation)

# 5. Bot starten
python main.py
```

**Windows (PowerShell):**

```powershell
# 1. Repository klonen
git clone <repository-url>
cd AMO-telegram-bot

# 2. Virtuelle Umgebung erstellen
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Abhängigkeiten installieren
pip install -r requirements.txt

# 4. Konfiguration anlegen
copy .env.example .env
# .env mit eigenen Werten bearbeiten (siehe Dokumentation)

# 5. Bot starten
python main.py
```

**Voraussetzungen:**
- Python 3.12+
- Windows, macOS oder Linux
- Telegram Bot Token von [@BotFather](https://t.me/BotFather)
- Optional: Lokale [Ollama](https://ollama.com/)-Instanz, OpenAI API-Key, Anthropic API-Key, Google/Gemini API-Key, OpenRouter API-Key, [Groq](https://groq.com/) API-Key, Mistral API-Key, xAI API-Key, DeepSeek API-Key, Together API-Key, Fireworks API-Key, AWS-Credentials für Amazon Bedrock, LM Studio, vLLM oder SGLang für KI-Funktionen

### Start-Modi / Runtime-Modi

Der normale Bot-Start nutzt die Multi-Prozess Queue-Runtime mit Worker-Supervisor.

| Modus | Beschreibung | Standard |
|-------|--------------|----------|
| **Queue** | Multi-Prozess Queue-Runtime mit Worker-Supervisor | ✅ Standard |

**Queue-Modus (Standard):**
```bash
venv/bin/python -m amo_bot.main
venv/bin/python -m amo_bot.main --serve
```

**Umgebungsvariablen:**
- `AMO_TELEGRAM_QUEUE_IDLE_SLEEP_SECONDS` — Pausenzeit für Idle-Worker

**Queue-Modus Einschränkungen:**
- Nur Text/Markup Outbox-Pfad (keine `send_photo`/`send_document` im Queue-Worker)
- Kein Live-Telegram-Restart (neuer Start erforderlich)

### Dokumentation

| Dokument | Inhalt |
|----------|--------|
| [📗 Setup-Anleitung (DE)](docs/SETUP_DE.md) | Vollständige Installation und Konfiguration |
| [📘 Setup Guide (EN)](docs/SETUP_EN.md) | English setup guide |
| [🧪 Testanleitung (DE)](docs/BETATEST_DE.md) | Schritt-für-Schritt Testanleitung |
| [🧪 Test Guide (EN)](docs/BETATEST_EN.md) | Step-by-step testing guide |
| [🔌 Userplugin Guide](docs/USERPLUGINS.md) | Plugin-Entwicklung mit Do/Don't-Regeln (bilingual) |
| [📺 YT-RSS Plugin (DE/EN)](docs/YT-RSS.md) | YouTube-Kanal-RSS-Abos / YouTube channel RSS subscriptions (topic-scoped) |
| [📝 Changelog (DE/EN)](CHANGELOG.md) | Änderungsprotokoll / Changelog (bilingual) |
| [🚀 Release Baseline](docs/release-baseline.md) | Support-Matrix und Release-Status |
| [🗺️ Roadmap](ROADMAP.md) | Projekt-Richtung und geplante Features |

> **Hinweis:** Detaillierte Installationsanleitungen für alle Plattformen siehe [SETUP_DE.md](docs/SETUP_DE.md) (Deutsch) oder [SETUP_EN.md](docs/SETUP_EN.md) (Englisch).

---

## English 🇬🇧

### Overview

AMO is an extensible Telegram bot for groups and private chats. It provides a role-based permission system, a local WebUI for management, and optional AI features via Ollama or OpenAI.

**Status:** MVP Complete / Stable — Feature set is stable; production hardening (load, security audit) not complete.

### Supported Platforms

- ✅ Linux
- ✅ macOS
- ✅ Windows (PowerShell / Command Prompt)

### Features

| Feature | Description |
|---------|-------------|
| 🤖 **Modular Design** | Plugin system for custom extensions |
| 🔐 **Role System** | Owner, Admin, VIP, Normal, Ignore — group-scoped and permission-based |
| ✅ **Role-based Permissions** | Automatic usage for human users; roles (owner/admin/vip/normal/ignore) control permissions. Bot-to-bot communication requires explicit approval |
| 🌐 **WebUI** | Local Flask interface for management and configuration |
| 🤖 **AI Integration** | Optional `/ask` command with scoped sessions, `/new` and `/reset` for session management, auto-replies via Ollama, OpenAI, Anthropic, Google, OpenRouter, Groq, Mistral, xAI, DeepSeek, Together AI, Fireworks AI, Amazon Bedrock, LiteLLM, LM Studio, vLLM, or SGLang |
| 🧠 **Memory System** | Daily long-term memory curation with privacy-first defaults; **New:** Scoped memory with C2 review service for enhanced privacy control; **New:** Scoped user profile memory with scope isolation (no cross-scope leaks, current participants only) |
| 🖼️ **Image Analysis & Sending** | Image Analysis interface (IMG) with send_photo/send_document wrappers; WebUI per-topic image recognition toggle (inherit/enabled/disabled); WebUI role quotas for image analysis (IMG-B7); Runtime quota enforcement with rolling 24h window (IMG-B8); **New:** Follow-up image analysis for context continuation |
| 🔍 **Webtool Quotas** | Role-based usage quotas for websearch/webscraping via `/webtoolquota` and WebUI; metadata-only audit logging (no queries/URLs/prompts) |
| 🔒 **Sandbox Runtime** | **New:** Plugin execution via sandbox workers with capability gating for commands, scheduled, and worker plugins (command/scheduled/worker runtime isolated) |
| 📺 **YT-RSS Plugin** | YouTube channel subscriptions via RSS with improved handle/channel ID resolution and more robust scheduler |

### Quick Start

**Linux / macOS:**

```bash
# 1. Clone repository
git clone <repository-url>
cd AMO-telegram-bot

# 2. Create virtual environment
python3.12 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your values (see documentation)

# 5. Start the bot
python main.py
```

**Windows (PowerShell):**

```powershell
# 1. Clone repository
git clone <repository-url>
cd AMO-telegram-bot

# 2. Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env
# Edit .env with your values (see documentation)

# 5. Start the bot
python main.py
```

**Requirements:**
- Python 3.12+
- Windows, macOS or Linux
- Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- Optional: Local [Ollama](https://ollama.com/) instance, OpenAI API key, Anthropic API key, Google/Gemini API key, OpenRouter API key, [Groq](https://groq.com/) API key, Mistral API key, xAI API key, DeepSeek API key, Together API key, Fireworks API key, AWS credentials for Amazon Bedrock, LM Studio, vLLM, or SGLang for AI features

### Runtime Modes

The regular bot start uses the multi-process queue runtime with worker supervisor.

| Mode | Description | Default |
|------|-------------|---------|
| **Queue** | Multi-process queue runtime with worker supervisor | ✅ Default |

**Queue mode (Default):**
```bash
venv/bin/python -m amo_bot.main
venv/bin/python -m amo_bot.main --serve
```

**Environment variables:**
- `AMO_TELEGRAM_QUEUE_IDLE_SLEEP_SECONDS` — Idle sleep time for workers

**Queue mode limitations:**
- Text/markup outbox path only (no `send_photo`/`send_document` in queue worker)
- No live Telegram restart (new start required)

### Documentation

| Document | Content |
|----------|---------|
| [📘 Setup Guide (EN)](docs/SETUP_EN.md) | Complete installation and configuration |
| [📗 Setup-Anleitung (DE)](docs/SETUP_DE.md) | German setup guide |
| [🧪 Test Guide (EN)](docs/BETATEST_EN.md) | Step-by-step testing guide |
| [🧪 Testanleitung (DE)](docs/BETATEST_DE.md) | German testing guide |
| [🔌 Userplugin Guide](docs/USERPLUGINS.md) | Plugin development with Do/Don't rules (bilingual) |
| [📺 YT-RSS Plugin (DE/EN)](docs/YT-RSS.md) | YouTube-Kanal-RSS-Abos / YouTube channel RSS subscriptions (topic-scoped) |
| [📝 Changelog (DE/EN)](CHANGELOG.md) | Changelog and version history (bilingual) |
| [🚀 Release Baseline](docs/release-baseline.md) | Support matrix and release status |
| [🗺️ Roadmap](ROADMAP.md) | Project direction and planned features |

> **Note:** Detailed platform-specific setup instructions see [SETUP_EN.md](docs/SETUP_EN.md) (English) or [SETUP_DE.md](docs/SETUP_DE.md) (German).

---

## 🔒 Security Notes / Sicherheitshinweise

**DE:**
- WebUI ist **lokal only** (`127.0.0.1`) — niemals ins Internet freigeben
- `BOT_TOKEN` niemals in Chats, Logs oder Repositories teilen
- `.env` ist in `.gitignore` — Secrets niemals committen
- Für öffentliche Deployments: Reverse Proxy (nginx, Caddy) mit HTTPS verwenden
- **Sandbox-Runtime:** Commands, Scheduled- und Worker-Plugins laufen isoliert mit Capability-Prüfung
- **Memory-C2-Review:** Scoped Memory mit internem C2-Review-Service (Foundation für zukünftige Datenschutz-Workflows)
- **Scoped User Profile Memory:** Profil-Daten sind strikt scope-gebunden; keine Cross-Scope-Leaks; nur aktuelle Scope-Teilnehmer als Memory-Kandidaten
- **Metadata-only Logging:** Audit-Logs enthalten keine Queries, URLs, Prompt-/Nachrichtentexte, Secrets, Tokens oder Memory-Inhalte (Webtools, Bildanalyse)

**EN:**
- WebUI is **local-only** (`127.0.0.1`) — never expose to the internet
- Never share `BOT_TOKEN` in chats, logs, or repositories
- `.env` is in `.gitignore` — never commit secrets
- For public deployments: Use a reverse proxy (nginx, Caddy) with HTTPS
- **Sandbox Runtime:** Commands, scheduled, and worker plugins run isolated with capability checking
- **Memory C2 Review:** Scoped memory with internal C2 review service (foundation for future privacy workflows)
- **Scoped User Profile Memory:** Profile data is strictly scope-bound; no cross-scope leaks; only current scope participants as memory candidates
- **Metadata-only logging:** Audit logs contain no queries, URLs, prompt/message text, secrets, tokens, or memory content (webtools, image analysis)

---

## 🤝 Contributing / Mitwirken

**EN:** Contributions are welcome. Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on:
- Local development setup
- Branch and commit conventions
- Pull request process
- Code style and tests

**DE:** Mitwirkungen sind willkommen. Siehe [CONTRIBUTING.md](CONTRIBUTING.md) für:
- Lokale Entwicklungsumgebung
- Branch- und Commit-Konventionen
- Pull-Request-Prozess
- Code-Stil und Tests

### Weitere Ressourcen

| Dokument | Inhalt |
|----------|--------|
| [SECURITY.md](SECURITY.md) | Verantwortungsvolle Meldung von Sicherheitsproblemen |
| [SUPPORT.md](SUPPORT.md) | Hilfe und Support-Informationen |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Gemeinschaftsrichtlinien |

---

## 📋 Roadmap

| Milestone | Status |
|-----------|--------|
| RR-01..RR-05 | ✅ Complete — Core Bot, WebUI, Consent, Security, AI Features |
| RR-06        | ✅ Complete — README Polish |
| RR-07        | ✅ Complete — Cross-Platform Setup Docs (Windows, macOS, Linux) |
| RR-08        | ✅ Complete — Contribution Guide |
| RR-09..RR-15 | ⚠️ Partial — Templates, Security Docs, Roadmap, CI, Release Notes vorhanden; RR-13 Cross-Platform Smoke Tests ausstehend (nur Linux validiert) |

---

## 📄 License / Lizenz

MIT License — see [LICENSE](LICENSE) for details.
MIT License — siehe [LICENSE](LICENSE) für Details.

---

<p align="center">
  <sub>AMO Telegram Bot — MVP Complete / Stable</sub>
</p>

LiteLLM provider (GH39): AI_PROVIDER=litellm with LITELLM_API_KEY, LITELLM_MODEL, LITELLM_BASE_URL, LITELLM_TIMEOUT_SECONDS.
Fireworks provider (GH38): AI_PROVIDER=fireworks with FIREWORKS_API_KEY, FIREWORKS_MODEL, FIREWORKS_BASE_URL, FIREWORKS_TIMEOUT_SECONDS.
