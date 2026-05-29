# AMO Telegram Bot — Setup-Anleitung

Vollständige Anleitung zum lokalen Betrieb des Bots.

---

## Voraussetzungen

- Python 3.12 oder höher
- Windows, macOS oder Linux
- Telegram Bot Token (von [@BotFather](https://t.me/BotFather))
- Optional: KI-Provider für das `/ask`-Kommando:
  - Lokale [Ollama](https://ollama.com/)-Instanz, **ODER**
  - OpenAI API-Key, **ODER**
  - Anthropic API-Key, **ODER**
  - Google/Gemini API-Key, **ODER**
  - OpenRouter API-Key, **ODER**
  - [Groq](https://groq.com/) API-Key, **ODER**
  - Mistral API-Key, **ODER**
  - xAI API-Key, **ODER**
  - DeepSeek API-Key, **ODER**
  - Together API-Key, **ODER**
  - Fireworks API-Key, oder
  - AWS-Credentials/-Profil für Amazon Bedrock, oder
  - SGLang lokale Server-Instanz

---

## Plattformspezifischer Schnellstart

### Linux / macOS

```bash
# Repository klonen
git clone <repository-url>
cd AMO-telegram-bot

# Virtuelle Umgebung erstellen
python3.12 -m venv venv

# Virtuelle Umgebung aktivieren
source venv/bin/activate

# Abhängigkeiten installieren
pip install -r requirements.txt

# Konfiguration kopieren und bearbeiten
cp .env.example .env
# .env bearbeiten: BOT_TOKEN, WEBUI_PASSWORD, etc.

# Bot starten
python main.py
```

### Windows (PowerShell)

```powershell
# Repository klonen
git clone <repository-url>
cd AMO-telegram-bot

# Virtuelle Umgebung erstellen
python -m venv venv

# Virtuelle Umgebung aktivieren
.\venv\Scripts\Activate.ps1

# Abhängigkeiten installieren
pip install -r requirements.txt

# Konfiguration kopieren und bearbeiten
copy .env.example .env
# .env bearbeiten: BOT_TOKEN, WEBUI_PASSWORD, etc. (mit Notepad, VS Code, etc.)

# Bot starten
python main.py
```

### Windows (Eingabeaufforderung / cmd.exe)

```cmd
REM Repository klonen
git clone <repository-url>
cd AMO-telegram-bot

REM Virtuelle Umgebung erstellen
python -m venv venv

REM Virtuelle Umgebung aktivieren
venv\Scripts\activate.bat

REM Abhängigkeiten installieren
pip install -r requirements.txt

REM Konfiguration kopieren
copy .env.example .env
REM .env bearbeiten: BOT_TOKEN, WEBUI_PASSWORD, etc.

REM Bot starten
python main.py
```

> **Windows-Hinweis:** Falls die PowerShell-Ausführungsrichtlinie Skripte blockiert, PowerShell als Administrator öffnen und ausführen: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

---

---

## Installation

### 1. Repository klonen und einrichten

**Linux / macOS:**

```bash
git clone <repository-url>
cd AMO-telegram-bot
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell):**

```powershell
git clone <repository-url>
cd AMO-telegram-bot
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Windows (Eingabeaufforderung):**

```cmd
git clone <repository-url>
cd AMO-telegram-bot
python -m venv venv
venv\Scripts\activate.bat
pip install -r requirements.txt
```

### 2. Umgebungsvariablen konfigurieren

Beispieldatei kopieren und bearbeiten:

**Linux / macOS:**

```bash
cp .env.example .env
```

**Windows (PowerShell):**

```powershell
copy .env.example .env
```

**Windows (Eingabeaufforderung):**

```cmd
copy .env.example .env
```

`.env` mit deinen Werten bearbeiten:

```ini
# Pflicht: Telegram
BOT_TOKEN=dein_bot_token_hier
BOT_USERNAME=dein_bot_username

# Pflicht: WebUI
WEBUI_PASSWORD=dein_sicheres_passwort
WEBUI_OWNER_TELEGRAM_ID=deine_telegram_user_id

# KI-Provider Konfiguration
AI_PROVIDER=ollama  # ollama (Standard), openai, anthropic, google, openrouter, groq, mistral, xai, deepseek, together, fireworks, litellm, lmstudio, vllm oder sglang

# Optional: OpenAI (für /ask Kommando)
# OPENAI_API_KEY=dein-openai-api-key-hier
# OPENAI_MODEL=gpt-4o-mini
# OPENAI_TIMEOUT_SECONDS=30

# Optional: Anthropic (für /ask Kommando)
# ANTHROPIC_API_KEY=dein-anthropic-api-key-hier
# ANTHROPIC_MODEL=anthropic/claude-opus-4-6
# ANTHROPIC_TIMEOUT_SECONDS=30
# ANTHROPIC_BASE_URL=https://api.anthropic.com

# Optional: Google/Gemini (für /ask Kommando)
# GEMINI_API_KEY=dein-google-api-key-hier
# GEMINI_MODEL=google/gemini-3-flash-preview
# GEMINI_TIMEOUT_SECONDS=30
# GEMINI_BASE_URL=https://generativelanguage.googleapis.com

# Optional: OpenRouter (für /ask Kommando)
# OPENROUTER_API_KEY=dein-openrouter-api-key-hier
# OPENROUTER_MODEL=openrouter/auto
# OPENROUTER_TIMEOUT_SECONDS=30
# OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Optional: Groq (für /ask Kommando)
# GROQ_API_KEY=
# GROQ_MODEL=groq/llama-3.1-8b-instant
# GROQ_TIMEOUT_SECONDS=30
# GROQ_BASE_URL=https://api.groq.com/openai/v1

# Optional: Mistral (für /ask Kommando)
# MISTRAL_API_KEY=
# MISTRAL_MODEL=mistral/mistral-large-latest
# MISTRAL_TIMEOUT_SECONDS=30
# MISTRAL_BASE_URL=https://api.mistral.ai/v1

# Optional: xAI (für /ask Kommando)
# XAI_API_KEY=
# XAI_MODEL=xai/grok-4.3
# XAI_TIMEOUT_SECONDS=30
# XAI_BASE_URL=https://api.x.ai/v1

# Optional: DeepSeek (für /ask Kommando)
# DEEPSEEK_API_KEY=
# DEEPSEEK_MODEL=deepseek/deepseek-v4-flash
# DEEPSEEK_TIMEOUT_SECONDS=30
# DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# Optional: Together AI (für /ask Kommando)
# TOGETHER_API_KEY=
# TOGETHER_MODEL=together/moonshotai/Kimi-K2.5
# TOGETHER_TIMEOUT_SECONDS=30
# TOGETHER_BASE_URL=https://api.together.xyz/v1

# Optional: LiteLLM (für /ask Kommando)
# LITELLM_API_KEY=
# LITELLM_MODEL=openai/gpt-4o-mini
# LITELLM_TIMEOUT_SECONDS=30
# LITELLM_BASE_URL=https://api.litellm.ai

# Optional: LM Studio (für /ask Kommando) - lokaler OpenAI-kompatibler Server
# LMSTUDIO_API_KEY=           # optional; weglassen für un authentifizierte lokale Server
# LMSTUDIO_MODEL=local-model  # Modellname, den LM Studio bereitstellt
# LMSTUDIO_TIMEOUT_SECONDS=60  # höherer Timeout empfohlen für lokale Inferenz
# LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1

# Optional: vLLM (für /ask Kommando) - lokaler OpenAI-kompatibler Server (z.B. OpenClaw Backend)
# VLLM_API_KEY=               # optional; weglassen für unauthentifizierte lokale Server
# VLLM_MODEL=                 # Modellname, den der vLLM Server bereitstellt
# VLLM_TIMEOUT_SECONDS=60     # höherer Timeout empfohlen für lokale Inferenz
# VLLM_BASE_URL=http://127.0.0.1:8000/v1

# Optional: SGLang (für /ask Kommando) - lokaler OpenAI-kompatibler Server
# SGLANG_API_KEY=             # optional; weglassen für unauthentifizierte lokale Server
# SGLANG_MODEL=               # Modellname, den der SGLang Server bereitstellt
# SGLANG_TIMEOUT_SECONDS=60   # höherer Timeout empfohlen für lokale Inferenz
# SGLANG_BASE_URL=http://127.0.0.1:8000/v1

# Optional: Ollama (für /ask Kommando)
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_MAX_PROMPT_CHARS=4000
OLLAMA_MAX_PREDICT_TOKENS=512
OLLAMA_MAX_RESPONSE_CHARS=1500
# OLLAMA_REQUEST_ENDPOINT=generate  # generate (Standard) oder chat; ungültige Werte verursachen Validierungsfehler beim Start
# OLLAMA_STREAMING_MODE=off  # off (Standard), collect_only, live_edit (nur geparstes Gate; kein Live-Telegram-Streaming)

# Optional: Datenbank (Standard: SQLite)
DATABASE_URL=sqlite:///./data/amo_bot.db

# Optional: Plugin-Verzeichnis
AMO_PLUGIN_DIR=./plugins

# Optional: Logging
LOG_LEVEL=info                # debug|info|warning|error (Standard: info)
LOG_FORMAT=text               # text|json (Standard: text)
# LOG_FILE=./logs/amo.log     # Optionaler Dateipfad für Log-Ausgabe
LOG_DEBUG_SCOPES=             # Komma-separierte Komponenten für DEBUG (z.B.: ai.router,plugin.runtime)
LOG_INCLUDE_PRIVATE_IDS=0     # Auf 1 setzen für unmaskierte IDs in strukturierten Logs

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

## Logging-Konfiguration

Der Bot verwendet strukturiertes Logging mit konfigurierbarem Ausgabeformat und Filterung.

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `LOG_LEVEL` | `info` | Minimales Log-Level: `debug`, `info`, `warning`, `error` |
| `LOG_FORMAT` | `text` | Ausgabeformat: `text` (menschenlesbar) oder `json` (strukturiert) |
| `LOG_FILE` | *(keiner)* | Optionaler Dateipfad für Log-Ausgabe (Standard: nur stderr) |
| `LOG_DEBUG_SCOPES` | *(keiner)* | Komma-separierte Komponentennamen für DEBUG-Level (z.B. `ai.router,plugin.runtime`) |
| `LOG_INCLUDE_PRIVATE_IDS` | `0` | Auf `1`/`true`/`yes` setzen für unmaskierte IDs in strukturierten Logs. **Datenschutz-Warnung:** Aktivierung kann sensible Kennungen in Log-Dateien offenlegen. |

---

## Dreaming / Memory-Curation Runtime (KI-F4)

Das Dreaming-System führt **nächtlich** eine automatische Kuratierung von täglichen Memory-Einträgen durch. Es identifiziert relevante Gesprächsmuster und hebt wichtige Informationen in das Langzeitgedächtnis. Der Worker läuft innerhalb eines konfigurierbaren Nachtfensters in Batches.

### Aktivierung

Standardmäßig ist das Dreaming-System **deaktiviert** (`DREAMING_ENABLED=0`). Um es zu aktivieren:

```ini
# Dreaming / Memory-Curation Runtime (deaktiviert by default)
DREAMING_ENABLED=1
```

### Konfigurationsvariablen

| Variable | Standard | Beschreibung |
|----------|----------|--------------|
| `DREAMING_ENABLED` | `0` | `1` zum Aktivieren der automatischen Memory-Kuratierung |
| `DREAMING_WINDOW_START` | `02:00` | Startzeit des Nachtfensters (HH:MM, Europe/Berlin) |
| `DREAMING_WINDOW_END` | `05:00` | Endzeit des Nachtfensters (HH:MM, Europe/Berlin) |
| `DREAMING_TIMEZONE` | `Europe/Berlin` | Zeitzone für das Nachtfenster |
| `DREAMING_MAX_SCOPES_PER_BATCH` | `3` | Maximale Scopes pro Batch |
| `DREAMING_BATCH_PAUSE_SECONDS` | `300` | Pause zwischen Batches (Sekunden) |
| `DREAMING_JITTER_SECONDS` | `120` | Zufällige Verzögerung pro Batch (Sekunden) |
| `DREAMING_MIN_DAILY_MEMORIES` | `1` | Mindestanzahl Daily-Memory-Einträge für Scope-Eligibility |
| `DREAMING_LOOKBACK_DAYS` | `7` | Wie viele Tage zurück für Memory-Prüfung |
| `DREAMING_TIMEOUT_SECONDS` | `300` | Timeout für einen einzelnen Kuratierungslauf |
| `DREAMING_MAX_DAILY_CANDIDATES_PER_SCOPE` | `3` | Maximale Kandidaten pro Scope pro Tag |
| `DREAMING_MAX_PROMOTIONS_PER_SCOPE` | `2` | Maximale Promotions pro Scope pro Tag |
| `DREAMING_AUTO_APPROVE_MODE` | `0` | `1` überspringt menschliche Review — **mit extremster Vorsicht verwenden** |

### Sicherheitsverhalten

- **Default-Off:** Das System ist standardmäßig deaktiviert — explizite Aktivierung erforderlich
- **Nightly Worker:** Läuft nur innerhalb des konfigurierten Nachtfensters (z. B. 02:00–05:00)
- **Kein Max-Scopes-pro-Nacht:** Der Worker läuft batchweise bis keine eligible Scopes mehr vorhanden sind oder das Fenster endet
- **Scope-Isolation:** Memory-Kuratierung erfolgt strikt pro Topic/Private-Chat — kein Cross-Scope-Zugriff
- **Begrenzte Batches:** Maximale Anzahl Scopes pro Batch konfigurierbar; Pause und Jitter zwischen Batches
- **Eligibility-Filter:** Nur Scopes mit ausreichend Daily-Memory-Material werden verarbeitet
- **Timeout-Schutz:** Einzelne Läufe haben feste Timeouts, um Endlosschleifen zu vermeiden
- **Auto-Approve:** Standardmäßig deaktiviert; Aktivierung überspringt menschliche Review und sollte nur in vertrauenswürdigen Umgebungen erfolgen
- **No-Overlap Enforcement:** Es kann nur ein Kuratierungsdurchlauf gleichzeitig ausgeführt werden; parallele Durchläufe werden durch eine interne Sperre blockiert
- **Metadata-only Logs:** Audit-Events enthalten nur Metadaten, keine Memory-Inhalte

### Empfohlene Konfiguration

**Für lokale Tests:**
```ini
DREAMING_ENABLED=0  # Deaktiviert (Standard)
```

**Für aktiviertes Dreaming mit sicheren Defaults:**
```ini
DREAMING_ENABLED=1
DREAMING_WINDOW_START=02:00
DREAMING_WINDOW_END=05:00
DREAMING_TIMEZONE=Europe/Berlin
DREAMING_MAX_SCOPES_PER_BATCH=3
DREAMING_BATCH_PAUSE_SECONDS=300
DREAMING_JITTER_SECONDS=120
DREAMING_MIN_DAILY_MEMORIES=1
DREAMING_LOOKBACK_DAYS=7
DREAMING_TIMEOUT_SECONDS=300
DREAMING_MAX_DAILY_CANDIDATES_PER_SCOPE=3
DREAMING_MAX_PROMOTIONS_PER_SCOPE=2
DREAMING_AUTO_APPROVE_MODE=0  # Menschliche Review erforderlich
```

> **Hinweis:** Der Nightly Worker prüft periodisch, ob das Nachtfenster aktiv ist. Innerhalb des Fensters verarbeitet er Scopes in Batches (max. 3 pro Batch), pausiert zwischen Batches und fügt zufällige Jitter hinzu. Es gibt kein globales Limit pro Nacht — der Worker läuft, bis alle eligible Scopes verarbeitet sind oder das Fenster endet. Die Ergebnisse werden in Audit-Events protokolliert (keine Memory-Inhalte, nur Metadaten).

**Datenschutz-Hinweis:**
- Standardmäßig ist `LOG_INCLUDE_PRIVATE_IDS` deaktiviert zum Schutz der Privatsphäre
- Bei Aktivierung werden sensible Kennungen (User-IDs, Chat-IDs) unmaskiert protokolliert
- Log-Dateien sollten bei aktivierten IDs mit entsprechenden Dateiberechtigungen geschützt werden
- JSON-Format-Logs enthalten strukturierte Daten; bei `LOG_INCLUDE_PRIVATE_IDS=1` auch unmaskierte IDs
- **Empfohlen:** `LOG_INCLUDE_PRIVATE_IDS=0` für Produktivumgebungen; nur bei Bedarf aktivieren

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

**Linux / macOS:**

```bash
source venv/bin/activate
python main.py
```

**Windows (PowerShell):**

```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

**Windows (Eingabeaufforderung):**

```cmd
venv\Scripts\activate.bat
python main.py
```

### Nur WebUI

**Linux / macOS:**

```bash
source venv/bin/activate
python main.py --webui
```

**Windows (PowerShell):**

```powershell
.\venv\Scripts\Activate.ps1
python main.py --webui
```

**Windows (Eingabeaufforderung):**

```cmd
venv\Scripts\activate.bat
python main.py --webui
```

### Bot + WebUI zusammen (Standard)

**Linux / macOS:**

```bash
source venv/bin/activate
python main.py
```

**Windows (PowerShell):**

```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

**Windows (Eingabeaufforderung):**

```cmd
venv\Scripts\activate.bat
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

**Linux / macOS:**

```bash
source venv/bin/activate
pytest -q
python -m amo_bot.smoke
```

**Windows (PowerShell):**

```powershell
.\venv\Scripts\Activate.ps1
pytest -q
python -m amo_bot.smoke
```

**Windows (Eingabeaufforderung):**

```cmd
venv\Scripts\activate.bat
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

### Virtuelle Umgebung lässt sich nicht aktivieren (Windows)

**PowerShell-Ausführungsrichtlinie-Fehler:**
```
.\venv\Scripts\Activate.ps1 : cannot be loaded because running scripts is disabled
```

**Lösung:** PowerShell als Administrator öffnen und ausführen:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Danach Aktivierung erneut versuchen.

### Python nicht gefunden
- Python 3.12+ muss installiert und im PATH sein
- Windows: `py` oder vollen Pfad verwenden, z.B. `C:\Python312\python.exe`
- Linux/macOS: `python3` verwenden, falls `python` auf Python 2 zeigt

### Zugriff verweigert beim Erstellen des `data/`-Verzeichnisses

**Linux / macOS:**
```bash
mkdir -p data
chmod 755 data
```

**Windows:**
- Ordner manuell im Explorer erstellen
- Oder Eingabeaufforderung/PowerShell als Administrator ausführen

### Datenbank/SQLite-Fehler

**Linux / macOS:**
- Existiert das Verzeichnis `data/`?
- Schreibrechte vorhanden?
- Nur für Tests: `rm data/amo_bot.db` und Neustart

**Windows:**
- Existiert das Verzeichnis `data\`?
- Ordnerberechtigungen prüfen (Rechtsklick → Eigenschaften → Sicherheit)
- Nur für Tests: `del data\amo_bot.db` und Neustart

### Ollama nicht erreichbar
- Läuft Ollama? `curl http://127.0.0.1:11434/api/tags`
- Ist die URL in `.env` korrekt?
- Firewall blockt Port 11434?

### WebUI-Login funktioniert nicht
- Ist `WEBUI_PASSWORD` in `.env` gesetzt?
- Ist der Wert nicht leer oder "change_me"?
- Wird `http://127.0.0.1:8080` aufgerufen?

---

## WebUI: KI-Topic-Agent-Status (Read-Only)

Das WebUI-Dashboard zeigt den aktuellen KI-Topic-Agent-Konfigurationsstatus an:

- **Scope:** Zeigt an, ob die Konfiguration für ein Thema (`topic`) oder einen privaten Chat (`private`) gilt
- **Chat ID:** Telegram-Chat-Kennung (für Topic-Scopes)
- **Topic ID:** Themen-/Thread-Kennung innerhalb des Chats (für Topic-Scopes)
- **User ID:** Nutzerkennung (für Private-Scopes)
- **AI-Status:** Zeigt `active` oder `inactive` — ob AI-Auto-Antwort für diesen Scope aktiviert ist
- **Response Mode:** Aktueller Antwortmodus (z.B. `command` für nur explizite Kommandos, oder andere konfigurierte Modi)

Dies ist eine **read-only** Ansicht. Das Bearbeiten von AI-Status und Response Mode über die WebUI erfordert zukünftige Implementierung.

---

## WebUI: Topic Soul Editor (nur Owner)

Die WebUI ermöglicht dem Owner auf der Gruppendetailseite das Bearbeiten von Topic-spezifischem **Soul-Text**:

- **Ort:** Groups → Details-Link → Gruppendetailseite → Topic-Abschnitt
- **Bearbeitbare Felder:**
  - Display Name (optional)
  - Notes (optional)
  - Topic Soul Text (optional, max 4000 Zeichen)
  - Enabled-Checkbox
- **Sicherheit:**
  - Nur der konfigurierte `WEBUI_OWNER_TELEGRAM_ID` kann bearbeiten
  - Erfordert Login + CSRF-Token
  - Eingabe ist HTML-escaped und längen-begrenzt
- **Verhalten:**
  - Änderungen wirken sofort (kein Neustart erforderlich)
  - Leerer Topic Soul entfernt den benutzerdefinierten Soul-Text

**Hinweis:** Nicht-Owner können den Topic Soul ansehen, aber nicht bearbeiten. Der Speichern-Button ist deaktiviert, wenn `WEBUI_OWNER_TELEGRAM_ID` nicht konfiguriert ist.

---

## WebUI: KI Memory Controls (KI-F3 + CP-G2)

Das WebUI-Dashboard enthält einen **KI Memory**-Bereich zum Einsehen und Verwalten von KI-Memory-Einträgen mit datenschutzgehärteten Kontrollen.

**Daily Memory (Redacted):**
- Zeigt nur Memory-Daten an (z.B. "2026-05-14, 2026-05-13")
- Raw-Summary-Text wird nicht angezeigt (datenschonender Default)
- Keine Raw-Memory-Inhalte werden im MVP preisgegeben

**Long Memory:**
- Listet Langzeit-Memory-Einträge mit Fakten-Text, Status und Timestamps
- Zeigt "active" oder "inactive" Status für jeden Eintrag
- Owner kann Einträge via CSRF-geschütztem Button deaktivieren
- Löschung und Deaktivierung sind auditierbar (kein Memory-Text in Audit-Events)

**Memory-Management-Policy (CP-G2):**
- **Default-deny:** Speicher-Operationen erfordern explizite Policy-Genehmigung (CP-G1)
- **Scope-Isolation:** Speicher ist streng an Topics/private Chats gebunden; kein Cross-Scope-Zugriff
- **Begrenzte Operationen:** put/get/search/delete/deactivate sind Größen-/Zeit-begrenzt
- **TTL/Retention:** Automatisches Pruning via Maintenance-Hooks
- **Redigierte Ausgaben:** Nur Metadaten-Platzhalter werden angezeigt; Raw-Speichertext nie preisgegeben
- **Audit-Events:** Enthalten nur Scope und Entry-ID — niemals Memory-Inhalt

**Anforderungen:**
- Authentifizierte WebUI-Session zum Ansehen des Memory
- `WEBUI_OWNER_TELEGRAM_ID` konfiguriert zum Deaktivieren von Einträgen

**Sicherheit:**
- Deaktivierung erfordert CSRF-Token
- Ohne Owner-Konfiguration gibt Deaktivierung 403 Forbidden zurück
- Audit-Events: Audit-Entscheidungen für Memory-Operationen protokollieren Reason-Codes für put/get/search/delete/deactivate-Operationen (z.B. `memory_put_ok`, `memory_get_ok`, etc.). Diese Events sind metadata-only und enthalten keinen Memory-Inhalt.

---

## Nächste Schritte

- Siehe [BETATEST_DE.md](BETATEST_DE.md) für detaillierte Testanleitungen
- Siehe [RELEASE_NOTES_2026.05.09-Beta_DE.md](RELEASE_NOTES_2026.05.09-Beta_DE.md) für das Changelog

## WebUI Security — Access Window (Block 3)

Der WebUI-Zugang kann über Telegram-Commands gesteuert werden. Das ermöglicht dem Owner, den Zugang zur WebUI von überall aus zu öffnen oder zu schließen.

### Telegram-Commands

| Command | Beschreibung | Anforderungen |
|---------|--------------|---------------|
| `/webui status` | Zeigt, ob das WebUI-Zugangsfenster OPEN oder CLOSED ist, und die verbleibende Zeit bei offenem Fenster | Privater Chat, nur Owner |
| `/webui on` | Öffnet das WebUI-Zugangsfenster für 60 Minuten (verlängert bei bereits offenem Fenster) | Privater Chat, nur Owner |
| `/webui off` | Schließt das WebUI-Zugangsfenster sofort | Privater Chat, nur Owner |

**Wichtig:** Diese Commands funktionieren nur im **privaten Chat** (nicht in Gruppen) und nur für den **Owner**.

### Zugriffsverweigerungs-Gründe

Bei abgelehntem Zugriff wird ein Audit-Event mit einem dieser Gründe protokolliert:
- `not_private` — Command wurde in einer Gruppe oder einem Channel verwendet
- `not_owner` — Nutzer ist nicht der konfigurierte Owner

### Audit-Events

Folgende Audit-Events werden generiert:

| Event | Beschreibung |
|-------|--------------|
| `webui_access_enabled` | WebUI-Zugangsfenster geöffnet via `/webui on` |
| `webui_access_disabled` | WebUI-Zugangsfenster geschlossen via `/webui off` |
| `webui_access_status` | Status abgefragt via `/webui status` |
| `webui_access_denied` | Zugriff verweigert (falscher Chat-Typ oder nicht autorisierter Nutzer) |

### Status-Informationen

Bei `/webui status` erhältst du:
- **OPEN** mit verbleibenden Minuten, wenn das Zugangsfenster aktiv ist
- **CLOSED**, wenn kein Zugangsfenster geöffnet ist

Das Zugangsfenster wird persistent in der Datenbank gespeichert und übersteht Bot-Neustarts.

---

## WebUI Security — HTTP Request Gate (Block 3C)

Wenn `WEBUI_PUBLIC_MODE=true`, verwendet die WebUI ein **HTTP-Request-Gate**, das den Zugriff auf geschützte Seiten blockiert, wenn das Zugangsfenster geschlossen ist.

### Funktionsweise

| Szenario | Verhalten |
|----------|-----------|
| `WEBUI_PUBLIC_MODE=false` | Gate ist inaktiv; lokale/LAN-Nutzung unverändert |
| `WEBUI_PUBLIC_MODE=true` + Zugangsfenster **geschlossen** | `/login` und geschützte Seiten geben **403 Forbidden** |
| `WEBUI_PUBLIC_MODE=true` + Zugangsfenster **offen** | Normaler Passwort-Login funktioniert; Zugriff erlaubt |

### Whitelist-Pfade

Folgende Pfade sind immer erreichbar (Gate blockiert nicht):
- `/health` — Health-Check-Endpunkt
- `/static/*` — Statische Assets (CSS, JS, Bilder)
- `/logout` — Logout-Endpunkt

### 403-Antworten

Bei blockiertem Zugriff gibt das Gate zurück:

**HTML/Plain-Text-Anfragen:**
```
403 Forbidden
```

**JSON/API-Anfragen:**
```json
{"error":"forbidden","status":403}
```

### Konfiguration

```ini
# Public-Modus aktivieren, um das Gate zu schalten
WEBUI_PUBLIC_MODE=true

# Zugangsfenster wird via Telegram-Commands gesteuert:
# /webui on  - öffnet das Fenster für 60 Minuten
# /webui off - schließt das Fenster sofort
# /webui status - zeigt aktuellen Zustand
```

> **Hinweis:** Wenn das Zugangsfenster offen ist, ist weiterhin die normale Passwort-Authentifizierung erforderlich. Das Gate steuert nur, *ob* die Login-Seite erreichbar ist, nicht den Login selbst.

---

## WebUI: Gruppenverwaltung

Nach dem Login unter "Groups" findest du die Gruppenübersicht. Diese zeigt eine kompakte Status-Liste aller Gruppen mit Topic-Anzahl und Details-Link.

### Übersicht /groups

- Liste aller Gruppen/Supergruppen
- Topic-Anzahl pro Gruppe
- **"Details"-Link** pro Gruppe zur Bearbeitung

### Gruppendetail-Seite /groups/<chat_id>

Über den Details-Link gelangst du zur Detailseite einer Gruppe. Dort befinden sich alle Bearbeitungsfunktionen:

- **Gruppenrollen:** Nutzer und deren aktuelle Rolle anzeigen; Rollen setzen: `admin`, `vip`, `normal`, `ignore`
  - `owner` kann nicht als Gruppenrolle vergeben werden (nur via `.env`)
  - `normal` löscht den gruppen-spezifischen Eintrag → Fallback auf `normal`
  - Rollen sind gruppen-spezifisch, nicht global gültig
- **Topic-Metadata:** Display Name, Notes, Enabled-Status
- **Topic Soul:** Themen-spezifische KI-Verhaltensanweisungen (nur Owner)
- **KI-Controls:** AI-Status und Response Mode pro Topic

**Hinweis zur Nutzerliste:** Die WebUI zeigt pro Gruppe nur Nutzer an, die der Bot in dieser Gruppe gesehen hat. Bereits zugewiesene Rollen bleiben sichtbar und werden mit `[zugewiesen/nicht gesehen]` markiert, falls der Nutzer noch nicht in der Gruppe aktiv war.

---

## WebUI: Users – Private-Chat-Rollen-Schwellen

Die Seite "Users" im WebUI konfiguriert Rollenschwellen für **private Bot-Chats** (Direktnachrichten):

- Gilt nur für private Chats, nicht für Gruppen oder Topics
- `owner` bleibt die einzige globale Sonderrolle
- Gruppen-/Topic-Berechtigungen werden weiterhin auf den jeweiligen Kontextseiten verwaltet

**Konfigurierbare Schwellen:**
- **AI/KI-Minimalrolle** für private Chats (Standard: `vip`)
- **Allgemeine/built-in Befehle** – Minimalrolle (Standard: `normal`)
- **Plugin-Befehle** – Minimalrolle (Standard: `normal`)

**Erlaubte Rollen für Schwellen:** `owner` > `admin` > `vip` > `normal`

- `ignore` ist nicht als Schwellwert wählbar und bleibt verweigert
- Hierarchie: `owner` > `admin` > `vip` > `normal` > `ignore`

---

## WebUI: Image Analysis Role Quotas (IMG-B7 + IMG-B8)

Die Seite "Users" im WebUI enthält einen Abschnitt **"Image analysis role quotas"** zur Konfiguration rollenbasierter Limits für Bildanalysen. IMG-B8 implementiert die Runtime-Durchsetzung mit Rolling-24h-Fenster.

### Quota-Modi

Jede Rolle (`owner`, `admin`, `vip`, `normal`, `ignore`) kann einen der folgenden Modi haben:

| Modus | Beschreibung |
|-------|--------------|
| `disabled` | Bildanalyse für diese Rolle deaktiviert |
| `unlimited` | Keine Begrenzung (nur für `owner` erlaubt) |
| `limited` | Limit mit positivem Ganzzahlwert (Rolling-24h-Fenster) |

### Rolling-24h-Semantik (IMG-B8)

- Das Limit gilt für ein **rolling 24h-Fenster** basierend auf Audit-Timestamps
- Ein Event vor 23 Stunden 59 Minuten zählt für das aktuelle Limit
- Ein Event vor 24 Stunden 1 Minute zählt nicht mehr
- Kein harter Tages-Reset um Mitternacht UTC

### Konservative Defaults

- **Owner:** `unlimited` – voller Zugriff
- **Admin:** `disabled` – muss explizit aktiviert werden
- **VIP:** `disabled` – muss explizit aktiviert werden
- **Normal:** `disabled` – muss explizit aktiviert werden
- **Ignore:** `disabled` – immer blockiert, unabhängig von Quota-Konfiguration

### Deny-Before-Provider (IMG-B8)

- **Prüfreihenfolge:** Bildvalidität → Topic-Gate → Quota-Deny → Provider-Aufruf
- Bei Quota-Überschreitung wird ein Audit-Eintrag geschrieben, der Provider **nicht** aufgerufen
- Bildinhalte werden nicht im Audit gespeichert (nur Metadaten: user_id, chat_id, outcome, timestamp)

### Konfiguration

1. Öffne http://127.0.0.1:8080 und melde dich an
2. Navigiere zur Seite "Users"
3. Scrolle zum Abschnitt "Image analysis role quotas"
4. Für jede Rolle wähle den Modus:
   - `disabled` – Dropdown auf "Disabled" setzen
   - `unlimited` – Dropdown auf "Unlimited" setzen (nur Owner)
   - `limited` – Dropdown auf "Limited" setzen und positives Limit eingeben
5. Klicke "Save quotas"

**Hinweise:**
- Bei Modus `limited` muss ein positiver Wert (≥ 1) eingegeben werden
- `ignore` kann nicht auf `unlimited` gesetzt werden (bleibt immer `disabled`)
- Diese Einstellungen sind die **Source of Truth** für die Runtime-Durchsetzung (IMG-B8)
- Änderungen wirken sofort auf neue Anfragen (kein Neustart erforderlich)

---

## WebUI: Plugin AI Tool Toggle (Read-Only)

Die Plugins-Seite zeigt für jedes Plugin einen **AI Tool**-Toggle-Indikator:

- **Read-only-Indikator:** Zeigt an, ob das Plugin aktuell als AI Tool erlaubt ist
- **Default-off:** Deaktivierte Tools bleiben zur Laufzeit denied
- **Policy-gesteuert:** Die tatsächliche Freigabe erfolgt über KI-E-Policy-Gates, nicht über den WebUI-Toggle

Dies ist ein Transparenz-/Sicherheitsfeature, das Ownern hilft zu verstehen, welche Plugins vom KI-System aufgerufen werden können. Um AI-Tool-Berechtigungen zu ändern, konfiguriere die entsprechenden Policy-Gates.

---

## Image Analysis Coreplugin (IMG-B4..IMG-B8)

Das `image_analyse`-Coreplugin bietet eine sichere Bildanalyse-Schnittstelle für KI und User-Plugins.

### Sicherheitsmodell

**Default-off mit expliziter Topic-Freigabe:**
- Bildanalyse ist standardmäßig deaktiviert
- Muss explizit pro Topic aktiviert werden
- In aktivierten Topics analysiert der Bot Telegram-Fotos und Bild-Dokumente automatisch
- Außerhalb aktivierter Topics erfolgt keine automatische Bildanalyse

**Nutzungs-Policy:**
- `consent_required` (Standard: true) — Nutzer müssen Consent erteilt haben
- `min_role` (Standard: admin) — Mindestrolle für Bildanalyse
- Unterstützte Rollen: `owner` > `admin` > `vip` > `normal` > `ignore`

**Rollenbasierte Limits (IMG-B8):**
| Rolle | Limit | Beschreibung |
|-------|-------|--------------|
| `owner` | unbegrenzt | Keine Begrenzung |
| `admin` | unbegrenzt | Keine Begrenzung (wenn aktiviert) |
| `vip` | konfigurierbar | Limit mit Rolling-24h-Fenster |
| `normal` | konfigurierbar | Limit mit Rolling-24h-Fenster |
| `ignore` | 0 | Immer blockiert, unabhängig von Quota-Konfiguration |

**Rolling-24h-Semantik (IMG-B8):**
- Das Limit gilt für ein **rolling 24h-Fenster** basierend auf Audit-Timestamps
- Ein Event vor 23 Stunden 59 Minuten zählt für das aktuelle Limit
- Ein Event vor 24 Stunden 1 Minute zählt nicht mehr
- Kein harter Tages-Reset um Mitternacht UTC

**Topic-Gate (IMG-B2b/IMG-B8):**
- Bildanalyse kann pro Topic einzeln aktiviert werden
- Schlüssel: `(chat_id, message_thread_id)`
- Standard: deaktiviert (keine Bildanalyse ohne explizite Aktivierung)
- Wird datenbankseitig verwaltet (keine `.env`-Konfiguration)

**Eingabevalidierung:**
- `image_ref` erforderlich und nicht-leer
- `prompt` optional, maximal 512 Zeichen
- `locale` optional, maximal 16 Zeichen, nur Buchstaben und `-`/`_`

**Deterministische Reason Codes:**
- `not_enabled` — Bildanalyse ist deaktiviert
- `consent_required` — Nutzer hat keinen Consent erteilt
- `role_forbidden` — Nutzerrolle unzureichend
- `role_disabled` — Rolle ist `ignore` oder auf `disabled` gesetzt
- `quota_exceeded` — Rolling-24h-Limit erreicht (nur NORMAL/VIP)
- `topic_disabled` — Topic-Gate ist deaktiviert für diesen Kontext
- `network_not_allowed` — Netzwerk-Zugriff nicht erlaubt
- `provider_not_allowed` — Vision-Provider nicht konfiguriert/erlaubt
- `not_configured` — Bildanalyse nicht konfiguriert (Stub-Verhalten)
- `invalid_image_ref` — Ungültige Bildreferenz
- `invalid_prompt` — Prompt zu lang oder ungültig
- `invalid_locale` — Ungültiges Locale-Format

**User-Facing Deny Reasons (IMG-B3):**
Die folgenden Fehler werden explizit an Nutzer kommuniziert:
- `missing_image` — Kein Bild im Kontext gefunden (z.B. `/analyze_image` ohne Bild-Anhang)
- `invalid_type` — Anhang ist kein unterstütztes Bildformat (nur JPEG, PNG, WebP, GIF)
- `oversize` — Bild überschreitet die maximale Dateigröße (konfigurierbar, Standard: 10 MB)
- `topic_disabled` — Bildanalyse ist für dieses Topic deaktiviert
- `role_disabled` — Deine Rolle hat keine Berechtigung für Bildanalyse
- `quota_exceeded` — Limit für Bildanalysen erreicht (Rolling-24h-Fenster)
- `provider_timeout` — Bildanalyse-Provider nicht erreichbar (Timeout)
- `provider_error` — Provider-Fehler (technische Details werden aus Sicherheitsgründen nicht angezeigt)
- `provider_empty` — Provider lieferte keine Antwort (technische Details werden aus Sicherheitsgründen nicht angezeigt)

>Hinweis: Provider-Fehler (`provider_error`, `provider_empty`) werden generisch/redacted dargestellt, um keine internen Details preiszugeben. Audit-Logs enthalten Outcome-Codes für Diagnosezwecke.

**Deny-Before-Provider (IMG-B8):**
- **Prüfreihenfolge:** Bildvalidität → Topic-Gate → Quota-Deny → Provider-Aufruf
- Bei Quota-Überschreitung wird ein Audit-Eintrag geschrieben, der Provider **nicht** aufgerufen
- Blockierte Anfragen verursachen keine Provider-Kosten
- Fail-fast bei ungültigen Eingaben

**Audit-Persistenz (IMG-B8):**
- Alle Anfragen werden protokolliert mit:
  - `user_id`, `chat_id`, `message_thread_id`
  - `outcome` (z.B. `allowed`, `quota_exceeded`, `topic_disabled`)
  - Timestamp (UTC)
- Audit-Events enthalten keine Bildinhalte (nur Metadaten)
- Persistiert in `image_analyze_audit_events` Tabelle
- Quota-Deny schreibt Audit ohne Provider-Aufruf
- **Temporäre Bildverarbeitung:** Heruntergeladene Bilder werden nach Analyse automatisch bereinigt (keine dauerhafte Speicherung)

**Scope-Isolierung:**
- Bilder werden scope-spezifisch verarbeitet
- Keine Cross-Scope-Bildweitergabe
- Audit-Events enthalten nur Metadaten, keine Bildinhalte

### Telegram-Integration

**Bildanhang-Erkennung:**
- `photo` und `document` mit Bild-MIME-Types werden als Anhänge erkannt
- Telegram-Fotos und Bild-Dokumente werden in aktivierten Topics automatisch zur Analyse berücksichtigt
- `application/octet-stream` wird nur im vertrauenswürdigen Telegram-Photo-Pfad akzeptiert, wenn der Dateipfad eine erlaubte Bild-Endung hat
- Metadaten nur: `file_id`, `file_unique_id`, Dimensionen, Dateigröße
- Downloads erfolgen nur für erlaubte Bildtypen und werden in einem kurzlebigen Temp-Verzeichnis mit TTL-Cleanup gespeichert

**Trigger:**
- Automatisch: Telegram-Foto oder Bild-Dokument in einem Topic mit aktivierter Bildanalyse
- `/analyze_image` — Analysiert ein Bild im aktuellen Kontext
- Reply-to-image — Antwort auf ein Bild mit Bot-Erwähnung

**Attachment-Kontext:**
- Plugin-Commands erhalten sicheren Attachment-Kontext
- `media_ref` enthält nur: `reason_code`, `mime_type`, `bytes_stored`
- Keine Rohbilddaten oder Dateipfade im Plugin-Kontext

**Fehlerbehandlung:**
- `missing_image` — Kein Bild im Kontext gefunden
- `invalid_type` — Anhang ist kein unterstütztes Bildformat
- `oversize` — Bild überschreitet maximale Dateigröße
- `invalid_image` — Bildvalidierung fehlgeschlagen

### MediaStore-Limits

**Download-Policy:**
- MIME-Type-Whitelist: `image/jpeg`, `image/png`, `image/webp`, `image/gif`
- Maximale Dateigröße: Konfigurierbar (Standard: 10 MB)
- Timeout: Konfigurierbar (Standard: 30 Sekunden)
- Temporäre Speicherung mit TTL-Cleanup

**Sicherheitsgrenzen:**
- Keine Rohbilddaten in Logs oder Audit-Events
- Keine persistente Speicherung ohne explizite Konfiguration
- Automatische Cleanup nach Verarbeitung

### WebUI-Status (Read-Only)

Das WebUI zeigt den Bildanalyse-Status an:
- **Enabled:** `true`/`false` — Ist die Bildanalyse aktiviert?
- **Min Role:** Aktuelle Mindestrolle
- **Consent Required:** Ist Consent erforderlich?

**Hinweis:** Die Konfiguration erfolgt über Settings/Policy, nicht direkt über WebUI-Toggles.

---

## WebUI: Topic-spezifische Bildanalyse-Einstellung (IMG-B5)

Die WebUI ermöglicht die Konfiguration der Bildanalyse pro Topic über die Gruppendetailseite.

### Bildanalyse-Modus

Für jedes Topic kann ein `image_analysis_mode` konfiguriert werden:

| Modus | Verhalten |
|-------|-----------|
| `inherit` (Standard) | Erbt vom globalen Default — effektiv deaktiviert, bis der Runtime-Resolver (IMG-B6) aktiv wird |
| `enabled` | Bildanalyse explizit für dieses Topic aktiviert |
| `disabled` | Bildanalyse explizit für dieses Topic deaktiviert |

### WebUI-Konfiguration

1. **Gruppenübersicht:** `/groups` zeigt den effektiven Bildanalyse-Status pro Gruppe an.

2. **Gruppendetails:** `/groups/<chat_id>` zeigt pro Topic:
   - Aktuellen `image_analysis_mode`
   - Auswahlmöglichkeiten: inherit / enabled / disabled
   - Speichern-Button (nur mit konfiguriertem `WEBUI_OWNER_TELEGRAM_ID`)

3. **Sicheres Default:** Topics mit `inherit` oder fehlender Konfiguration bleiben effektiv deaktiviert, bis explizit aktiviert.

### Hinweis

Die Einstellung wird in der Datenbank gespeichert (`topic_agent_configs.image_analysis_mode`). Änderungen wirken sofort (kein Neustart erforderlich). Die tatsächliche Durchsetzung der Bildanalyse-Richtlinien erfolgt durch den Runtime-Resolver (IMG-B6).

---

## Bildsendung (IMG-B4)

Der Bot unterstützt das Senden von Bildern über Telegrams `send_photo`- und `send_document`-APIs mit vollständiger Policy/Role/Topic-Gate-Integration.

### Sicherheitsmodell

**Capability-gated:**
- Bildsenden erfordert die `send_message`-Capability
- Spezifische `send_image`-Capability kann für granulare Kontrolle konfiguriert werden
- Alle Policy-Prüfungen erfolgen vor dem Senden (Deny-Before-Send)

**Topic-sicher:**
- Bilder respektieren den aktuellen `message_thread_id`-Kontext
- Antworten in Topics bleiben im korrekten Thread
- Cross-Topic-Bildsendung ist blockiert

**Dateityp-Handling:**
- Bilder (JPEG, PNG, WebP, GIF) → `send_photo`
- Dokumente/Generische Dateien → `send_document`
- MIME-Type-Validierung vor dem Senden

### Reason Codes

- `role_forbidden` — Nutzerrolle unzureichend zum Senden von Bildern
- `topic_disabled` — Bildsendung für dieses Topic deaktiviert
- `consent_required` — Nutzer hat keinen Consent erteilt
- `rate_limited` — Zu viele Bildsendungen in kurzer Zeit
- `invalid_file` — Dateityp oder Größe nicht erlaubt
- `send_failed` — Telegram-API-Fehler (generische Nutzer-Nachricht)

### Plugin-Integration

Plugins können Bilder über die `send_image`-Capability senden:

```json
{
  "capability": "send_image",
  "params": {
    "file_path": "/pfad/zum/bild.jpg",
    "caption": "Optionaler Bildtext",
    "reply_to_message_id": 123
  }
}
```

**Audit:** Alle Bildsendungen werden mit Metadaten only protokolliert (file_id, mime_type, Größe).

---

## SQL-Capability-Templates (CP-H1)

Das SQL-Coreplugin bietet eine **Template-basierte, nur-Lesen** SQL-Ausführungsschnittstelle für KI und User-Plugins. Raw-SQL wird niemals direkt ausgeführt.

### Sicherheitsmodell

**Default-deny:**
- Alle SQL-Ausführungen sind blockiert, sofern nicht explizit durch Capability-Policy und Template-Allowlist erlaubt
- Unbekannte Templates werden abgelehnt

**Nur Template-Ausführung:**
- Nur vordefinierte Templates mit gebundenen Parametern können ausgeführt werden
- Keine Raw-SQL-Injection möglich
- Template-SQL wird auf reine `SELECT`-Statements validiert

**Nur-Lesen-Views:**
- Templates können nur allowgelistete Views abfragen (z.B. `v_topic_activity_summary`, `v_plugin_health_overview`)
- Verbotene Tabellen (sensible Daten wie `users`, `user_secrets`, `topic_daily_memories`, `plugin_settings`) werden blockiert

**Begrenzte Ergebnisse:**
- Zeilenlimits erzwungen (Standard 100, global max 500)
- Spaltenlimits erzwungen (max 12 Spalten, capped bei 24)
- Ergebnisse werden bei Überschreitung sicher abgeschnitten

**Spalten-Masking:**
- Sensible Spalten (`chat_id`, `user_id`, `topic_id`) werden standardmäßig maskiert
- Ausgabe zeigt `***MASKED***` statt tatsächlicher Werte

**Actor/Scope-Validierung:**
- Erfordert gültigen `actor_type` (`ki` oder `user_plugin`)
- Erfordert gültigen `scope_type` (`chat` oder `topic`)
- Erhöhte Context-Flags (`admin`, `tunnel`, `elevated`) werden explizit abgelehnt
- KI erbt keine Admin-Rechte
- UserPlugins können nicht durch KI-Privilegien tunneln

**Injection-Schutz:**
- Parameter-Validierung lehnt SQL-Injection-Versuche ab (`--`, `;`, `/*`, `*/`, `UNION`, `DROP`)
- Parameter-Länge begrenzt (120 Zeichen)
- Nur Skalarwerte (String, Int, Float, Bool) akzeptiert

### Reason Codes

Audit-Events enthalten Reason Codes für Transparenz:
- `unknown_template` — Template-ID nicht in Allowlist
- `forbidden_table` — SQL referenziert sensible Tabellen
- `invalid_sql_template` — SQL ist kein sicheres SELECT über allowgelistete Views
- `invalid_params` — Parameter außerhalb des erlaubten Sets oder malformed
- `injection_detected` — Verdächtige Muster in Parametern
- `missing_or_invalid_actor` — Actor/Scope nicht angegeben oder ungültig
- `elevated_context_denied` — Versuch, erhöhte Privilegien zu nutzen
- `db_error` — Datenbank-Ausführungsfehler (sicheres Fail)
- `ok` — Ausführung erfolgreich

### Keine autonomen Operationen

Die SQL-Capability:
- **Kann keine** Daten modifizieren (INSERT/UPDATE/DELETE blockiert)
- **Kann nicht** auf Raw-Memory-Tabellen zugreifen
- **Kann keine** Privilegien eskalieren
- **Kann kein** beliebiges SQL ausführen
- **Kann nicht** Audit-Logging umgehen

---

## Fehlerbehebung

### Bot antwortet nicht
- Terminal prüfen: Läuft `python main.py`?
- `.env` prüfen: Ist `BOT_TOKEN` korrekt?
- Telegram prüfen: Wurde "Start" im Bot-Chat geklickt?

### Virtuelle Umgebung lässt sich nicht aktivieren (Windows)

**PowerShell-Ausführungsrichtlinie-Fehler:**
```
.\venv\Scripts\Activate.ps1 : cannot be loaded because running scripts is disabled
```

**Lösung:** PowerShell als Administrator öffnen und ausführen:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Danach Aktivierung erneut versuchen.

### Python nicht gefunden
- Python 3.12+ muss installiert und im PATH sein
- Windows: `py` oder vollen Pfad verwenden, z.B. `C:\Python312\python.exe`
- Linux/macOS: `python3` verwenden, falls `python` auf Python 2 zeigt

### Zugriff verweigert beim Erstellen des `data/`-Verzeichnisses

**Linux / macOS:**
```bash
mkdir -p data
chmod 755 data
```

**Windows:**
- Ordner manuell im Explorer erstellen
- Oder Eingabeaufforderung/PowerShell als Administrator ausführen

### Datenbank/SQLite-Fehler

**Linux / macOS:**
- Existiert das Verzeichnis `data/`?
- Schreibrechte vorhanden?
- Nur für Tests: `rm data/amo_bot.db` und Neustart

**Windows:**
- Existiert das Verzeichnis `data\`?
- Ordnerberechtigungen prüfen (Rechtsklick → Eigenschaften → Sicherheit)
- Nur für Tests: `del data\amo_bot.db` und Neustart
