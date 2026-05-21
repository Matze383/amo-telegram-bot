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
- Optional: KI-Provider für `/ask`:
  - Lokale Ollama-Instanz, **ODER**
  - OpenAI API-Key

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

# KI-Provider Konfiguration
AI_PROVIDER=ollama  # ollama (Standard) oder openai

# Optional: OpenAI (für /ask Kommando)
# OPENAI_API_KEY=sk-your-key-hier
# OPENAI_MODEL=gpt-4o-mini
# OPENAI_TIMEOUT_SECONDS=30

# Optional: Ollama (für /ask Kommando)
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1
OLLAMA_TIMEOUT_SECONDS=20
OLLAMA_MAX_PROMPT_CHARS=4000
OLLAMA_MAX_PREDICT_TOKENS=512
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

# Sicherheitseinstellungen (Block 1)
# WEBUI_PUBLIC_MODE=false
# WEBUI_REQUIRE_HTTPS=false
# WEBUI_SESSION_COOKIE_SECURE=

# Sicherheitseinstellungen (Block 2 – Login-Schutz)
# WEBUI_LOGIN_DELAY_BASE_SECONDS=0.25
# WEBUI_LOGIN_DELAY_MAX_SECONDS=2.0

# Polling-Konfiguration
POLL_TIMEOUT_SECONDS=30
POLL_LIMIT=100
POLL_RETRY_MAX_SECONDS=30
OFFSET_STATE_FILE=.state/offset.json
```

---

### Sicherheitsfeatures (Block 1 + Block 2)

Die WebUI enthält nun gehärtete Sicherheit:

**Security Headers (immer aktiv):**
- Content-Security-Policy (CSP)
- X-Frame-Options: DENY
- X-Content-Type-Options: nosniff
- Referrer-Policy
- Permissions-Policy
- HSTS (in HTTPS-Kontexten)

**Session-Cookie-Sicherheit:**
- HttpOnly-Flag
- SameSite=Lax
- Secure-Flag (auto-aktiviert für Public/HTTPS)

**Login-Schutz (Block 2):**
- Progressive Verzögerung nach fehlgeschlagenen Login-Versuchen (exponentieller Backoff / Brute-Force-Schutz)
- Konfigurierbar via `WEBUI_LOGIN_DELAY_BASE_SECONDS` (Standard: 0,25 s) und `WEBUI_LOGIN_DELAY_MAX_SECONDS` (Standard: 2,0 s)
- Verzögerung ist gecappt beim Maximalwert
- Erfolgreicher Login setzt den Verzögerungszähler zurück
- Pro-IP-Tracking über `remote_addr` (konservatives Keying)

**Konfiguration:**
- `WEBUI_PUBLIC_MODE=false` — Standard für lokale Entwicklung
- `WEBUI_REQUIRE_HTTPS=false` — Standard für lokale Entwicklung
- `WEBUI_LOGIN_DELAY_BASE_SECONDS=0.25` — Initiale Verzögerung nach erstem Fehlversuch
- `WEBUI_LOGIN_DELAY_MAX_SECONDS=2.0` — Maximale Verzögerung

**⚠️ Produktionswarnung:** Flask sollte nicht direkt ins Internet gestellt werden. Reverse Proxy mit HTTPS verwenden.

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

**Test 3: /consent**
- Sende: `/consent`
- Erwartet: Zeigt deinen aktuellen Consent-Status und verfügbare Commands
  - **Privater Chat**: Vollständiger Status und Details werden angezeigt
  - **Gruppen**: Nur Datenschutzhinweis (keine Statusdetails in Gruppen aus Datenschutzgründen)

**Test 4: /accept**
- Sende: `/accept`
- Erwartet: Bestätigung, dass Consent akzeptiert wurde
- Hinweis: Falls du zuvor abgelehnt hast, kannst du später mit `/accept` erneut zustimmen

**Test 5: /decline**
- Sende: `/decline`
- Erwartet: Bestätigung, dass Consent abgelehnt wurde
- Hinweis: Du kannst später mit `/accept` erneut zustimmen, falls du deine Meinung änderst

**Test 6: /role**
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

**Audit-Events (optionaler Check):**
- [ ] Gruppenrollen-Änderungen via `/setrole` sind auditierbar (Logs oder Datenbank prüfen, falls zutreffend)

---

### /ask-Test mit KI-Provider (optional)

**Voraussetzung:** KI-Provider konfiguriert (Ollama oder OpenAI)

**Für Ollama:**
```bash
# Prüfe Ollama-Status
curl http://127.0.0.1:11434/api/tags
```

**Für OpenAI:**
- Stelle sicher, dass `OPENAI_API_KEY` in `.env` gesetzt ist
- Stelle sicher, dass `AI_PROVIDER=openai` in `.env` gesetzt ist

**Test:**
- Sende: `/ask Was ist Python?`
- Erwartet: Eine kurze Antwort vom KI-Modell

**Einschränkungen im MVP:**
- Stateless (kein Chat-Verlauf)
- Timeout nach 20 Sekunden (Ollama) oder 30 Sekunden (OpenAI)
- Maximale Antwortlänge: 1500 Zeichen

---

### KI-Auto-Antwort (Erwähnung/Antwort)

Der Bot kann bei Erwähnung oder als Antwort in **aktiven Scopes** (Themen oder private Chats mit aktivierter KI) automatisch per KI antworten.

**Funktionsweise:**
- **Erwähnung:** Tippe `@DeinBotName` in einem aktiven Thema oder privaten Chat
- **Antwort:** Antworte auf eine Nachricht des Bots in einem aktiven Scope
- Der Bot sendet den Nachrichtentext an die konfigurierte KI und gibt die Antwort zurück

**Voraussetzungen:**
- Nutzer muss Rolle `vip`, `admin` oder `owner` haben
- Nutzer muss Consent akzeptiert haben (`/accept`)
- Der Scope (Thema oder privater Chat) muss KI-aktiviert konfiguriert sein
- Der KI-Service muss konfiguriert sein (Ollama oder OpenAI)

**Audit-Events:**
- `ai_autoreply_sent` — Antwort erfolgreich gesendet
- `ai_autoreply_denied` — Blockiert (Rolle oder Consent)
- `ai_autoreply_error` — Fehler beim KI-Service

**Hinweis:** Dies ist separate vom `/ask`-Kommando. Auto-Antwort wird implizit durch Erwähnungen/Antworten ausgelöst; `/ask` ist ein explizites Kommando.

---

### Plugin-Test über WebUI

1. Öffne http://127.0.0.1:8080 im Browser
2. Melde dich mit deinem `WEBUI_PASSWORD` an
3. Gehe zur Plugin-Übersicht – zeigt alle Plugins
4. Aktiviere/Deaktiviere Plugins über die Betriebsoberfläche

**Hinweis:** Plugins müssen zuerst im `AMO_PLUGIN_DIR` liegen. Das Plugin-System unterstützt Command-, Scheduled- und Worker-Runtimes (MVP).

### Gruppenrollenverwaltung über WebUI

1. Öffne http://127.0.0.1:8080 im Browser und melde dich an
2. Gehe zur Seite "Groups" – zeigt alle Gruppen/Supergruppen mit Topic-Anzahl
3. Klicke bei einer Gruppe auf **"Details"** – Nutzer mit aktueller Rolle werden angezeigt
4. Rolle ändern: `admin`, `vip`, `normal`, `ignore`

**Wichtig:**
- `owner` kann nicht als Gruppenrolle gesetzt werden (nur via `.env`)
- `normal` entfernt den gruppen-spezifischen Eintrag → Fallback auf `normal`
- Rollen sind gruppen-spezifisch, nicht global gültig
- Mutationsschutz: Login + CSRF-Token + Owner-Gate erforderlich

---

### WebUI-Zugangskontrolle via Telegram (Block 3)

Die `/webui`-Commands ermöglichen dem Owner, den WebUI-Zugang über Telegram zu steuern.

**Test-Schritte:**

1. **`/webui status` testen (geschlossener Zustand):**
   - Sende: `/webui status`
   - Erwartet: "CLOSED" oder ähnliche Nachricht, die anzeigt, dass das Zugangsfenster nicht geöffnet ist

2. **`/webui on` testen:**
   - Sende: `/webui on`
   - Erwartet: Bestätigung, dass das WebUI-Zugangsfenster nun für 60 Minuten OPEN ist

3. **`/webui status` testen (geöffneter Zustand):**
   - Sende: `/webui status`
   - Erwartet: "OPEN" mit Anzeige der verbleibenden Minuten

4. **`/webui off` testen:**
   - Sende: `/webui off`
   - Erwartet: Bestätigung, dass das WebUI-Zugangsfenster nun CLOSED ist

**Negative Tests:**

5. **Test in einer Gruppe (sollte verweigert werden):**
   - Füge den Bot zu einer Testgruppe hinzu
   - Sende: `/webui status` in der Gruppe
   - Erwartet: Zugriff verweigert, möglicherweise mit einer Nachricht, dass dies nur im privaten Chat funktioniert

6. **Test als Nicht-Owner (sollte verweigert werden):**
   - Lass einen Nicht-Owner `/webui status` senden
   - Erwartet: Zugriff verweigert, möglicherweise mit einem Autorisierungsfehler

**Wichtige Hinweise:**
- Diese Commands funktionieren nur im **privaten Chat** mit dem Owner
- Der Zustand des Zugangsfensters wird in der Datenbank persistiert (übersteht Bot-Neustarts)

**Checkliste:**
- [ ] `/webui status` zeigt initial CLOSED
- [ ] `/webui on` öffnet das Zugangsfenster
- [ ] `/webui status` zeigt OPEN mit verbleibender Zeit
- [ ] `/webui off` schließt das Zugangsfenster
- [ ] `/webui`-Commands in Gruppen werden verweigert
- [ ] `/webui`-Commands für Nicht-Owner werden verweigert

---

### WebUI HTTP Request Gate (Block 3C)

Wenn `WEBUI_PUBLIC_MODE=true`, blockiert das HTTP-Request-Gate den Zugriff auf geschützte WebUI-Seiten, wenn das Zugangsfenster geschlossen ist.

**Voraussetzungen:**
- Setze `WEBUI_PUBLIC_MODE=true` in `.env`
- Starte Bot/WebUI neu

**Test-Schritte:**

1. **Mit geschlossenem Zugangsfenster:**
   - Stelle sicher, dass das Fenster geschlossen ist: `/webui off` im Owner-DM
   - Versuche Zugriff auf: `http://127.0.0.1:8080/login`
   - Erwartet: **403 Forbidden**-Seite

2. **Geschützte Routen ebenfalls blockiert:**
   - Versuche Zugriff auf: `http://127.0.0.1:8080/groups`
   - Erwartet: **403 Forbidden**

3. **Zugangsfenster öffnen:**
   - Sende: `/webui on` im Owner-DM
   - Versuche Zugriff auf: `http://127.0.0.1:8080/login`
   - Erwartet: Login-Seite lädt normal
   - Einloggen mit Passwort: Sollte wie gewohnt funktionieren

4. **Zugangsfenster läuft ab oder wird geschlossen:**
   - Warte auf Ablauf (oder sende `/webui off`)
   - Versuche erneut Zugriff auf geschützte Seiten
   - Erwartet: **403 Forbidden** wird zurückgegeben

5. **Whitelist-Pfade bleiben erreichbar:**
   - Bei geschlossenem Fenster teste:
     - `http://127.0.0.1:8080/health` — Erwartet: Health-Check-Antwort (nicht 403)
     - `http://127.0.0.1:8080/static/css/style.css` — Erwartet: Statische Datei wird ausgeliefert
     - `/logout` — Erwartet: Logout funktioniert (falls vor Fensterschluss eingeloggt)

6. **JSON/API-Antworten:**
   - Sende Anfrage mit `Accept: application/json`-Header an blockierten Pfad
   - Erwartet: `{"error":"forbidden","status":403}`

**Public Mode Off (Standard):**

7. **Mit `WEBUI_PUBLIC_MODE=false`:**
   - Schließe Zugangsfenster: `/webui off`
   - Greife auf `http://127.0.0.1:8080/login` zu
   - Erwartet: Login-Seite lädt normal (Gate ist im Nicht-Public-Modus inaktiv)

**Checkliste:**
- [ ] Public mode + geschlossenes Fenster → `/login` gibt 403 zurück
- [ ] Public mode + geschlossenes Fenster → `/groups` gibt 403 zurück
- [ ] `/webui on` im Owner-DM → `/login` wird erreichbar
- [ ] Normaler Passwort-Login funktioniert, wenn Fenster offen
- [ ] `/webui off` oder Ablauf → 403 wird wieder zurückgegeben
- [ ] `/health` bleibt erreichbar, wenn Fenster geschlossen
- [ ] Statische Assets bleiben erreichbar, wenn Fenster geschlossen
- [ ] JSON-Anfragen erhalten `{"error":"forbidden","status":403}`
- [ ] Nicht-Public-Modus (`WEBUI_PUBLIC_MODE=false`) → Gate inaktiv, lokale Nutzung unverändert

**Wichtige Hinweise:**
- Das Gate ist **nur aktiv, wenn `WEBUI_PUBLIC_MODE=true`**
- Wenn das Fenster offen ist, ist weiterhin die normale Passwort-Authentifizierung erforderlich
- Das Gate steuert, ob die Login-Seite *erreichbar* ist, nicht den Login selbst
- Für Produktions-/Internet-Deployment ist weiterhin ein Reverse Proxy (nginx, Caddy, Traefik) mit HTTPS erforderlich — Flask sollte nicht direkt ins Internet freigegeben werden

**⚠️ Deployment-Hinweis:** Dieses Feature ermöglicht Zugangskontrolle für Public-Deployments, ersetzt aber nicht das korrekte Reverse-Proxy- und HTTPS-Setup. Nginx/Internet-Deployment-Konfiguration ist außerhalb des Scope dieses Betas.

---

### Consent Commands (Block 1)

Der Bot enthält nun ein Consent-Management über Telegram-Commands.

**Test-Schritte:**

1. **`/consent` im privaten Chat testen:**
   - Sende: `/consent`
   - Erwartet: Zeigt aktuellen Consent-Status, Details und verfügbare Commands

2. **`/accept` testen:**
   - Sende: `/accept`
   - Erwartet: Bestätigung, dass Consent akzeptiert wurde

3. **`/decline` testen:**
   - Sende: `/decline`
   - Erwartet: Bestätigung, dass Consent abgelehnt wurde

4. **`/consent` nach Ablehnung testen:**
   - Sende: `/consent`
   - Erwartet: Zeigt abgelehnten Status und erinnert daran, dass du jederzeit mit `/accept` erneut zustimmen kannst

5. **Erneutes Annehmen testen:**
   - Sende: `/accept` (nach vorheriger Ablehnung)
   - Erwartet: Consent erneut erfolgreich akzeptiert

6. **`/consent` in Gruppen testen:**
   - Sende: `/consent` in einer Gruppe, in der der Bot vorhanden ist
   - Erwartet: Nur Datenschutzhinweis — keine Consent-Statusdetails in Gruppen aus Datenschutzgründen

**Checkliste:**
- [ ] `/consent` im privaten Chat zeigt vollständigen Status
- [ ] `/accept` bestätigt Consent akzeptiert
- [ ] `/decline` bestätigt Consent abgelehnt
- [ ] `/consent` zeigt abgelehnten Status korrekt
- [ ] `/accept` funktioniert nach vorheriger Ablehnung
- [ ] `/consent` in Gruppen zeigt nur Datenschutzhinweis (keine Details)

---

### Automatischer privater Consent-DM-Prompt (Block 2)

Der Bot sendet automatisch einen privaten Consent-Hinweis an Nutzer mit dem Status "pending" (noch nicht akzeptiert oder abgelehnt).

**Funktionsweise:**
- Wenn ein pending User in einer Gruppe gesehen wird, sendet der Bot automatisch eine private DM mit Consent-Hinweis
- Die DM enthält **Inline-Buttons** (✅ Akzeptieren / ❌ Ablehnen) für schnelle Zustimmung, plus Fallback-Commands: `/accept`, `/decline`, `/consent`
- **One-Shot-Policy:** Genau 1 automatische DM pro User — wird nur gesendet wenn `consent_prompt_count == 0`. Nach erfolgreicher Zustellung wird `prompt_count` auf 1 gesetzt, keine weiteren automatischen DMs.
- **Unerreichbare User:** Wenn der Bot kein privates Gespräch starten kann (User hat den Bot nicht gestartet), wird der User als `unreachable` markiert und erhält keine Prompts. Der User muss den Bot privat starten und `/accept` (oder den Akzeptieren-Button) nutzen, um zu consenten.

**Test-Schritte:**

1. **Automatischen Prompt testen:**
   - Einen neuen User zu einer Gruppe hinzufügen, in der der Bot ist
   - User sollte genau eine private DM vom Bot mit dem Consent-Hinweis erhalten

2. **One-Shot-Policy testen:**
   - Nach Erhalt des ersten (und einzigen) automatischen Prompts erhält der User keine weiteren automatischen DMs
   - Der `consent_prompt_count` wird nach erfolgreicher Zustellung auf 1 gesetzt

3. **Unerreichbar-Handling testen:**
   - Wenn der User noch keinen privaten Chat mit dem Bot gestartet hat, kann die DM nicht zugestellt werden
   - User wird im System als `unreachable` markiert
   - Um erreichbar zu werden und zu consenten, muss der User den Bot zuerst privat starten und `/accept` nutzen

**Checkliste:**
- [ ] Pending-User erhalten genau einen automatischen DM-Prompt beim ersten Erscheinen in Gruppen
- [ ] DM enthält **Inline-Buttons** (Akzeptieren/Ablehnen) und `/accept`, `/decline`, `/consent` Fallback-Commands
- [ ] Inline-Buttons funktionieren: Akzeptieren-Button setzt Consent auf akzeptiert, Ablehnen-Button setzt Consent auf abgelehnt
- [ ] Fallback-Commands bleiben nutzbar neben Buttons
- [ ] One-Shot-Policy eingehalten: nur 1 automatischer Prompt pro User (bei `consent_prompt_count == 0`)
- [ ] Keine automatischen Retries nach erfolgreicher Zustellung oder Fehler
- [ ] Unerreichbare User werden entsprechend markiert und müssen den Bot privat starten, um zu consenten
- [ ] Runtime-Gate blockiert normale Nutzung für `pending`/`declined`/`unreachable` User
- [ ] Erlaubte Commands funktionieren trotz Gate: `/accept`, `/decline`, `/consent`, `/start`
- [ ] `accepted` User können alle Commands normal nutzen
- [ ] Owner-Bypass funktioniert für Consent (Owner kann den Bot immer nutzen)
- [ ] Globale `ignore`-Rolle bleibt blockierend unabhängig vom Consent

**Runtime Consent Gate:** Das Runtime-Gate ist **jetzt aktiv**. Nutzer mit Status `pending`, `declined` oder `unreachable` können normale Bot-Funktionen nicht nutzen, bis sie `/accept` senden.

**Erlaubte Commands trotz Gate:** `/accept`, `/decline`, `/consent`, `/start` — diese funktionieren immer.

**Verhalten in Gruppen:** In Gruppen wird nur ein datenschonender Hinweis gezeigt. Keine Statusdetails werden preisgegeben.

**Private Block-Nachricht:** Blockierte User im privaten Chat werden aufgefordert, `/accept` oder `/consent` zu nutzen. Für `unreachable` User: Bot zuerst privat starten, dann `/accept`.

---

### WebUI: Topic Soul Editor (KI-F2)

Die Gruppendetailseite enthält einen **Topic Soul Editor** zur Konfiguration von Themen-spezifischen KI-Verhaltensanweisungen.

**Voraussetzungen:**
- `WEBUI_OWNER_TELEGRAM_ID` muss in `.env` gesetzt sein
- Mindestens eine Gruppe mit Topics (Supergruppe mit Themen/Threads)

**Test-Schritte:**

1. **Zur Gruppendetailseite navigieren:**
   - http://127.0.0.1:8080 öffnen und einloggen
   - Zur Seite "Groups" gehen
   - Auf **"Details"** bei einer Gruppe mit Topics klicken
   - Erwartet: Gruppendetailseite mit Topic-Abschnitt wird angezeigt

2. **Topic Soul ansehen:**
   - Topic-Abschnitt auf der Detailseite finden
   - "Topic Soul"-Feld ansehen
   - Erwartet: Zeigt aktuellen Soul-Text oder "-" falls nicht gesetzt
   - Hinweis: Inhalt ist HTML-escaped (sichere Darstellung)

3. **Als Owner bearbeiten:**
   - Text in "Topic Soul"-Textarea eingeben (max 4000 Zeichen)
   - Optional Display Name und Notes eingeben
   - "enabled"-Checkbox bei Bedarf toggeln
   - Auf "Speichern" klicken
   - Erwartet: Seite lädt neu, Änderungen persistiert

4. **Persistenz prüfen:**
   - Detailseite neu laden
   - Erwartet: Bearbeitete Werte werden angezeigt

5. **HTML-Escaping prüfen:**
   - Eingabe versuchen: `<script>alert(1)</script>`
   - Speichern und neu laden
   - Erwartet: Text ist escaped, kein Alert-Dialog

**Negative Tests:**

6. **Nicht-Owner kann nicht bearbeiten (falls zutreffend):**
   - Wenn `WEBUI_OWNER_TELEGRAM_ID` nicht gesetzt oder anderer User
   - Erwartet: Speichern-Button ist deaktiviert

7. **Längenvalidierung:**
   - Versuch mit >4000 Zeichen
   - Erwartet: Formularvalidierung lehnt ab oder kürzt

**Checkliste:**
- [ ] Groups-Seite zeigt Gruppen mit Details-Link
- [ ] Gruppendetailseite zeigt Topics mit Topic Soul-Formular
- [ ] Topic Soul-Textarea akzeptiert Eingabe (max 4000 Zeichen)
- [ ] Display Name und Notes können bearbeitet werden
- [ ] Enabled-Checkbox funktioniert
- [ ] Änderungen bleiben nach Reload erhalten
- [ ] HTML-Inhalt wird korrekt escaped
- [ ] Speichern-Button deaktiviert, wenn Owner nicht konfiguriert
- [ ] Formular erfordert CSRF-Token

---

### WebUI: KI Memory Controls (KI-F3)

Das Dashboard enthält einen **KI Memory**-Bereich zum Einsehen und Verwalten von KI-Memory-Einträgen.

**Voraussetzungen:**
- `WEBUI_OWNER_TELEGRAM_ID` muss in `.env` gesetzt sein für Deaktivierungs-Aktionen
- Authentifizierte WebUI-Session

**Test-Schritte:**

1. **Memory-Bereich ansehen:**
   - http://127.0.0.1:8080 öffnen und einloggen
   - Zum Dashboard navigieren
   - Erwartet: Abschnitt "KI Memory (Read-Only + Deactivate Long Memory)" ist sichtbar

2. **Daily Memory (Redacted):**
   - Die "Daily memory"-Einträge für einen Scope ansehen
   - Erwartet: Nur Daten werden angezeigt (z.B. "2026-05-14, 2026-05-13")
   - Erwartet: Kein Raw-Summary-Text wird angezeigt (Datenschutz/konservativer Default)

3. **Long Memory Liste:**
   - Tabelle "Long Memories" für Scopes mit Memory-Einträgen prüfen
   - Erwartet: Spalten zeigen ID, Summary (fact_text), Status, Created, Updated, Action
   - Erwartet: Status zeigt "active" oder "inactive"

4. **Long Memory als Owner deaktivieren:**
   - Sicherstellen, dass `WEBUI_OWNER_TELEGRAM_ID` in `.env` konfiguriert ist
   - Einen aktiven Long-Memory-Eintrag finden
   - "Deactivate"-Button klicken (CSRF-geschütztes Formular)
   - Erwartet: Seite lädt neu, Eintrag zeigt jetzt "inactive"-Status

5. **Deaktivierung-Persistenz prüfen:**
   - Dashboard neu laden
   - Erwartet: Deaktivierter Eintrag bleibt "inactive"

**Negative Tests:**

6. **Deaktivieren ohne Owner-Config:**
   - `WEBUI_OWNER_TELEGRAM_ID` temporär aus `.env` entfernen (oder leer setzen)
   - WebUI neu starten
   - Versuch, einen Long-Memory-Eintrag zu deaktivieren
   - Erwartet: **403 Forbidden** — Mutation ist deaktiviert

7. **CSRF-Schutz:**
   - POST an `/memory/long/<id>/deactivate` ohne CSRF-Token senden
   - Erwartet: **400 Bad Request** oder Redirect mit Fehler

**Checkliste:**
- [ ] Dashboard zeigt KI Memory-Abschnitt
- [ ] Daily Memory zeigt nur Daten (kein Raw-Text)
- [ ] Long Memory zeigt fact_text, Status, Timestamps
- [ ] Deactivate-Button sichtbar für aktive Einträge (mit Owner-Config)
- [ ] Deaktivierung funktioniert via CSRF-geschütztem POST
- [ ] Deaktivierte Einträge zeigen "inactive"-Status
- [ ] Ohne Owner-Config gibt Deaktivierung 403 zurück
- [ ] CSRF-Token für Deaktivierung erforderlich

---

### Image Analysis Coreplugin (IMG-B4..IMG-B7)

Das Bildanalyse-Coreplugin bietet eine sichere, default-off Bildanalyse für KI und Plugins.

**Status:** Stub-Implementierung (kein echter Vision-Provider)
- Bildanalyse ist standardmäßig deaktiviert
- Alle Anfragen werden mit `image analysis not configured` abgelehnt
- Policy- und Consent-Prüfungen werden trotzdem durchgeführt

**Voraussetzungen:**
- `vip`, `admin` oder `owner` Rolle
- Consent erteilt (`/accept`)

**Telegram-Test:**

1. **Ohne Bild (sollte fehlschlagen):**
   - Sende: `/analyze_image` ohne Bild
   - Erwartet: Fehlermeldung oder Hinweis, dass kein Bild gefunden wurde

2. **Mit Bild als Anhang:**
   - Lade ein Bild hoch mit `/analyze_image` als Caption
   - Erwartet: "image analysis not configured" (Stub-Verhalten)

3. **Als Antwort auf Bild:**
   - Antworte auf ein Bild im Chat mit `/analyze_image`
   - Erwartet: "image analysis not configured" (Stub-Verhalten)

**Hinweis:** Das Feature ist ein Security-Stub. Die Policy-Prüfung (Rolle, Consent) funktioniert, aber die eigentliche Bildanalyse ist nicht konfiguriert.

**Sicherheits-Checkliste:**
- [ ] Bildanalyse ist default-off (keine automatische Aktivierung)
- [ ] Mindestrolle wird geprüft
- [ ] Consent wird geprüft
- [ ] Keine Rohbilddaten in Logs/Audit-Events
- [ ] Attachment-Kontext enthält nur Metadaten

---

### Zukünftige Features (Noch nicht implementiert)

Folgende Features sind für zukünftige Releases geplant und im aktuellen Beta **nicht verfügbar**:

- Zusätzliche Sicherheitsverbesserungen — zukünftige Blöcke

---

### Hinweise zum Block 2 Security-Test

**Login-Verhalten:**
- Falsche Zugangsdaten liefern eine **generische Fehlermeldung** — es werden keine detaillierten Informationen preisgegeben
- Wiederholte Fehlversuche werden **progressiv verzögert** (exponentieller Backoff, gecappt beim Maximum)
- Erfolgreicher Login nach Fehlversuchen funktioniert normal — der Verzögerungszähler wird sofort zurückgesetzt

**Audit-Events (intern/optional):**
- Login-Versuche erzeugen Audit-Events: `webui_login_failure` und `webui_login_success`
- Events enthalten nur die IP-Adresse (`remote_addr`)
- Es werden keine Passwörter oder sensible Daten protokolliert
- Diese Events dienen der internen Protokollierung/Überwachung und beeinflussen nicht das Nutzerverhalten

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
- [ ] Privater Chat /consent: OK
- [ ] Privater Chat /accept: OK
- [ ] Privater Chat /decline: OK
- [ ] Privater Chat /role: OK
- [ ] Gruppen-Test /ping: OK
- [ ] Gruppen-Test /help: OK
- [ ] Rollen-Test /setrole normal: OK
- [ ] Rollen-Test /setrole vip: OK
- [ ] Rollen-Test Einschränkung Admin/Owner: OK
- [ ] /ask-Test (optional): OK / Nicht getestet
- [ ] KI-Auto-Antwort via Erwähnung in aktivem Scope (optional): OK / Nicht getestet
- [ ] KI-Auto-Antwort via Antwort in aktivem Scope (optional): OK / Nicht getestet
- [ ] WebUI Login: OK
- [ ] WebUI Plugin-Liste: OK
- [ ] WebUI Plugin aktivieren/deaktivieren: OK / Nicht getestet
- [ ] WebUI Gruppenrollenverwaltung: OK / Nicht getestet
- [ ] WebUI KI-Topic-Agent-Status auf Dashboard sichtbar: OK / Nicht getestet
- [ ] WebUI Topic Soul Editor (nur Owner, in Groups): OK / Nicht getestet
- [ ] WebUI KI Memory Controls (redacted Daily, Long-Memory-Deaktivierung): OK / Nicht getestet
- [ ] CP-G2 Memory Privacy: Scope-Isolation, Default-Deny-Policy, redigierte Ausgaben verifiziert: OK / Nicht getestet
- [ ] Image Analysis Coreplugin (default-off, Stub-Verhalten): OK / Nicht getestet
- [ ] Security Headers vorhanden (Browser-Dev-Tools prüfen): OK

**Notizen:**

```
Datum: ___________
Tester: __________
Ergebnis: Bestanden / Fehlgeschlagen / Teilweise
Auffälligkeiten: _________________________________
_________________________________________________
```
