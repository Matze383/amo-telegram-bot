# i18n Inventory / Übersicht der Internationalisierung

> **Scope:** RR-03 – i18n Inventory: Bot + Flask UI + Repo Docs
> **Updated:** 2026-05-22 (after #10/#11/#12/#13/#14 implementation)
> **Git Issues:** #9 (previous release), #10/#11 (runtime i18n), #12/#13 (language conventions), #14 (this inventory update)

---

## Deutsch

Dieses Dokument listet alle sprachsensitiven Oberflächen des AMO Telegram Bots auf. Es dient als Planungs- und Tracking-Grundlage für die vollständige Zweisprachigkeit (Deutsch + Englisch).

**Status nach #10/#11:**
- ✅ Telegram Bot Commands: Vollständig bilingualisiert (siehe Abschnitt 1)
- ✅ Consent-Flow: Vollständig bilingualisiert (Buttons, Prompts, Callbacks)
- ✅ Dispatcher: Bilinguale Fehlermeldungen und Consent-Block-Nachrichten
- ✅ Flask WebUI: Vollständig bilingualisiert über i18n-Modul
- ✅ Dokumentation: Language Conventions etabliert (LANGUAGE_CONVENTIONS.md)

**Wichtig:** Nach Abschluss von #10/#11/#12/#13 sind alle user-facing Surfaces bilingual. Verbleibende EN-only Bereiche sind technische Interna (Logging, Code-Kommentare), die absichtlich nicht übersetzt werden.

---

## English

This document lists all language-sensitive surfaces of the AMO Telegram Bot. It serves as planning and tracking foundation for complete bilingual support (German + English).

**Status after #10/#11:**
- ✅ Telegram Bot Commands: Fully bilingualized (see Section 1)
- ✅ Consent Flow: Fully bilingualized (buttons, prompts, callbacks)
- ✅ Dispatcher: Bilingual error messages and consent block messages
- ✅ Flask WebUI: Fully bilingualized via i18n module
- ✅ Documentation: Language Conventions established (LANGUAGE_CONVENTIONS.md)

**Important:** After completion of #10/#11/#12/#13, all user-facing surfaces are bilingual. Remaining EN-only areas are technical internals (logging, code comments) that are intentionally not translated.

---

## Inventar-Struktur / Inventory Structure

| Spalte / Column | Beschreibung / Description |
|-----------------|---------------------------|
| **Category** | Oberflächen-Bereich (Bot, WebUI, Docs, GitHub) |
| **Element** | Konkretes UI-Element oder Text-Schlüssel |
| **Source** | Quelldatei (relativer Pfad im Repo) |
| **DE Status** | DE-Vollständigkeit: ✅ vollständig, ⚠️ teilweise, ❌ fehlt |
| **EN Status** | EN-Vollständigkeit: ✅ vollständig, ⚠️ teilweise, ❌ fehlt |
| **Owner** | Zuständigkeit: Backend (Code), Docs (Doku), QA (Prüfung) |
| **Priority** | P0 = Blocker für Release, P1 = Wichtig, P2 = Nice-to-have |
| **Notes/Gap** | Befunde, Lücken, Verweise auf Issues |

---

## 1. Bot Commands & Help

### 1.1 Command Names (Internal/Technical)

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Bot | `/ping` | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | Command names are technical identifiers, same in both languages |
| Bot | `/help` | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | |
| Bot | `/role` | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | |
| Bot | `/start` | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | |
| Bot | `/accept` | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | |
| Bot | `/decline` | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | |
| Bot | `/consent` | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | |
| Bot | `/ask` | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | |
| Bot | `/setrole` | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | |
| Bot | `/test` | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | |
| Bot | `/webui` | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | |

### 1.2 Command Descriptions (Help Text)

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Bot | `/ping` description | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | "Bot-Erreichbarkeit prüfen" / "Check bot health" – Implemented in #10/#11 |
| Bot | `/help` description | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | "Verfügbare Befehle anzeigen" / "List available commands" – Implemented in #10/#11 |
| Bot | `/role` description | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | "Deine aktuelle Rolle anzeigen" / "Show your current role" – Implemented in #10/#11 |
| Bot | `/start` description | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | "Consent-Flow im privaten Chat starten" / "Start consent flow in private chat" – Implemented in #10/#11 |
| Bot | `/accept` description | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | "Consent akzeptieren" / "Accept consent" – Implemented in #10/#11 |
| Bot | `/decline` description | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | "Consent ablehnen" / "Decline consent" – Implemented in #10/#11 |
| Bot | `/consent` description | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | "Consent-Status anzeigen" / "Show consent status" – Implemented in #10/#11 |
| Bot | `/ask` description | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | "Ollama fragen: /ask <frage>" / "Ask Ollama: /ask <question>" – Implemented in #10/#11 |
| Bot | `/setrole` description | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | "Rolle setzen: /setrole <telegram_user_id> <rolle>" / "Set role: /setrole <telegram_user_id> <role>" – Implemented in #10/#11 |
| Bot | `/test` description | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | "Inline-Button-Smoketest senden" / "Send inline button smoke test" – Implemented in #10/#11 |
| Bot | `/webui` description | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | "WebUI-Zugriff: /webui <on|off|status>" / "WebUI access window: /webui <on|off|status>" – Implemented in #10/#11 |

### 1.3 Command Responses / Confirmations

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Bot | `/ping` response | `src/amo_bot/telegram/commands.py:71` | ✅ | ✅ | Backend | P0 | "pong" – universal, returned via `_lang()` helper |
| Bot | `/role` response | `src/amo_bot/telegram/commands.py:99` | ⚠️ | ✅ | Backend | P2 | "your role: {role}" – EN only; follows technical convention for simple role display |
| Bot | `/accept` success | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/decline` success | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/consent` in group | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/consent` status text | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | Full bilingual status explanations via `_consent_status_explanation()` – Implemented #10/#11 |
| Bot | `/setrole` success | `src/amo_bot/telegram/commands.py` | ⚠️ | ✅ | Backend | P2 | EN only: "role updated..." / "no change..." – Technical admin message, follow-up optional |
| Bot | `/setrole` permission denied | `src/amo_bot/telegram/commands.py:163` | ⚠️ | ✅ | Backend | P2 | EN only: "permission denied" – Technical admin message |
| Bot | `/setrole` usage error | `src/amo_bot/telegram/commands.py:169` | ⚠️ | ✅ | Backend | P2 | EN only: "usage: /setrole..." – Technical admin message |
| Bot | `/setrole` invalid user ID | `src/amo_bot/telegram/commands.py:178` | ⚠️ | ✅ | Backend | P2 | EN only: "invalid telegram_user_id" – Technical admin message |
| Bot | `/setrole` invalid role | `src/amo_bot/telegram/commands.py:182-183` | ⚠️ | ✅ | Backend | P2 | EN only: "invalid role..." – Technical admin message |
| Bot | `/ask` empty prompt | `src/amo_bot/telegram/commands.py:343` | ✅ | ✅ | Backend | P1 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/ask` no AI service | `src/amo_bot/telegram/commands.py:345` | ✅ | ✅ | Backend | P1 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/ask` Ollama error | `src/amo_bot/telegram/commands.py:349-352` | ✅ | ✅ | Backend | P1 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/webui` not private | `src/amo_bot/telegram/commands.py` | ⚠️ | ⚠️ | Backend | P2 | EN technical messages (audit logging focus) |
| Bot | `/webui` not owner | `src/amo_bot/telegram/commands.py` | ⚠️ | ✅ | Backend | P2 | EN only: "permission denied" – Technical admin message |
| Bot | `/webui` status OPEN | `src/amo_bot/telegram/commands.py` | ⚠️ | ✅ | Backend | P2 | EN only: "webui access: OPEN..." – Technical status message |
| Bot | `/webui` status CLOSED | `src/amo_bot/telegram/commands.py` | ⚠️ | ✅ | Backend | P2 | EN only: "webui access: CLOSED" – Technical status message |
| Bot | `/test` button text | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P0 | Mixed via `_lang()`: "Inline-Button-Test: Bitte klicken." / "Inline button test: please click." – Implemented #10/#11 |
| Bot | `/test` group success text | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/test` group fallback text | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | Bilingual via `_lang()` – Implemented #10/#11 |

### 1.4 Consent & Onboarding Flows

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Bot | Consent prompt text | `src/amo_bot/consent/prompt_service.py` | ✅ | ✅ | Backend | P0 | Full bilingual via `_PROMPT_TEXTS` – Implemented #10/#11 |
| Bot | Consent button Accept | `src/amo_bot/consent/prompt_service.py` | ✅ | ✅ | Backend | P0 | "✅ Akzeptieren" / "✅ Accept" – Implemented #10/#11 |
| Bot | Consent button Decline | `src/amo_bot/consent/prompt_service.py` | ✅ | ✅ | Backend | P0 | "❌ Ablehnen" / "❌ Decline" – Implemented #10/#11 |
| Bot | `/start` already accepted | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/start` declined status | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/start` not configured | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P2 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/start` wrong chat type | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P1 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | Consent block message (private) | `src/amo_bot/telegram/dispatcher.py` | ✅ | ✅ | Backend | P0 | Bilingual via `_consent_block_message()` – Implemented #10/#11 |
| Bot | Consent block message (group) | `src/amo_bot/telegram/dispatcher.py` | ✅ | ✅ | Backend | P0 | Bilingual via `_consent_block_message()` – Implemented #10/#11 |
| Bot | Callback consent accept | `src/amo_bot/telegram/dispatcher.py` | ✅ | ✅ | Backend | P1 | Bilingual via `_consent_callback_message()` – Implemented #10/#11 |
| Bot | Callback consent decline | `src/amo_bot/telegram/dispatcher.py` | ✅ | ✅ | Backend | P1 | Bilingual via `_consent_callback_message()` – Implemented #10/#11 |
| Bot | Callback consent not available | `src/amo_bot/telegram/dispatcher.py` | ✅ | ✅ | Backend | P2 | Bilingual via `_consent_callback_message()` – Implemented #10/#11 |
| Bot | Callback profile not found | `src/amo_bot/telegram/dispatcher.py` | ✅ | ✅ | Backend | P2 | Bilingual via `_consent_callback_message()` – Implemented #10/#11 |
| Bot | `/accept` not configured | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P2 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/accept` user not found | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P2 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/decline` not configured | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P2 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | `/decline` user not found | `src/amo_bot/telegram/commands.py` | ✅ | ✅ | Backend | P2 | Bilingual via `_lang()` – Implemented #10/#11 |

### 1.5 Error Messages / Fallbacks

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Bot | No commands available | `src/amo_bot/telegram/commands.py:453` | ✅ | ✅ | Backend | P2 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | Available commands header | `src/amo_bot/telegram/commands.py:454` | ✅ | ✅ | Backend | P2 | Bilingual via `_lang()` – Implemented #10/#11 |
| Bot | Unknown command | `src/amo_bot/telegram/dispatcher.py` | ✅ | ✅ | Backend | P1 | Bilingual via `_unknown_command_message()` – Implemented #10/#11 |
| Bot | AI autoreply error | `src/amo_bot/telegram/dispatcher.py` | ✅ | ✅ | Backend | P1 | Bilingual via `AI_AUTOREPLY_ERROR_FALLBACK_TEXT` – Implemented #10/#11 |
| Bot | Button test OK | `src/amo_bot/telegram/dispatcher.py` | ✅ | ✅ | Backend | P2 | Bilingual callback response – Implemented #10/#11 |
| Bot | Runtime not configured (various) | Various handlers | ⚠️ | ✅ | Backend | P3 | EN technical messages for misconfiguration |

---

## 2. Flask UI (WebUI)

### 2.1 Navigation / Page Titles

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| WebUI | All navigation labels | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P1 | Full i18n via `translate()` function – Implemented #10/#11 |
| WebUI | Page titles | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P1 | dashboard.title, users.title, groups.title, plugins.title – Implemented #10/#11 |
| WebUI | Action buttons | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P1 | save, logout, set_role, enable/disable, etc. – Implemented #10/#11 |

### 2.2 Form Labels / Table Headers

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| WebUI | Login form | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P1 | login.title, login.password, login.disabled, login.invalid_password – Implemented #10/#11 |
| WebUI | Dashboard sections | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P1 | All dashboard strings – Implemented #10/#11 |
| WebUI | Table headers | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P1 | scope, chat_id, topic_id, user_id, role, etc. – Implemented #10/#11 |
| WebUI | Users page | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P1 | All users.* strings – Implemented #10/#11 |
| WebUI | Groups page | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P1 | All groups.* strings – Implemented #10/#11 |
| WebUI | Plugins page | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P1 | All plugins.* strings – Implemented #10/#11 |
| WebUI | Image quotas section | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P1 | users.image_quotas_title, users.image_quotas_note, etc. – Implemented #10/#11 |

### 2.3 API Responses / Status Messages

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| WebUI | HTTP error handlers | `src/amo_bot/webui/flask_app.py` | ⚠️ | ✅ | Backend | P2 | Technical error descriptions (400, 401, 403, 404, 500) – Internal API use |
| WebUI | Legacy WebUI disabled | `src/amo_bot/webui/app.py` | ✅ | ✅ | Backend | P2 | EN technical message with fail-closed semantics |

### 2.4 Language Switching

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| WebUI | Language resolver | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P0 | `resolve_lang()` with session storage – Implemented #10/#11 |
| WebUI | Language switcher UI | Templates | ✅ | ✅ | Backend | P1 | `lang_url()` helper for switching – Implemented #10/#11 |
| WebUI | Default language | `src/amo_bot/webui/i18n.py` | ✅ | ✅ | Backend | P0 | Defaults to "de" (German) per `_DEFAULT_LANG` – Implemented #10/#11 |

---

## 3. Repository Documentation

### 3.1 README.md

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Docs | Main title | `README.md` | ✅ | ✅ | Docs | P0 | Bilingual header present |
| Docs | Status banner | `README.md` | ✅ | ✅ | Docs | P0 | Bilingual Beta warning |
| Docs | Features table | `README.md` | ✅ | ✅ | Docs | P0 | Bilingual feature descriptions |
| Docs | Quick Start | `README.md` | ✅ | ✅ | Docs | P0 | EN code comments, but universal commands |
| Docs | Documentation links | `README.md` | ✅ | ✅ | Docs | P0 | Links to bilingual docs |
| Docs | Security notes | `README.md` | ✅ | ✅ | Docs | P0 | Bilingual section |
| Docs | License | `README.md` | ✅ | ✅ | Docs | P0 | Bilingual reference |

### 3.2 Setup Guides (SETUP_DE.md / SETUP_EN.md)

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Docs | Full setup guide | `docs/SETUP_DE.md` / `docs/SETUP_EN.md` | ✅ | ✅ | Docs | P0 | Complete bilingual guides |
| Docs | Security settings | `docs/SETUP_DE.md` / `docs/SETUP_EN.md` | ✅ | ✅ | Docs | P0 | Bilingual security documentation |
| Docs | WebUI sections | `docs/SETUP_DE.md` / `docs/SETUP_EN.md` | ✅ | ✅ | Docs | P0 | KI Memory, Topic Soul, etc. |

### 3.3 Beta Test Guides (BETATEST_DE.md / BETATEST_EN.md)

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Docs | Full beta test guide | `docs/BETATEST_DE.md` / `docs/BETATEST_EN.md` | ✅ | ✅ | Docs | P0 | Complete bilingual guides |
| Docs | Test checklists | `docs/BETATEST_DE.md` / `docs/BETATEST_EN.md` | ✅ | ✅ | Docs | P0 | Bilingual checkboxes and instructions |

### 3.4 Release Notes

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Docs | Release notes | `docs/RELEASE_NOTES_*.md` | ✅ | ✅ | Docs | P0 | Separate DE/EN files exist |

### 3.5 Language Conventions

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Docs | Language Conventions | `docs/LANGUAGE_CONVENTIONS.md` | ✅ | ✅ | Docs | P0 | Bilingual language standards doc – Implemented #12/#13 |

### 3.6 Other Documentation

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Docs | Release baseline | `docs/release-baseline.md` | ✅ | ✅ | Docs | P1 | Bilingual technical doc |
| Docs | Public repo metadata | `docs/public-repo-metadata.md` | ✅ | ✅ | Docs | P1 | Bilingual checklist |
| Docs | Userplugin Guide | `docs/USERPLUGINS.md` | ✅ | ✅ | Docs | P0 | Bilingual single file |
| Docs | Contributing | `CONTRIBUTING.md` | ✅ | ✅ | Docs | P0 | Bilingual single file |
| Docs | Security | `SECURITY.md` | ✅ | ✅ | Docs | P0 | Bilingual single file |
| Docs | Code of Conduct | `CODE_OF_CONDUCT.md` | ✅ | ✅ | Docs | P0 | Bilingual single file |
| Docs | Support | `SUPPORT.md` | ✅ | ✅ | Docs | P0 | Bilingual single file |
| Docs | Context/Memory Architecture | `docs/CONTEXT_MEMORY_ARCHITECTURE.md` | N/A | ✅ | Docs | P2 | EN-only technical architecture doc (by design) |
| Docs | WebUI Plugin docs | `docs/WEBUI_PLUGIN_*.md` | ✅ | ✅ | Docs | P1 | Bilingual single files |

---

## 4. GitHub Surfaces (Templates)

### 4.1 GitHub Metadata

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| GitHub | LICENSE | `LICENSE` | ✅ | ✅ | Docs | P0 | MIT License (EN only, standard) |
| GitHub | Topics/tags | GitHub UI | ❌ | ✅ | Docs | P2 | GitHub topics are typically EN only |

### 4.2 Templates (Completed in #9 - previous release)

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| GitHub | Bug report template | `.github/ISSUE_TEMPLATE/bug_report.yml` | ✅ | ✅ | Docs | P1 | Bilingual – Completed #9 |
| GitHub | Feature request template | `.github/ISSUE_TEMPLATE/feature_request.yml` | ✅ | ✅ | Docs | P1 | Bilingual – Completed #9 |
| GitHub | PR template | `.github/PULL_REQUEST_TEMPLATE.md` | ✅ | ✅ | Docs | P1 | Bilingual – Completed #9 |
| GitHub | CONTRIBUTING.md | Root | ✅ | ✅ | Docs | P1 | Bilingual – Completed #9 |
| GitHub | SECURITY.md | Root | ✅ | ✅ | Docs | P1 | Bilingual – Completed #9 |
| GitHub | SUPPORT.md | Root | ✅ | ✅ | Docs | P2 | Bilingual – Completed #9 |
| GitHub | CODE_OF_CONDUCT.md | Root | ✅ | ✅ | Docs | P2 | Bilingual – Completed #9 |
| GitHub | ROADMAP.md | Root | ⚠️ | ✅ | Docs | P2 | EN-only roadmap – Can be bilingual in future update |

---

## 5. Code Comments & Docstrings

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Code | Inline comments | Various | ❌ | ✅ | Backend | P3 | Technical comments mostly EN – Intentionally not translated |
| Code | Docstrings | Various | ❌ | ✅ | Backend | P3 | Docstrings in EN (standard practice) – Intentionally not translated |
| Code | Logging messages | Various | ❌ | ✅ | Backend | P3 | Internal logging EN-only – Intentionally not translated |

---

## Zusammenfassung / Summary

### i18n-Implementierungs-Status / i18n Implementation Status

| Bereich / Area | Status | Implementiert in / Implemented in |
|----------------|--------|-------------------------------------|
| Telegram Command Descriptions | ✅ Complete | #10/#11 |
| Telegram Command Responses (User-facing) | ✅ Complete | #10/#11 |
| Telegram Command Responses (Admin/Technical) | ⚠️ Partial | EN-only for technical errors (acceptable) |
| Consent Flow (Prompts, Buttons, Callbacks) | ✅ Complete | #10/#11 |
| Consent Block Messages | ✅ Complete | #10/#11 |
| Dispatcher Error Messages | ✅ Complete | #10/#11 |
| Flask WebUI Navigation & Labels | ✅ Complete | #10/#11 |
| Flask WebUI Forms & Tables | ✅ Complete | #10/#11 |
| Flask WebUI Language Switching | ✅ Complete | #10/#11 |
| Documentation Language Conventions | ✅ Complete | #12/#13 |
| README & Setup Guides | ✅ Complete | Previous releases |
| GitHub Templates | ✅ Complete | #9 |

### Verbleibende Lücken (Acceptable) / Remaining Gaps (Acceptable)

| # | Bereich / Area | Lücke / Gap | Begründung / Rationale |
|---|---------------|-------------|------------------------|
| 1 | Technical Admin Messages | `/setrole`, `/webui` EN-only responses | Admin-facing technical commands; operators typically comfortable with EN |
| 2 | API Error Responses | HTTP error descriptions EN-only | Internal API use, not user-facing |
| 3 | Code Comments | EN only | Standard development practice; not user-facing |
| 4 | Logging | EN only | Internal diagnostics only; not user-facing |
| 5 | GitHub Topics | EN only | GitHub platform limitation |
| 6 | ROADMAP.md | EN only | Can be bilingual in future update; low priority |

### Empfohlene Follow-up-Aktionen / Recommended Follow-up Actions

1. **Optional:** Translate remaining admin/technical messages in `/setrole` and `/webui` commands
   - Priority: Low
   - Audience: Admin users (typically EN-proficient)

2. **Optional:** Make ROADMAP.md bilingual
   - Priority: Low
   - Effort: Low

3. **Future:** Consider i18n for plugin system
   - Priority: Future consideration
   - Depends on plugin architecture evolution

---

## Anhang: Source-Dateien Referenz / Appendix: Source Files Reference

### Bot-Code (Telegram)
- `src/amo_bot/telegram/commands.py` – Command handlers and descriptions (bilingual)
- `src/amo_bot/telegram/dispatcher.py` – Message dispatching, consent blocks (bilingual)
- `src/amo_bot/consent/prompt_service.py` – Consent prompt text and buttons (bilingual)
- `src/amo_bot/consent/service.py` – Consent service logic

### WebUI-Code (Flask)
- `src/amo_bot/webui/flask_app.py` – Flask app with i18n context
- `src/amo_bot/webui/i18n.py` – Translation dictionary (DE/EN)
- `src/amo_bot/webui/flask_blueprints/` – Route handlers using `t()` helper

### Legacy (Disabled)
- `src/amo_bot/webui/app.py` – Legacy FastAPI WebUI (hard-disabled, fail-closed)

### Dokumentation
- `README.md` – Main project readme (bilingual)
- `docs/LANGUAGE_CONVENTIONS.md` – Language standards (bilingual)
- `docs/SETUP_DE.md` / `docs/SETUP_EN.md` – Setup guides
- `docs/BETATEST_DE.md` / `docs/BETATEST_EN.md` – Beta test guides
- `CHANGELOG.md` – Release notes (bilingual)
- `docs/RELEASE_NOTES_*` – Version history
- `docs/release-baseline.md` – Release readiness
- `docs/public-repo-metadata.md` – Metadata checklist

### GitHub Templates (from #9)
- `.github/ISSUE_TEMPLATE/bug_report.yml` – Bug report form (bilingual)
- `.github/ISSUE_TEMPLATE/feature_request.yml` – Feature request form (bilingual)
- `.github/PULL_REQUEST_TEMPLATE.md` – PR template (bilingual)
- `CONTRIBUTING.md` – Contribution guidelines (bilingual)
- `SECURITY.md` – Security policy (bilingual)
- `SUPPORT.md` – Support info (bilingual)
- `CODE_OF_CONDUCT.md` – Code of conduct (bilingual)

### Lizenz
- `LICENSE` – MIT License

---

## Anhang: GH-DOCS-13 – DE/EN Counterpart Decision Matrix

Dieser Abschnitt dokumentiert die Entscheidungen zu bilingualen Dokumenten-Counterparts für zukünftige Maintenance.

### Decision Matrix: Core Dokumente

| Dokument | DE-Version | EN-Version | Struktur | Rationale |
|----------|------------|------------|----------|-----------|
| README.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Haupt-Entry-Point; bilingual inline mit Sprachwahl |
| CHANGELOG.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Release-History; Versions-Entries bilingual |
| CONTRIBUTING.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Contribution-Guidelines für DE+EN Communities |
| SECURITY.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Sicherheitskontakt-Info; wichtig für beide Sprachgruppen |
| SETUP_DE.md / SETUP_EN.md | ✅ | ✅ | Separate Files | Umfangreich; separate Sprachversionen erforderlich |
| BETATEST_DE.md / BETATEST_EN.md | ✅ | ✅ | Separate Files | Umfangreich; parallele Test-Anleitungen |
| USERPLUGINS.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Plugin-Entwicklung; Zielgruppe bilingual |
| YT-RSS.md | ✅ (inline) | ✅ (inline) | Bilingual Single | EN-primary (Plugin-Beispiel), DE-Inline-Headers vorhanden |
| LANGUAGE_CONVENTIONS.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Selbst-dokumentierend bilingual |
| CONTEXT_MEMORY_ARCHITECTURE.md | ❌ (N/A) | ✅ | EN-Only | Technische Architektur-Spezifikation; Lingua Franca |
| ROADMAP.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Projekt-Richtung; Community-Update |
| CODE_OF_CONDUCT.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Verhaltenskodex; rechtlich relevant |
| SUPPORT.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Support-Info; Nutzer-relevant |
| i18n-inventory.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Meta-Dokument; bilingual by design |

### Legende: Struktur-Typen

| Typ | Verwendung | Beispiele |
|-----|------------|-----------|
| **Bilingual Single** | Eine Datei mit parallelen DE/EN-Abschnitten | README.md, USERPLUGINS.md |
| **Separate Files** | Vollständige getrennte Dateien pro Sprache | SETUP_DE.md + SETUP_EN.md |
| **EN-Only** | Technische Dokumente; Lingua Franca | CONTEXT_MEMORY_ARCHITECTURE.md |

### Crosslinks / Sprach-Referenzen

- README.md enthält Sprachwahl-Table mit Links zu SETUP_DE.md / SETUP_EN.md
- SETUP_DE.md ↔ SETUP_EN.md: Gegenseitige Verlinkung im Header
- BETATEST_DE.md ↔ BETATEST_EN.md: Gegenseitige Verlinkung im Header
- Alle bilinguale Dokumente nutzen `## Deutsch 🇩🇪` / `## English 🇬🇧` Header

### Maintenance-Checkliste (bei neuen Dokumenten)

1. [ ] Sprachstruktur entschieden (Bilingual Single / Separate Files / EN-Only)
2. [ ] Für EN-Only: Rationale dokumentiert (warum keine DE-Version)
3. [ ] Für Separate Files: Gegenseitige Crosslinks im Header
4. [ ] Für Bilingual Single: Parallele Abschnitte mit `## Deutsch` / `## English` Header
5. [ ] In LANGUAGE_CONVENTIONS.md Tabelle aktualisiert
6. [ ] In i18n-inventory.md Decision Matrix aktualisiert

### Status: GH-DOCS-13

✅ **Complete** – Alle Core-Dokumente klassifiziert und strukturiert.
- Bilingual Single: 13 Dokumente
- Separate Files: 2 Dokument-Paare (SETUP, BETATEST)
- EN-Only: 1 Dokument (CONTEXT_MEMORY_ARCHITECTURE.md)

---

*Letzte Aktualisierung / Last updated: 2026-05-27 (GH-DOCS-13 completion)*
*Issue: #14 – i18n Inventory Refresh*
*Related: #9 (GitHub templates), #10/#11 (runtime i18n), #12/#13 (language conventions), GH-DOCS-13 (doc counterparts)*
*Status: ✅ User-facing surfaces fully bilingual; technical internals EN-only by design*
