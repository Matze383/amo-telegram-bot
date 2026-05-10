# AMO Telegram Bot — Setup-Anleitung

Vollständige Anleitung zum lokalen Betrieb des Bots.

---

## Voraussetzungen

- Python 3.12 oder höher
- Linux- oder macOS-Entwicklungsumgebung
- Telegram Bot Token (von [@BotFather](https://t.me/BotFather))
- Optional: Lokale [Ollama](https://ollama.com/)-Instanz für KI-Funktionen

---

## Installation

### 1. Repository klonen und einrichten

```bash
git clone <repository-url>
cd AMO-telegram-bot
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Umgebungsvariablen konfigurieren

Beispieldatei kopieren und bearbeiten:

```bash
cp .env.example .env
```

`.env` mit deinen Werten bearbeiten:

```ini
# Pflicht: Telegram
BOT_TOKEN=dein_bot_token_hier
BOT_USERNAME=dein_bot_username

# Pflicht: WebUI
WEBUI_PASSWORD=dein_sicheres_passwort
WEBUI_OWNER_TELEGRAM_ID=deine_telegram_user_id

# Optional: Ollama (für /ask Kommando)
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_MAX_RESPONSE_CHARS=1500

# Optional: Datenbank (Standard: SQLite)
DATABASE_URL=sqlite:///./data/amo_bot.db

# Optional: Plugin-Verzeichnis
AMO_PLUGIN_DIR=./plugins

# Optional: WebUI-Einstellungen
WEBUI_HOST=127.0.0.1
WEBUI_PORT=8080
WEBUI_SESSION_TTL_SECONDS=3600

# Sicherheitseinstellungen (Block 1)
# WEBUI_PUBLIC_MODE=false
# WEBUI_REQUIRE_HTTPS=false
# WEBUI_SESSION_COOKIE_SECURE=

# Sicherheitseinstellungen (Block 2 – Login-Schutz)
# WEBUI_LOGIN_DELAY_BASE_SECONDS=0.25
# WEBUI_LOGIN_DELAY_MAX_SECONDS=2.0
```

> **Config-Priorität:** Beim lokalen Start überschreibt `.env` Shell-Umgebungsvariablen. Setze `AMO_ENV_OVERRIDE=0`, um dies zu deaktivieren.

---

## Sicherheitseinstellungen (Block 1 + Block 2)

Die WebUI enthält konfigurierbare Sicherheitsfeatures:

## Umgebungsvariablen

### Block 1: Session-Sicherheit

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `WEBUI_PUBLIC_MODE` | `false` | Aktivieren für öffentliche/Internet-Deployments. Erzwingt strengere Sicherheitsprüfungen. |
| `WEBUI_REQUIRE_HTTPS` | `false` | HTTPS erforderlich. Sollte bei öffentlichem Betrieb `true` sein. |
| `WEBUI_SESSION_COOKIE_SECURE` | *(auto)* | Überschreibt das Secure-Flag des Cookies. Leer = auto (true bei public ODER require_https). |

### Block 2: Login-Schutz

| Variable | Standard | Einschränkungen | Beschreibung |
|----------|----------|-----------------|--------------|
| `WEBUI_LOGIN_DELAY_BASE_SECONDS` | `0,25` | nicht-negativ | Basisverzögerung nach erstem fehlgeschlagenen Login (Sekunden) |
| `WEBUI_LOGIN_DELAY_MAX_SECONDS` | `2,0` | nicht-negativ, muss >= Basis sein | Maximale Verzögerung (Sekunden) |

---

## Security Headers

Die WebUI setzt folgende HTTP-Security-Header:

- **Content-Security-Policy (CSP):** Beschränkt das Laden von Ressourcen
- **X-Frame-Options: DENY:** Verhindert Clickjacking
- **X-Content-Type-Options: nosniff:** Verhindert MIME-Sniffing
- **Referrer-Policy: strict-origin-when-cross-origin:** Begrenzt Referrer-Lecks
- **Permissions-Policy:** Beschränkt Browser-Features
- **HSTS:** Nur in HTTPS/Secure-Kontexten

---

## Session-Cookie-Sicherheit

Session-Cookies verwenden:
- **HttpOnly:** Verhindert JavaScript-Zugriff
- **SameSite=Lax:** CSRF-Schutz
- **Secure:** Automatisch bei Public-Modus oder HTTPS; Überschreibung via `WEBUI_SESSION_COOKIE_SECURE`

---

## Login-Schutz (Block 2)

Zum Schutz vor Brute-Force-Angriffen implementiert die WebUI **progressive Verzögerungen** nach fehlgeschlagenen Login-Versuchen (exponentieller Backoff).

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `WEBUI_LOGIN_DELAY_BASE_SECONDS` | `0,25` | Basisverzögerung nach erstem Fehlversuch (Sekunden) |
| `WEBUI_LOGIN_DELAY_MAX_SECONDS` | `2,0` | Maximale Verzögerung (Sekunden). Muss >= Basis sein. |

**Verhalten:**
- Verzögerung erhöht sich progressiv nach jedem fehlgeschlagenen Versuch (exponentieller Backoff)
- Verzögerung ist gecappt bei `WEBUI_LOGIN_DELAY_MAX_SECONDS`
- Erfolgreicher Login setzt den Zähler zurück
- Verzögerungen gelten pro IP-Adresse (`remote_addr`)
- `LoginAttemptTracker` ist In-Memory pro Prozess mit `max_keys`-Limit und oldest eviction
- Multi-Prozess/Shared-State bleibt ein zukünftiges Enhancement

**Audit-Events:**
- `webui_login_failure` — Protokolliert fehlgeschlagenen Login-Versuch
- `webui_login_success` — Protokolliert erfolgreichen Login

Beide Events enthalten nur `remote_addr`. Es werden keine Passwörter oder andere sensible Daten protokolliert.

> **Reverse-Proxy-Hinweis:** Bei Betrieb hinter einem Reverse Proxy muss `remote_addr` korrekt und vertrauenswürdig durch die Infrastruktur gesetzt werden. Die WebUI verwendet `remote_addr` direkt ohne Auswertung von `X-Forwarded-For`. Flask niemals direkt öffentlich exponieren.

---

## Lokale Entwicklungs-Defaults

Für lokale Tests die Standardwerte beibehalten:

```ini
WEBUI_PUBLIC_MODE=false
WEBUI_REQUIRE_HTTPS=false
# WEBUI_SESSION_COOKIE_SECURE=  # leer lassen für auto
```

---

## Produktions-/Internet-Deployment

**⚠️ Warnung:** Flask nicht direkt ins Internet stellen. Reverse Proxy (nginx, Caddy, Traefik) mit HTTPS verwenden.

Empfohlene Produktionskonfiguration:

```ini
WEBUI_PUBLIC_MODE=true
WEBUI_REQUIRE_HTTPS=true
# WEBUI_SESSION_COOKIE_SECURE=  # auto-aktiviert
```

Die WebUI bricht bei unsicherer Konfiguration im Public-Modus sofort mit einer klaren Fehlermeldung ab.

---

## Bot starten

### Nur Bot (Polling)

```bash
source venv/bin/activate
python main.py
```

### Nur WebUI

```bash
source venv/bin/activate
python main.py --webui
```

### Bot + WebUI zusammen

```bash
source venv/bin/activate
python main.py
```

---

## Telegram Bot einrichten

1. [@BotFather](https://t.me/BotFather) auf Telegram anschreiben
2. Neuen Bot erstellen: `/newbot`
3. Den bereitgestellten Token kopieren
4. Bot-Username in `.env` eintragen

---

## Preflight-Tests

Vor der Verbindung zu echten Telegram-APIs:

```bash
source venv/bin/activate
pytest -q
python -m amo_bot.smoke
```

Erwartete Ergebnisse:
- pytest: Alle Tests bestanden
- smoke: Bootstrap und Basis-Commands OK

---

## Fehlerbehebung

### Bot antwortet nicht
- Terminal prüfen: Läuft `python main.py`?
- `.env` prüfen: Ist `BOT_TOKEN` korrekt?
- Telegram prüfen: Wurde "Start" im Bot-Chat geklickt?

### Datenbank/SQLite-Fehler
- Existiert das Verzeichnis `data/`?
- Schreibrechte vorhanden?
- Nur für Tests: `rm data/amo_bot.db` und Neustart

### Ollama nicht erreichbar
- Läuft Ollama? `curl http://127.0.0.1:11434/api/tags`
- Ist die URL in `.env` korrekt?
- Firewall blockt Port 11434?

### WebUI-Login funktioniert nicht
- Ist `WEBUI_PASSWORD` in `.env` gesetzt?
- Ist der Wert nicht leer oder "change_me"?
- Wird `http://127.0.0.1:8080` aufgerufen?

---

## Nächste Schritte

- Siehe [BETATEST_DE.md](BETATEST_DE.md) für detaillierte Testanleitungen
- Siehe [RELEASE_NOTES_2026.05.09-Beta_DE.md](RELEASE_NOTES_2026.05.09-Beta_DE.md) für das Changelog

## WebUI: Gruppenrollenverwaltung

Nach dem Login unter "Groups" können Gruppenrollen verwaltet werden:

- Nutzer und deren aktuelle Rolle anzeigen
- Rollen setzen: `admin`, `vip`, `normal`, `ignore`
- `owner` kann nicht als Gruppenrolle vergeben werden (nur via `.env`)
- `normal` löscht den gruppen-spezifischen Eintrag → Fallback auf `normal`
- Rollen sind gruppen-spezifisch, nicht global gültig
