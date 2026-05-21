# Changelog / Änderungsprotokoll

> **HARD STOP:** Kein push/tag/release/publication ohne explizite Matze-Freigabe.  
> **HARD STOP:** No push/tag/release/publication without explicit Matze approval.

---

## [2026.05.21-1] – AI stability bugfix

**Datum / Date:** 2026-05-21

### 🇩🇪 Deutsch

#### Übersicht
Kleines Stabilitäts-Release für KI-Antworten über Ollama/Kimi. Der Bot verarbeitet Thinking-Ausgaben jetzt so, dass keine leeren Antworten mehr entstehen.

#### Behoben
- **KI-Antworten stabilisiert:** Ollama/Kimi liefert wieder zuverlässig sichtbare, nutzbare Bot-Antworten.
- **Leere Antworten verhindert:** Fälle mit leeren AI-Antworten durch Thinking-Output-Handling wurden behoben.

#### Betriebsnotiz
- Nach dem Neustart wurden erfolgreiche KI-Anfragen beobachtet; keine neuen `empty response`- oder `ai_autoreply failed`-Fehler nach Patch/Restart.

---

### 🇬🇧 English

#### Overview
Small stability release for AI replies via Ollama/Kimi. The bot now handles thinking output in a way that prevents empty replies.

#### Fixed
- **Stabilized AI replies:** Ollama/Kimi now reliably returns visible, usable bot responses again.
- **Prevented empty replies:** Fixed empty AI responses caused by thinking-output handling.

#### Operational Note
- After restart, AI requests succeeded; no new `empty response` or `ai_autoreply failed` errors were observed after the patch/restart.

---

## [2026.05.21] – Local Release Candidate

**Datum / Date:** 2026-05-21

### 🇩🇪 Deutsch

#### Übersicht
Dieser lokale Release-Kandidat enthält OpenAI-Provider-Support, Verbesserungen beim KI-Kontext-Management, die A5-Kontext-/Memory-Architekturspezifikation, Abschaltung der veralteten FastAPI-WebUI, gehärtete CSP-Richtlinien sowie vollständige Sandbox-Isolation für Command-, Scheduled- und Worker-Plugin-Runtimes.

#### Neu (Highlights)
- **OpenAI Provider Support:** Alternative AI provider for `/ask` and auto-reply features
  - Configure via `.env`: `AI_PROVIDER` (ollama/openai), `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`
  - Runtime provider selection without code changes
  - Secure API key handling with redaction in diagnostics
- **Scoped Recent Context (default ON):** Pro-Scope (Topic/Gruppe/Privat) werden jetzt bis zu 20 normale Nachrichten persistiert. Der KI-Prompt erhält automatisch das passende Recent Context-Fenster, basierend auf der Router-Entscheidung.
- **Group/Topic Trigger-Guard beibehalten:** In Gruppen/Topics antwortet die KI weiterhin nur bei Mention (`@botname`) oder echtem Reply-to-Bot (Owner ist stets eingeschlossen).
- **FastAPI WebUI hard-disabled:** Die alte FastAPI-WebUI-Oberfläche wurde komplett deaktiviert; nur Flask-WebUI bleibt unterstützt.
- **Flask CSP gehärtet:** `style-src 'unsafe-inline'` entfernt; Styles wurden in statische CSS-Datei (`/static/webui.css`) verschoben.
- **Command Sandbox Hardening (GitHub Issue #2):**
  - SEC-SB2: Protokoll-Vertrag `command.execute.v1` mit typisiertem Request/Response-Validierung
  - SEC-SB3: Worker-Adapter für Sandbox-Ausführung mit sicherer Plugin-Entry-Auflösung
  - SEC-SB4: Commands laufen jetzt immer durch Sandbox-Worker (Cutover); Legacy-In-Process-Pfad entfernt
  - SEC-SB5: Audit- und Fehlercode-Härtung ohne Traceback-Leakage

#### Sicherheit / Security
- **GH-SEC-5/6 – Command Runtime Sandbox Isolation (Cutover):** Command-Plugin-Ausführung jetzt immer über Sandbox-Worker (`command.execute.v1`); veralteter In-Process Command-Pfad entfernt. Command-Worker erzwingt `send_message`-Capability für alle Send/Reply-Operationen.
- **GH-SEC-5/6 – Scheduled + Worker Runtime Sandbox Isolation:** Plugin-Ausführung für Scheduled- und Worker-Runtime jetzt vollständig über Sandbox-Worker (`command.execute.v1`) mit Capability-Enforcement (`plugin.runtime.schedule.execute`, `plugin.runtime.worker.execute`), striktem Op-Replay und sanitized Errors. Worker-Timeout reduziert auf 3s.

#### Architektur / Interna
- **A5 Context & Memory Architecture:** Architektur-/Spezifikationsdokument für Kontext-Layer, Telegram-Identity-Scope, Memory-Promotion-Policy, Auditmodell sowie DM/Gruppe/Topic-Isolation ergänzt (`docs/CONTEXT_MEMORY_ARCHITECTURE.md`).
- **AI Response Contract (AI-LAT-B3):** Interner Vertrag zwischen Provider-Response und Bot-Ausgabe; aktuell wird Ollama-Volltext über `envelope_from_full_response_text` normalisiert. Semantik ist fail-closed (ungültige/leere Responses werden abgelehnt). Vorbereitung für inkrementelles Streaming ohne aktiviertes Live-Streaming.
- **AI Empty Response Classification:** Leere oder ungültige Provider-Antworten werden intern spezifisch als `empty_response` bzw. `invalid_response` klassifiziert statt generisch als `other`.

#### Bekannte Einschränkungen / Betriebsnotizen
- **Command Runtime:** Ab diesem Release werden Commands immer über den Sandbox-Worker ausgeführt (vollständige Isolation).
- **Scheduled/Worker Runtime:** Scheduled- und Worker-Plugins laufen jetzt immer über den Sandbox-Worker (`command.execute.v1`). Worker-Timeout (Default: 60s, max 60s) wird als normaler Heartbeat/Slice-Timeout behandelt (kein Crash, sanftes Retry).
- **Transportmodus:** Long Polling bleibt aktueller Beta-Modus; Webhook-Migration ist in diesem Release nicht enthalten
- **Cross-Platform:** Linux validiert; macOS/Windows Native-Smoke-Tests noch nicht abgeschlossen (keine nativen Runner verfügbar)

---

### 🇬🇧 English

#### Overview
This local release candidate includes OpenAI provider support, AI context-management improvements, the A5 context/memory architecture specification, removal of the legacy FastAPI WebUI, hardened CSP policies, and complete sandbox isolation for command, scheduled, and worker plugin runtimes.

#### New (Highlights)
- **OpenAI Provider Support:** Alternative AI provider for `/ask` and auto-reply features
  - Configure via `.env`: `AI_PROVIDER` (ollama/openai), `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`
  - Runtime provider selection without code changes
  - Secure API key handling with redaction in diagnostics
- **Scoped Recent Context (default ON):** Up to 20 normal messages are now persisted per scope (topic/group/private). The AI prompt automatically receives the appropriate recent context window based on the router decision.
- **Group/Topic Trigger Guard Preserved:** In groups/topics, AI replies only on mention (`@botname`) or genuine reply-to-bot (owner always included).
- **FastAPI WebUI Hard-Disabled:** The legacy FastAPI WebUI surface has been completely disabled; only Flask WebUI remains supported.
- **Flask CSP Hardened:** Removed `style-src 'unsafe-inline'`; styles moved to static CSS file (`/static/webui.css`).
- **Command Sandbox Hardening (GitHub Issue #2):**
  - SEC-SB2: Protocol contract `command.execute.v1` with typed request/response validation
  - SEC-SB3: Worker adapter for sandbox execution with safe plugin-entry resolution
  - SEC-SB4: Commands now always run through sandbox worker (cutover); legacy in-process path removed
  - SEC-SB5: Audit and error code hardening without traceback leakage

#### Security
- **GH-SEC-5/6 – Command Runtime Sandbox Isolation (Cutover):** Command plugin execution now always routes through sandbox worker (`command.execute.v1`); legacy in-process command execution path removed. Command worker enforces `send_message` capability for all send/reply operations.
- **GH-SEC-5/6 – Scheduled + Worker Runtime Sandbox Isolation:** Scheduled and worker plugin execution now fully routed through sandbox worker (`command.execute.v1`) with capability enforcement (`plugin.runtime.schedule.execute`, `plugin.runtime.worker.execute`), strict op replay, and sanitized errors. Worker timeout reduced to 3s.

#### Architecture / Internal
- **A5 Context & Memory Architecture:** Added architecture/spec document for context layers, Telegram identity scoping, memory-promotion policy, audit model, and DM/group/topic isolation (`docs/CONTEXT_MEMORY_ARCHITECTURE.md`).
- **AI Response Contract (AI-LAT-B3):** Internal contract between provider response and bot output; Ollama full-text is currently normalized via `envelope_from_full_response_text`. Semantics are fail-closed (invalid/empty responses are rejected). Prepares for incremental streaming without live streaming currently enabled.
- **AI Empty Response Classification:** Empty or invalid provider responses are now classified internally as `empty_response` / `invalid_response` instead of generic `other`.

#### Known Limitations / Operational Notes
- **Command Runtime:** Commands now always execute via sandbox worker (complete isolation).
- **Scheduled/Worker Runtime:** Scheduled and worker plugins now always run through sandbox worker (`command.execute.v1`). Worker timeout (default: 60s, max 60s) is treated as a normal heartbeat/slice timeout (no crash, graceful retry).
- **Transport Mode:** Long Polling remains current beta mode; webhook migration is not included in this release
- **Cross-Platform:** Linux validated; macOS/Windows native smoke tests not yet completed (no native runners available)

---

## [Unreleased] – 2026.05.16 (Public Release Candidate)

**Datum / Date:** 2026-05-16

---

### Security

- SEC-SB4: Added default-off runtime switch `PLUGIN_COMMAND_SANDBOX_ENABLED` to route command-plugin execution optionally through the sandbox worker path (`command.execute.v1`) after existing policy checks, with safe op replay and sanitized error handling.
- SEC-SB3: Added plugin command sandbox worker adapter (`command.execute.v1`) with safe relative plugin-entry resolution, restricted recording host API (`send_message`/`reply` only), protocol-bound op/text limits, and sanitized error mapping without traceback leakage.

### 🇩🇪 Deutsch

#### Übersicht

Dies ist der erste öffentliche Release-Kandidat des AMO Telegram Bot. Die Software ist funktional und getestet unter Linux, jedoch nicht vollständig validiert unter macOS und Windows.

#### Was ist neu

- **Core Bot:** Vollständig implementiert mit rollenbasiertem Berechtigungssystem (Owner, Admin, VIP, Normal, Ignore), Consent-Management und grundlegenden Befehlen
- **Plugin-System:** Manifest-basierter Plugin-Loader mit Discovery, Validierung, Registry und Aktivierung (I1-I6 abgeschlossen)
- **WebUI:** Lokale Flask-Oberfläche zur Verwaltung und Konfiguration
- **KI-Integration:** `/ask`-Befehl, Auto-Antworten und Memory-System (Daily + Long Memory) über Ollama
- **Topic-Agent-System:** Konfigurierbares KI-Verhalten pro Topic mit Memory-Kuratierung (KI-A bis KI-F4 abgeschlossen)
- **Core Plugins:** Policy-gesteuerte KI-Fähigkeiten für RSS-Feeds, Websuche, Webscraping, API-Integration, Context-Window-Builder, Memory-Management, SQL-Lesezugriff (Templates/Views) und Selbstverbesserungs-Vorschläge (CP-I1 und CP-Z1 abgeschlossen)
- **Image Analysis Coreplugin:** Sichere Bildanalyse-Schnittstelle (IMG-B4..IMG-B7) – default-off, Stub-Implementierung mit Policy/Consent-Checks
- **Dokumentation:** Bilinguale README, Setup-Guides und Beta-Test-Anleitungen

#### Bekannte Einschränkungen

- **Repository-Status:** Privat – öffentliche Freigabe pending expliziter Matze-Freigabe
- **Cross-Platform:** Linux validiert; macOS/Windows Smoke-Tests pending (keine nativen Runner verfügbar)
- **Python-Version:** 3.12 erforderlich; 3.13 nicht getestet
- **Produktionslast:** Schwere Produktionslast noch nicht validiert
- **CI-Status:** GitHub Actions konfiguriert; Cross-Platform-Tests unter Linux validiert, macOS/Windows native Tests pending (keine nativen Runner verfügbar)

#### Sicherheit

- WebUI ist **lokal only** (`127.0.0.1`) – niemals ins Internet freigeben
- Flask-WebUI-CSP gehärtet: `style-src 'unsafe-inline'` entfernt, Styles auf statische Datei (`/static/webui.css`) umgestellt
- Alle Secrets gehören in `.env` (bereits in `.gitignore`)
- Für öffentliche Deployments: Reverse Proxy mit HTTPS verwenden

---

### 🇬🇧 English

#### Overview

This is the first public release candidate of the AMO Telegram Bot. The software is functional and tested on Linux, but not fully validated on macOS and Windows.

#### What's New

- **Core Bot:** Fully implemented with role-based permission system (Owner, Admin, VIP, Normal, Ignore), consent management, and basic commands
- **Plugin System:** Manifest-based plugin loader with discovery, validation, registry, and activation (I1-I6 complete)
- **WebUI:** Local Flask interface for management and configuration
- **AI Integration:** `/ask` command, auto-replies, and memory system (Daily + Long Memory) via Ollama
- **Topic Agent System:** Configurable per-topic AI behavior with memory curation (KI-A to KI-F4 complete)
- **Core Plugins:** Policy-controlled AI capabilities for RSS feeds, web search, web scraping, API integration, context window builder, memory management, SQL read-only access (templates/views), and self-improvement proposals (CP-I1 and CP-Z1 complete)
- **Image Analysis Coreplugin:** Secure image analysis interface (IMG-B4..IMG-B7) — default-off, stub implementation with policy/consent checks
- **Documentation:** Bilingual README, setup guides, and beta test instructions

#### Known Limitations

- **Repository Status:** Private – public release pending explicit Matze approval
- **Cross-Platform:** Linux validated; macOS/Windows smoke tests pending (no native runners available)
- **Python Version:** 3.12 required; 3.13 not tested
- **Production Load:** Heavy production load not yet validated
- **CI Status:** GitHub Actions configured; cross-platform tests validated on Linux, macOS/Windows native tests pending (no native runners available)

#### Security
- Added strict `command.execute.v1` sandbox command protocol contract (typed request/response validation, host-op allowlist, and defensive limits checks) as preparatory hardening without runtime integration switch yet.

- WebUI is **local-only** (`127.0.0.1`) – never expose to the internet
- Flask WebUI CSP hardened: removed `style-src 'unsafe-inline'` and moved styles to static stylesheet (`/static/webui.css`)
- All secrets belong in `.env` (already in `.gitignore`)
- For public deployments: Use reverse proxy with HTTPS

---

## Projekt-Status / Project Status

| Release Readiness Block | Status |
|------------------------|--------|
| RR-01 – Release Baseline + Support Matrix | ✅ Complete |
| RR-02 – Public Repo Metadata + License Decision Prep | ✅ Complete |
| RR-03 – i18n Inventory: Bot + Flask UI + Repo Docs | ✅ Complete |
| RR-04 – Bot Bilingual Completion | ✅ Complete |
| RR-05 – Flask UI Bilingual Completion | ✅ Complete |
| RR-06 – README + Quickstart Public Polish | ✅ Complete |
| RR-07 – Cross-Platform Setup Docs: Windows/macOS/Linux | ✅ Complete |
| RR-08 – Contribution Guide | ✅ Complete |
| RR-09 – GitHub Issue Templates: Bug + Feature | ✅ Complete |
| RR-10 – PR Template + Review Checklist | ✅ Complete |
| RR-11 – Security + Support + Conduct Docs | ✅ Complete |
| RR-12 – Public Roadmap | ✅ Complete |
| RR-13 – Cross-Platform Smoke Validation | ⚠️ Partial (Linux pass, macOS/Windows blocked) |
| RR-14 – Minimal CI Check Decision | ✅ Complete |
| RR-15 – Release Notes + Changelog Publicization | ✅ Complete |

---

## Weitere Informationen / Further Information

- **Vollständige Dokumentation / Full documentation:** Siehe `README.md` und `docs/`-Ordner
- **Release-Baseline:** Siehe `docs/release-baseline.md`
- **Roadmap:** Siehe `ROADMAP.md`
- **Mitwirken / Contributing:** Siehe `CONTRIBUTING.md`

---

*Letzte Aktualisierung / Last updated: 2026-05-21*
