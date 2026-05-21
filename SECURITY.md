# Security / Sicherheit

> **DE:** Verantwortungsvolle Meldung von Sicherheitsproblemen
> **EN:** Responsible disclosure of security issues

---

## 🇩🇪 Deutsch

### Sicherheitslücken melden

Wenn du eine Sicherheitslücke im AMO Telegram Bot entdeckst, melde sie bitte **nicht** über öffentliche Issues.

Stattdessen:

1. **Nutze GitHub Private Vulnerability Reporting** (falls aktiviert) oder
2. **Kontaktiere den Maintainer direkt** über einen sicheren Kanal

### Was du melden solltest

- Schwachstellen, die zu unautorisiertem Zugriff führen können
- Datenlecks oder Exposition sensibler Informationen
- CSRF- oder Injection-Schwachstellen
- Authentifizierungs- oder Autorisierungsfehler

### Was **nicht** zu melden ist

- Fehlende Funktionen oder Verbesserungsvorschläge (diese gehören in normale Issues)
- Bekannte Einschränkungen, die in der Dokumentation dokumentiert sind
- Theoretische Angriffe ohne praktischen Nachweis

### Vertraulichkeit

- Wir behandeln alle Sicherheitsmeldungen vertraulich
- Wir geben keine Details preis, bevor ein Fix verfügbar ist
- Wir kreditieren Finder nach Absprache (oder anonym, wenn gewünscht)

### Do's and Don'ts

**✅ Do:**
- Teste nur auf eigenen Instanzen
- Halte den Angriffsvektor minimal (Proof of Concept statt Exploit)
- Gib uns Zeit zur Behebung (90 Tage sind Standard)

**❌ Don't:**
- Nutze Schwachstellen für unautorisierte Zugriffe
- Veröffentliche Details vor einem vereinbarten Zeitpunkt
- Überflute Systeme mit Requests (DoS-Tests ohne Absprache)

---

## 🇬🇧 English

### Reporting Security Vulnerabilities

If you discover a security vulnerability in AMO Telegram Bot, please **do not** report it via public issues.

Instead:

1. **Use GitHub Private Vulnerability Reporting** (if enabled) or
2. **Contact the maintainer directly** via a secure channel

### What to Report

- Vulnerabilities that could lead to unauthorized access
- Data leaks or exposure of sensitive information
- CSRF or injection vulnerabilities
- Authentication or authorization flaws

### What **Not** to Report

- Missing features or enhancement suggestions (these belong in regular issues)
- Known limitations documented in the docs
- Theoretical attacks without practical proof of concept

### Confidentiality

- We treat all security reports confidentially
- We do not disclose details before a fix is available
- We credit finders by agreement (or anonymously if preferred)

### Do's and Don'ts

**✅ Do:**
- Test only on your own instances
- Keep the attack vector minimal (Proof of Concept, not exploit)
- Give us time to fix (90 days is standard)

**❌ Don't:**
- Exploit vulnerabilities for unauthorized access
- Disclose details before an agreed timeline
- Flood systems with requests (DoS testing without coordination)

---

## Schlüsselmanagement / Key Management

### BOT_TOKEN

- **Niemals** committen oder in Logs ausgeben
- Nur in `.env` speichern (`.env` ist in `.gitignore`)
- Bei Verdacht auf Kompromittierung: Bei @BotFather neu generieren

### OPENAI_API_KEY

- **Niemals** committen — behandle wie `BOT_TOKEN`
- Nur in `.env` speichern, niemals in Code oder Config-Dateien
- Bei Verdacht auf Kompromittierung: Bei OpenAI neu generieren
- Wird intern redacted (nur `***` in Diagnostics sichtbar)

### OPENAI_API_KEY

- **Never** commit — treat like `BOT_TOKEN`
- Store only in `.env`, never in code or config files
- If compromised: Regenerate at OpenAI dashboard
- Internally redacted (only `***` visible in diagnostics)

### WEBUI_PASSWORD

- Starke Passwörter verwenden (mindestens 16 Zeichen)
- WebUI bleibt lokal (`127.0.0.1`) – keine Port-Weiterleitung ohne Proxy
- Für öffentliche Deployments: Reverse Proxy mit HTTPS erforderlich

### API-Keys und Secrets

- Alle Secrets über Umgebungsvariablen oder `.env` laden
- Keine Secrets in Config-Dateien oder Code hinterlegen
- Regular auf ungewollte Commits prüfen

---

## Empfohlene Sicherheitspraktiken / Recommended Security Practices

### Für Betreiber

1. **Aktuelle Version** verwenden
2. **WebUI nicht öffentlich** erreichbar machen
3. **Regelmäßige Backups** der Datenbank
4. **Logs überwachen** auf verdächtige Aktivitäten
5. **Plugins nur aus vertrauenswürdigen Quellen** installieren

### Für Entwickler

1. **Keine Secrets in Code**
2. **Eingaben validieren** (SQL-Injection, XSS)
3. **CSRF-Token** bei WebUI-Formularen nutzen
4. **Rate-Limiting** für sensiblen Endpunkte
5. **Audit-Logs** für sensible Operationen

---

## Sicherheitskontakt / Security Contact

- **GitHub:** Privates Vulnerability Reporting (bevorzugt)
- **Alternativ:** Maintainer über sicheren Kanal kontaktieren

---

<p align="center">
  <sub>Danke, dass du zur Sicherheit von AMO beiträgst! / Thank you for helping keep AMO secure! 🔒</sub>
</p>
