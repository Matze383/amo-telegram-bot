# Changelog / Änderungsprotokoll

---

## [Unreleased] – Current-Info Telegram Bot Integration (Issue #74)

**Datum / Date:** 2026-06-17

### 🇩🇪 Deutsch

#### Übersicht
Integration von Current-Info Antwort-Synthese in den Telegram Bot. Current-Info wird vor dem Legacy-Webtool als automatische Antwort verwendet, wenn aktuelle Informationen benötigt werden.

#### Neu
- **AMO_CURRENT_INFO_ENABLED:** Feature-Gate zum Aktivieren/Deaktivieren der Current-Info-Integration.
- **Service-Konstruktion:** Current-Info-Service wird in `main.py` konditional bei aktiviertem Feature-Gate erstellt.
- **Dispatcher Autoreply:** Current-Info-Autoreply wird vor dem Legacy-AI-Autoreply ausgeführt; Fallback bei Timeout oder Fehler.
- **Locale-Aware Formatierung:** "Quellen:" (DE) oder "Sources:" (EN) basierend auf User-Locale.
- **Timeout-Handling:** Konfigurierbares Timeout mit Fallback zu Legacy-AI bei Überschreitung.

#### Technisch
- Neue Dispatcher-Parameter: `current_info_enabled`, `current_info_timeout_seconds`, `current_info_max_results`, `current_info_max_documents`.
- `_try_current_info_autoreply()` prüft auf Research-Bedarf und führt Current-Info-Query durch.
- `_format_current_info_telegram_answer()` formatiert Antwort mit lokalisierten Quellen.
- Alle Tests passieren.

### 🇬🇧 English

#### Overview
Integration of Current-Info answer synthesis into the Telegram bot. Current-Info is used as an automatic reply before the legacy webtool when current information is needed.

#### New
- **AMO_CURRENT_INFO_ENABLED:** Feature gate to enable/disable Current-Info integration.
- **Service Construction:** Current-Info service is conditionally built in `main.py` when feature gate is enabled.
- **Dispatcher Autoreply:** Current-Info autoreply runs before legacy AI autoreply; fallback on timeout or error.
- **Locale-Aware Formatting:** "Quellen:" (DE) or "Sources:" (EN) based on user locale.
- **Timeout Handling:** Configurable timeout with fallback to legacy AI on exceedance.

#### Technical
- New dispatcher parameters: `current_info_enabled`, `current_info_timeout_seconds`, `current_info_max_results`, `current_info_max_documents`.
- `_try_current_info_autoreply()` checks for research need and performs Current-Info query.
- `_format_current_info_telegram_answer()` formats response with localized sources.
- All tests passing.

---

## [Unreleased] – Current-Info Evidence Assembly (Issue #73)

**Datum / Date:** 2026-06-16

### 🇩🇪 Deutsch

#### Übersicht
Einführung von Evidence Assembly und Verification für Current-Info-Abfragen. Der Service liefert jetzt strukturierte Evidenz-Pakete mit Konfidenz, Warnungen und Qualitätsflags.

#### Neu
- **EvidencePackage:** Erweiterte Current-Info-Evidenz mit Chunks, Dokumenten, Quellen, Freshness, Konfidenz (0.0-1.0) und Warnungen.
- **EvidencePackageSource:** Strukturierte Quellenangaben inklusive Host, Source-Type, Fetch-Status und Stale-Flag.
- **Confidence Scoring:** Konservative Konfidenzbewertung basierend auf Fetch-Status, unabhängigen Hosts, Freshness und Konflikten.
- **Warning System:** Explizite Warnungen bei Snippet-only-Evidenz, unfetched Chunks, veralteten Daten oder Quellenkonflikten.
- **Quality Gates:** Snippets allein gelten nicht mehr als verifizierte Current-Info-Antwort; News/Evidenz wird konservativer bewertet.

#### Technisch
- Neue `current_info/evidence.py` für Evidence Assembly, angebunden an die bestehende Domain-Klassifizierung aus `webtool_evidence.py`.
- `CurrentInfoAnswer` exportiert Konfidenz und Warnungen sichtbar für nachgelagerte Nutzung.
- Tests decken Single-Source, zwei unabhängige Quellen, Konflikte, stale Quellen und Snippet-only-Rejection ab.

### 🇬🇧 English

#### Overview
Introduction of evidence assembly and verification for current-info queries. The service now delivers structured evidence packages with confidence scores, warnings, and quality flags.

#### New
- **EvidencePackage:** Extended current-info evidence with chunks, documents, sources, freshness, confidence (0.0-1.0), and warnings.
- **EvidencePackageSource:** Structured source references with host, source type, fetch status, and stale flag.
- **Confidence Scoring:** Conservative confidence assessment based on fetch status, independent hosts, freshness, and conflicts.
- **Warning System:** Explicit warnings for snippet-only evidence, unfetched chunks, stale data, or source conflicts.
- **Quality Gates:** Snippets alone no longer count as verified current-info answers; news evidence is evaluated more conservatively.

#### Technical
- New `current_info/evidence.py` for evidence assembly, connected to the existing domain classifier from `webtool_evidence.py`.
- `CurrentInfoAnswer` exposes confidence and warnings visibly for downstream usage.
- Tests cover single-source, two independent sources, conflicts, stale sources, and snippet-only rejection.

---

## [Unreleased] – Bounded Browser Provider

**Datum / Date:** 2026-06-16

### 🇩🇪 Deutsch

#### Übersicht
Einführung eines begrenzten Browser-Providers für dynamische/aktuelle Quellen mit verbessertem Output-Format, Sicherheitsbeschränkungen und manuellem Chat-Trigger.

#### Neu
- **Bounded Browser Evidence:** Browser-Ausgabe enthält URL, Seitentitel, UTC-Timestamp, HTTP-Status und gecappte Text-Snippets (keine vollständigen Seiteninhalte).
- **Per-Request Limits:** Maximale Seitenanzahl, Zeitbudget pro Request, begrenzte Snippet-Anzahl/Länge und Output-Cap.
- **Protokoll-Beschränkungen:** Nur HTTP/HTTPS erlaubt; Credentials blockiert; localhost/private/internal IPs blockiert; DNS-Auflösung zu privaten IPs blockiert.
- **Route Guard:** Blockiert private/internal Subrequests und nicht-GET/HEAD/OPTIONS-Methoden; unterdrückt Formular-Submits.
- **Browser-Telemetry:** Erfasst Erfolg, HTTP-Fehler, Timeouts und Failures.
- **Manueller Chat-Trigger:** `browser: <url>` oder `webbrowser: <url>` im Chat löst direkten Browser-Fetch aus (unterstützt http:// und https://).

#### Sicherheit & Privacy
- Keine Credentials oder Authentifizierung möglich.
- Keine privaten/lokalen Netzwerkadressen erreichbar.
- Metadata-only Logging (keine Queries, keine vollständigen URLs).

### 🇬🇧 English

#### Overview
Introduction of a bounded browser provider for dynamic/current sources with improved output format, security restrictions, and manual chat trigger.

#### New
- **Bounded Browser Evidence:** Browser output includes URL, page title, UTC timestamp, HTTP status, and capped text snippets (no full page content).
- **Per-Request Limits:** Max pages, time budget per request, limited snippet count/length, and output cap.
- **Protocol Restrictions:** HTTP/HTTPS only; credentials blocked; localhost/private/internal IPs blocked; DNS resolution to private IPs blocked.
- **Route Guard:** Blocks private/internal subrequests and non-GET/HEAD/OPTIONS methods; suppresses form submits.
- **Browser Telemetry:** Captures success, HTTP errors, timeouts, and failures.
- **Manual Chat Trigger:** `browser: <url>` or `webbrowser: <url>` in chat triggers direct browser fetch (supports http:// and https://).

#### Security & Privacy
- No credentials or authentication possible.
- No private/local network addresses reachable.
- Metadata-only logging (no queries, no full URLs).

---

## [Unreleased] – Entfernung Human-User-Consent / Removal of Human User Consent

**Datum / Date:** 2026-06-12

### 🇩🇪 Deutsch

#### Übersicht
Entfernung des expliziten Consent-Dialogs für menschliche Nutzer. Neue Human-User können den Bot automatisch nutzen.

#### Geändert
- **Consent-Pflicht entfernt:** Menschliche Nutzer benötigen keinen expliziten Consent-Dialog (`/accept`, `/consent`) mehr.
- **Automatische Nutzung:** Human-User sind direkt nach dem ersten Kontakt nutzbar (Rolle `normal` als Default).
- **Rollen-System bleibt:** Owner/Admin/VIP/Normal/Ignore-Rechte weiterhin aktiv.
- **Bot-to-Bot unverändert:** Bot-to-Bot-Kommunikation erfordert weiterhin explizite Freigabe via Consent-Commands.

#### Technisch
- Keine `pending`/`declined`/`unreachable`-Runtime-Blocks mehr für Human-User.
- Commands `/accept`, `/decline`, `/consent` weiterhin verfügbar für Bot-to-Bot-Freigabe.

### 🇬🇧 English

#### Overview
Removal of explicit consent dialog for human users. New human users can now use the bot automatically.

#### Changed
- **Consent requirement removed:** Human users no longer need an explicit consent dialog (`/accept`, `/consent`).
- **Automatic usage:** Human users are usable immediately after first contact (role `normal` as default).
- **Role system remains:** Owner/Admin/VIP/Normal/Ignore permissions remain active.
- **Bot-to-bot unchanged:** Bot-to-bot communication still requires explicit approval via consent commands.

#### Technical
- No more `pending`/`declined`/`unreachable` runtime blocks for human users.
- Commands `/accept`, `/decline`, `/consent` remain available for bot-to-bot approval.

---

## [Unreleased] – Search Planner & Auto Follow-up Research

**Datum / Date:** 2026-06-11

### 🇩🇪 Deutsch

#### Übersicht
Automatische Follow-up-Recherche mit geplantem Search-Planner: Erkennung schwacher initialer Evidenz und gezielte Verbesserung durch maximal einen Follow-up-Websearch.

#### Neu
- **ResearchPlan / SearchPlanStep:** Strukturierte Planung von Follow-up-Suchen in `webtool_research_orchestrator.py`.
- **Schwache Evidenz-Erkennung:** Auto-Research erkennt einseitige News, geringe Source-Host-Coverage oder schwache/konfliktbehaftete `source_observations`.
- **Geplanter Follow-up:** Höchstens ein geplanter `websearch` als Follow-up bei unzureichender initialer Evidenz.
- **Ergebnis-Merging:** Brauchbare Follow-up-Ergebnisse werden mit initialen Ergebnissen gemerged und durch die bestehende Search->Scrape Chain weiterverarbeitet.
- **Fail-Closed:** Bei weiterhin unzureichender Evidenz nach Follow-up: sicheres Fail-Closed-Verhalten.
- **Eval Harness:** Erweiterte Test-Infrastruktur für Search-Planner-Validierung.

#### Technisch / Privacy
- **Logging:** Metadata-only (keine raw Queries, keine vollen URLs).
- Keine neuen Commands.
- Keine neue Config erforderlich.

### 🇬🇧 English

#### Overview
Automatic follow-up research with planned search planner: detection of weak initial evidence and targeted improvement through at most one follow-up websearch.

#### New
- **ResearchPlan / SearchPlanStep:** Structured planning of follow-up searches in `webtool_research_orchestrator.py`.
- **Weak Evidence Detection:** Auto-research detects one-sided news, low source-host coverage, or weak/conflicting `source_observations`.
- **Planned Follow-up:** At most one planned `websearch` as follow-up when initial evidence is insufficient.
- **Result Merging:** Viable follow-up results are merged with initial results and processed through the existing Search->Scrape Chain.
- **Fail-Closed:** Safe fail-closed behavior when evidence remains insufficient after follow-up.
- **Eval Harness:** Extended test infrastructure for search planner validation.

#### Technical / Privacy
- **Logging:** Metadata-only (no raw queries, no full URLs).
- No new commands.
- No new config required.

---

## [Unreleased] – Web Research Provider Registry & Health Monitoring

**Datum / Date:** 2026-06-11

### 🇩🇪 Deutsch

#### Übersicht
Erweiterte Webtool-Infrastruktur mit Provider-Registry, Health-Monitoring und Quality-Gates für zuverlässigere Datenquellen.

#### Neu
- **Source/Provider Registry:** Zentrale Registry für Weather- und Crypto-Provider mit definierten Default-Kandidaten.
- **DB-Source-Auswahl:** Provider-Auswahl nutzt `research_providers` plus Health/Freshness/Observations stärker für zuverlässigere Quellen.
- **Kein Legacy-Fallback:** Explicit leere DB-Candidates fallen nicht mehr auf Legacy-Defaults zurück.
- **Disabled-Provider-Schutz:** Disabled Provider bleiben dauerhaft disabled (keine automatische Reaktivierung).
- **Source-Quality/Corroboration Gate:** News/Chain-Extracts erkennen Konflikte und zu schwache Quellen konservativer (Fail-Closed bei Unsicherheit).
- **Weather-Provider:** Open-Meteo (primär) + wttr.in (Fallback) für Wetterabfragen.
- **Crypto-Provider:** CoinGecko (primär) + Binance public ticker (Fallback), eng begrenzt auf BTC/ETH in USD/USDT; unbekannte Assets oder EUR-Paare führen zu Fail-Closed.
- **Health-Monitoring (DB-gestützt):** Provider-Health wird in der Datenbank persistiert (`research_provider_health`) und über Neustarts hinweg nutzbar.
- **Research-Tabellen:** Neue DB-Tabellen für zukünftige Research-Einheit: `research_providers`, `research_source_observations`, `research_eval_cases`.
- **Stock/Sports:** Bleiben fail-closed ohne strukturierte Provider (keine unsicheren Annahmen).
- **Quota/Audit:** Metadata-only Persistenz für Audit-Zwecke (keine raw Queries/URLs/Secrets).
- **Source Observations:** Webtool success/fail-closed/error sowie Role-/Quota-Denials erzeugen automatisch persistente Source-Observations in `research_source_observations`.
- **Eval Cases:** Negatives Research-/Source-Feedback erzeugt automatisch sanitisierte Eval-Cases in `research_eval_cases`.
- **Eval Harness/Tests:** Test-Infrastruktur für stored sanitized Eval-Cases (Validierung von Source-Quality-Gates).
- **Privacy-Gate:** Alle Observations/Eval-Cases speichern ausschließlich Metadata (keine raw Queries, keine vollständigen URLs, keine Tokens/Secrets/Bearer-artigen Werte; `source_hosts` nur Hostnamen).

#### Technisch
- **DB-Persistenz:** Provider-Health über Neustarts nutzbar; atomare Counter-Updates mit Race-Recovery bei first-create.
- **Privacy:** Keine raw Queries/URLs/Secrets in Health-Daten – nur Metadata.
- Keine neue Config erforderlich (bestehende `AMO_WEBSEARCH_SEARXNG_BASE_URL` bzw. Legacy-Alias `SEARXNG_BASE_URL` bleibt primärer Websearch-Provider).
- Keine neuen Commands.
- Fail-Closed-Verhalten bei nicht verifizierbaren Live-Daten.

#### Maintenance / Datenbank-Migration
Für bestehende Datenbanken können die Research-Tabellen gezielt über CLI erstellt werden:

```bash
# Dry-run: zeigt vorhandene/fehlende Tabellen
python -m amo_bot.db.research_tables --database-url "$DATABASE_URL" --dry-run

# Apply: erstellt fehlende Tabellen (Backup vorher empfohlen)
python -m amo_bot.db.research_tables --database-url "$DATABASE_URL"
```

**Hinweise:**
- Das Kommando erstellt **nur** die vier Research-Tabellen (`research_providers`, `research_provider_health`, `research_source_observations`, `research_eval_cases`).
- Es berührt **keine** anderen Tabellen (kein `init_db`, kein Seeding).
- **Sicherheit:** Die URL im Kommandozeilen-Argument kann in der Prozessliste sichtbar sein (ps/top). Secrets werden im Code nicht geloggt.

### 🇬🇧 English

#### Overview
Enhanced web tool infrastructure with provider registry, health monitoring, and quality gates for more reliable data sources.

#### New
- **Source/Provider Registry:** Central registry for Weather and Crypto providers with defined default candidates.
- **DB-Source Selection:** Provider selection leverages `research_providers` plus Health/Freshness/Observations more strongly for more reliable sources.
- **No Legacy Fallback:** Explicit empty DB candidates no longer fall back to Legacy defaults.
- **Disabled-Provider Protection:** Disabled providers remain permanently disabled (no automatic reactivation).
- **Source-Quality/Corroboration Gate:** News/Chain-extracts detect conflicts and overly weak sources more conservatively (fail-closed on uncertainty).
- **Weather Providers:** Open-Meteo (primary) + wttr.in (fallback) for weather queries.
- **Crypto Providers:** CoinGecko (primary) + Binance public ticker (fallback), strictly limited to BTC/ETH in USD/USDT; unknown assets or EUR pairs result in fail-closed behavior.
- **Health Monitoring (DB-backed):** Provider health persisted to database (`research_provider_health`) and survives restarts.
- **Research Tables:** New DB tables for future research unit: `research_providers`, `research_source_observations`, `research_eval_cases`.
- **Stock/Sports:** Remain fail-closed without structured providers (no unsafe assumptions).
- **Quota/Audit:** Metadata-only persistence for audit purposes (no raw queries/URLs/secrets).
- **Source Observations:** Webtool success/fail-closed/error as well as role/quota denials automatically generate persistent source observations in `research_source_observations`.
- **Eval Cases:** Negative research/source feedback automatically generates sanitized eval cases in `research_eval_cases`.
- **Eval Harness/Tests:** Test infrastructure for stored sanitized eval cases (validation of source-quality gates).
- **Privacy Gate:** All observations/eval cases store metadata only (no raw queries, no full URLs, no tokens/secrets/bearer-like values; `source_hosts` contains hostnames only).

#### Technical
- **DB Persistence:** Provider health survives restarts; atomic counter updates with race recovery on first-create.
- **Privacy:** No raw queries/URLs/secrets in health data – metadata only.
- No new config required (existing `AMO_WEBSEARCH_SEARXNG_BASE_URL` or legacy alias `SEARXNG_BASE_URL` remains primary websearch provider).
- No new commands.
- Fail-closed behavior for unverifiable live data.

#### Maintenance / Database Migration
For existing databases, the research tables can be created specifically via CLI:

```bash
# Dry-run: shows existing/missing tables
python -m amo_bot.db.research_tables --database-url "$DATABASE_URL" --dry-run

# Apply: creates missing tables (backup recommended beforehand)
python -m amo_bot.db.research_tables --database-url "$DATABASE_URL"
```

**Notes:**
- This command creates **only** the four research tables (`research_providers`, `research_provider_health`, `research_source_observations`, `research_eval_cases`).
- It does **not** touch any other tables (no `init_db`, no seeding).
- **Security:** The URL in the command-line argument may be visible in the process list (ps/top). Secrets are not logged in code.

---

## [Unreleased] – Auto Web Research Feedback Follow-up (v3)

**Datum / Date:** 2026-06-01

### 🇩🇪 Deutsch

#### Übersicht
Feedback-gesteuerte Follow-up-Recherche für den Auto Web Research Flow. Nutzer können eine weitere begrenzte Recherche-Runde auslösen, wenn die Antwort als unzureichend empfunden wird.

#### Neu
- **Feedback-Trigger:** Phrasen wie "such weiter", "andere Quellen", "öffne/prüfe die Quellen", "das reicht nicht", "search more", "more sources" lösen eine Follow-up-Recherche aus.
- **Bounded Follow-up:** SearXNG zuerst, statische Extraktion gecappt, maximal ein Browser-Fallback.
- **Transparentes Scheitern:** Falls weiterhin keine Bestätigung möglich ist, teilt der Bot dies mit.
- **Kontextnutzung:** Für die Follow-up-Suche kann Kontext aus der vorherigen Bot-Antwort/Reply verwendet werden.
- **Privacy:** Die Rohanfrage wird nicht geloggt, aber an den konfigurierten Websearch-Provider übermittelt.

### 🇬🇧 English

#### Overview
Feedback-driven follow-up research for the Auto Web Research flow. Users can trigger another bounded research round when the answer is perceived as insufficient.

#### New
- **Feedback triggers:** Phrases like "search more", "other sources", "open/check the sources", "that's not enough", "more sources" trigger a follow-up search.
- **Bounded follow-up:** SearXNG first, static extraction capped, max one browser fallback.
- **Transparent failure:** If still unconfirmed, the bot states so.
- **Context usage:** Context from the previous bot answer/reply may be used for the follow-up search query.
- **Privacy:** Raw query/context is not logged but is sent to the configured websearch provider.

---

## [Unreleased] – Auto Web Research (Search→Scrape Chain v1)

**Datum / Date:** 2026-06-01

### 🇩🇪 Deutsch

#### Übersicht
Verbesserte automatische Web-Recherche für aktuelle/zeitnahe Fragen (Markt/Kurs/Preis, News, Releases, Status, Wetter, Verkehr, Ausfälle, Versionen, Updates, etc.). Die Recherche startet mit SearXNG-Websearch und kann bei Bedarf mit bounded statischer Seitenextraktion und optional einem Browser-Fallback fortgesetzt werden.

#### Neu
- **Search→Scrape Chain:** Automatische Folge von Websuche → statische Extraktion → optionaler Browser-Fallback (max. eine URL).
- **Bounded/Transparent:** Verhalten ist begrenzt (max. eine URL bei static-extraction-miss) und transparent (keine Behauptungen über fehlende Tools).
- **Keine neue Config:** Funktioniert mit bestehender SearXNG-Konfiguration (`AMO_WEBSEARCH_SEARXNG_BASE_URL` oder Legacy-Alias `SEARXNG_BASE_URL`).
- **Keine neuen Commands:** Vollständig automatisch bei entsprechenden Intents (Fragen zu aktuellen Werten).

#### Verhalten
- Bei Fragen nach aktuellen Werten (Kurse, Preise, News, Status, Wetter, Verkehr, etc.) wird zuerst SearXNG-Websearch verwendet.
- Bei "freshness + current data"-Intents folgt ggf. statische Extraktion der Top-URL.
- Zeitlose/allgemeine Bildungsfragen lösen die Kette nicht aus.
- Falls static extraction leer/unbrauchbar: maximal ein Chromium/Browser-Fallback für eine URL.
- Kann keine exakten aktuellen Werte bestätigen → Bot antwortet wahrheitsgemäß, dass Websuche erfolgreich war, Extraktion aber keine exakten Werte lieferte.

#### Sicherheit & Privacy
- Bounded: Max. eine URL pro Anfrage bei Browser-Fallback.
- Transparent: Keine Behauptungen über "fehlende Webtools".
- Keine Rohinhalte in Audit-Logs (nur Metadaten).

### 🇬🇧 English

#### Overview
Enhanced automatic web research for current/freshness-relevant questions (market/rate/price, news, releases, status, weather, traffic, outages, versions, updates, etc.). Research starts with SearXNG websearch and can continue with bounded static page extraction and optional browser fallback.

#### New
- **Search→Scrape Chain:** Automatic sequence of web search → static extraction → optional browser fallback (max one URL).
- **Bounded/Transparent:** Behavior is bounded (max one URL on static-extraction-miss) and transparent (no claims about missing tools).
- **No new config:** Works with existing SearXNG configuration (`AMO_WEBSEARCH_SEARXNG_BASE_URL` or legacy alias `SEARXNG_BASE_URL`).
- **No new commands:** Fully automatic for relevant intents (current value questions).

#### Behavior
- For current value questions (rates, prices, news, status, weather, traffic, etc.), SearXNG websearch is used first.
- For "freshness + current data" intents, static extraction of top URL may follow.
- Timeless/general educational questions do not trigger the chain.
- If static extraction is empty/unusable: max one Chromium/browser fallback for one URL.
- If exact current values cannot be confirmed → bot truthfully states web search succeeded but extraction did not provide exact values.

#### Security & Privacy
- Bounded: Max one URL per request on browser fallback.
- Transparent: No claims about "missing webtools".
- No raw content in audit logs (metadata only).

---

## [Unreleased] – Admin Prompt Context Docs Commands

**Datum / Date:** 2026-06-01

### 🇩🇪 Deutsch

#### Übersicht
Neue admin-only Telegram-Commands zur Verwaltung von DB-gestützten Prompt Context Docs. Diese Docs steuern das Bot-Verhalten für KI-Antworten und sind von Memory.md getrennt (keine Lernkurve).

#### Neu
- **`/ctxdoc_set <kind> <global|topic> <text...>`:** Erstellt/überschreibt Context Doc für Kind/Scope
- **`/ctxdoc_get <kind> <global|topic>`:** Zeigt aktuellen Context Doc an
- **`/ctxdoc_del <kind> <global|topic>`:** Löscht Context Doc
- **`/ctxdoc_list [scope] [kind]`:** Listet alle Context Docs auf (optional filterbar)

#### Details
- **Kinds:** `AGENT`, `SOUL`, `PLUGINS`, `AUFGABE`
- **Scopes:** `global` (gilt überall) oder `topic` (nur aktuelles Telegram-Topic)
- **Langer Text:** Reply-to Text/Caption kann für lange Inhalte verwendet werden
- **Berechtigung:** Nur Owner/Admin

#### Limitation
- Kein WebUI/API-Editor in diesem Release — Verwaltung ausschließlich über Telegram-Commands
- Docs sind separate Config-Ebene (nicht Memory.md)

### 🇬🇧 English

#### Overview
New admin-only Telegram commands for managing DB-backed prompt context docs. These docs control bot behavior for AI responses and are separate from Memory.md (no learning curve).

#### New
- **`/ctxdoc_set <kind> <global|topic> <text...>`:** Creates/overwrites context doc for kind/scope
- **`/ctxdoc_get <kind> <global|topic>`:** Shows current context doc
- **`/ctxdoc_del <kind> <global|topic>`:** Deletes context doc
- **`/ctxdoc_list [scope] [kind]`:** Lists all context docs (optionally filterable)

#### Details
- **Kinds:** `AGENT`, `SOUL`, `PLUGINS`, `AUFGABE`
- **Scopes:** `global` (applies everywhere) or `topic` (current Telegram topic only)
- **Long text:** Reply-to text/caption can be used for long content
- **Permission:** Owner/Admin only

#### Limitation
- No WebUI/API editor in this release — management via Telegram commands only
- Docs are separate config layer (not Memory.md)

---

## [Unreleased] – Daily Memory Runtime (OpenClaw-like Light Memory)

**Datum / Date:** 2026-05-31

### 🇩🇪 Deutsch

#### Übersicht
Neues OpenClaw-ähnliches Daily/Light Memory Runtime-System als leichtere Alternative zu Dreaming. Erstellt begrenzte tägliche Zusammenfassungen pro Scope (Topic/Private-Chat) ohne vollständige Memory-Kuratierung. Kann parallel zu Dreaming oder standalone betrieben werden.

#### Neu
- **Daily Memory Runtime:** Leichtgewichtiges Memory-System mit begrenzten täglichen Zusammenfassungen
  - Neue Konfigurationsfelder: `MEMORY_DAILY_ENABLED` (Standard: 0), `MEMORY_DAILY_INTERVAL_SECONDS` (Standard: 21600), `MEMORY_DAILY_MAX_INPUT_MESSAGES` (Standard: 200), `MEMORY_DAILY_MAX_CHARS_PER_MESSAGE` (Standard: 500), `MEMORY_DAILY_MAX_SUMMARY_CHARS` (Standard: 6000), `MEMORY_DAILY_MIN_MESSAGES` (Standard: 1), `MEMORY_DAILY_MAX_SCOPES_PER_RUN` (Standard: 10)
  - Läuft im gleichen Nachtfenster wie Dreaming (02:00–05:00 Europe/Berlin)
  - Bounded Input/Output: Nachrichtenanzahl und Zusammenfassungslänge sind konfigurierbar begrenzt
  - No-Overlap mit Dreaming: Respektiert interne Sperre (max. ein Memory-Job gleichzeitig)
- **Dreaming erhalten:** Nightly Long Memory Curation (Dreaming) bleibt vollständig erhalten und unabhängig

#### Sicherheit & Privacy
- **Bounded Output:** Zusammenfassungen sind strikt zeichenbegrenzt (`MEMORY_DAILY_MAX_SUMMARY_CHARS`)
- **Metadata-only Logs:** Audit-Events enthalten nur Metadaten (Scope, Nachrichtenanzahl, Timestamp); kein Rohtext in Logs
- **Summary Privacy:** Zusammenfassungen werden in DB gespeichert (Digest möglich), Logs enthalten nur Metadaten
- **No Secrets in Config:** Alle neuen Konfigurationsfelder sind environment-basiert; keine Secrets erforderlich

#### Konfiguration
Siehe `.env.example` und `docs/SETUP_DE.md` für vollständige Dokumentation der neuen Daily Memory Einstellungen.

### 🇬🇧 English

#### Overview
New OpenClaw-like Daily/Light Memory runtime system as a lighter alternative to Dreaming. Creates bounded daily summaries per scope (topic/private chat) without full memory curation. Can run alongside Dreaming or standalone.

#### New
- **Daily Memory Runtime:** Lightweight memory system with bounded daily summaries
  - New config fields: `MEMORY_DAILY_ENABLED` (default: 0), `MEMORY_DAILY_INTERVAL_SECONDS` (default: 21600), `MEMORY_DAILY_MAX_INPUT_MESSAGES` (default: 200), `MEMORY_DAILY_MAX_CHARS_PER_MESSAGE` (default: 500), `MEMORY_DAILY_MAX_SUMMARY_CHARS` (default: 6000), `MEMORY_DAILY_MIN_MESSAGES` (default: 1), `MEMORY_DAILY_MAX_SCOPES_PER_RUN` (default: 10)
  - Runs during same night window as Dreaming (02:00–05:00 Europe/Berlin)
  - Bounded Input/Output: Message count and summary length are configurable and capped
  - No-overlap with Dreaming: Respects internal lock (at most one memory job at a time)
- **Dreaming Preserved:** Nightly Long Memory Curation (Dreaming) remains fully intact and independent

#### Security & Privacy
- **Bounded Output:** Summaries are strictly character-limited (`MEMORY_DAILY_MAX_SUMMARY_CHARS`)
- **Metadata-only Logs:** Audit events contain only metadata (scope, message count, timestamp); no raw text in logs
- **Summary Privacy:** Summaries stored in DB (digest possible), logs contain metadata only
- **No Secrets in Config:** All new configuration fields are environment-based; no secrets required

#### Configuration
See `.env.example` and `docs/SETUP_EN.md` for complete documentation of new Daily Memory settings.

---

## [Unreleased] – Bugfix Weekend: Telegram Image Analysis + Websearch/SearXNG

**Datum / Date:** 2026-05-30

### 🇩🇪 Deutsch

#### Übersicht
Wochenend-Release mit Korrekturen für Bildanalyse-Verhalten in privaten Chats und Websearch/SearXNG-Konfiguration.

#### Bildanalyse-Verhalten (Telegram)
- **Private Chats (1:1):** Fotos und Bild-Dokumente werden automatisch in den Bildanalyse-Pfad geleitet, ohne explizite Adressierung.
- **Gruppen:** Bildanalyse bleibt defensiv – nur bei Adressierung/Antwort/Caption/Follow-up-Regeln.
- **Vision-Provider-Fehler:** Wenn der Vision-Provider nicht verfügbar oder nicht konfiguriert ist, bestätigt der Bot den Empfang des Bildes und teilt mit, dass die Analyse nicht verfügbar/nicht konfiguriert ist. Der Bot behauptet nicht mehr fälschlicherweise, dass Telegram kein Bild gesendet hat oder dass keine Bilder empfangen werden können.
- **Ollama Vision-Modelle:** Neue Einstellung `IMAGE_ANALYSIS_OLLAMA_VISION_MODELS` zur expliziten Freigabe von Vision-Modellen für die Bildanalyse. Standard: `llava,llama3.2-vision,qwen2.5vl`. Nicht-standardnamen (z.B. `kimi-k2.5:cloud`) können explizit erlaubt werden.
- **Fail-Closed für unbrauchbare Antworten:** Generische Ablehnungs-/Policy-Antworten des Providers werden als Fehler behandelt und auf eine wahrheitsgemäße "Nicht verfügbar"-Nachricht abgebildet.

#### Websearch/SearXNG
- **Konfiguration:** Websearch nutzt primär `AMO_WEBSEARCH_SEARXNG_BASE_URL` (Legacy-Alias: `SEARXNG_BASE_URL`).
- **Fail-closed:** Ohne konfigurierten SearXNG-Endpoint wird keine öffentliche Fallback-Suche verwendet; stattdessen wird leer/abgelehnt zurückgegeben. Wenn SearXNG konfiguriert ist, wird ausschließlich SearXNG verwendet – auch bei leeren/fehlerhaften Ergebnissen.
- **URL-Sicherheit:** Öffentliche HTTP-URLs werden abgelehnt; HTTPS-URLs sind erlaubt. HTTP nur für Loopback/Private/Interne Netzwerke.
- **Browser-Provider-Abhängigkeit:** Playwright-Runtime-Abhängigkeit und System-Chromium-Fallback, falls relevant.

### 🇬🇧 English

#### Overview
Weekend release with fixes for image analysis behavior in private chats and websearch/SearXNG configuration.

#### Image Analysis Behavior (Telegram)
- **Private Chats (1:1):** Photos and image documents now automatically enter the image analysis path without explicit addressing.
- **Groups:** Image analysis remains defensive — only when addressing/reply/caption/follow-up rules apply.
- **Vision Provider Errors:** When the vision provider is unavailable or not configured, the bot acknowledges the image was received and states that analysis is unavailable/not configured. The bot no longer falsely claims that Telegram sent no image or that images cannot be received.
- **Ollama Vision Models:** New setting `IMAGE_ANALYSIS_OLLAMA_VISION_MODELS` for explicit allowlisting of vision models for image analysis. Default: `llava,llama3.2-vision,qwen2.5vl`. Non-standard names (e.g., `kimi-k2.5:cloud`) can be explicitly allowed.
- **Fail-Closed for Unusable Responses:** Generic refusal/policy responses from the provider are treated as failures and mapped to a truthful "unavailable" message.

#### Websearch/SearXNG
- **Configuration:** Websearch uses configured SearXNG JSON endpoint via `AMO_WEBSEARCH_SEARXNG_BASE_URL` (legacy alias: `SEARXNG_BASE_URL`).
- **Fail-Closed:** If no SearXNG endpoint is configured, no public fallback search is used; returns empty/denied instead. If SearXNG is configured, it is SearXNG-only, even when empty/error.
- **URL Safety:** Public HTTP is rejected; HTTPS public is allowed; HTTP only for loopback/private/internal networks.
- **Browser Provider Dependency:** Playwright runtime dependency and system Chromium fallback if relevant.

---

## [Unreleased] – Webtool Quota per Role (Issue #48)

**Datum / Date:** 2026-05-29

### 🇩🇪 Deutsch

#### Übersicht
Rollenbasierte Nutzungsquotas für Webtools (Websearch, Webscraping). Separate Quota-Verwaltung für Webtools über `/webtoolquota` Command und WebUI.

#### Neu
- **`/webtoolquota` Command:** Zeigt aktuelle Webtool-Nutzung und verbleibende Quota pro Rolle.
- **Webtool Role Quotas (WebUI):** Neue Sektion in WebUI "/users" zur Konfiguration von Webtool-Quotas pro Rolle.
- **Quota-Modi:** `disabled` (keine Nutzung), `unlimited` (nur Owner), `limited` (tägliches Limit).
- **Separate Dispatcher/Subagent-Architektur:** Webtools laufen über eigenen Dispatcher mit Provider-Adapter.

#### Geltungsbereich
- **Betroffen:** Webtools (Websearch, Webscraping)
- **Nicht betroffen:** Normale `/ask` AI-Antworten (kein AI-Antwort-Limit)

#### Sicherheit & Privacy
- **Metadata-only Logging:** Audit-Events enthalten keine Queries, URLs, Prompt-/Nachrichtentexte, Secrets, Tokens oder Memory-Inhalte.

#### Architektur / Interna (für Entwickler)
- Webtool Dispatcher mit Subagent/Provider-Adapter-Grenze
- Provider-Aufrufe über separate Adapter (nicht direkt im Core)

### 🇬🇧 English

#### Overview
Role-based usage quotas for webtools (websearch, webscraping). Separate quota management for webtools via `/webtoolquota` command and WebUI.

#### New
- **`/webtoolquota` command:** Shows current webtool usage and remaining quota per role.
- **Webtool role quotas (WebUI):** New section in WebUI "/users" for configuring webtool quotas per role.
- **Quota modes:** `disabled` (no usage), `unlimited` (owner only), `limited` (daily limit).
- **Separate dispatcher/subagent architecture:** Webtools run via dedicated dispatcher with provider adapter.

#### Scope
- **Affected:** Webtools (websearch, webscraping)
- **Not affected:** Normal `/ask` AI responses (no AI response quota)

#### Security & Privacy
- **Metadata-only logging:** Audit events contain no queries, URLs, prompt/message text, secrets, tokens, or memory content.

#### Architecture / Internal (for developers)
- Webtool dispatcher with subagent/provider adapter boundary
- Provider calls via separate adapters (not directly in core)

---

## [2026.05.27] – Memory scoping, C2 review service, and image follow-up analysis

**Datum / Date:** 2026-05-27

### 🇩🇪 Deutsch

#### Übersicht
Dieses Release bringt ein neues Scoped-Memory-System mit C2-Review-Service für verbesserte Datenschutzkontrolle, Follow-up-Bildanalyse für natürlichere Konversationen, sowie weitere Härtung des YT-RSS-Plugins und der Sandbox-Runtime.

#### Neu
- **Scoped Memory mit C2-Review-Service:** Neues Speicher-Architektur-Modell mit kontextspezifischem Scoping (Topic/Gruppe/Privat) und internem C2-Review-Service als Foundation für Datenschutz-Workflows. Memory-Antworten unterliegen "Effectiveness Gates" für verbesserte Qualitätssicherung.
- **Image Follow-up Analysis:** Bilder können nun im Kontext einer laufenden Konversation analysiert werden — natürliche Fortführung von Dialogen mit visuellem Bezug.
- **AI Session Lifecycle Management:** Verbessertes Session-Management mit klar definierten Zustandsübergängen und automatischer Bereinigung.
- **Memory Answer-Effectiveness Gates:** Automatische Qualitätsprüfung von Memory-Antworten vor der Auslieferung.

#### Verbessert
- **YT-RSS Plugin Härtung:** Robusterer Handle-/Channel-ID-Resolver mit verbesserter Fehlerbehandlung und resilienterem Scheduler-Verhalten bei Netzwerkproblemen.
- **Sandbox Runtime Stabilität:** Verbesserte Isolation und Fehlerbehandlung in Plugin-Sandbox-Workern.

#### Sicherheit & Privacy
- **C2 Review/Dreaming Service:** Interner C2-Service für Memory-Review und -Kuratierung (Foundation für zukünftige Datenschutz-Workflows; noch nicht als automatischer Runtime-Review für Endnutzer freigeschaltet).
- **Scoped Memory Privacy:** Speicherdaten sind nun strikt nach Kontext (Topic, Gruppe, Privat) getrennt — keine ungewollte Quer-Verbindung von Informationen.
- **Verbesserte Memory-Gates:** Strengere Prüfung von Memory-Antworten auf Relevanz und Qualität.

#### Architektur / Interna
- **Memory C2 Service:** Neuer dedizierter Service für Memory-Review und -Kuratierung.
- **AI Session Lifecycle:** Klare Zustandsmaschine für AI-Sessions mit definierten Übergängen.
- **Effectiveness Gates:** Modularer Gate-System für Memory-Antwort-Validierung.

#### Qualitätssicherung
- **QA:** ✅ PASS — Vollständige Test-Suite: 899 Tests in 88.82s bestanden. `git diff --check`: sauber. `python -m compileall -q src tests`: sauber.

---

### 🇬🇧 English

#### Overview
This release brings a new scoped memory system with C2 review service for enhanced privacy control, follow-up image analysis for more natural conversations, plus further hardening of the YT-RSS plugin and sandbox runtime.

#### New
- **Scoped memory with C2 review service:** New memory architecture model with context-specific scoping (topic/group/private) and internal C2 review service as foundation for privacy workflows. Memory responses undergo "effectiveness gates" for improved quality assurance.
- **Image follow-up analysis:** Images can now be analyzed within the context of an ongoing conversation — natural continuation of dialogues with visual reference.
- **AI session lifecycle management:** Improved session management with clearly defined state transitions and automatic cleanup.
- **Memory answer-effectiveness gates:** Automatic quality checking of memory responses before delivery.

#### Improved
- **YT-RSS plugin hardening:** More robust handle/channel ID resolver with improved error handling and more resilient scheduler behavior during network issues.
- **Sandbox runtime stability:** Enhanced isolation and error handling in plugin sandbox workers.

#### Security & Privacy
- **C2 review/dreaming service:** Internal C2 service for memory review and curation (foundation for future privacy workflows; not yet enabled as automatic runtime review for end users).
- **Scoped memory privacy:** Memory data is now strictly separated by context (topic, group, private) — no unwanted cross-connection of information.
- **Improved memory gates:** Stricter validation of memory responses for relevance and quality.

#### Architecture / Internal
- **Memory C2 service:** New dedicated service for memory review and curation.
- **AI session lifecycle:** Clear state machine for AI sessions with defined transitions.
- **Effectiveness gates:** Modular gate system for memory response validation.

#### Quality Assurance
- **QA:** ✅ PASS — Full test suite: 899 tests passed in 88.82s. `git diff --check`: clean. `python -m compileall -q src tests`: clean.

---

## [2026.05.26] – YT-RSS plugin and sandbox runtime hardening

**Datum / Date:** 2026-05-26

### 🇩🇪 Deutsch

#### Übersicht
Dieses Release härtet den YouTube-RSS-Plugin-Stack und die Sandbox-Runtime ab. Neue Kommandos `/addyt` und `/delyt` zum Hinzufügen und Entfernen von YouTube-RSS-Feeds. Der Handle-/Channel-ID-Resolver wurde robuster gemacht, der Scheduler verbessert seine Cursor- und Backlog-Verarbeitung, und die Diagnose-Ausgaben bleiben privacy-safe (keine Callback-UI-Reintro, keine sensitiven Logs).

#### Neu
- **YT-RSS Commands:** `/addyt` und `/delyt` zum Hinzufügen und Entfernen von YouTube-RSS-Feeds.
- **YouTube Handle/Channel-ID Resolver Härtung:** Verbesserte Auflösung von YouTube-Handles und Channel-IDs mit robusterer Fehlerbehandlung.
- **Scheduler Cursor & Backlog:** Verbessertes Cursor-Verhalten und Backlog-Verarbeitung für zuverlässigere Feed-Updates.
- **Legacy Handle Migration & Deduplizierung:** Automatische Migration und Deduplizierung von Legacy-Handles.

#### Sicherheit & Privacy
- **Safe Diagnostics & Log Redaction:** Diagnose-Ausgaben enthalten keine sensiblen Daten; automatische Redaktion von Tokens und persönlichen Identifikatoren.
- **No Callback/UI Reintro:** Keine Re-Introduktion von Callback- oder UI-Code; alle Interaktionen über Sandbox-Runtime geregelt.
- **Sandbox/Runtime RSS Support:** RSS-Fetching läuft vollständig innerhalb der Sandbox mit Capability-Gating.

#### Architektur / Interna
- **Sandbox Runtime Tests:** Erweiterte Tests für die Sandbox-Runtime mit RSS-Feed-Handling.
- **Capability-Gating:** Alle RSS-Operationen unterliegen strikter Capability-Prüfung (`rss.fetch`).

### 🇬🇧 English

#### Overview
This release hardens the YouTube RSS plugin stack and sandbox runtime. New commands `/addyt` and `/delyt` to add and remove YouTube RSS feeds. The handle/channel ID resolver is more robust, the scheduler improves cursor and backlog handling, and diagnostic outputs remain privacy-safe (no callback/UI reintro, no sensitive logs).

#### New
- **YT-RSS commands:** `/addyt` and `/delyt` to add and remove YouTube RSS feeds.
- **YouTube handle/channel ID resolver hardening:** Improved resolution of YouTube handles and channel IDs with more robust error handling.
- **Scheduler cursor & backlog:** Improved cursor behavior and backlog processing for more reliable feed updates.
- **Legacy handle migration & deduplication:** Automatic migration and deduplication of legacy handles.

#### Security & Privacy
- **Safe diagnostics & log redaction:** Diagnostic outputs contain no sensitive data; automatic redaction of tokens and personal identifiers.
- **No callback/UI reintro:** No re-introduction of callback or UI code; all interactions governed through sandbox runtime.
- **Sandbox/runtime RSS support:** RSS fetching runs entirely within the sandbox with capability gating.

#### Architecture / Internal
- **Sandbox runtime tests:** Extended tests for sandbox runtime with RSS feed handling.
- **Capability gating:** All RSS operations subject to strict capability checking (`rss.fetch`).

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

## [2026.05.22-1] – RSS capability contracts and log redaction

**Datum / Date:** 2026-05-22

### 🇩🇪 Deutsch

#### Übersicht
Dieses Release verbessert die Audit-Trail-Sicherheit durch härtes Redaktion sensibler Daten in Logs und fügt saubere Capability-Verträge für RSS-Feed-Features hinzu. Neue Plugin-Entwickler-Dokumentation hilft bei der Erstellung sicherer Userplugins.

#### Dokumentation
- **Userplugin Development Guide:** Neue umfassende Anleitung für Plugin-Entwickler (`docs/USERPLUGINS.md`). Enthält Do/Don't-Struktur, Minimalbeispiel, Capability-Referenz (inkl. `rss.fetch`), Sicherheitsregeln und KI-spezifische Guidelines.

#### Sicherheit & Privacy
- **Sensitive Log Redaction:** Audit-Logs und Diagnose-Ausgaben enthalten keine sensiblen Daten mehr. Tokens, API-Keys und persönliche Identifikatoren werden automatisch redacted (`***`).
- **Sichere Fehlerberichterstattung:** Fehler bei externen Aufrufen enthalten keine internen URLs oder Credentials mehr.

#### Architektur / Interna
- **RSS Capability Rename:** Umbenennung der RSS-Capability von `rss` zu `rss.fetch` für konsistente Namespacing-Semantik.
- **RSS Core Contract:** Neuer `rss.fetch` Capability-Vertrag mit Request/Response-Validierung und Timeout-Defaults.
- **RSS Policy Audit:** Policy-Prüfungen für RSS-Feeds mit Audit-Trail-Integration.
- **RSS Plugin UI Contract:** Plugin-UI-Vertrag für RSS-Konfiguration über die WebUI.

#### Migration
- Plugins, die die `rss`-Capability verwenden, müssen auf `rss.fetch` migrieren.
- Keine Breaking Changes für Endnutzer.

### 🇬🇧 English

#### Overview
This release improves audit trail security through hardened redaction of sensitive data in logs and adds clean capability contracts for RSS feed features. New plugin developer documentation helps create secure userplugins.

#### Documentation
- **Userplugin Development Guide:** New comprehensive guide for plugin developers (`docs/USERPLUGINS.md`). Includes Do/Don't structure, minimal example, capability reference (including `rss.fetch`), security rules, and AI-specific guidelines.

#### Security & Privacy
- **Sensitive Log Redaction:** Audit logs and diagnostic outputs no longer contain sensitive data. Tokens, API keys, and personal identifiers are automatically redacted (`***`).
- **Secure Error Reporting:** External call failures no longer include internal URLs or credentials.

#### Architecture / Internal
- **RSS Capability Rename:** Renamed RSS capability from `rss` to `rss.fetch` for consistent namespacing semantics.
- **RSS Core Contract:** New `rss.fetch` capability contract with request/response validation and timeout defaults.
- **RSS Policy Audit:** Policy checks for RSS feeds with audit trail integration.
- **RSS Plugin UI Contract:** Plugin UI contract for RSS configuration via WebUI.

#### Migration
- Plugins using the `rss` capability must migrate to `rss.fetch`.
- No breaking changes for end users.

---

## [Unreleased] – i18n Completion and Language Conventions

**Datum / Date:** 2026-05-22

### 🇩🇪 Deutsch

#### Übersicht
Dieses Release vervollständigt die Zweisprachigkeit (Deutsch + Englisch) für alle user-facing Oberflächen des Bots und der WebUI. Es etabliert Language Conventions für zukünftige Dokumentation.

#### Neu
- **Telegram Bot i18n (#10/#11):** Alle Command-Descriptions und User-facing Responses sind nun bilingual.
  - Command-Descriptions für alle 11 Commands mit DE/EN Varianten
  - Consent-Flow komplett bilingual (Prompts, Buttons, Callbacks, Status)
  - Dispatcher-Fehlermeldungen und Consent-Block-Nachrichten bilingual
  - AI-Fehlertexte bei Autoreply bilingual
  - Locale-Detection aus Telegram `language_code` mit Fallback auf DE
- **Flask WebUI i18n (#10/#11):** Vollständige Internationalisierung der WebUI.
  - Neues i18n-Modul (`src/amo_bot/webui/i18n.py`) mit DE/EN Übersetzungen
  - Language Switcher in der UI verfügbar
  - Alle Navigation-Labels, Formulare, Tabellen und Buttons bilingual
  - Session-basierte Sprachspeicherung
- **Language Conventions (#12/#13):** Dokumentierte Standards für mehrsprachige Dokumentation.
  - `docs/LANGUAGE_CONVENTIONS.md` mit Richtlinien für Dateistruktur, Namenskonventionen, Linkstruktur
  - Entscheidungsmatrix für Separate Files / Bilingual Single / EN-Only
  - Checkliste für neue Dokumente
- **i18n Inventory Refresh (#14):** Aktualisierte Übersicht aller sprachsensitiven Oberflächen.
  - Vollständige Inventarisierung nach #10/#11/#12/#13
  - Status: User-facing surfaces ✅ bilingual; technische Interna EN-only (by design)

#### Architektur / Interna
- `_lang()` Helper für Command-Handler mit kontextbasierter Sprachauflösung
- `ConsentPromptService` mit `_PROMPT_TEXTS` und `_PROMPT_MARKUP` Dictionaries
- `Dispatcher._consent_block_message()` und `_consent_callback_message()` mit Locale-Support
- `resolve_locale()` mit explizitem Argument und Telegram language_code Fallback

### 🇬🇧 English

#### Overview
This release completes bilingual support (German + English) for all user-facing surfaces of the bot and WebUI. It establishes Language Conventions for future documentation.

#### New
- **Telegram Bot i18n (#10/#11):** All command descriptions and user-facing responses are now bilingual.
  - Command descriptions for all 11 commands with DE/EN variants
  - Consent flow fully bilingual (prompts, buttons, callbacks, status)
  - Dispatcher error messages and consent block messages bilingual
  - AI error texts for autoreply bilingual
  - Locale detection from Telegram `language_code` with DE fallback
- **Flask WebUI i18n (#10/#11):** Complete WebUI internationalization.
  - New i18n module (`src/amo_bot/webui/i18n.py`) with DE/EN translations
  - Language switcher available in UI
  - All navigation labels, forms, tables, and buttons bilingual
  - Session-based language storage
- **Language Conventions (#12/#13):** Documented standards for multilingual documentation.
  - `docs/LANGUAGE_CONVENTIONS.md` with guidelines for file structure, naming conventions, link structure
  - Decision matrix for Separate Files / Bilingual Single / EN-Only
  - Checklist for new documents
- **i18n Inventory Refresh (#14):** Updated inventory of all language-sensitive surfaces.
  - Complete inventory after #10/#11/#12/#13
  - Status: User-facing surfaces ✅ bilingual; technical internals EN-only (by design)

#### Architecture / Internal
- `_lang()` helper for command handlers with context-based language resolution
- `ConsentPromptService` with `_PROMPT_TEXTS` and `_PROMPT_MARKUP` dictionaries
- `Dispatcher._consent_block_message()` and `_consent_callback_message()` with locale support
- `resolve_locale()` with explicit argument and Telegram language_code fallback

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

- **Core Bot:** Fully implemented with role-based permission system (Owner, Admin, VIP, Normal, Ignore) and basic commands. Note: Legacy consent management for human users has been removed (human users are now auto-approved; bot-to-bot communication remains consent-gated)
- **Plugin System:** Manifest-based plugin loader with discovery, validation, registry, and activation (I1-I6 complete)
- **WebUI:** Local Flask interface for management and configuration
- **AI Integration:** `/ask` command, auto-replies, and memory system (Daily + Long Memory) via Ollama
- **Topic Agent System:** Configurable per-topic AI behavior with memory curation (KI-A to KI-F4 complete)
- **Core Plugins:** Policy-controlled AI capabilities for RSS feeds, web search, web scraping, API integration, context window builder, memory management, SQL read-only access (templates/views), and self-improvement proposals (CP-I1 and CP-Z1 complete)
- **Image Analysis Coreplugin:** Secure image analysis interface (IMG-B4..IMG-B7) — default-off, stub implementation with policy/role checks (human users auto-approved; bot-to-bot remains consent-gated)
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
