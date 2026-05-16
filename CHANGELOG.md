# Changelog / Änderungsprotokoll

> **HARD STOP:** Kein push/tag/release/publication ohne explizite Matze-Freigabe.  
> **HARD STOP:** No push/tag/release/publication without explicit Matze approval.

---

## [Unreleased] – 2026.05.16 (Public Release Candidate)

**Datum / Date:** 2026-05-16

---

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
- **Dokumentation:** Bilinguale README, Setup-Guides und Beta-Test-Anleitungen

#### Bekannte Einschränkungen

- **Repository-Status:** Privat – öffentliche Freigabe pending expliziter Matze-Freigabe
- **Cross-Platform:** Linux validiert; macOS/Windows Smoke-Tests pending (keine nativen Runner verfügbar)
- **Python-Version:** 3.12 erforderlich; 3.13 nicht getestet
- **Produktionslast:** Schwere Produktionslast noch nicht validiert
- **CI-Status:** GitHub Actions konfiguriert; Cross-Platform-Tests unter Linux validiert, macOS/Windows native Tests pending (keine nativen Runner verfügbar)

#### Sicherheit

- WebUI ist **lokal only** (`127.0.0.1`) – niemals ins Internet freigeben
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
- **Documentation:** Bilingual README, setup guides, and beta test instructions

#### Known Limitations

- **Repository Status:** Private – public release pending explicit Matze approval
- **Cross-Platform:** Linux validated; macOS/Windows smoke tests pending (no native runners available)
- **Python Version:** 3.12 required; 3.13 not tested
- **Production Load:** Heavy production load not yet validated
- **CI Status:** GitHub Actions configured; cross-platform tests validated on Linux, macOS/Windows native tests pending (no native runners available)

#### Security

- WebUI is **local-only** (`127.0.0.1`) – never expose to the internet
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

*Letzte Aktualisierung / Last updated: 2026-05-16*
