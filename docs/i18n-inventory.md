# i18n Inventory / Übersicht der Internationalisierung

> **Scope:** RR-03 – i18n Inventory: Bot + Flask UI + Repo Docs
> **Status:** Inventory only – no code/text fixes (RR-04/RR-05/RR-06+)

---

## Deutsch

Dieses Dokument listet alle sprachsensitiven Oberflächen des AMO Telegram Bots auf. Es dient als Planungs- und Tracking-Grundlage für die vollständige Zweisprachigkeit (Deutsch + Englisch).

**Wichtig:** Dies ist ein reines Inventar-Dokument. Keine Code- oder Textänderungen werden hier durchgeführt. Festgestellte Lücken werden an die zuständigen RR-Blöcke (RR-04 Bot, RR-05 Flask UI, RR-06+ Docs) weitergegeben.

---

## English

This document lists all language-sensitive surfaces of the AMO Telegram Bot. It serves as planning and tracking foundation for complete bilingual support (German + English).

**Important:** This is a pure inventory document. No code or text changes are made here. Identified gaps are forwarded to the responsible RR blocks (RR-04 Bot, RR-05 Flask UI, RR-06+ Docs).

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
| **Notes/Gap** | Befunde, Lücken, Zuordnung zu RR-Block |

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
| Bot | `/ping` description | `src/amo_bot/telegram/commands.py:467` | ❌ | ✅ | Backend | P1 | "Health check" – only EN, needs DE |
| Bot | `/help` description | `src/amo_bot/telegram/commands.py:468` | ❌ | ✅ | Backend | P1 | "List available commands" – only EN |
| Bot | `/role` description | `src/amo_bot/telegram/commands.py:469` | ❌ | ✅ | Backend | P1 | "Show your current role" – only EN |
| Bot | `/start` description | `src/amo_bot/telegram/commands.py:470` | ❌ | ✅ | Backend | P1 | "Start consent flow in private chat" – only EN |
| Bot | `/accept` description | `src/amo_bot/telegram/commands.py:471` | ❌ | ✅ | Backend | P1 | "Accept consent" – only EN |
| Bot | `/decline` description | `src/amo_bot/telegram/commands.py:472` | ❌ | ✅ | Backend | P1 | "Decline consent" – only EN |
| Bot | `/consent` description | `src/amo_bot/telegram/commands.py:473` | ❌ | ✅ | Backend | P1 | "Show consent status" – only EN |
| Bot | `/ask` description | `src/amo_bot/telegram/commands.py:474` | ❌ | ✅ | Backend | P1 | "Ask Ollama: /ask <question>" – only EN |
| Bot | `/setrole` description | `src/amo_bot/telegram/commands.py:478` | ❌ | ✅ | Backend | P1 | "Set role: /setrole <telegram_user_id> <role>" – only EN |
| Bot | `/test` description | `src/amo_bot/telegram/commands.py:485` | ❌ | ✅ | Backend | P1 | "Send inline-button smoke test" – only EN |
| Bot | `/webui` description | `src/amo_bot/telegram/commands.py:491` | ❌ | ✅ | Backend | P1 | "WebUI access window: /webui <on|off|status>" – only EN |

### 1.3 Command Responses / Confirmations

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Bot | `/ping` response | `src/amo_bot/telegram/commands.py:71` | ✅ | ✅ | Backend | P0 | "pong" – universal |
| Bot | `/role` response | `src/amo_bot/telegram/commands.py:99` | ❌ | ✅ | Backend | P1 | "your role: {role}" – only EN |
| Bot | `/accept` success | `src/amo_bot/telegram/commands.py:293` | ❌ | ✅ | Backend | P1 | "consent accepted..." – only EN |
| Bot | `/decline` success | `src/amo_bot/telegram/commands.py:306` | ❌ | ✅ | Backend | P1 | "consent declined..." – only EN |
| Bot | `/consent` in group | `src/amo_bot/telegram/commands.py:318` | ✅ | ⚠️ | Backend | P1 | "for privacy, please use..." – EN only, but DE variant in code path exists? |
| Bot | `/consent` status text | `src/amo_bot/telegram/commands.py:325-332` | ❌ | ✅ | Backend | P1 | Status explanations in EN only |
| Bot | `/setrole` success | `src/amo_bot/telegram/commands.py:287` | ❌ | ✅ | Backend | P1 | "role updated..." / "no change..." – EN only |
| Bot | `/setrole` permission denied | `src/amo_bot/telegram/commands.py:163` | ❌ | ✅ | Backend | P1 | "permission denied" – EN only |
| Bot | `/setrole` usage error | `src/amo_bot/telegram/commands.py:169` | ❌ | ✅ | Backend | P1 | "usage: /setrole..." – EN only |
| Bot | `/setrole` invalid user ID | `src/amo_bot/telegram/commands.py:178` | ❌ | ✅ | Backend | P1 | "invalid telegram_user_id" – EN only |
| Bot | `/setrole` invalid role | `src/amo_bot/telegram/commands.py:182-183` | ❌ | ✅ | Backend | P1 | "invalid role..." – EN only |
| Bot | `/ask` empty prompt | `src/amo_bot/telegram/commands.py:343` | ❌ | ✅ | Backend | P1 | "usage: /ask <question>" – EN only |
| Bot | `/ask` no AI service | `src/amo_bot/telegram/commands.py:345` | ❌ | ✅ | Backend | P1 | "AI service is not configured" – EN only |
| Bot | `/ask` Ollama error | `src/amo_bot/telegram/commands.py:349-352` | ❌ | ✅ | Backend | P1 | "Sorry, I cannot answer..." – EN only |
| Bot | `/webui` not private | `src/amo_bot/telegram/commands.py:363` | ⚠️ | ⚠️ | Backend | P1 | Mixed: DE response in groups, needs consolidation |
| Bot | `/webui` not owner | `src/amo_bot/telegram/commands.py:381` | ❌ | ✅ | Backend | P1 | "permission denied" – EN only |
| Bot | `/webui` status OPEN | `src/amo_bot/telegram/commands.py:403` | ❌ | ✅ | Backend | P1 | "webui access: OPEN..." – EN only |
| Bot | `/webui` status CLOSED | `src/amo_bot/telegram/commands.py:405` | ❌ | ✅ | Backend | P1 | "webui access: CLOSED" – EN only |
| Bot | `/test` button text | `src/amo_bot/telegram/commands.py:408-421` | ✅ | ✅ | Backend | P0 | Mixed DE/EN: "Inline-Button-Test: Bitte klicken." – DE |

### 1.4 Consent & Onboarding Flows

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Bot | Consent prompt text | `src/amo_bot/consent/prompt_service.py:86-98` | ✅ | ❌ | Backend | P0 | Full German text, no EN equivalent |
| Bot | Consent button Accept | `src/amo_bot/consent/prompt_service.py:74` | ✅ | ❌ | Backend | P0 | "✅ Akzeptieren" – DE only |
| Bot | Consent button Decline | `src/amo_bot/consent/prompt_service.py:75` | ✅ | ❌ | Backend | P0 | "❌ Ablehnen" – DE only |
| Bot | `/start` already accepted | `src/amo_bot/telegram/commands.py:121` | ✅ | ❌ | Backend | P1 | "Consent ist bereits akzeptiert. ✅" – DE only |
| Bot | `/start` declined status | `src/amo_bot/telegram/commands.py:124` | ✅ | ❌ | Backend | P1 | "Consent ist aktuell abgelehnt..." – DE only |
| Bot | `/start` not configured | `src/amo_bot/telegram/commands.py:106` | ❌ | ✅ | Backend | P2 | "consent management not configured" – EN only |
| Bot | `/start` wrong chat type | `src/amo_bot/telegram/commands.py:108` | ✅ | ❌ | Backend | P1 | "Bitte öffne die Policy privat über den Button." – DE only |
| Bot | Consent block message (private) | `src/amo_bot/telegram/dispatcher.py:349-354` | ✅ | ⚠️ | Backend | P0 | German text for unreachable/block, needs EN |
| Bot | Consent block message (group) | `src/amo_bot/telegram/dispatcher.py:348` | ✅ | ❌ | Backend | P0 | "Bitte kläre Consent privat mit dem Bot." – DE only |
| Bot | Callback consent accept | `src/amo_bot/telegram/dispatcher.py:254` | ✅ | ❌ | Backend | P1 | "Consent akzeptiert" – DE only |
| Bot | Callback consent decline | `src/amo_bot/telegram/dispatcher.py:259` | ✅ | ❌ | Backend | P1 | "Consent abgelehnt" – DE only |
| Bot | Callback consent not available | `src/amo_bot/telegram/dispatcher.py:229` | ✅ | ❌ | Backend | P2 | "Consent nicht verfügbar" – DE only |
| Bot | Callback profile not found | `src/amo_bot/telegram/dispatcher.py:234` | ✅ | ❌ | Backend | P2 | "Profil nicht gefunden" – DE only |
| Bot | `/accept` not configured | `src/amo_bot/telegram/commands.py:289` | ❌ | ✅ | Backend | P2 | "consent management not configured" – EN only |
| Bot | `/accept` user not found | `src/amo_bot/telegram/commands.py:292` | ❌ | ✅ | Backend | P2 | "user profile not found yet..." – EN only |
| Bot | `/decline` not configured | `src/amo_bot/telegram/commands.py:302` | ❌ | ✅ | Backend | P2 | Same as above |
| Bot | `/decline` user not found | `src/amo_bot/telegram/commands.py:305` | ❌ | ✅ | Backend | P2 | Same as above |

### 1.5 Error Messages / Fallbacks

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Bot | No commands available | `src/amo_bot/telegram/commands.py:453` | ❌ | ✅ | Backend | P2 | "no commands available" – EN only |
| Bot | Available commands header | `src/amo_bot/telegram/commands.py:454` | ❌ | ✅ | Backend | P2 | "available commands:" – EN only |
| Bot | Runtime not configured (various) | Various handlers | ❌ | ✅ | Backend | P2 | Multiple "...not configured" messages – EN only |

---

## 2. Flask UI (WebUI)

### 2.1 Navigation / Page Titles

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| WebUI | Dashboard title | `src/amo_bot/webui/app.py:83` | ❌ | ✅ | Backend | P1 | "MVP dashboard" – EN only |
| WebUI | Health check | `src/amo_bot/webui/app.py:80` | ✅ | ✅ | WebUI | P0 | {"status": "ok"} – technical |

### 2.2 API Responses / Status Messages

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| WebUI | Login error | `src/amo_bot/webui/app.py:89` | ❌ | ✅ | Backend | P1 | "missing or invalid auth" – EN only |
| WebUI | Login invalid credentials | `src/amo_bot/webui/app.py:102` | ❌ | ✅ | Backend | P1 | "invalid credentials" – EN only |
| WebUI | Mutation not allowed | `src/amo_bot/webui/app.py:91-95` | ❌ | ✅ | Backend | P1 | "WEBUI_PASSWORD not set..." – EN only |
| WebUI | Owner not configured | `src/amo_bot/webui/app.py:117-120` | ❌ | ✅ | Backend | P1 | "WEBUI_OWNER_TELEGRAM_ID not configured..." – EN only |
| WebUI | Set role warning | `src/amo_bot/webui/app.py:133` | ❌ | ✅ | Backend | P2 | "owner role assignment via webui is powerful..." – EN only |
| WebUI | Plugin policy error | `src/amo_bot/webui/app.py:158` | ❌ | ✅ | Backend | P2 | Error messages from PluginPolicyError – EN only |
| WebUI | Plugin not found | `src/amo_bot/webui/app.py:160` | ❌ | ✅ | Backend | P2 | "...not found" – EN only |

### 2.3 App Metadata (FastAPI)

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| WebUI | App title | `src/amo_bot/webui/app.py:56` | ❌ | ✅ | Backend | P2 | "AMO Telegram Bot WebUI (MVP only)" – EN only |
| WebUI | App description | `src/amo_bot/webui/app.py:58` | ❌ | ✅ | Backend | P2 | "Local-only MVP WebUI..." – EN only |
| WebUI | Default app description | `src/amo_bot/webui/app.py:170` | ❌ | ✅ | Backend | P2 | "App not configured yet..." – EN only |
| WebUI | Dashboard warning | `src/amo_bot/webui/app.py:88` | ❌ | ✅ | Backend | P2 | "MVP only. Keep local..." – EN only |

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

### 3.5 Other Documentation

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Docs | Release baseline | `docs/release-baseline.md` | ✅ | ✅ | Docs | P1 | Bilingual technical doc |
| Docs | Public repo metadata | `docs/public-repo-metadata.md` | ✅ | ✅ | Docs | P1 | Bilingual checklist |

---

## 4. GitHub Surfaces (Templates)

### 4.1 GitHub Metadata

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| GitHub | LICENSE | `LICENSE` | ✅ | ✅ | Docs | P0 | MIT License (EN only, standard) |
| GitHub | Topics/tags | GitHub UI | ❌ | ✅ | Docs | P2 | GitHub topics are typically EN only |

### 4.2 Planned Templates (RR-09, RR-10)

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| GitHub | Bug report template | `.github/ISSUE_TEMPLATE/` | N/A | N/A | Docs | P1 | To be created in RR-09 – must be bilingual |
| GitHub | Feature request template | `.github/ISSUE_TEMPLATE/` | N/A | N/A | Docs | P1 | To be created in RR-09 – must be bilingual |
| GitHub | PR template | `.github/PULL_REQUEST_TEMPLATE.md` | N/A | N/A | Docs | P1 | To be created in RR-10 – must be bilingual |
| GitHub | CONTRIBUTING.md | Root | N/A | N/A | Docs | P1 | To be created in RR-08 – must be bilingual |
| GitHub | SECURITY.md | Root | N/A | N/A | Docs | P1 | To be created in RR-11 – must be bilingual |
| GitHub | SUPPORT.md | Root | N/A | N/A | Docs | P2 | To be created in RR-11 – must be bilingual |
| GitHub | CODE_OF_CONDUCT.md | Root | N/A | N/A | Docs | P2 | To be created in RR-11 – must be bilingual |
| GitHub | ROADMAP.md | Root | N/A | N/A | Docs | P1 | To be created in RR-12 – must be bilingual |

---

## 5. Code Comments & Docstrings

| Category | Element | Source | DE Status | EN Status | Owner | Priority | Notes/Gap |
|----------|---------|--------|-----------|-----------|-------|----------|-----------|
| Code | Inline comments | Various | ❌ | ✅ | Backend | P3 | Technical comments mostly EN |
| Code | Docstrings | Various | ❌ | ✅ | Backend | P3 | Docstrings in EN (standard practice) |

---

## Zusammenfassung / Summary

### Kritische Lücken (P0/P1)

| # | Bereich / Area | Lücke / Gap | Zugeordnet zu / Assigned to |
|---|---------------|-------------|----------------------------|
| 1 | Bot Commands | All command descriptions are EN-only | RR-04 |
| 2 | Bot Responses | Most command responses are EN-only | RR-04 |
| 3 | Consent Flow | Complete consent flow is DE-only (prompt, buttons, callbacks) | RR-04 |
| 4 | Error Messages | Many error messages EN-only | RR-04 |
| 5 | WebUI | All UI messages/API responses EN-only | RR-05 |
| 6 | GitHub Templates | Not yet created, must be bilingual | RR-09, RR-10 |
| 7 | Contributing/Roadmap/Security | Not yet created | RR-08, RR-11, RR-12 |

### Empfohlene Umsetzungs-Reihenfolge / Recommended Implementation Order

1. **RR-04** – Bot Bilingual Completion
   - Add i18n framework (e.g., gettext or simple dict-based)
   - Translate all command descriptions
   - Translate all command responses
   - Make consent flow bilingual (currently DE-heavy)
   - Add language detection/preference

2. **RR-05** – Flask UI Bilingual Completion
   - Add i18n to FastAPI responses
   - Translate all error/status messages
   - Consider Accept-Language header support

3. **RR-06+** – Documentation Polish
   - Ensure all new docs follow bilingual pattern
   - Update existing docs if gaps found

4. **RR-08 bis RR-12** – GitHub Surfaces
   - Create all templates bilingual from start

---

## Anhang: Source-Dateien Referenz / Appendix: Source Files Reference

### Bot-Code (Telegram)
- `src/amo_bot/telegram/commands.py` – Command handlers and descriptions
- `src/amo_bot/telegram/dispatcher.py` – Message dispatching, consent blocks
- `src/amo_bot/consent/prompt_service.py` – Consent prompt text and buttons
- `src/amo_bot/consent/service.py` – Consent service logic

### WebUI-Code (FastAPI)
- `src/amo_bot/webui/app.py` – FastAPI routes and responses

### Dokumentation
- `README.md` – Main project readme
- `docs/SETUP_DE.md` – German setup guide
- `docs/SETUP_EN.md` – English setup guide
- `docs/BETATEST_DE.md` – German beta test guide
- `docs/BETATEST_EN.md` – English beta test guide
- `docs/RELEASE_NOTES_*` – Version history
- `docs/release-baseline.md` – Release readiness
- `docs/public-repo-metadata.md` – Metadata checklist

### Lizenz
- `LICENSE` – MIT License

---

*Letzte Aktualisierung / Last updated: 2026-05-15*
*Block: RR-03 – i18n Inventory*
*Status: Inventory complete, no code changes*
