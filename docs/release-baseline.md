# Release Baseline / Release-Baseline

> **HARD STOP: Kein push/tag/release/publication ohne explizite Matze-Freigabe.**  
> **HARD STOP: No push/tag/release/publication without explicit Matze approval.**

---

## English

### Overview

This document defines the first public release readiness baseline for the AMO Telegram Bot.

### Target Release

**Proposed version:** `v0.1.0-beta.1` → `v0.1.0`

- Follows SemVer-like versioning
- Beta phase for initial public testing
- Stable release after quality gates pass

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
- **Plugin System:** Manifest-based plugin loader (I1-I6 complete)
- **WebUI:** Flask-based management interface
- **KI/AI Integration:** Ollama-based AI responses, topic memory system
- **Topic Agent System:** Configurable per-topic AI behavior, memory curation (KI-A to KI-F4 complete)

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
- Plugin system (beta)
- Topic memory curation (beta)
- WebUI memory controls (beta)

### Known Limitations

- macOS and Windows platform support pending smoke validation (RR-13)
- Python 3.13 not yet tested
- Heavy production load not yet validated

---

## Deutsch

### Übersicht

Dieses Dokument definiert die Release-Baseline für die erste öffentliche Version des AMO Telegram Bot.

### Ziel-Release

**Vorgeschlagene Version:** `v0.1.0-beta.1` → `v0.1.0`

- SemVer-ähnliches Versionsschema
- Beta-Phase für erstes öffentliches Testing
- Stabiles Release nach Bestehen der Quality Gates

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
- **Plugin-System:** Manifest-basierter Plugin-Loader (I1-I6 komplett)
- **WebUI:** Flask-basierte Verwaltungsoberfläche
- **KI/AI-Integration:** Ollama-basierte KI-Antworten, Topic-Memory-System
- **Topic-Agent-System:** Konfigurierbares KI-Verhalten pro Topic, Memory-Kuratierung (KI-A bis KI-F4 komplett)

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
- Plugin-System (Beta)
- Topic-Memory-Kuratierung (Beta)
- WebUI Memory-Controls (Beta)

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
- [ ] **Matze explicit approval obtained**

### Hard Stop

```
┌─────────────────────────────────────────────────────────┐
│  HARD STOP: Kein push/tag/release/publication             │
│           ohne explizite Matze-Freigabe.                │
│                                                         │
│  HARD STOP: No push/tag/release/publication             │
│           without explicit Matze approval.              │
└─────────────────────────────────────────────────────────┘
```

---

*Last updated: 2026-05-14*  
*RR-01 Block – Release Baseline + Support Matrix*
