# Mitwirken / Contributing

> **DE:** Danke, dass du AMO verbessern möchtest! Diese Anleitung hilft dir, strukturiert mitzuwirken.
> **EN:** Thank you for wanting to improve AMO! This guide helps you contribute in a structured way.

---

## 🇩🇪 Deutsch

### Übersicht

Diese Anleitung erklärt, wie du zum AMO Telegram Bot beitragen kannst – von kleinen Fixes bis zu größeren Features.

### Schnellstart für Mitwirkende

#### 1. Repository klonen und einrichten

```bash
git clone <repository-url>
cd AMO-telegram-bot
python3.12 -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt  # Falls vorhanden
```

#### 2. Konfiguration für Entwicklung

```bash
cp .env.example .env
# .env bearbeiten – minimale Konfiguration für lokale Tests:
# BOT_TOKEN=dein_test_bot_token
# WEBUI_PASSWORD=dev_password_123
# WEBUI_OWNER_TELEGRAM_ID=deine_telegram_id
```

> **Tipp:** Erstelle einen eigenen Test-Bot bei [@BotFather](https://t.me/BotFather) für die Entwicklung.

#### 3. Tests ausführen

```bash
# Alle Tests
pytest -q

# Mit Coverage (optional)
pytest -q --cov=src --cov-report=term-missing
```

Alle Tests müssen bestehen, bevor ein PR eingereicht wird.

### Branch-Strategie

| Branch | Zweck |
|--------|-------|
| `main` | Produktions-Code, stabil |
| `feature/*` | Neue Features |
| `fix/*` | Bugfixes |
| `docs/*` | Dokumentations-Updates |

### Commit-Konventionen

Wir verwenden beschreibende Commit-Nachrichten:

```
typ(scope): kurze Beschreibung im Imperativ

Optionale ausführliche Beschreibung...
```

**Typen:**
- `feat:` – Neues Feature
- `fix:` – Bugfix
- `docs:` – Dokumentation
- `test:` – Tests
- `refactor:` – Refactoring
- `chore:` – Wartung/Build

**Beispiele:**
```
feat(plugins): add RSS feed parser capability
fix(webui): resolve CSRF token validation error
docs(readme): update installation instructions for Windows
test(memory): add retention policy tests
```

### Pull Request (PR) Prozess

1. **Fork erstellen** (für externe Mitwirkende) oder Branch aus `main`
2. **Feature/Fix implementieren** mit Tests
3. **Alle Tests lokal ausführen:** `pytest -q`
4. **PR erstellen** mit:
   - Klarem Titel und Beschreibung
   - Verknüpfung zu relevanten Issues (z.B. `Closes #123`)
   - Checkliste abgearbeitet (siehe PR-Template)
5. **Code Review** abwarten
6. **Änderungen einpflegen** bei Feedback

### Code-Style

- **Python:** PEP 8
- **Zeilenlänge:** 100 Zeichen (nicht 80)
- **Import-Stil:** Gruppiert nach stdlib, third-party, local
- **Typisierung:** Type Hints wo sinnvoll
- **Dokstrings:** Für öffentliche APIs

### Plugin-Entwicklung

Für **Userplugin-Entwicklung** siehe die ausführliche Anleitung:
**[docs/USERPLUGINS.md](docs/USERPLUGINS.md)** — Enthält Manifest-Spezifikation, Capability-Referenz (`rss.fetch` etc.), Do/Don't-Regeln, Minimalbeispiel und KI-Richtlinien.

**Vor dem Commit automatisch prüfen:**

```bash
# Formatierung (falls black installiert)
black src/ tests/

# Linting (falls flake8 installiert)
flake8 src/ tests/

# Imports sortieren (falls isort installiert)
isort src/ tests/
```

### Test-Erwartungen

- Neue Features brauchen Tests
- Bugfixes brauchen Regressionstests
- Alle PRs müssen `pytest -q` bestehen
- Mocking für externe APIs (Telegram, Ollama)
- Keine Secrets in Testdaten

### Agenten- und QA-Nachweise

- Große lokale Logs und generierte Artefakte sind kein Rohmaterial für vollständige Agenten-Reads.
- Lies oder kopiere keine vollständigen Dateien aus `logs/`, `.state/`, Datenbank-Dumps,
  Migrationskopien, Diagnoseausgaben, Backups oder ähnlichen generierten Artefakten.
- Für Logs nur begrenzte Nachweise verwenden: `tail`, gezieltes `rg`, Zeitfenster,
  Fehlercodes, Counts, Dateigrößen und kurze relevante Treffer. `logs/bot.out` und andere
  große Logs nicht vollständig lesen.
- Datenbanken nur eng abfragen: konkrete Rows, Counts, Schemas oder Metadaten. Keine ganzen
  Tabellen oder Datenbankinhalte in Agenten-/QA-Kontext dumpen.
- `.state/` ist außerhalb des Agenten- und QA-Scopes, solange Matze/Main keine Cleanup- oder
  Archivierungsaufgabe ausdrücklich freigibt. Bis dahin nur Metadaten wie `du`, begrenztes
  `find`, Dateinamen, Größen und Zeitstempel prüfen.
- Zukünftige Backend-/QA-Aufgaben sollen diesen Scope nennen: keine Rohlogs, keine vollständigen
  generierten Artefakte, keine DB-Dumps; nur Metadaten oder relevante begrenzte Treffer.

### Dokumentation

- Änderungen an Features → README/docs aktualisieren
- Neue Konfigurationen → SETUP_DE.md + SETUP_EN.md
- Breaking Changes → Changelog + Migrationshinweise

### Wo kann ich fragen?

| Kanal | Für |
|-------|-----|
| Issues | Bug-Reports, Feature-Requests |
| Discussions | Fragen, Ideen, Hilfe |
| Telegram (im Bot) | Nur für konfigurierte Owner/Admins |

### Sicherheit

- **Niemals Secrets committen** (Token, Passwörter, private Pfade)
- `.env` ist in `.gitignore` – das so lassen!
- Bei Sicherheitslücken: Siehe [SECURITY.md](SECURITY.md)

### Entwicklungs-Hinweise

**Lokaler WebUI-Test:**
```bash
python main.py --webui
# Öffne http://127.0.0.1:8080
```

**Bot-Only-Modus (für schnelle Tests):**
```bash
python main.py
```

**Smoke-Test:**
```bash
python -m amo_bot.smoke
```

### Ressourcen

- [Setup-Anleitung (DE)](docs/SETUP_DE.md)
- [Setup Guide (EN)](docs/SETUP_EN.md)
- [Roadmap](ROADMAP.md) – Projekt-Richtung und geplante Features
- [Release Baseline](docs/release-baseline.md) für Support-Status
- [Release Notes](docs/) für Versionshistorie
- [Issue Templates](.github/ISSUE_TEMPLATE/) (geplant)

---

## 🇬🇧 English

### Overview

This guide explains how to contribute to the AMO Telegram Bot – from small fixes to larger features.

### Quick Start for Contributors

#### 1. Clone and Setup Repository

```bash
git clone <repository-url>
cd AMO-telegram-bot
python3.12 -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt  # If available
```

#### 2. Configuration for Development

```bash
cp .env.example .env
# Edit .env – minimal config for local testing:
# BOT_TOKEN=your_test_bot_token
# WEBUI_PASSWORD=dev_password_123
# WEBUI_OWNER_TELEGRAM_ID=your_telegram_id
```

> **Tip:** Create your own test bot at [@BotFather](https://t.me/BotFather) for development.

#### 3. Run Tests

```bash
# All tests
pytest -q

# With coverage (optional)
pytest -q --cov=src --cov-report=term-missing
```

All tests must pass before submitting a PR.

### Branch Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Production code, stable |
| `feature/*` | New features |
| `fix/*` | Bug fixes |
| `docs/*` | Documentation updates |

### Commit Conventions

We use descriptive commit messages:

```
type(scope): short description in imperative mood

Optional detailed description...
```

**Types:**
- `feat:` – New feature
- `fix:` – Bug fix
- `docs:` – Documentation
- `test:` – Tests
- `refactor:` – Refactoring
- `chore:` – Maintenance/build

**Examples:**
```
feat(plugins): add RSS feed parser capability
fix(webui): resolve CSRF token validation error
docs(readme): update installation instructions for Windows
test(memory): add retention policy tests
```

### Pull Request (PR) Process

1. **Create fork** (for external contributors) or branch from `main`
2. **Implement feature/fix** with tests
3. **Run all tests locally:** `pytest -q`
4. **Create PR** with:
   - Clear title and description
   - Link to relevant issues (e.g., `Closes #123`)
   - Checklist completed (see PR template)
5. **Wait for code review**
6. **Address feedback** with changes

### Code Style

- **Python:** PEP 8
- **Line length:** 100 characters (not 80)
- **Import style:** Grouped by stdlib, third-party, local
- **Typing:** Type hints where sensible
- **Docstrings:** For public APIs

### Plugin Development

For **userplugin development**, see the comprehensive guide:
**[docs/USERPLUGINS.md](docs/USERPLUGINS.md)** — Contains manifest specification, capability reference (`rss.fetch` etc.), Do/Don't rules, minimal example, and AI guidelines.

**Auto-check before commit:**

```bash
# Formatting (if black installed)
black src/ tests/

# Linting (if flake8 installed)
flake8 src/ tests/

# Sort imports (if isort installed)
isort src/ tests/
```

### Test Expectations

- New features need tests
- Bug fixes need regression tests
- All PRs must pass `pytest -q`
- Mock external APIs (Telegram, Ollama)
- No secrets in test data

### Agent and QA Evidence

- Large local logs and generated artifacts are not raw material for full agent reads.
- Do not read or copy complete files from `logs/`, `.state/`, database dumps, migration copies,
  diagnostics, backups, or similar generated artifacts.
- For logs, use bounded evidence only: `tail`, targeted `rg`, timestamps/time windows, error
  codes, counts, file sizes, and short relevant matches. Do not read `logs/bot.out` or other
  large logs in full.
- Query databases narrowly: specific rows, counts, schemas, or metadata only. Do not dump whole
  tables or database contents into agent/QA context.
- `.state/` is outside agent and QA scope unless Matze/Main explicitly approves a cleanup or
  archive task. Until then, inspect only metadata such as `du`, bounded `find`, filenames,
  sizes, and timestamps.
- Future Backend/QA tasks should state this scope: no raw logs, no complete generated artifacts,
  no DB dumps; provide only metadata or relevant bounded matches.

### Documentation

- Feature changes → Update README/docs
- New configurations → Update SETUP_EN.md + SETUP_DE.md
- Breaking changes → Changelog + migration notes

### Where to Ask Questions?

| Channel | For |
|---------|-----|
| Issues | Bug reports, feature requests |
| Discussions | Questions, ideas, help |
| Telegram (in bot) | For configured owners/admins only |

### Security

- **Never commit secrets** (tokens, passwords, private paths)
- `.env` is in `.gitignore` – keep it that way!
- For security issues: See [SECURITY.md](SECURITY.md)

### Development Notes

**Local WebUI testing:**
```bash
python main.py --webui
# Open http://127.0.0.1:8080
```

**Bot-only mode (for quick tests):**
```bash
python main.py
```

**Smoke test:**
```bash
python -m amo_bot.smoke
```

### Resources

- [Setup Guide (EN)](docs/SETUP_EN.md)
- [Setup-Anleitung (DE)](docs/SETUP_DE.md)
- [Roadmap](ROADMAP.md) – Project direction and planned features
- [Release Baseline](docs/release-baseline.md) for support status
- [Release Notes](docs/) for version history
- [Issue Templates](.github/ISSUE_TEMPLATE/) (planned)

---

## Gemeinsame Standards / Shared Standards

### Python-Kompatibilität / Python Compatibility

- **Minimum:** Python 3.12+
- **Getestet auf / Tested on:** Linux, macOS, Windows
- **Nicht unterstützt / Not supported:** Python 3.11

### Definition of Done

**DE:** Ein PR ist bereit für Review, wenn:
- [ ] Code implementiert
- [ ] Tests geschrieben und alle bestehenden Tests grün
- [ ] Dokumentation aktualisiert (falls nötig)
- [ ] Keine Secrets oder privaten Pfade im Code
- [ ] Commit-Nachrichten beschreiben den Change
- [ ] PR-Template ausgefüllt

**EN:** A PR is ready for review when:
- [ ] Code implemented
- [ ] Tests written and all existing tests green
- [ ] Documentation updated (if needed)
- [ ] No secrets or private paths in code
- [ ] Commit messages describe the change
- [ ] PR template filled out

---

<p align="center">
  <sub>Vielen Dank für deine Unterstützung! / Thank you for your support! 🎉</sub>
</p>
