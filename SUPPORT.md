# Support / Hilfe

> **DE:** Wo du Hilfe findest und was du erwarten kannst
> **EN:** Where to find help and what to expect

---

## 🇩🇪 Deutsch

### Erste Hilfe

Bevor du nach Unterstützung fragst, prüfe bitte:

1. **Dokumentation lesen**
   - [Setup-Anleitung (DE)](docs/SETUP_DE.md)
   - [Beta-Test Anleitung](docs/BETATEST_DE.md)
   - [Release Notes](docs/)

2. **README prüfen**
   - Aktuelle Features und Einschränkungen
   - Bekannte Probleme

3. **Bestehende Issues durchsuchen**
   - Vielleicht wurde dein Problem bereits gemeldet

---

### Support-Kanäle

| Kanal | Verwendung | Antwortzeit |
|-------|------------|-------------|
| **GitHub Issues** | Bug-Reports, Feature-Requests | 7 Tage |
| **GitHub Discussions** | Fragen, Ideen, Hilfe | 7-14 Tage |
| **Issue-Templates** | Strukturierte Bug-/Feature-Meldungen | – |

### Was wir unterstützen

**✅ Unterstützt:**
- Installation auf unterstützten Plattformen (Linux, macOS, Windows)
- Konfiguration gemäß offizieller Dokumentation
- Bugs in aktueller `main`-Version
- Feature-Requests mit klarer Beschreibung

**❌ Nicht unterstützt:**
- Modifizierte oder veraltete Versionen
- Unsupportete Python-Versionen (< 3.12)
- Individuelle Deployment-Setups (Server-Konfiguration, Reverse-Proxies)
- Drittanbieter-Plugins (außer offiziellen)

---

### Erwartungshaltung

**Beta-Status:**
- AMO befindet sich in der Beta-Phase
- API und Features können sich ändern
- Produktiveinsatz ohne Backup nicht empfohlen

**Antwortzeiten:**
- Issues: 7 Tage (länger bei komplexen Problemen)
- Discussions: 7-14 Tage
- Keine garantierten SLAs

---

### Gute Fragen stellen

**Ein gutes Issue enthält:**

1. **Klaren Titel** (z.B. "Bot reagiert nicht auf /start im privaten Chat")
2. **Beschreibung** des Problems
3. **Reproduktionsschritte** (Schritt für Schritt)
4. **Erwartetes vs. tatsächliches** Verhalten
5. **System-Info:**
   - Betriebssystem und Version
   - Python-Version (`python --version`)
   - AMO-Version/Commit
6. **Logs** (ohne Token/Secrets!)

**Beispiel:**

```
Titel: WebUI zeigt Fehler 500 nach Login

Beschreibung:
Nach Eingabe des Passworts erscheint "Internal Server Error".

Reproduktion:
1. Bot starten: python main.py --webui
2. http://127.0.0.1:8080 öffnen
3. Passwort eingeben
4. Enter drücken

Erwartet: Dashboard wird angezeigt
Tatsächlich: 500 Fehlerseite

System:
- Ubuntu 22.04
- Python 3.12.3
- Commit: abc1234

Logs:
[Relevante Log-Zeilen ohne Token]
```

---

## 🇬🇧 English

### First Aid

Before asking for help, please check:

1. **Read the documentation**
   - [Setup Guide (EN)](docs/SETUP_EN.md)
   - [Beta Test Guide](docs/BETATEST_EN.md)
   - [Release Notes](docs/)

2. **Check the README**
   - Current features and limitations
   - Known issues

3. **Search existing issues**
   - Your problem may already be reported

---

### Support Channels

| Channel | Usage | Response Time |
|---------|-------|---------------|
| **GitHub Issues** | Bug reports, feature requests | 7 days |
| **GitHub Discussions** | Questions, ideas, help | 7-14 days |
| **Issue Templates** | Structured bug/feature reports | – |

### What We Support

**✅ Supported:**
- Installation on supported platforms (Linux, macOS, Windows)
- Configuration per official documentation
- Bugs in current `main` version
- Feature requests with clear description

**❌ Not Supported:**
- Modified or outdated versions
- Unsupported Python versions (< 3.12)
- Individual deployment setups (server configuration, reverse proxies)
- Third-party plugins (except official ones)

---

### Expectations

**Beta Status:**
- AMO is in beta
- API and features may change
- Production use without backup not recommended

**Response Times:**
- Issues: 7 days (longer for complex problems)
- Discussions: 7-14 days
- No guaranteed SLAs

---

### Asking Good Questions

**A good issue contains:**

1. **Clear title** (e.g., "Bot doesn't respond to /start in private chat")
2. **Description** of the problem
3. **Reproduction steps** (step by step)
4. **Expected vs. actual** behavior
5. **System info:**
   - OS and version
   - Python version (`python --version`)
   - AMO version/commit
6. **Logs** (without tokens/secrets!)

**Example:**

```
Title: WebUI shows error 500 after login

Description:
After entering password, "Internal Server Error" appears.

Reproduction:
1. Start bot: python main.py --webui
2. Open http://127.0.0.1:8080
3. Enter password
4. Press enter

Expected: Dashboard displays
Actual: 500 error page

System:
- Ubuntu 22.04
- Python 3.12.3
- Commit: abc1234

Logs:
[Relevant log lines without tokens]
```

---

## Diagnose-Tools / Diagnostic Tools

### Logs überprüfen

```bash
# Logs im Projektverzeichnis suchen
find . -name "*.log" -type f

# Konsole mit Verbose-Output starten
python main.py --verbose
```

### Test-Suite laufen lassen

```bash
# Alle Tests
pytest -q

# Spezifische Test-Datei
pytest tests/test_ask.py -v
```

### Umgebung prüfen

```bash
# Python-Version
python --version  # Sollte 3.12+ zeigen

# Installierte Pakete
pip list | grep -E "(telegram|flask|ollama)"

# .env laden prüfen
python -c "from dotenv import load_dotenv; load_dotenv(); import os; print('BOT_TOKEN:', 'gesetzt' if os.getenv('BOT_TOKEN') else 'fehlt')"
```

---

## Keine Unterstützung für / No Support For

### Privatkontakt

- Bitte keine persönlichen Nachrichten an Maintainer
- Öffentliche Kanäle ermöglichen Community-Hilfe

### Soforthilfe-Erwartungen

- Dies ist ein Open-Source-Projekt ohne bezahlten Support
- Antworten erfolgen nach Verfügbarkeit

### Server-Administration

- Reverse-Proxy-Konfiguration (nginx, Caddy, Apache)
- SSL/TLS-Zertifikate
- Docker/Container-Setups
- Cloud-Deployment (AWS, GCP, Azure)

Diese Themen sind Projekt-extern und nicht Teil des AMO-Supports.

---

## Mitmachen / Contributing

Du kannst selbst helfen:

- **Issues beantworten** – Hilf anderen Nutzern
- **Dokumentation verbessern** – PRs willkommen
- **Tests schreiben** – Erhöht die Code-Qualität
- **Features implementieren** – Siehe [CONTRIBUTING.md](CONTRIBUTING.md)

---

<p align="center">
  <sub>Gemeinsam bauen wir AMO besser! / Let's build AMO better together! 🤝</sub>
</p>
