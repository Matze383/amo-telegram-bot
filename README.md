# AMO Telegram Bot

> **DE:** Ein modularer, rollenbasierter Telegram-Bot mit Plugin-Unterstützung, WebUI-Verwaltung und optionaler KI-Integration.
> **EN:** A modular, role-based Telegram bot with plugin support, WebUI management, and optional AI integration.

---

## Deutsch 🇩🇪

### Übersicht

AMO ist ein erweiterbarer Telegram-Bot für Gruppen und private Chats. Er bietet ein rollenbasiertes Berechtigungssystem, eine lokale WebUI zur Verwaltung und optionale KI-Funktionen über Ollama.

**Status:** Beta / MVP — Nicht für den Produktivbetrieb geeignet.

### Unterstützte Plattformen

- ✅ Linux
- ✅ macOS
- ✅ Windows (PowerShell / Eingabeaufforderung)

### Funktionen

| Feature | Beschreibung |
|---------|--------------|
| 🤖 **Modularer Aufbau** | Plugin-System für eigene Erweiterungen |
| 🔐 **Rollen-System** | Owner, Admin, VIP, Normal, Ignore — gruppenspezifisch und berechtigungsbasiert |
| ✅ **Consent-Management** | Nutzer müssen explizit zustimmen, bevor der Bot aktiv wird |
| 🌐 **WebUI** | Lokale Flask-Oberfläche für Verwaltung und Konfiguration |
| 🤖 **KI-Integration** | Optionales `/ask`-Kommando und Auto-Antworten via Ollama |
| 🧠 **Memory-System** | Tägliche Langzeitgedächtnis-Kuratierung mit Datenschutz-Defaults |

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

### Dokumentation

| Dokument | Inhalt |
|----------|--------|
| [📗 Setup-Anleitung (DE)](docs/SETUP_DE.md) | Vollständige Installation und Konfiguration |
| [📘 Setup Guide (EN)](docs/SETUP_EN.md) | English setup guide |
| [🧪 Beta-Test (DE)](docs/BETATEST_DE.md) | Schritt-für-Schritt Testanleitung |
| [🧪 Beta Test (EN)](docs/BETATEST_EN.md) | Step-by-step testing guide |
| [📝 Release Notes](docs/) | Changelogs und Versionshistorie |
| [🚀 Release Baseline](docs/release-baseline.md) | Support-Matrix und Release-Status |
| [🗺️ Roadmap](ROADMAP.md) | Projekt-Richtung und geplante Features |

> **Hinweis:** Detaillierte Installationsanleitungen für alle Plattformen siehe [SETUP_DE.md](docs/SETUP_DE.md) (Deutsch) oder [SETUP_EN.md](docs/SETUP_EN.md) (Englisch).

---

## English 🇬🇧

### Overview

AMO is an extensible Telegram bot for groups and private chats. It provides a role-based permission system, a local WebUI for management, and optional AI features via Ollama.

**Status:** Beta / MVP — Not production-ready.

### Supported Platforms

- ✅ Linux
- ✅ macOS
- ✅ Windows (PowerShell / Command Prompt)

### Features

| Feature | Description |
|---------|-------------|
| 🤖 **Modular Design** | Plugin system for custom extensions |
| 🔐 **Role System** | Owner, Admin, VIP, Normal, Ignore — group-scoped and permission-based |
| ✅ **Consent Management** | Users must explicitly opt-in before the bot becomes active |
| 🌐 **WebUI** | Local Flask interface for management and configuration |
| 🤖 **AI Integration** | Optional `/ask` command and auto-replies via Ollama |
| 🧠 **Memory System** | Daily long-term memory curation with privacy-first defaults |

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

### Documentation

| Document | Content |
|----------|---------|
| [📘 Setup Guide (EN)](docs/SETUP_EN.md) | Complete installation and configuration |
| [📗 Setup-Anleitung (DE)](docs/SETUP_DE.md) | German setup guide |
| [🧪 Beta Test (EN)](docs/BETATEST_EN.md) | Step-by-step testing guide |
| [🧪 Beta-Test (DE)](docs/BETATEST_DE.md) | German testing guide |
| [📝 Release Notes](docs/) | Changelogs and version history |
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

**EN:**
- WebUI is **local-only** (`127.0.0.1`) — never expose to the internet
- Never share `BOT_TOKEN` in chats, logs, or repositories
- `.env` is in `.gitignore` — never commit secrets
- For public deployments: Use a reverse proxy (nginx, Caddy) with HTTPS

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
| RR-08        | ⏳ Planned — Contribution Guide |

---

## 📄 License / Lizenz

MIT License — see [LICENSE](LICENSE) for details.
MIT License — siehe [LICENSE](LICENSE) für Details.

---

<p align="center">
  <sub>AMO Telegram Bot — Beta / MVP</sub>
</p>
