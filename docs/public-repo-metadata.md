# Public Repository Metadata / Öffentliche Repository-Metadaten

> **HARD STOP:** Kein push/tag/release/publication ohne explizite Matze-Freigabe.
> **HARD STOP:** No push/tag/release/publication without explicit Matze approval.

---

## English

### Overview

This document tracks public-facing repository metadata requirements for the AMO Telegram Bot project. This is a **pre-publication checklist** to ensure the repository is ready for public visibility.

---

### Repository Basics

| Field | Status | Notes |
|-------|--------|-------|
| **Repository name** | `AMO-telegram-bot` | Set |
| **Description** | A modular, role-based Telegram bot with plugin support, WebUI management, and optional Ollama AI integration. | Ready |
| **Topics / Tags** | `telegram-bot`, `python`, `flask`, `ollama`, `ai`, `plugin-system`, `webui` | Candidate list |
| **Default branch** | `main` | Set |
| **License** | MIT | ✅ Added (see `LICENSE`) |

---

### License Decision

- **License:** MIT License
- **Decision date:** 2026-05-15
- **Decision by:** Matze
- **Rationale:** Free/open project for everyone; no restrictive rights or patent constraints desired over the code.

---

### Badges Plan (Candidate)

| Badge | Purpose | Provider |
|-------|---------|----------|
| Python Version | Show supported Python | shields.io |
| License | Show MIT license | shields.io |
| Build/Tests | CI status | GitHub Actions (future) |

---

### Issue / Discussion Templates

| Template | Status |
|----------|--------|
| Bug report (EN/DE) | Pending (RR-09) |
| Feature request (EN/DE) | Pending (RR-09) |

---

### Release / Publication Hard Stop

- [ ] All implementation blocks complete
- [ ] Full test suite passing
- [ ] Security/privacy audit clean
- [ ] Documentation bilingual and complete
- [ ] Cross-platform smoke validation (RR-13)
- [ ] **Matze explicit approval obtained**

**Current status:** Not approved for release.

---

## Deutsch

### Übersicht

Dieses Dokument erfasst öffentliche Repository-Metadaten-Anforderungen für das AMO Telegram Bot Projekt. Dies ist eine **Vor-Veröffentlichungs-Checkliste**, um sicherzustellen, dass das Repository für öffentliche Sichtbarkeit bereit ist.

---

### Repository-Grundlagen

| Feld | Status | Hinweise |
|------|--------|----------|
| **Repository-Name** | `AMO-telegram-bot` | Gesetzt |
| **Beschreibung** | Ein modularer, rollenbasierter Telegram-Bot mit Plugin-Unterstützung, WebUI-Verwaltung und optionaler Ollama-KI-Integration. | Bereit |
| **Themen / Tags** | `telegram-bot`, `python`, `flask`, `ollama`, `ai`, `plugin-system`, `webui` | Kandidatenliste |
| **Standard-Branch** | `main` | Gesetzt |
| **Lizenz** | MIT | ✅ Hinzugefügt (siehe `LICENSE`) |

---

### Lizenz-Entscheidung

- **Lizenz:** MIT License
- **Entscheidungsdatum:** 2026-05-15
- **Entschieden von:** Matze
- **Begründung:** Freies/offenes Projekt für alle; keine restriktiven Rechte oder Patentbeschränkungen über den Code gewünscht.

---

### Badges-Plan (Kandidat)

| Badge | Zweck | Provider |
|-------|-------|----------|
| Python Version | Unterstützte Python-Version anzeigen | shields.io |
| Lizenz | MIT-Lizenz anzeigen | shields.io |
| Build/Tests | CI-Status | GitHub Actions (zukünftig) |

---

### Issue / Discussion Templates

| Template | Status |
|----------|--------|
| Bug report (EN/DE) | Pending (RR-09) |
| Feature request (EN/DE) | Pending (RR-09) |

---

### Release / Publication Hard Stop

- [ ] Alle Implementierungsblöcke komplett
- [ ] Full test suite bestanden
- [ ] Security/Privacy-Audit clean
- [ ] Dokumentation bilingual und vollständig
- [ ] Cross-Platform Smoke-Validierung (RR-13)
- [ ] **Matze-Explizitfreigabe erhalten**

**Aktueller Status:** Nicht für Release freigegeben.

---

## Bilingual Expectations

All public-facing documentation is expected to support both **German (DE)** and **English (EN)** where practical:

- README files: Bilingual (DE + EN)
- Setup guides: Separate `SETUP_EN.md` and `SETUP_DE.md`
- Beta testing docs: Separate `BETATEST_EN.md` and `BETATEST_DE.md`
- Release notes: Separate `RELEASE_NOTES_*_EN.md` and `RELEASE_NOTES_*_DE.md`
- Root `LICENSE`: Standard MIT text (English only, as per convention)

---

## Public Safety Checklist

- [x] No secrets in repository files
- [x] No private local paths in public docs
- [x] No raw memory/logs/DB dumps
- [x] No internal planning chatter in public files
- [x] `.gitignore` covers sensitive files (`.env`, `data/`, `logs/`, etc.)
- [x] License file present at root

---

*Document created: 2026-05-15*
*RR-02 Block – Public Repo Metadata + License Decision*
