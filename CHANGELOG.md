# Changelog / Änderungsprotokoll

> **HARD STOP:** Kein push/tag/release/publication ohne explizite Matze-Freigabe.
> **HARD STOP:** No push/tag/release/publication without explicit Matze approval.

---

## [2026.05.22] – Image analysis runtime and Telegram reply fixes

**Datum / Date:** 2026-05-22

### 🇩🇪 Deutsch

#### Übersicht
Dieses Release macht die Bildanalyse in aktivierten Telegram-Topics praktisch nutzbar und dokumentiert die zugehörigen Datenschutz- und Sicherheitsgrenzen. Zusätzlich wurden Reply-Kontext und Plugin-Topic-Isolation stabilisiert.

#### Neu
- **Pro-Topic Bildanalyse-Steuerung:** Die WebUI zeigt und speichert Bildanalyse-Modi pro Topic (`inherit`, `enabled`, `disabled`); die Runtime wertet diese Einstellung vor der Analyse aus.
- **Rollenbasierte 24h-Quotas:** Die WebUI verwaltet Quotas pro Rolle; die Runtime erzwingt Rolling-24h-Limits vor dem Provider-Aufruf.
- **Automatische Telegram-Bildanalyse:** Fotos und Bild-Dokumente werden in aktivierten Topics automatisch erkannt und an den Bildanalyse-Pfad übergeben.
- **Reply-Kontext:** Telegram-Replies auf Bot- oder Nutzernachrichten erhalten allgemeinen Antwortkontext, damit Folgefragen natürlicher funktionieren.

#### Behoben
- **Async-sicherer Provider-Pfad:** Der Bildanalyse-Provider wird async-safe aufgerufen; Diagnosen bleiben privacy-safe und enthalten keine Rohbilder.
- **Telegram Photo Octet-Stream Hotfix:** Telegram-Foto-Downloads mit `application/octet-stream` werden akzeptiert, aber nur über vertrauenswürdige Telegram-Pfade und erlaubte Dateisuffixe.
- **Plugin Topic Thread Isolation:** Regression gegen Topic-Leaks abgesichert; Plugin-Antworten bleiben im richtigen Telegram-Thread isoliert.
- **Reply-Kontext gehärtet:** Kontextauflösung für Antworten wurde robuster gemacht und bleibt auf passende Bot-/User-Reply-Szenarien begrenzt.

#### Datenschutz & Sicherheit
- Bilder werden nur temporär in einem kurzlebigen Temp-Verzeichnis verarbeitet und per TTL-Cleanup bereinigt.
- Keine Rohbilder werden ins Repository, in Audit-Events oder Logs geschrieben.
- Policy-, Topic- und Quota-Prüfungen erfolgen vor dem Provider-Aufruf; blockierte Anfragen verursachen keinen Provider-Call.
- Diagnose- und Fehlermeldungen bleiben generisch/redacted und enthalten keine sensiblen Bilddaten.

### 🇬🇧 English

#### Overview
This release makes image analysis practically usable in enabled Telegram topics and documents the related privacy and security boundaries. It also stabilizes reply context and plugin topic isolation.

#### New
- **Per-topic image analysis controls:** The WebUI displays and stores image analysis modes per topic (`inherit`, `enabled`, `disabled`); runtime evaluates this setting before analysis.
- **Role-based 24h quotas:** The WebUI manages quotas per role; runtime enforces rolling 24h limits before provider invocation.
- **Automatic Telegram image analysis:** Photos and image documents are detected automatically in enabled topics and passed into the image-analysis path.
- **Reply context:** Telegram replies to bot or user messages now receive general reply context, making follow-up questions work more naturally.

#### Fixed
- **Async-safe provider path:** The image-analysis provider is invoked safely from async runtime code; diagnostics remain privacy-safe and contain no raw images.
- **Telegram photo octet-stream hotfix:** Telegram photo downloads with `application/octet-stream` are accepted, but only through trusted Telegram paths and allowed file suffixes.
- **Plugin topic thread isolation:** Regression coverage protects against topic leaks; plugin replies stay isolated to the correct Telegram thread.
- **Hardened reply context:** Reply-context resolution is more robust and remains limited to appropriate bot/user reply scenarios.

#### Privacy & Security
- Images are processed temporarily only in a short-lived temp directory and cleaned up via TTL cleanup.
- No raw images are written to the repository, audit events, or logs.
- Policy, topic, and quota checks happen before provider invocation; denied requests do not call the provider.
- Diagnostics and error messages remain generic/redacted and contain no sensitive image data.

---

## [Unreleased] – IMG-B8 Runtime Role Quota Enforcement

**Datum / Date:** 2026-05-21

### 🇩🇪 Deutsch

#### Übersicht
IMG-B8 implementiert die Runtime-Durchsetzung der rollenbasierten Bildanalyse-Quotas aus IMG-B7. Der Orchestrator liest WebUI-Quotas vor dem Provider-Aufruf und wendet Rolling-24h-Semantik an.

#### Neu
- **Runtime Quota-Prüfung:** Orchestrator liest WebUI-Quotas vor dem Provider-Aufruf.
- **Rolling 24h-Fenster:** Quota-Zählung verwendet Rolling-24h-Fenster basierend auf Audit-Timestamps (Event bei now-23h59m zählt, now-24h01m nicht).
- **Prüfreihenfolge:** Bildvalidität → Topic-Gate → Quota-Deny → Provider-Aufruf.
- **Ignore-Rolle:** Vollständig blockiert, unabhängig von Quota-Konfiguration.
- **Unbekannte/fehlende Rollen:** Werden als `disabled` behandelt.
- **Audit-Metadaten:** Quota-Deny schreibt Audit-Eintrag ohne Provider-Aufruf; Bildinhalte werden nicht in Audit gespeichert.
- **Temporäre Bildverarbeitung:** Heruntergeladene Bilder werden nach Analyse automatisch bereinigt (keine dauerhafte Speicherung).

#### Sicherheitsaspekte
- **Deny-Before-Provider:** Alle Policy-Prüfungen erfolgen vor dem Provider-Aufruf.
- **Keine Bildspeicherung:** Audit-Events enthalten nur Metadaten, keine Bildinhalte.
- **Keine Übertragung:** Rest-Quota wird nicht an Nutzer übermittelt.

### 🇬🇧 English

#### Overview
IMG-B8 implements runtime enforcement of image analysis role quotas from IMG-B7. The orchestrator reads WebUI quotas before provider invocation and applies rolling 24h window semantics.

#### New
- **Runtime quota checking:** Orchestrator reads WebUI quotas before provider invocation.
- **Rolling 24h window:** Quota counting uses rolling 24h window based on audit timestamps (event at now-23h59m counts, now-24h01m does not).
- **Check order:** Image validity → topic gate → quota deny → provider invocation.
- **Ignore role:** Completely blocked, regardless of quota configuration.
- **Unknown/missing roles:** Treated as `disabled`.
- **Audit metadata:** Quota deny writes audit entry without provider invocation; image content is not stored in audit.

#### Security Aspects
- **Deny-before-provider:** All policy checks happen before provider invocation.
- **No image storage:** Audit events contain metadata only, no image content.
- **No leakage:** Remaining quota is not communicated to users.
- **Temporary images:** Downloaded images are cleaned up after processing (no persistent storage).

---

## [Unreleased] – IMG-B7 WebUI Image Analysis Role Quotas

**Datum / Date:** 2026-05-21

### 🇩🇪 Deutsch

#### Übersicht
IMG-B7 implementiert persistente rollenbasierte Limits für Bildanalysen als Source of Truth. Die WebUI-Seite "/users" enthält einen neuen Abschnitt zur Konfiguration von Quotas für jede Rolle.

#### Neu
- **ImageAnalysisRoleQuota Tabelle:** Neue Datenbanktabelle speichert Quota-Konfiguration pro Rolle (owner, admin, vip, normal, ignore).
- **WebUI /users – Image analysis role quotas:** Neuer Abschnitt auf der Users-Seite mit Dropdowns für jeden Quota-Modus.
- **Quota-Modi:** `disabled` (deaktiviert), `unlimited` (nur Owner erlaubt), `limited` (positives Limit).
- **Konservative Defaults:** Owner = unlimited, Admin/VIP/Normal/Ignore = disabled.
- **Validierung:** `limited` erfordert positive Ganzzahl; `ignore` kann nicht auf `unlimited` gesetzt werden.
- **Source of Truth:** Diese Konfiguration ist die persistente Quelle für Runtime-Durchsetzung (IMG-B8).

#### Betriebsnotizen
- Quotas werden in `image_analysis_role_quotas` Tabelle persistiert.
- Änderungen wirken sofort auf neue Anfragen (kein Neustart erforderlich).
- IMG-B8 Runtime-Enforcement verwendet Rolling-24h-Fenster (siehe IMG-B8).

### 🇬🇧 English

#### Overview
IMG-B7 implements persistent role-based limits for image analysis as the source of truth. The WebUI "/users" page includes a new section for configuring quotas per role.

#### New
- **ImageAnalysisRoleQuota table:** New database table stores quota configuration per role (owner, admin, vip, normal, ignore).
- **WebUI /users – Image analysis role quotas:** New section on Users page with dropdowns for each quota mode.
- **Quota modes:** `disabled` (disabled), `unlimited` (owner only), `limited` (positive limit).
- **Conservative defaults:** Owner = unlimited, Admin/VIP/Normal/Ignore = disabled.
- **Validation:** `limited` requires positive integer; `ignore` cannot be set to `unlimited`.
- **Source of truth:** This configuration is the persistent source for runtime enforcement (IMG-B8).

#### Operational Notes
- Quotas are persisted in `image_analysis_role_quotas` table.
- Changes take effect immediately for new requests (no restart required).
- IMG-B8 runtime enforcement uses rolling 24h window (see IMG-B8).

---

## [Unreleased] – IMG-B5 WebUI Per-Topic Image Recognition Toggle

**Datum / Date:** 2026-05-21

### 🇩🇪 Deutsch

#### Übersicht
IMG-B5 fügt der WebUI eine pro-Topic-Einstellung für die Bilderkennung hinzu. Admins können in der Gruppendetailseite für jedes Topic festlegen, ob Bildanalyse aktiviert, deaktiviert oder vererbt werden soll.

#### Neu
- **TopicAgentConfig.image_analysis_mode:** Neue Spalte mit Werten `inherit` (Standard), `enabled`, `disabled`.
- **WebUI /groups Übersicht:** Zeigt pro Gruppe den effektiven Bildanalyse-Status an (aktiviert/deaktiviert/vererbt).
- **WebUI /groups/<chat_id> Detail:** Zeigt und erlaubt das Ändern des `image_analysis_mode` pro Topic.
- **Sicheres Default-Verhalten:** Topics mit `inherit` oder fehlender Konfiguration bleiben effektiv deaktiviert, bis explizit aktiviert.

#### Betriebsnotizen
- Der Bildanalyse-Modus wird datenbankseitig in `topic_agent_configs.image_analysis_mode` gespeichert.
- Der effektive Status wird vom WebUI angezeigt (nicht von der Laufzeit-Resolver-Logik, die mit IMG-B6 kommt).
- Änderungen wirken sofort (kein Neustart erforderlich).

### 🇬🇧 English

#### Overview
IMG-B5 adds a per-topic image recognition setting to the WebUI. Admins can configure for each topic on the group detail page whether image analysis should be enabled, disabled, or inherited.

#### New
- **TopicAgentConfig.image_analysis_mode:** New column with values `inherit` (default), `enabled`, `disabled`.
- **WebUI /groups overview:** Shows effective image analysis status per group (enabled/disabled/inherited).
- **WebUI /groups/<chat_id> detail:** Displays and allows changing `image_analysis_mode` per topic.
- **Safe default behavior:** Topics with `inherit` or missing configuration remain effectively disabled until explicitly enabled.

#### Operational Notes
- Image analysis mode is stored in `topic_agent_configs.image_analysis_mode`.
- Effective status is displayed by the WebUI (not by runtime resolver logic, which arrives with IMG-B6).
- Changes take effect immediately (no restart required).

---

## [Unreleased] – IMG-B4 Telegram Image Sending

**Datum / Date:** 2026-05-21

### 🇩🇪 Deutsch

#### Übersicht
IMG-B4 implementiert das Senden von Bildern über Telegram mit Policy/Role/Topic-Gates und sicherem Datei-Handling.

#### Neu
- **send_photo/send_document Wrapper:** Vereinfachte APIs zum Senden von Bildern über Telegram.
- **Topic-sichere message_thread_id:** Automatische Thread-Kontext-Beibehaltung bei Bildantworten in Topics.
- **MIME-Type-Auswahl:** Intelligente Auswahl zwischen send_photo (Bilder) und send_document (Dokumente/Generische Dateien).
- **Policy/Role/Topic Gates:** Bildsenden unterliegt denselben Berechtigungsprüfungen wie Textnachrichten.
- **Plugin/Command Flow:** Plugins können Bilder senden via `send_image` Capability mit vollständigem Audit-Trail.

#### User-Facing Verhalten
- Bilder werden als Antwort auf Analyseanfragen gesendet (wenn konfiguriert).
- Deny-Reasons folgen denselben Regeln wie Textnachrichten: `role_forbidden`, `topic_disabled`, `consent_required`.
- Fehler beim Senden werden generisch an Nutzer kommuniziert (keine technischen Details).

#### Sicherheitsaspekte
- Bildsenden erfordert `send_message` Capability (oder spezifische `send_image` Capability).
- Minimale Rollenprüfung analog zu Textnachrichten.
- Audit-Events für alle Bildsend-Versuche (Metadaten nur: file_id, mime_type, Größe).

### 🇬🇧 English

#### Overview
IMG-B4 implements sending images via Telegram with policy/role/topic gates and secure file handling.

#### New
- **send_photo/send_document Wrappers:** Simplified APIs for sending images via Telegram.
- **Topic-safe message_thread_id:** Automatic thread context preservation for image replies in topics.
- **MIME-Type Selection:** Intelligent selection between send_photo (images) and send_document (documents/generic files).
- **Policy/Role/Topic Gates:** Image sending is subject to the same permission checks as text messages.
- **Plugin/Command Flow:** Plugins can send images via `send_image` capability with full audit trail.

#### User-Facing Behavior
- Images are sent in response to analysis requests (when configured).
- Deny reasons follow the same rules as text messages: `role_forbidden`, `topic_disabled`, `consent_required`.
- Sending errors are communicated generically to users (no technical details).

#### Security Aspects
- Image sending requires `send_message` capability (or specific `send_image` capability).
- Minimum role checks analogous to text messages.
- Audit events for all image send attempts (metadata only: file_id, mime_type, size).

---

## [Unreleased] – IMG-B3 Real analyze_image Provider Path

**Datum / Date:** 2026-05-21

### 🇩🇪 Deutsch

#### Übersicht
IMG-B3 implementiert den echten Provider-Pfad für Bildanalyse mit Timeout-Handling, Provider-Fehler-Behandlung und sicheren, generischen Fehlermeldungen.

#### Neu
- **Echter Provider-Pfad:** `analyze_image` Capability verwendet jetzt den echten Vision-Provider statt Stub.
- **Timeout-Handling:** Konfigurierbare Timeouts für Bild-Download und Provider-Aufruf.
- **Provider-Fehler-Handling:** Klare Unterscheidung zwischen Provider-Timeout, Provider-Fehler und leeren Antworten.
- **Sichere Fehlermeldungen:** Provider-Fehler werden für Nutzer generisch/redacted dargestellt (Sicherheit).
- **MIME-Type-Validierung:** Strikte Validierung erlaubter Bildformate vor Provider-Aufruf.
- **Größenlimits:** Konfigurierbare Maximallimits für Bilddateien (Standard: 10 MB).

#### User-Facing Deny Reasons (analyse_image)
Die folgenden Ablehnungsgründe werden explizit an Nutzer kommuniziert:
- `missing_image` — Kein Bild im Kontext gefunden
- `invalid_type` — Anhang ist kein unterstütztes Bildformat (JPEG, PNG, WebP, GIF)
- `oversize` — Bild überschreitet maximale Dateigröße
- `topic_disabled` — Bildanalyse für dieses Topic deaktiviert
- `role_disabled` — Rolle hat keine Bildanalyse-Berechtigung
- `quota_exceeded` — Tageslimit für Bildanalysen erreicht
- `provider_timeout` — Bildanalyse-Provider nicht erreichbar (Timeout)
- `provider_error` — Provider-Fehler (generisch/redacted für Sicherheit)
- `provider_empty` — Provider lieferte leere Antwort (generisch/redacted für Sicherheit)

#### Betriebsnotizen
- Provider-Fehler enthalten keine technischen Details in Nutzer-Ausgaben (Sicherheit).
- Audit-Events enthalten Outcome-Codes, aber keine sensiblen Fehlerdetails.
- Fail-fast: Alle Policy-Prüfungen erfolgen vor Provider-Aufruf.

### 🇬🇧 English

#### Overview
IMG-B3 implements the real provider path for image analysis with timeout handling, provider error handling, and secure, generic error messages.

#### New
- **Real Provider Path:** `analyze_image` capability now uses the real vision provider instead of stub.
- **Timeout Handling:** Configurable timeouts for image download and provider invocation.
- **Provider Error Handling:** Clear distinction between provider timeout, provider error, and empty responses.
- **Secure Error Messages:** Provider failures are shown to users as generic/redacted (security).
- **MIME-Type Validation:** Strict validation of allowed image formats before provider call.
- **Size Limits:** Configurable maximum limits for image files (default: 10 MB).

#### User-Facing Deny Reasons (analyze_image)
The following denial reasons are explicitly communicated to users:
- `missing_image` — No image found in context
- `invalid_type` — Attachment is not a supported image format (JPEG, PNG, WebP, GIF)
- `oversize` — Image exceeds maximum file size
- `topic_disabled` — Image analysis disabled for this topic
- `role_disabled` — Role has no image analysis permission
- `quota_exceeded` — Daily image analysis limit reached
- `provider_timeout` — Image analysis provider unreachable (timeout)
- `provider_error` — Provider error (generic/redacted for security)
- `provider_empty` — Provider returned empty response (generic/redacted for security)

#### Operational Notes
- Provider errors contain no technical details in user-facing output (security).
- Audit events contain outcome codes but no sensitive error details.
- Fail-fast: All policy checks happen before provider invocation.

---

## [Unreleased] – IMG-B2b Image Analysis Quota + Topic Gate

**Datum / Date:** 2026-05-21

### 🇩🇪 Deutsch

#### Übersicht
IMG-B2b führt rollenbasierte Tageslimits, ein Topic-spezifisches Aktivierungs-Gate sowie Audit-Persistenz für die Bildanalyse ein.

#### Neu
- **Rollenbasierte Tageslimits:** Jede Benutzerrolle hat ein konfigurierbares Tageslimit für Bildanalysen:
  - `OWNER` / `ADMIN` — unbegrenzt
  - `VIP` — 5 Analysen pro Tag
  - `NORMAL` — 2 Analysen pro Tag
  - `IGNORE` — 0 (deaktiviert)
- **Topic-Gate:** Bildanalyse kann pro Topic (chat_id, message_thread_id) einzeln aktiviert/deaktiviert werden. Standard: deaktiviert.
- **Tagesbasierte Reset:** Kontingente werden täglich (UTC) zurückgesetzt.
- **Audit-Persistenz:** Alle Anfragen werden mit Outcome-Codes protokolliert (z.B. `allowed`, `quota_exceeded`, `topic_disabled`, `role_disabled`).
- **Deny-Before-Provider:** Alle Policy-Prüfungen (Rolle, Kontingent, Topic-Gate) erfolgen *vor* dem Provider-Aufruf. Keine Kosten für blockierte Anfragen.

#### Betriebsnotizen
- Topic-Policies werden aktuell datenbankseitig verwaltet (keine `.env`-Konfiguration).
- Quota-Defaults sind im Orchestrator hinterlegt; spätere Releases können externe Konfiguration ergänzen.
- Audit-Events enthalten Metadaten (user_id, chat_id, outcome), keine Bildinhalte.

### 🇬🇧 English

#### Overview
IMG-B2b introduces role-based daily limits, per-topic enable gates, and audit persistence for image analysis.

#### New
- **Role-Based Daily Limits:** Each user role has a configurable daily limit for image analyses:
  - `OWNER` / `ADMIN` — unlimited
  - `VIP` — 5 analyses per day
  - `NORMAL` — 2 analyses per day
  - `IGNORE` — 0 (disabled)
- **Topic Gate:** Image analysis can be enabled/disabled per topic (chat_id, message_thread_id). Default: disabled.
- **Day-Based Reset:** Quotas reset daily (UTC).
- **Audit Persistence:** All requests are logged with outcome codes (e.g., `allowed`, `quota_exceeded`, `topic_disabled`, `role_disabled`).
- **Deny-Before-Provider:** All policy checks (role, quota, topic gate) happen *before* provider invocation. No costs for blocked requests.

#### Operational Notes
- Topic policies are currently database-managed (no `.env` configuration).
- Quota defaults are hardcoded in the orchestrator; future releases may add external configuration.
- Audit events contain metadata (user_id, chat_id, outcome), no image content.

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
