# GitHub Page Draft – AMO-telegram-bot

**Status:** Lokaler Entwurf für spätere GitHub-Veröffentlichung  
**Aktueller Stand:** MVP / Betatest-Phase – kein Production-Release

---

## 1. Repo-Name Vorschlag

**`amo-telegram-bot`**

Alternativ:
- `amo-bot` (kürzer, aber weniger spezifisch)
- `defensive-telegram-bot` (beschreibend, aber lang)

Empfehlung: `amo-telegram-bot` – klar, prägnant, suchbar.

---

## 2. Short Description / GitHub About Text

### Deutsch
> Defensiver Telegram-Bot mit WebUI, rollenbasierter Berechtigungssteuerung und Ollama-Integration. MVP-Status – erweiterbar und testbereit.

### English
> Defensive Telegram bot with WebUI, role-based permissions, and Ollama integration. MVP status – extensible and ready for testing.

---

## 3. Topics/Tags Vorschläge

```
telegram-bot, python, telegram-api, ollama, llm-integration, 
webui, role-based-access, mvp, extensible-architecture, 
plugin-system, flask, sqlite
```

Empfohlene Top-5 für Sichtbarkeit:
- `telegram-bot`
- `python`
- `ollama`
- `llm-integration`
- `webui`

---

## 4. README Hero/Intro Draft

```markdown
# AMO Telegram Bot

Ein defensiver, erweiterbarer Telegram-Bot mit lokaler WebUI, 
rollenbasierter Berechtigungssteuerung und optionaler Ollama-Integration.

**Status:** MVP / Betatest – erstes stabiles Grundgerüst, noch nicht Production.

## Schnellstart

```bash
pip install -r requirements.txt
python main.py  # Startet Bot + WebUI
```

- **Telegram-Commands:** `/ping`, `/help`, `/role`, `/setrole`, `/ask`
- **Rollen:** `owner`, `admin`, `vip`, `normal`, `ignore`
- **WebUI:** http://127.0.0.1:8080 – Login, Nutzerverwaltung, Plugin-Steuerung
- **Ollama:** `/ask <frage>` für KI-gestützte Antworten (optional)
```

---

## 5. Featureliste – Aktueller Stand

| Feature | Status | Hinweis |
|---------|--------|---------|
| Telegram Long Polling | ✅ MVP | Via `getUpdates` |
| Command-Dispatcher | ✅ MVP | Rollenabhängige Hilfe |
| `/ping`, `/help`, `/role` | ✅ MVP | Alle Rollen |
| `/setrole <id> <rolle>` | ✅ MVP | Owner/Admin |
| Role-Resolver (DB-basiert) | ✅ MVP | SQLite |
| Audit-Events | ✅ MVP | Rollenwechsel |
| WebUI (Flask) | ✅ MVP | Nur lokal, nicht für Production |
| WebUI-Auth + Sessions | ✅ MVP | Login/Session-basiert mit CSRF |
| Nutzerverwaltung via WebUI | ✅ MVP | Rollen ändern |
| Plugin-System (Manifest) | ✅ MVP | Command/Scheduled/Worker Runtime MVP + Betriebsoberfläche |
| Ollama `/ask` | ✅ MVP | Stateless, ohne Verlauf |
| Echte Plugin-Ausführung | 🚧 Später | Nicht im MVP |
| Kanäle (Channels) | 🚧 Später | Nur Chats/Gruppen |
| Medienversand | 🚧 Später | Nur Text |
| Production-Deployment | 🚧 Später | Nur lokaler Betatest |

---

## 6. Betatest Setup Snippet

```bash
# 1. Repository klonen
git clone <repo-url>
cd amo-telegram-bot

# 2. Virtuelle Umgebung erstellen
python3.12 -m venv venv
source venv/bin/activate

# 3. Abhängigkeiten installieren
pip install -r requirements.txt

# 4. Konfiguration kopieren und anpassen
cp .env.example .env
# .env mit BOT_TOKEN, WEBUI_PASSWORD, etc. füllen

# 5. Ordner erstellen
mkdir -p data .state plugins

# 6. Tests ausführen
pytest -q

# 7. Bot + WebUI starten
python main.py
```

**Erreichbarkeit:**
- WebUI: http://127.0.0.1:8080
- Bot: Über Telegram @<dein_bot_name>

---

## 7. Security-Hinweise

> ⚠️ **WICHTIG:** Dies ist ein MVP für lokale Tests. Nicht für öffentlichen Zugriff ohne weitere Härtung.

- **WebUI** läuft standardmäßig nur auf `127.0.0.1` – nicht auf `0.0.0.0` oder öffentlichen IPs binden
- **Secrets** (BOT_TOKEN, WEBUI_PASSWORD) niemals ins Git committen – `.env` verwenden
- **Admin-Rechte** können keine Owner ernennen – nur Owner → Admin
- **Plugin-Aktivierung** im MVP nur via WebUI erlaubt, nicht via Telegram
- **Keine Secrets** in Logs, Chats oder Issues posten

---

## 8. Roadmap / Next Steps

### Kurzfristig (vor öffentlichem Release)
- [ ] Code-Review und Härtung
- [ ] Tests für Edge-Cases erweitern
- [ ] Dokumentation vervollständigen
- [ ] Lizenz wählen (MIT/Apache/etc.)

### Mittelfristig
- [ ] Plugin-Laufzeitumgebung (sichere Code-Ausführung)
- [ ] Channel-Support
- [ ] Medienversand (Bilder, Dokumente)
- [ ] Chat-Verlauf für `/ask`

### Langfristig
- [ ] Production-Ready Konfiguration
- [ ] Multi-User-WebUI mit RBAC
- [ ] Containerisierung (Docker)
- [ ] CI/CD Pipeline

---

## 9. Was vor öffentlichem Upload geprüft werden muss

### Code
- [ ] Keine hardcoded Secrets oder Tokens
- [ ] `.env.example` enthält keine echten Werte
- [ ] `.gitignore` umfasst `.env`, `data/`, `.state/`
- [ ] Keine Debug- oder Test-Accounts im Code

### Dokumentation
- [ ] README ist aktuell und vollständig
- [ ] Lizenz-Datei vorhanden
- [ ] Contribution-Guidelines (falls gewünscht)

### Security
- [ ] WebUI-Default auf `127.0.0.1` geprüft
- [ ] Session-Management geprüft
- [ ] Input-Validierung überall vorhanden

### Repo
- [ ] Commit-History geprüft (keine Secrets in alten Commits)
- [ ] Branch-Protection für `main` eingerichtet

---

## 10. GitHub Repo Settings Vorschlag

### Sichtbarkeit
**Empfehlung:** Zunächst **privat**

- MVP ist noch nicht Production-ready
- Ermöglicht interne Tests vor öffentlichem Release
- Später auf public umstellen, wenn stabiler

### Features aktivieren

| Feature | Empfehlung | Begründung |
|---------|------------|------------|
| **Issues** | ✅ Ja | Für Bug-Reports und Feature-Requests |
| **Discussions** | ✅ Ja | Für Fragen und Community-Austausch |
| **Wiki** | ❌ Nein | Doku lieber im Repo (versioniert) |
| **Projects** | ⚪ Optional | Für Roadmap-Tracking nützlich |
| **Actions** | ⚪ Optional | CI/CD später sinnvoll |

### Default Branch
- **Name:** `main` (Standard)
- **Protection:** Empfohlen – Reviews erforderlich, direkte Pushes auf `main` verbieten

### Weitere Einstellungen
- **Template für Issues:** Ja (Bug-Report, Feature-Request)
- **Dependabot:** Empfohlen für Dependency-Updates
- **Security Advisories:** Aktivieren für verantwortungsvolle Disclosure

---

## Zusammenfassung

Dieser Entwurf dient als Vorlage für die spätere GitHub-Projektseite. Das Projekt befindet sich im MVP/Betatest-Status – funktionsfähig, aber noch nicht für Production oder öffentlichen Zugriff ohne weitere Härtung bestimmt.

**Nächster Schritt:** Nach internem Betatest und Code-Review kann das Repo auf public geschaltet und die GitHub-Page entsprechend dieses Entwurfs erstellt werden.
