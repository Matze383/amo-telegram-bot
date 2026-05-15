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
| ✅ **Consent Management** | `/accept`, `/decline`, `/consent` commands for user consent; automatic one-shot DM prompt with **inline buttons** (✅ Accept / ❌ Decline) for pending users; fallback commands remain available; runtime gate blocks normal usage until consent is accepted (allowed: `/accept`, `/decline`, `/consent`, `/start`) | `/accept`, `/decline`, `/consent` Commands für Nutzer-Consent; automatischer One-Shot-DM-Prompt mit **Inline-Buttons** (✅ Akzeptieren / ❌ Ablehnen) für Pending-User; Fallback-Commands weiterhin verfügbar; Runtime-Gate blockiert normale Nutzung bis Consent akzeptiert ist (erlaubt: `/accept`, `/decline`, `/consent`, `/start`) |
| 🔌 **Plugin System** | Defensive manifest-based plugin loader | Defensiver, manifest-basierter Plugin-Loader |
| 🌐 **WebUI** | Local Flask-based management interface | Lokale Flask-basierte Verwaltungsoberfläche |
| 🤖 **AI Integration** | Optional Ollama `/ask` command (stateless); auto-reply on mentions/replies in active scopes (VIP/Admin/Owner + consent required) | Optionales Ollama `/ask` Kommando (stateless); Auto-Antwort bei Erwähnungen/Antworten in aktiven Scopes (VIP/Admin/Owner + Consent erforderlich) |
| 🧠 **Topic Soul Editor** | Owner-only WebUI editing of topic-specific Soul text on groups/topics UI; non-owner cannot edit; bounded/escaped (KI-F2) | Owner-only WebUI-Bearbeitung von Topic-spezifischem Soul-Text auf der Groups/Topics-Oberfläche; Nicht-Owner können nicht bearbeiten; begrenzt/escaped (KI-F2) |
| 🧠 **Memory Curation (KI-D5)** | Optional automatic, bounded daily→long-memory curation (candidate-only); failure-safe (no partial writes on promotion failure); can be disabled via maintenance config | Optionale automatische, begrenzte Daily→Long-Memory-Kuratierung (nur als Kandidaten); fehlersicher (keine Teilwrites bei Fehlern); per Maintenance-Konfiguration deaktivierbar |
| 🧠 **WebUI Memory Controls (KI-F3)** | Owner/authenticated WebUI can inspect safe/high-level memory entries; daily memory visibility is conservative/redacted (dates only); long memory summaries/facts listable; owner can deactivate long-memory entries via CSRF-protected POST; denied (403) without owner mutation config | Owner/authentifizierte WebUI kann sichere/high-level Memory-Einträge einsehen; Daily-Memory-Sichtbarkeit ist konservativ/redacted (nur Daten); Long-Memory-Summary/Fakten listbar; Owner kann Long-Memory-Einträge via CSRF-geschütztem POST deaktivieren; verweigert (403) ohne Owner-Mutation-Konfiguration |
| 📊 **WebUI KI Status** | Read-only dashboard view showing topic/private AI config status (scope, active/inactive, response mode) | Read-only Dashboard-Ansicht mit Topic/Private AI-Konfigurationsstatus (Scope, aktiv/inaktiv, Antwortmodus) |
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
| [🚀 Release Baseline](docs/release-baseline.md) | Release readiness and support matrix | Release-Bereitschaft und Support-Matrix |

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

This project is licensed under the MIT License — see [LICENSE](LICENSE) for details.

Dieses Projekt steht unter der MIT License — siehe [LICENSE](LICENSE) für Details.

## Memory Management MVP Safe Operations (CP-G2)

Memory operations are policy-gated with default-deny. All memory access requires explicit capability approval.

**Scope Isolation:**
- Memory is strictly scoped to topics, private chats, or users
- No cross-scope memory leakage possible
- Each scope maintains independent memory boundaries

**Operations (Bounded & Auditable):**
- `put` — Store memory entries with TTL/retention rules
- `get` — Retrieve memory (redacted outputs, no raw memory text in MVP)
- `search` — Query memory (returns metadata placeholders only)
- `delete` — Permanent removal (auditable, no memory text in audit)
- `deactivate` — Soft-disable entries without deletion (reversible)

**Privacy & Security:**
- **Default-deny:** All memory operations blocked unless explicitly allowed by CP-G1 policy
- **Redacted outputs:** Raw memory text is never exposed in WebUI, Telegram, or audit events
- **Audit events:** Include scope, action, and metadata only — never memory content
- **TTL/Retention:** Automatic pruning via maintenance hooks
- **MVP limitation:** Raw memory output intentionally not exposed; only redacted metadata placeholders

**Audit Event Types:**
- `memory_put` — Memory stored (scope, entry ID, no content)
- `memory_get` — Memory accessed (scope, entry ID, no content)
- `memory_delete` — Memory permanently deleted (scope, entry ID)
- `memory_deactivate` — Memory soft-disabled (scope, entry ID)

---

## Memory Management MVP Safe Operations (CP-G2) — Deutsch

Speicher-Operationen sind Policy-gated mit Default-Deny. Jeder Speicherzugriff erfordert explizite Capability-Genehmigung.

**Scope-Isolation:**
- Speicher ist streng an Topics, private Chats oder Nutzer gebunden
- Keine Cross-Scope-Speicherlecks möglich
- Jeder Scope pflegt unabhängige Speichergrenzen

**Operationen (Begrenzt & Auditierbar):**
- `put` — Speicher-Einträge mit TTL/Retention-Regeln speichern
- `get` — Speicher abrufen (redigierte Ausgaben, kein Raw-Memory-Text im MVP)
- `search` — Speicher abfragen (gibt nur Metadaten-Platzhalter zurück)
- `delete` — Permanentes Löschen (auditierbar, kein Memory-Text im Audit)
- `deactivate` — Soft-Disable von Einträgen ohne Löschung (reversibel)

**Datenschutz & Sicherheit:**
- **Default-deny:** Alle Speicher-Operationen blockiert, sofern nicht durch CP-G1-Policy explizit erlaubt
- **Redigierte Ausgaben:** Raw-Speichertext wird nie in WebUI, Telegram oder Audit-Events preisgegeben
- **Audit-Events:** Enthalten nur Scope, Aktion und Metadaten — niemals Memory-Inhalt
- **TTL/Retention:** Automatisches Pruning via Maintenance-Hooks
- **MVP-Einschränkung:** Raw-Speicherausgabe absichtlich nicht verfügbar; nur redigierte Metadaten-Platzhalter

**Audit-Event-Typen:**
- `memory_put` — Speicher gespeichert (Scope, Entry-ID, kein Inhalt)
- `memory_get` — Speicher abgerufen (Scope, Entry-ID, kein Inhalt)
- `memory_delete` — Speicher permanent gelöscht (Scope, Entry-ID)
- `memory_deactivate` — Speicher soft-deaktiviert (Scope, Entry-ID)

---

## Websearch Provider MVP (CP-C2)

Websearch provider execution remains default-deny unless capability policy and tool policy gates explicitly allow it.
Provider configuration is hook-based only (no secrets in code/logs/docs):

- `provider_name` (example: `fake`)
- `provider_allowlist` (must include configured provider)
- `timeout_seconds` (positive, bounded by runtime policy)
- `retry_count` (0..3)

Security/safety expectations:

- Never log provider tokens/credentials/raw private text.
- Return only normalized safe result fields (`title`, `url`, `snippet`) with strict caps.
- Enforce quota before provider execution.
- Provider timeout/failure must fail closed with safe reason codes.
