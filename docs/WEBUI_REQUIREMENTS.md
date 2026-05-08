# AMO-telegram-bot – WebUI-Anforderungen

## Ziel

Die WebUI soll eine benutzbare Oberfläche für den lokalen/LAN-Betrieb des AMO-telegram-bot werden. Sie soll zentrale Admin-Aufgaben ohne curl/API-Kommandos ermöglichen.

Stand: Anforderungen für die nächste WebUI-Ausbaustufe.

---

## Hauptnavigation / Bereiche

### 1. Dashboard

Soll einen schnellen Überblick geben:

- Bot-Status
- letzte Aktivität
- Anzahl bekannter User
- aktive Plugins
- Ollama-/KI-Status

### 2. User & Rollen

Verwaltung der bekannten Telegram-User:

- User suchen
- User anzeigen
- Rolle anzeigen
- Rolle ändern
- User ignorieren
- User reaktivieren
- Audit/Änderungsverlauf für User/Rollen sehen

Rollen bleiben zunächst die bestehenden festen Rollen:

- owner
- admin
- vip
- normal
- ignore

### 3. Plugins

Plugin-Verwaltung für den MVP-Stand:

- Plugins anzeigen
- Plugin-Status anzeigen
- Plugin aktivieren
- Plugin deaktivieren
- Plugin-Details anzeigen
- später: Plugin-Konfiguration

Wichtig: In diesem Block weiterhin keine echte Plugin-Code-Ausführung, nur Manifest/Status/Enable/Disable.

### 4. Chats / Gruppen

Übersicht über bekannte Chats und Gruppen:

- private Chats
- Gruppen
- Topics/Forum-Threads
- Bot-Status pro Chat
- später ggf. chat-/topic-spezifische Einstellungen

Wichtig: Telegram-Gruppen können Topics haben. Die UI soll das perspektivisch berücksichtigen.

### 5. KI / Ollama

KI-/Ollama-Verwaltung:

- aktuelles Modell anzeigen
- Ollama-URL anzeigen
- Testprompt senden
- Timeout anzeigen
- Antwortlimit anzeigen
- später: Modell wechseln

Keine Secrets anzeigen.

### 6. Logs / Ereignisse

Einblick in System- und Admin-Ereignisse:

- Audit-Events
- Fehler
- letzte Telegram-Updates
- Rollenänderungen
- Plugin-Aktionen

### 7. Einstellungen

Konfiguration sichtbar machen, ohne Secrets offenzulegen:

- `.env`-Status ohne geheime Werte
- Bot-Username
- WebUI-Konfiguration
- Plugin-Pfad
- Datenbankpfad
- Ollama-Modell/URL

Secrets wie Bot-Token und WebUI-Passwort dürfen nicht im Klartext angezeigt werden.

### 8. Setup / Wartung

Hilfen für Betrieb und Erstsetup:

- First-Run Owner Bootstrap
- Smoke-Test starten oder Anleitung anzeigen
- DB-Status anzeigen
- Backup-Hinweise
- später: einfache Wartungsaktionen

---

## Erster sinnvoller UI-Block

Für den ersten echten UI-Ausbau sollen maximal diese Bereiche umgesetzt werden:

1. Dashboard
2. User & Rollen
3. Plugins
4. Einstellungen / Status

Die übrigen Bereiche bleiben geplant und werden später ausgebaut.

---

## Sicherheitsrahmen

- WebUI bleibt lokal/LAN, nicht öffentlich im Internet.
- Login erforderlich.
- Mutierende Aktionen brauchen serverseitigen Actor (`WEBUI_OWNER_TELEGRAM_ID`).
- Rollenänderungen und Plugin-Aktionen müssen auditierbar bleiben.
- Keine Secrets anzeigen.
- Bei Formularen CSRF-Schutz berücksichtigen.

---

## Technische Entscheidung

Matze möchte die WebUI auf **Flask** umstellen.

Damit ist Flask der verbindliche Zielpfad für die nächste WebUI-Ausbaustufe.

**FastAPI soll vollständig aus dem Projekt entfernt werden.** Es soll künftig nur noch Flask für die WebUI verwendet werden.

Wichtig: Die Umstellung soll trotzdem kontrolliert erfolgen: erst Migrationsplan, dann Backend-Umsetzung, dann QA. Ziel ist am Ende aber klar: keine FastAPI-Abhängigkeit, keine FastAPI-App, keine FastAPI-Tests mehr.
