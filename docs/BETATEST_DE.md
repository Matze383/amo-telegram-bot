# AMO Telegram Bot — Betatest-Anleitung

[English version](BETATEST_EN.md)

### Ziel des Betatests

Diese Anleitung unterstützt dich beim Testen des MVP-Status des Bots:

- Commands funktionieren in privaten Chats und Gruppen
- Rollenverwaltung via Telegram und WebUI
- Plugin-Aktivierung über die WebUI
- Ollama-Integration für `/ask`

---

### Voraussetzungen

- Python 3.12 oder höher
- Linux/macOS-Entwicklungsumgebung
- Ein Telegram-Bot-Token (von @BotFather)
- Optional: Lokale Ollama-Instanz für `/ask`

---

### .env Konfiguration

Kopiere die Beispieldatei:

```bash
cp .env.example .env
```

Bearbeite `.env` mit deinen Werten:

```
# Telegram (Pflicht)
BOT_TOKEN=dein_bot_token_hier
BOT_USERNAME=dein_bot_username
TELEGRAM_API_BASE=https://api.telegram.org

# Ollama (optional für /ask)
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_MAX_RESPONSE_CHARS=1500

# Datenbank
DATABASE_URL=sqlite:///./data/amo_bot.db

# Plugins
AMO_PLUGIN_DIR=./plugins

# WebUI (Pflicht für Betatest)
WEBUI_HOST=127.0.0.1
WEBUI_PORT=8080
WEBUI_PASSWORD=dein_sicheres_passwort
WEBUI_OWNER_TELEGRAM_ID=deine_telegram_user_id
WEBUI_SESSION_TTL_SECONDS=3600

# Polling-Konfiguration
POLL_TIMEOUT_SECONDS=30
POLL_LIMIT=100
POLL_RETRY_MAX_SECONDS=30
OFFSET_STATE_FILE=.state/offset.json
```

---

### Setup-Schritte

1. **Virtuelle Umgebung erstellen:**

```bash
cd AMO-telegram-bot
python3.12 -m venv venv
source venv/bin/activate
```

2. **Abhängigkeiten installieren:**

```bash
pip install -r requirements.txt
```

3. **Projekt-Ordner vorbereiten:**

```bash
mkdir -p data
mkdir -p .state
mkdir -p plugins
```

---

### Lokaler Preflight

Vor dem ersten Telegram-Test:

```bash
source venv/bin/activate

# Unit-Tests ausführen
pytest -q

# Smoke-Test (lokale Checks ohne echte API-Calls)
python -m amo_bot.smoke
```

**Erwartete Ergebnisse:**
- pytest: Alle Tests bestanden
- smoke: Bootstrap und Basis-Commands OK

---

### Bot starten

```bash
source venv/bin/activate
python main.py
```

**Erfolgsindikatoren:**
- Bot meldet "Bot started"
- Polling beginnt ohne Fehler
- Offset wird aus `.state/offset.json` geladen oder neu erstellt

---

### WebUI starten

Bei `--serve` startet Bot + WebUI zusammen. Optional kannst du die WebUI separat starten:

```bash
source venv/bin/activate
python main.py --webui
```

**Wichtig:** WebUI läuft nur lokal (`127.0.0.1`). Nicht ins Internet freigeben.

**Erreichbarkeit:** http://127.0.0.1:8080

---

### Private Chat Tests

Starte einen privaten Chat mit deinem Bot:

**Test 1: /ping**
- Sende: `/ping`
- Erwartet: `Pong!`

**Test 2: /help**
- Sende: `/help`
- Erwartet: Liste der verfügbaren Commands (abhängig von deiner Rolle)

**Test 3: /role**
- Sende: `/role`
- Erwartet: Deine aktuelle Rolle (z.B. "owner")

---

### Gruppen-Test

1. Füge den Bot zu einer Testgruppe hinzu
2. Stelle sicher, dass der Bot Admin-Rechte hat (zum Lesen aller Nachrichten)
3. Teste Commands:
   - `/ping`
   - `/help`
   - `/role`

**Wichtig:** Commands in Gruppen beginnen oft mit dem Bot-Username: `/ping@dein_bot_username`

---

### Rollen-Test mit /setrole

**Voraussetzung:** Du bist Owner oder Admin

**Rollen-Bereichsregeln:**
- Im **privaten Chat (DM)**: Globale Rolle gilt überall
- In **Gruppen**: Global `owner` oder `ignore` überschreibt alles; sonst gilt die gruppenspezifische Rolle; sonst `normal`
- Gruppen-Admins dürfen nur `vip`, `normal`, `ignore` in ihrer eigenen Gruppe setzen, **nicht** `admin` oder `owner`
- Ein Admin in Gruppe A ist **nicht** automatisch Admin in Gruppe B

**Als Owner:**
- `/setrole <user_id> admin` – User wird Admin (global im DM, gruppenspezifisch in Gruppen)
- `/setrole <user_id> vip` – User wird VIP (global im DM, gruppenspezifisch in Gruppen)
- `/setrole <user_id> normal` – User wird Normal (global im DM, gruppenspezifisch in Gruppen)
- `/setrole <user_id> ignore` – User wird ignoriert (global, überschreibt alles)

**Als Admin:**
- `/setrole <user_id> vip` – Erlaubt
- `/setrole <user_id> normal` – Erlaubt
- `/setrole <user_id> ignore` – Erlaubt
- `/setrole <user_id> admin` – **Nicht erlaubt**
- `/setrole <user_id> owner` – **Nicht erlaubt**

**Test-Workflow:**
1. Erstelle einen zweiten Telegram-Account oder frage einen Freund
2. Ermittle die Telegram-User-ID des Test-Accounts
3. Im **privaten Chat**: Setze Rolle auf `normal` → `/role` zeigt "normal (global)"
4. In **Gruppe A**: Setze Rolle auf `vip` → `/role` zeigt "vip (diese Gruppe)"
5. In **Gruppe B**: `/role` zeigt globale Rolle (oder "normal"), außer explizit gesetzt
6. Teste, ob der Account `/ask` nutzen kann (bei `vip`: ja, bei `normal`: nein)

**Gruppenspezifische Rollen-Tests:**
- [ ] `/role` im DM zeigt globale Rolle mit Quelle
- [ ] `/role` in Gruppe A zeigt gruppenspezifische oder globale Rolle
- [ ] `/setrole` im DM setzt globale Rolle
- [ ] `/setrole` in Gruppe A setzt Rolle nur für Gruppe A
- [ ] User mit `vip` in Gruppe A hat `normal`-Berechtigungen in Gruppe B (falls keine globale Rolle)
- [ ] Gruppen-Admin kann nicht zu `admin`/`owner` befördern (nur `vip`/`normal`/`ignore`)

---

### /ask-Test mit Ollama (optional)

**Voraussetzung:** Ollama läuft lokal

```bash
# Prüfe Ollama-Status
curl http://127.0.0.1:11434/api/tags
```

**Test:**
- Sende: `/ask Was ist Python?`
- Erwartet: Eine kurze Antwort vom KI-Modell

**Einschränkungen im MVP:**
- Stateless (kein Chat-Verlauf)
- Timeout nach 20 Sekunden
- Maximale Antwortlänge: 1500 Zeichen

---

### Plugin-Test über WebUI

1. Öffne http://127.0.0.1:8080 im Browser
2. Melde dich mit deinem `WEBUI_PASSWORD` an
3. Gehe zur Plugin-Übersicht – zeigt alle Plugins
4. Aktiviere/Deaktiviere Plugins über die Betriebsoberfläche

**Hinweis:** Plugins müssen zuerst im `AMO_PLUGIN_DIR` liegen. Das Plugin-System unterstützt Command-, Scheduled- und Worker-Runtimes (MVP).

### Gruppenrollenverwaltung über WebUI

1. Öffne http://127.0.0.1:8080 im Browser und melde dich an
2. Gehe zur Seite "Groups" – zeigt alle Gruppen/Supergruppen
3. Wähle eine Gruppe aus – Nutzer mit aktueller Rolle werden angezeigt
4. Rolle ändern: `admin`, `vip`, `normal`, `ignore`

**Wichtig:**
- `owner` kann nicht als Gruppenrolle gesetzt werden (nur via `.env`)
- `normal` entfernt den gruppen-spezifischen Eintrag → Fallback auf `normal`
- Rollen sind gruppen-spezifisch, nicht global gültig
- Mutationsschutz: Login + CSRF-Token + Owner-Gate erforderlich

---

### Was NICHT getestet wird im MVP

Folgende Features sind **nicht** im MVP enthalten:

- Kanäle (nur private Chats und Gruppen)
- Medienversand (Bilder, Videos, Dokumente)
- Produktionsreife Sicherheitshärtung
- Chat-Verlauf für `/ask`
- Multi-User-WebUI (nur Owner-Login)

---

### Sicherheitsregeln

- **Token niemals posten:** Dein `BOT_TOKEN` gehört niemals in Chats, Logs oder Git
- **WebUI nur lokal:** Nicht auf `0.0.0.0` oder öffentliche IPs binden
- **Owner-ID prüfen:** `WEBUI_OWNER_TELEGRAM_ID` muss korrekt gesetzt sein
- **Starke Passwörter:** `WEBUI_PASSWORD` sollte nicht "password123" sein
- **Keine Secrets im Repo:** `.env` steht in `.gitignore`

---

### Fehlerdiagnose

**Bot antwortet nicht:**
- Prüfe Terminal: Läuft `python main.py`?
- Prüfe `.env`: Ist `BOT_TOKEN` korrekt?
- Prüfe Telegram: Hast du den Bot gestartet (im Chat auf "Start" geklickt)?

**DB/SQLite Fehler:**
- Existiert der `data/`-Ordner?
- Schreibrechte vorhanden?
- Löschen der DB-Datei (nur im Test!): `rm data/amo_bot.db`

**Ollama nicht erreichbar:**
- Läuft Ollama? `curl http://127.0.0.1:11434/api/tags`
- Korrekte URL in `.env`?
- Firewall blockt Port 11434?

**WebUI Login geht nicht:**
- Ist `WEBUI_PASSWORD` in `.env` gesetzt?
- Ist der Wert nicht auf "change_me" oder leer?
- Rufst du `http://127.0.0.1:8080` auf?

---

### Betatest-Protokoll

Nutze diese Checkliste für deinen Test:

- [ ] Setup abgeschlossen (venv, pip install)
- [ ] .env korrekt konfiguriert
- [ ] pytest: Alle Tests bestanden
- [ ] Smoke-Test: OK
- [ ] Bot startet ohne Fehler
- [ ] WebUI startet ohne Fehler
- [ ] Privater Chat /ping: OK
- [ ] Privater Chat /help: OK
- [ ] Privater Chat /role: OK
- [ ] Gruppen-Test /ping: OK
- [ ] Gruppen-Test /help: OK
- [ ] Rollen-Test /setrole normal: OK
- [ ] Rollen-Test /setrole vip: OK
- [ ] Rollen-Test Einschränkung Admin/Owner: OK
- [ ] /ask-Test (optional): OK / Nicht getestet
- [ ] WebUI Login: OK
- [ ] WebUI Plugin-Liste: OK
- [ ] WebUI Plugin aktivieren/deaktivieren: OK / Nicht getestet
- [ ] WebUI Gruppenrollenverwaltung: OK / Nicht getestet

**Notizen:**

```
Datum: ___________
Tester: __________
Ergebnis: Bestanden / Fehlgeschlagen / Teilweise
Auffälligkeiten: _________________________________
_________________________________________________
```

---

---
