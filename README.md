# AMO Telegram Bot

> **EN:** A modular, role-based Telegram bot with plugin support, WebUI management, and optional Ollama AI integration.  
> **DE:** Ein modularer, rollenbasierter Telegram-Bot mit Plugin-Unterstützung, WebUI-Verwaltung und optionaler Ollama-KI-Integration.

---

## Status

**Beta / MVP — Private Beta — Not Production-Ready**

This project is in early development. Features may change, and security hardening is not complete. Do not use in production environments.

Dieses Projekt befindet sich in der frühen Entwicklung. Funktionen können sich ändern, und die Sicherheitshärtung ist nicht vollständig. Nicht für den Produktivbetrieb verwenden.

---

## Features

| Feature | EN | DE |
|---------|-----|-----|
| 🤖 **Telegram Bot** | Long polling, custom API integration (no external bot library) | Long Polling, eigene API-Integration (ohne externe Bot-Bibliothek) |
| 🔐 **Role System** | Owner, Admin, VIP, Normal, Ignore — group-scoped and permission-based | Owner, Admin, VIP, Normal, Ignore — gruppenspezifisch und berechtigungsbasiert |
| ✅ **Consent Management** | `/accept`, `/decline`, `/consent` commands for user consent; automatic one-shot DM prompt for pending users; runtime gate blocks normal usage until consent is accepted (allowed: `/accept`, `/decline`, `/consent`, `/start`) | `/accept`, `/decline`, `/consent` Commands für Nutzer-Consent; automatischer One-Shot-DM-Prompt für Pending-User; Runtime-Gate blockiert normale Nutzung bis Consent akzeptiert ist (erlaubt: `/accept`, `/decline`, `/consent`, `/start`) |
| 🔌 **Plugin System** | Defensive manifest-based plugin loader | Defensiver, manifest-basierter Plugin-Loader |
| 🌐 **WebUI** | Local Flask-based management interface | Lokale Flask-basierte Verwaltungsoberfläche |
| 🤖 **AI Integration** | Optional Ollama `/ask` command (stateless) | Optionales Ollama `/ask` Kommando (stateless) |
| 🧪 **Testing** | pytest + smoke tests included | pytest + Smoke-Tests enthalten |

---

## Quick Start

```bash
git clone <repository-url>
cd AMO-telegram-bot
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your values
python main.py
```

---

## Documentation / Dokumentation

| Document | Description | Beschreibung |
|----------|-------------|--------------|
| [📘 Setup Guide (EN)](docs/SETUP_EN.md) | Full setup instructions | — |
| [📗 Setup-Anleitung (DE)](docs/SETUP_DE.md) | — | Vollständige Setup-Anleitung |
| [🧪 Beta Testing (EN)](docs/BETATEST_EN.md) / [DE](docs/BETATEST_DE.md) | Step-by-step beta test guide | Schritt-für-Schritt-Betatest-Anleitung |
| [📝 Release Notes (EN)](docs/RELEASE_NOTES_2026.05.10-Beta_EN.md) / [DE](docs/RELEASE_NOTES_2026.05.10-Beta_DE.md) | Changelog and version history | Changelog und Versionshistorie |

---

## Security Notes / Sicherheitshinweise

- **Never commit secrets** — `.env` is gitignored
- **WebUI is local-only** — Default `127.0.0.1`, never expose to internet
- **Token protection** — Never share `BOT_TOKEN` in chats, logs, or repositories
- **Niemals Secrets committen** — `.env` ist in .gitignore
- **WebUI nur lokal** — Standard `127.0.0.1`, niemals ins Internet freigeben
- **Token-Schutz** — `BOT_TOKEN` niemals in Chats, Logs oder Repositories teilen

---

## License / Lizenz

License not specified yet.

Lizenz noch nicht festgelegt.
