# Roadmap / Roadmap

> **DE:** Projekt-Richtung und geplante Features — ohne Überbietung.
> **EN:** Project direction and planned features — without overpromising.

---

## 🇩🇪 Deutsch

### Übersicht

Diese Roadmap zeigt die geplante Entwicklung des AMO Telegram Bots. Sie ist in drei Zeithorizonte unterteilt: **Jetzt**, **Als Nächstes** und **Später**.

> **Hinweis:** Zeitschätzungen sind unverbindlich. Prioritäten können sich ändern, basierend auf Feedback und technischen Erfordernissen.

---

### Jetzt (Now)

Aktueller Fokus: Stabilität und Fundament.

| Feature | Status | Beschreibung |
|---------|--------|--------------|
| **Core Bot** | ✅ Stabil | Rollenbasiertes Berechtigungssystem, Consent-Management, grundlegende Befehle |
| **WebUI** | ✅ Stabil | Lokale Flask-Oberfläche für Verwaltung und Konfiguration |
| **KI-Integration** | ✅ Stabil | `/ask`-Befehl, Auto-Antworten, Memory-System (Daily + Long Memory) |
| **Plugin-System** | ✅ Stabil | Discovery, Manifest-Validierung, Registry, Aktivierung |
| **Bilinguale Oberfläche** | ✅ Stabil | Deutsche und englische Unterstützung für Bot und WebUI |

---

### Als Nächstes (Next)

Kurzfristige Ziele (1–3 Monate):

| Feature | Status | Beschreibung |
|---------|--------|--------------|
| **Core Plugins (KI-Fähigkeiten)** | ✅ Abgeschlossen (CP-Z1) | RSS-Feeds, Websuche, Webscraping als policy-gesteuerte KI-Fähigkeiten |
| **API-Integration** | 🔄 In Planung | Konfigurierbare API-Aufrufe für erlaubte Endpoints |
| **Speicher-Verwaltung** | 📋 Geplant | Erweiterte Memory-Operationen mit Scope-Isolation |
| **WebUI-Verbesserungen** | 📋 Geplant | Bessere Übersicht, Filter, Export-Optionen |

---

### Später (Later)

Mittel- bis langfristige Ziele (3–6+ Monate):

| Feature | Status | Beschreibung |
|---------|--------|--------------|
| **Context Window Builder** | 🔍 Explorativ | Fortgeschrittenes Kontext-Management für KI-Anfragen |
| **SQL-Templates** | 🔍 Explorativ | Lesende SQL-Abfragen über konfigurierte Templates |
| **Selbstverbesserung** | 💡 Idee | Analyse-Vorschläge, keine autonomen Änderungen |
| **Mobile-Optimierung** | 💡 Idee | Bessere Unterstützung für mobile WebUI-Nutzung |
| **Community-Plugins** | 💡 Idee | Ökosystem für Drittanbieter-Plugins |

---

### Bug Reports & Feature Requests

#### Fehler Melden

1. Prüfe [bestehende Issues](https://github.com/Matze383/amo-telegram-bot/issues) auf Duplikate
2. Erstelle einen neuen Bug-Report mit dem Template
3. Beschreibe das Problem, Reproduktionsschritte, erwartetes vs. tatsächliches Verhalten
4. Füge Systeminformationen bei (OS, Python-Version, Bot-Version)

> **Wichtig:** Niemals Bot-Token, Passwörter oder private Pfade in öffentlichen Issues posten!

#### Features Vorschlagen

1. Prüfe [bestehende Requests](https://github.com/Matze383/amo-telegram-bot/issues) auf Duplikate
2. Erstelle einen neuen Feature-Request mit dem Template
3. Beschreibe das Problem, den gewünschten Nutzen, mögliche Alternativen
4. Markiere Scope-Praferenz (Bot, WebUI, KI, Plugin-System)

---

## 🇬🇧 English

### Overview

This roadmap shows the planned development of the AMO Telegram Bot. It is divided into three time horizons: **Now**, **Next**, and **Later**.

> **Note:** Time estimates are non-binding. Priorities may change based on feedback and technical requirements.

---

### Now

Current focus: Stability and foundation.

| Feature | Status | Description |
|---------|--------|-------------|
| **Core Bot** | ✅ Stable | Role-based permission system, consent management, basic commands |
| **WebUI** | ✅ Stable | Local Flask interface for management and configuration |
| **AI Integration** | ✅ Stable | `/ask` command, auto-replies, memory system (Daily + Long Memory) |
| **Plugin System** | ✅ Stable | Discovery, manifest validation, registry, activation |
| **Bilingual Interface** | ✅ Stable | German and English support for bot and WebUI |

---

### Next

Short-term goals (1–3 months):

| Feature | Status | Description |
|---------|--------|-------------|
| **Core Plugins (AI Capabilities)** | ✅ Complete (CP-Z1) | RSS feeds, web search, web scraping as policy-controlled AI capabilities |
| **API Integration** | 🔄 In Planning | Configurable API calls for allowed endpoints |
| **Memory Management** | 📋 Planned | Advanced memory operations with scope isolation |
| **WebUI Improvements** | 📋 Planned | Better overview, filters, export options |

---

### Later

Medium- to long-term goals (3–6+ months):

| Feature | Status | Description |
|---------|--------|-------------|
| **Context Window Builder** | 🔍 Exploratory | Advanced context management for AI requests |
| **SQL Templates** | 🔍 Exploratory | Read-only SQL queries via configured templates |
| **Self-Improvement** | 💡 Idea | Analysis proposals, no autonomous changes |
| **Mobile Optimization** | 💡 Idea | Better support for mobile WebUI usage |
| **Community Plugins** | 💡 Idea | Ecosystem for third-party plugins |

---

### Bug Reports & Feature Requests

#### Reporting Bugs

1. Check [existing issues](https://github.com/Matze383/amo-telegram-bot/issues) for duplicates
2. Create a new bug report using the template
3. Describe the problem, reproduction steps, expected vs. actual behavior
4. Include system information (OS, Python version, bot version)

> **Important:** Never post bot tokens, passwords, or private paths in public issues!

#### Requesting Features

1. Check [existing requests](https://github.com/Matze383/amo-telegram-bot/issues) for duplicates
2. Create a new feature request using the template
3. Describe the problem, desired benefit, possible alternatives
4. Mark scope preference (Bot, WebUI, AI, Plugin System)

---

## 🏷️ Legend / Legende

| Symbol | Deutsch | English |
|--------|---------|---------|
| ✅ | Stabil / Fertig / Abgeschlossen | Stable / Complete |
| 🔄 | In Arbeit / In Planung | In Progress / In Planning |
| 📋 | Geplant | Planned |
| 🔍 | Explorativ / Evaluierung | Exploratory / Evaluation |
| 💡 | Idee / Backlog | Idea / Backlog |

---

## 📅 Letzte Aktualisierung / Last Updated

2026-05-16

---

<p align="center">
  <sub>Roadmap ist lebendig — Feedback willkommen! / Roadmap is living — feedback welcome! 🚀</sub>
</p>
