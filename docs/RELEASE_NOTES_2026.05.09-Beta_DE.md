# Release 2026.05.09-Beta

[English version](RELEASE_NOTES_2026.05.09-Beta_EN.md)

### Zusammenfassung

Dieses Beta-Release bringt den AMO Telegram Bot auf MVP-Status. Der Bot ist bereit für begrenztes Testing mit Fokus auf Kernfunktionalität: rollenbasierte Commands, lokale Ollama-Integration, eine leichtgewichtige WebUI und eine Plugin-Runtime-Basis. **Nicht für Produktivnutzung.**

---

### Highlights

- **Einfacher Start**: `pip install -r requirements.txt`, dann `python main.py`
- **Vereinigter Start**: Bot + WebUI laufen nun gemeinsam via `--serve`
- **Live getestet**: WebUI und Bot wurden im echten Betrieb verifiziert
- **Topic-Awareness**: Nutzer, Gruppen und Topics werden erkannt inkl. Topic-Namen; Antworten bleiben im richtigen Topic
- **Ollama-Integration**: `/ask`-Command funktioniert mit lokalem Ollama für KI-Antworten
- **Plugin-Runtime MVP**: Unterstützt Command-, Scheduled- und Worker-Runtimes plus WebUI-Betriebsoberfläche
- **Owner-Bootstrap**: Automatisches Owner-Setup und Schema-Drift-Fixes
- **Token-Redaction**: Sensitive Tokens werden aus Logs entfernt

---

### Betatest-Setup

1. **Klonen und Setup:**
   ```bash
   python3.12 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env
   ```

2. **`.env` konfigurieren:**
   - `BOT_TOKEN` – Dein Telegram-Bot-Token von @BotFather
   - `BOT_USERNAME` – Username deines Bots
   - `WEBUI_PASSWORD` – Sicheres lokales Passwort
   - `WEBUI_OWNER_TELEGRAM_ID` – Deine Telegram-User-ID

3. **Starten:**
   ```bash
   python main.py
   ```

   Startet sowohl den Bot (Polling) als auch die WebUI auf `http://127.0.0.1:8080`.

---

### Live Bestätigt Funktionsfähig

Getestet und funktional bestätigt:

| Feature | Status |
|---------|--------|
| `/ping` im privaten Chat | ✅ Funktioniert |
| `/ping` in Gruppen | ✅ Funktioniert |
| `/help` mit rollenbasierter Ausgabe | ✅ Funktioniert |
| `/role` Selbstcheck | ✅ Funktioniert |
| `/setrole` mit Berechtigungsprüfung | ✅ Funktioniert |
| `/ask` mit Ollama | ✅ Funktioniert |
| Topic-Erkennung & Antworten | ✅ Funktioniert |
| WebUI Login & Session-Management | ✅ Funktioniert |
| WebUI Plugin-Verwaltung | ✅ Funktioniert |
| Offset-Persistenz | ✅ Funktioniert |

---

### Security-Hinweise

- **Nur lokale WebUI**: Bindet standardmäßig an `127.0.0.1` – nicht ins Internet freigeben
- **Token-Redaction**: Bot-Tokens und sensitive Werte werden automatisch in Logs maskiert
- **Rollenbasierte Zugriffe**: Owner/Admin/VIP/Normal/Ignore-Rollen mit korrekten Berechtigungsprüfungen
- **Keine Secrets im Repo**: `.env` ist gitignored; Beispieldatei zeigt nur Struktur

---

### Bekannte Einschränkungen / Nicht Produktiv

- **MVP-Status**: Dies ist ein Beta-Release, nicht produktionsreif
- **Nur lokales Ollama**: Keine Cloud-AI-Integration
- **Stateless `/ask`**: Kein Gesprächsverlauf
- **Nur SQLite**: Noch kein PostgreSQL oder andere Datenbank-Unterstützung
- **Einfacher Owner-Login**: Das WebUI-MVP ist auf einen einfachen Owner-Login-Flow ausgelegt
- **Keine Kanäle**: Nur private Chats und Gruppen
- **Keine Medien**: Nur Textnachrichten
- **Manuelle Plugin-Installation**: Plugins müssen manuell in `AMO_PLUGIN_DIR` platziert werden

---

### Checkliste für Tester

- [ ] Setup abgeschlossen (venv, Dependencies, .env konfiguriert)
- [ ] Bot startet ohne Fehler
- [ ] WebUI erreichbar unter `http://127.0.0.1:8080`
- [ ] Privater Chat `/ping` antwortet
- [ ] Gruppen-Commands funktionieren
- [ ] Rollenverwaltung (`/setrole`) respektiert Berechtigungen
- [ ] `/ask` liefert KI-Antworten (falls Ollama konfiguriert)
- [ ] WebUI Plugin-Liste lädt
- [ ] Keine sensiblen Tokens in Logs

---

### Upgrade / Start-Hinweise

**Neustart:**
```bash
python main.py
```

**Mit Cleanup (löscht Datenbank):**
```bash
rm data/amo_bot.db
python main.py
```

Der Bot bootstrapped das Datenbank-Schema beim ersten Start automatisch.

---

## Main-Branch-Updates (Nach Beta-Tag)

### Gruppenspezifische Rollen

**Commit:** `5bde088 feat(auth): add group-scoped roles`

Das Rollensystem wurde um gruppenspezifische Berechtigungen erweitert:

- **Privater/DM-Chat**: Globale Rolle gilt überall
- **Gruppen**: Global `owner` und `ignore` überschreibt alles; sonst gilt die gruppenspezifische Rolle; sonst `normal`
- **`/role`** ist jetzt gruppenbewusst und zeigt die Rollenquelle (global vs. diese Gruppe)
- **`/setrole`** im DM setzt die globale Rolle; in Gruppen setzt die Rolle nur für genau diese Gruppe
- **Gruppen-Admins** dürfen nur `vip`, `normal`, `ignore` in ihrer eigenen Gruppe setzen (nicht `admin`/`owner`)
- **Gruppenübergreifende Isolation**: Ein Admin in Gruppe A ist nicht automatisch Admin in Gruppe B

### WebUI Gruppenrollenverwaltung

**Commit:** Block 3 – WebUI Group Role Management

Die WebUI wurde um eine vollständige Gruppenrollenverwaltung erweitert:

- **Groups-Seite**: Zeigt alle gruppen/supergroup, in denen der Bot aktiv ist
- **Nutzer-Anzeige**: Jeder Nutzer mit aktueller Gruppenrolle oder `normal (default)`
- **Rolle ändern**: `admin`, `vip`, `normal`, `ignore` können gesetzt werden
- **`owner` nicht setzbar**: Die `owner`-Rolle kann nicht als Gruppenrolle vergeben werden (nur via `.env`)
- **`normal` als Clear**: Setzen auf `normal` löscht den gruppen-spezifischen Eintrag → Fallback auf `normal`
- **Gruppen-spezifisch**: Rollen sind pro Gruppe unabhängig, nicht global gültig
- **Mutationsschutz**: Login erforderlich + CSRF-Token + Owner-Gate
- **Live-Testet**: Funktioniert in echten Gruppen/Supergruppen

### Gruppenrollen-Audit-Events

**Commits:** `6b3ad79` (Audit), `b6e4ef2` (Vorherige Rolle melden)

Gruppenrollen-Änderungen sind jetzt vollständig auditierbar:

- **Audit-Events**: `group_role_set` und `group_role_clear` werden für alle Gruppenrollen-Änderungen geloggt
- **Quellen erfasst**: Änderungen via `telegram_command` (Telegram) und `webui` werden unterschieden
- **Payload enthält**:
  - `chat_id` – Die Gruppe, in der die Änderung erfolgte
  - `target_telegram_user_id` – Nutzer, dessen Rolle geändert wurde
  - `previous_role` – Die Rolle vor der Änderung (jetzt korrekt bei Löschungen gemeldet)
  - `new_role` – Die Rolle nach der Änderung
  - `source` – Ursprung der Änderung (`telegram_command` oder `webui`)
- **Clear/Fallback-Audit**: Das Setzen von `normal` in einer Gruppe (was die gruppenspezifische Rolle löscht) erzeugt jetzt ein `group_role_clear`-Event mit korrekt gemeldeter vorheriger Rolle in der Antwort
