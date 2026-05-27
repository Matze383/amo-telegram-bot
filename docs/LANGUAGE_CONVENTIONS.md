# Language Conventions for AMO Documentation / Sprachkonventionen für AMO-Dokumentation

> **Scope:** This document defines the language structure, naming conventions, and linking standards for all public-facing documentation in the AMO Telegram Bot repository.
> **Geltungsbereich:** Dieses Dokument definiert die Sprachstruktur, Namenskonventionen und Link-Standards für alle öffentliche Dokumentation im AMO Telegram Bot Repository.

---

## 1. Dokumenten-Sprachstruktur / Document Language Structure

### 1.1 Arten von Sprachaufteilung / Types of Language Split

| Typ / Type | Beschreibung / Description | Beispiele / Examples |
|------------|---------------------------|----------------------|
| **Separate Files** / **Getrennte Dateien** | Vollständige DE- und EN-Versionen als eigenständige Dateien. Wird für umfangreiche Anleitungen verwendet. | `SETUP_DE.md` + `SETUP_EN.md`<br>`BETATEST_DE.md` + `BETATEST_EN.md` |
| **Bilingual Single File** / **Bilinguale Einzeldatei** | Eine Datei mit parallelen DE- und EN-Abschnitten. Wird für mittelgroße Dokumente verwendet. | `USERPLUGINS.md`<br>`CONTRIBUTING.md`<br>`WEBUI_PLUGIN_DETAIL.md` |
| **EN-Only** | Englisch als technische Lingua Franca für Architektur- und Spezifikationsdokumente. | `CONTEXT_MEMORY_ARCHITECTURE.md` |

### 1.2 Entscheidungsmatrix / Decision Matrix

**Wann welche Struktur verwenden?**

| Kriterium / Criterion | Separate Files | Bilingual Single | EN-Only |
|----------------------|----------------|------------------|---------|
| Dokument > 500 Zeilen / Doc > 500 lines | ✅ | ❌ | ✅ |
| Primäre User-Doku / Primary user docs | ✅ | ✅ | ❌ |
| Technische Architektur / Technical architecture | ❌ | ❌ | ✅ |
| Enge Kopplung an Code / Tight code coupling | ❌ | ✅ | ❌ |
| Häufige Übersetzungsänderungen / Frequent translation changes | ✅ | ❌ | N/A |

---

## 2. Naming Conventions / Namenskonventionen

### 2.1 Dateinamen / File Names

**Separate Sprachversionen:**
```
DOKUMENTNAME_DE.md  # Deutsch
DOKUMENTNAME_EN.md  # English
```

**Bilinguale Dokumente:**
```
dokumentname.md     # Kein Sprachsuffix
```

**Technische EN-Only Dokumente:**
```
TECHNICAL_SPEC.md   # Kein Sprachsuffix, EN implizit
```

### 2.2 Sprachcodes / Language Codes

| Code | Sprache / Language | Verwendung |
|------|-------------------|------------|
| `DE` | Deutsch (Deutschland) | Dokumente, Links |
| `EN` | English | Dokumente, Links |
| `🇩🇪` | Deutsch (Emoji) | Header, visuelle Markierung |
| `🇬🇧` | English (Emoji) | Header, visuelle Markierung |

---

## 3. Linkstruktur / Link Structure

### 3.1 Interne Links / Internal Links

**Format für Sprach-spezifische Links:**
```markdown
<!-- In DE-Dokumenten -->
[Siehe Setup-Anleitung](SETUP_DE.md)

<!-- In EN-Dokuments -->
[See Setup Guide](SETUP_EN.md)
```

**Format für bilinguale Cross-Referenzen:**
```markdown
[Setup-Anleitung (DE)](SETUP_DE.md) / [Setup Guide (EN)](SETUP_EN.md)
```

**Format für bilinguale Dokumente:**
```markdown
[Userplugin Guide](USERPLUGINS.md)  <!-- Bilingual, keine Sprachkennung nötig -->
```

### 3.2 Link-Validierung / Link Validation

Alle Links müssen:
1. ✅ Relativ sein (keine absoluten Pfade zu internen Ressourcen)
2. ✅ Die korrekte Sprachversion referenzieren
3. ✅ Existierende Dateien targeten

---

## 4. Dokumenten-Header-Struktur / Document Header Structure

### 4.1 README.md Pattern

```markdown
# Project Name

> **DE:** Kurze Beschreibung auf Deutsch.
> **EN:** Short description in English.

---

## Deutsch 🇩🇪

[Vollständiger Inhalt auf Deutsch]

---

## English 🇬🇧

[Complete content in English]

---

## Gemeinsame Abschnitte / Shared Sections

[License, Security Notes, etc. - bilingual oder neutral]
```

### 4.2 Bilinguale Einzeldatei Pattern

```markdown
# Dokumententitel / Document Title

> **DE:** Beschreibung
> **EN:** Description

---

## Abschnitt 1 / Section 1

### Deutsch

Inhalt...

### English

Content...
```

---

## 5. Sprach-Labeling in UI / Language Labeling in UI

### 5.1 Für Flask-WebUI (nach Backend-Completion)

Strings sollen wie folgt strukturiert sein:
```python
# Beispiel für i18n-Ready-Struktur
LABELS = {
    "page_title": {
        "de": "Plugin-Übersicht",
        "en": "Plugin Overview"
    },
    "save_button": {
        "de": "Speichern",
        "en": "Save"
    }
}
```

### 5.2 Für Bot-Antworten (nach Backend-Completion)

```python
RESPONSES = {
    "welcome": {
        "de": "Willkommen! Nutze /help für Hilfe.",
        "en": "Welcome! Use /help for assistance."
    }
}
```

---

## 6. i18n Inventory Preparation (für Issue #14)

### 6.1 Backend-Strings zu inventarisieren (nach #10/#11)

- [ ] Telegram Bot-Antwortnachrichten (alle `/commands`)
- [ ] WebUI Flask Template-Strings
- [ ] Fehlermeldungen (User-facing)
- [ ] Logging-Strings (Internal, EN-only OK)
- [ ] Hilfetexte und Command-Beschreibungen
- [ ] Button-Labels (Inline-Keyboards)
- [ ] Validierungsmeldungen

### 6.2 Inventar-Format für #14

```yaml
# Beispiel-Eintrag für #14 Inventory
category: telegram_commands
string_id: cmd_help_response
german: "Verfügbare Befehle: {commands}"
english: "Available commands: {commands}"
context: "Dynamisch generierte Hilfe mit User-Rollen-Filterung"
status: "pending_translation"  # oder "complete"
```

---

## 7. Dokumenten-Status-Übersicht / Document Status Overview

| Dokument | Typ | DE | EN | Status |
|----------|-----|----|----|--------|
| README.md | Bilingual Single | ✅ | ✅ | ✅ Complete |
| CHANGELOG.md | Bilingual Single | ✅ | ✅ | ✅ Complete |
| SETUP_DE/EN.md | Separate Files | ✅ | ✅ | ✅ Complete |
| BETATEST_DE/EN.md | Separate Files | ✅ | ✅ | ✅ Complete |
| USERPLUGINS.md | Bilingual Single | ✅ | ✅ | ✅ Complete |
| YT-RSS.md | Bilingual Single | ⚠️ | ✅ | ✅ Complete (EN-primary; DE headers only — exception documented) |
| WEBUI_PLUGIN_DETAIL.md | Bilingual Single | ✅ | ✅ | ✅ Complete |
| WEBUI_PLUGIN_OVERVIEW.md | Bilingual Single | ✅ | ✅ | ✅ Complete |
| CONTRIBUTING.md | Bilingual Single | ✅ | ✅ | ✅ Complete |
| CONTEXT_MEMORY_ARCHITECTURE.md | EN-Only | N/A | ✅ | ✅ Complete (bewusst) |
| SECURITY.md | Bilingual Single | ✅ | ✅ | ✅ Complete |
| CODE_OF_CONDUCT.md | Bilingual Single | ✅ | ✅ | ✅ Complete |
| SUPPORT.md | Bilingual Single | ✅ | ✅ | ✅ Complete |
| ROADMAP.md | Bilingual Single | ✅ | ✅ | ✅ Complete |
| i18n-inventory.md | Bilingual Single | ✅ | ✅ | ✅ Complete |
| LANGUAGE_CONVENTIONS.md | Bilingual Single | ✅ | ✅ | ✅ Complete |

---

## 8. Checkliste für neue Dokumente / Checklist for New Documents

- [ ] Sprachstruktur entschieden (Separate/Bilingual/EN-Only)
- [ ] Für EN-Only: Rationale dokumentiert (warum keine DE-Version)
- [ ] Dateiname nach Konvention benannt
- [ ] Header mit bilingualer Kurzbeschreibung
- [ ] Alle internen Links validiert
- [ ] Cross-Referenzen zu anderen Docs geprüft
- [ ] In LANGUAGE_CONVENTIONS.md Tabelle eingetragen
- [ ] In i18n-inventory.md Decision Matrix aktualisiert

---

**Letzte Aktualisierung / Last Updated:** 2026-05-27 (GH-DOCS-18 — YT-RSS.md documented exception)
**Version:** 1.2.0
**Git Commit:** [wird nach Commit eingetragen]

---

## Anhang: GH-DOCS-13 – Decision Matrix für DE/EN Counterparts

> **Scope:** Dokumentation der Entscheidungen für bilingual strukturierte Core-Dokumente.

### Decision Matrix

| Dokument | DE-Version | EN-Version | Struktur | Rationale |
|----------|------------|------------|----------|-----------|
| README.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Haupt-Entry-Point; Sprachwahl-Table |
| CHANGELOG.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Release-Entries bilingual |
| CONTRIBUTING.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Community-Guide für DE+EN |
| SECURITY.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Sicherheitskontakt; beide Sprachgruppen |
| SETUP_DE.md / SETUP_EN.md | ✅ | ✅ | Separate Files | Umfangreich; separate Versionen |
| BETATEST_DE.md / BETATEST_EN.md | ✅ | ✅ | Separate Files | Umfangreich; parallele Anleitungen |
| USERPLUGINS.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Plugin-Dev-Guide; Zielgruppe bilingual |
| YT-RSS.md | ⚠️ (headers) | ✅ | Bilingual Single | Plugin-Example / Plugin-Beispiel; EN-primary with DE section headers only — documented exception (GH-DOCS-18) |
| LANGUAGE_CONVENTIONS.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Meta-Dokument; bilingual by design |
| CONTEXT_MEMORY_ARCHITECTURE.md | ❌ (N/A) | ✅ | EN-Only | Technische Architektur; Lingua Franca |
| ROADMAP.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Projekt-Richtung; Community-Update |
| CODE_OF_CONDUCT.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Verhaltenskodex; rechtlich relevant |
| SUPPORT.md | ✅ (inline) | ✅ (inline) | Bilingual Single | Support-Info; Nutzer-relevant |
| i18n-inventory.md | ✅ (inline) | ✅ (inline) | Bilingual Single | i18n-Tracking; bilingual by design |

### Zusammenfassung GH-DOCS-13

- **Bilingual Single:** 13 Dokumente
- **Separate Files:** 2 Dokument-Paare (SETUP, BETATEST)
- **EN-Only:** 1 Dokument (CONTEXT_MEMORY_ARCHITECTURE.md)

✅ **Status:** Alle Core-Dokumente klassifiziert mit Rationale.
