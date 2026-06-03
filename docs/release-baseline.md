# Release Baseline / Release-Baseline


---

## English

### Overview

This document defines the first public release readiness baseline for the AMO Telegram Bot.

### Target Release

**Version:** `2026.06.03`

- Date-based versioning (YYYY.MM.DD)
- MVP Complete release
- Stable release with known limitations

### Supported Python / Runtime Matrix

| Component | Supported | Candidate/Best Effort |
|-----------|-----------|----------------------|
| Python | 3.12 | 3.13 (future) |
| Runtime | CPython | PyPy (not tested) |

**Note:** Python 3.12 is the minimum supported version. Python 3.13 compatibility is future work. Python 3.11 is not supported.

### Supported Operating Systems

| OS | Status | Notes |
|----|--------|-------|
| Linux | Supported | Primary development platform |
| macOS | Target / Pending RR-13 | Cross-platform smoke validation planned |
| Windows | Target / Pending RR-13 | Cross-platform smoke validation planned |

### Included Components

- **Telegram Bot Core:** Long polling, role-based permissions, consent management
- **Plugin System:** Manifest-based plugin loader (I1-I6 complete), Sandbox Runtime
- **WebUI:** Flask-based management interface
- **KI/AI Integration:** Multi-provider AI (15+ providers), topic memory system
- **Topic Agent System:** Configurable per-topic AI behavior, memory curation (KI-A to KI-F4 complete)
- **Image Analysis:** Secure image analysis interface with role quotas
- **Webtools:** Web search, web scraping, auto web research with feedback follow-up
- **Memory Systems:** Daily Memory Runtime + Dreaming (Long Memory)
- **Context Docs:** DB-backed prompt context management

### Quality Gates

Before any release:

1. **All tests passing:** Full pytest suite green before release
2. **No security blockers:** Secrets audit, privacy grep clean
3. **Documentation complete:** README, setup guides, release notes bilingual
4. **Cross-platform smoke:** Linux confirmed, macOS/Windows RR-13 pending
5. **Matze approval:** Explicit sign-off required

### Beta vs Experimental

| Status | Meaning |
|--------|---------|
| **Supported** | Tested, expected to work, documented |
| **Beta** | Functional, may have edge cases, feedback welcome |
| **Experimental** | Not guaranteed, subject to change, use with caution |

Current beta areas:
- Sandbox Runtime (Beta – stabilized but production hardening pending)
- Cross-platform support macOS/Windows (Beta – Linux only validated)

### Known Limitations

- macOS and Windows platform support pending smoke validation (RR-13)
- Python 3.13 not yet tested
- Heavy production load not yet validated

---

## Deutsch

### Übersicht

Dieses Dokument definiert die Release-Baseline für die erste öffentliche Version des AMO Telegram Bot.

### Ziel-Release

**Version:** `2026.06.03`

- Datumsbasiertes Versionsschema (JJJJ.MM.TT)
- MVP Complete Release
- Stabiles Release mit bekannten Einschränkungen

### Unterstützte Python / Runtime-Matrix

| Komponente | Unterstützt | Kandidat/Best Effort |
|------------|-------------|---------------------|
| Python | 3.12 | 3.13 (zukünftig) |
| Runtime | CPython | PyPy (nicht getestet) |

**Hinweis:** Python 3.12 ist die minimal unterstützte Version. Python 3.13-Kompatibilität ist zukünftige Arbeit. Python 3.11 wird nicht unterstützt.

### Unterstützte Betriebssysteme

| OS | Status | Hinweise |
|----|--------|----------|
| Linux | Unterstützt | Primäre Entwicklungsplattform |
| macOS | Ziel / Pending RR-13 | Cross-Platform Smoke-Validierung geplant |
| Windows | Ziel / Pending RR-13 | Cross-Platform Smoke-Validierung geplant |

### Enthaltene Komponenten

- **Telegram Bot Core:** Long Polling, rollenbasierte Berechtigungen, Consent-Management
- **Plugin-System:** Manifest-basierter Plugin-Loader (I1-I6 komplett), Sandbox-Runtime
- **WebUI:** Flask-basierte Verwaltungsoberfläche
- **KI/AI-Integration:** Multi-Provider KI (15+ Provider), Topic-Memory-System
- **Topic-Agent-System:** Konfigurierbares KI-Verhalten pro Topic, Memory-Kuratierung (KI-A bis KI-F4 komplett)
- **Bildanalyse:** Sichere Bildanalyse-Schnittstelle mit Rollen-Quotas
- **Webtools:** Websuche, Webscraping, Auto Web Research mit Feedback Follow-up
- **Memory-Systeme:** Daily Memory Runtime + Dreaming (Long Memory)
- **Context Docs:** DB-gestütztes Prompt-Context-Management

### Quality Gates

Vor jedem Release:

1. **Alle Tests bestanden:** Full pytest suite grün vor Release
2. **Keine Security-Blocker:** Secrets-Audit, Privacy-Grep clean
3. **Dokumentation vollständig:** README, Setup-Guides, Release Notes bilingual
4. **Cross-Platform Smoke:** Linux bestätigt, macOS/Windows RR-13 pending
5. **Matze-Freigabe:** Explizite Freigabe erforderlich

### Beta vs Experimentell

| Status | Bedeutung |
|--------|-----------|
| **Unterstützt** | Getestet, sollte funktionieren, dokumentiert |
| **Beta** | Funktional, kann Randfälle haben, Feedback willkommen |
| **Experimentell** | Nicht garantiert, änderungsanfällig, mit Vorsicht nutzen |

Aktuelle Beta-Bereiche:
- Sandbox Runtime (Beta – stabilisiert, aber Production-Härtung ausstehend)
- Cross-Platform Support macOS/Windows (Beta – nur Linux validiert)

### Bekannte Einschränkungen

- macOS- und Windows-Plattformunterstützung pending Smoke-Validierung (RR-13)
- Python 3.13 noch nicht getestet
- Schwere Produktionslast noch nicht validiert

---

## Approval Workflow

### Release Readiness Checklist

- [x] All implementation blocks complete (I1-I6, KI-A to KI-F4)
- [x] Coreplugins CP-I1 and CP-Z1 complete
- [ ] Full test suite passing
- [ ] Security/privacy audit clean
- [ ] Documentation bilingual and complete
- [ ] Cross-platform smoke validation (RR-13)

---

---

## RR-13 – Cross-Platform Smoke Validation Evidence

### Deutsch

#### Linux Smoke-Validierung – PASS

- **Status:** PASS
- **Datum:** 2026-05-16
- **Python-Version:** 3.12.3
- **Repository-Zustand:** Clean, 30 Commits ahead of origin/main
- **Test-Ergebnisse:** 15 Tests bestanden in ~0,9s (fokussierte Smoke-Tests)
- **Manuelle Prüfungen:**
  - `main.py --help` beendet mit Exit Code 0
  - `pip check`: Keine defekten Requirements gefunden

#### macOS Smoke-Validierung – PENDING/BLOCKED

- **Status:** BLOCKED
- **Grund:** Kein nativer macOS-Runner verfügbar
- **Hinweis:** Vollständige Cross-Platform-Validierung nicht abgeschlossen

#### Windows Smoke-Validierung – PENDING/BLOCKED

- **Status:** BLOCKED
- **Grund:** Kein nativer Windows-Runner verfügbar
- **Hinweis:** Vollständige Cross-Platform-Validierung nicht abgeschlossen

#### Release-Status

- Öffentliches Release bleibt gesperrt, bis die ausstehende Validierung abgeschlossen und die finale Freigabe erteilt ist.
- Keine vollständige Cross-Platform-Unterstützung wird derzeit beansprucht.

---

### English

#### Linux Smoke Validation – PASS

- **Status:** PASS
- **Date:** 2026-05-16
- **Python Version:** 3.12.3
- **Repository State:** Clean, 30 commits ahead of origin/main
- **Test Results:** 15 tests passed in ~0.9s (focused smoke tests)
- **Manual Checks:**
  - `main.py --help` exits with exit code 0
  - `pip check`: No broken requirements found

#### macOS Smoke Validation – PENDING/BLOCKED

- **Status:** BLOCKED
- **Reason:** No native macOS runner available
- **Note:** Full cross-platform validation not yet completed

#### Windows Smoke Validation – PENDING/BLOCKED

- **Status:** BLOCKED
- **Reason:** No native Windows runner available
- **Note:** Full cross-platform validation not yet completed

#### Release Status

- Public release remains gated until pending validation is completed and final approval is obtained.
- No full cross-platform support is currently claimed.

---

*Last updated: 2026-06-03*
*RR-13 Block – Cross-Platform Smoke Validation Evidence*
