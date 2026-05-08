# WEBUI-Plan – nächste Ausbaustufe (AMO-telegram-bot)

## 1) Ziel der WebUI-Ausbaustufe

Ziel ist eine lokal nutzbare, einfache und sichere Weboberfläche für den operativen Alltag, damit zentrale Admin-Aufgaben ohne API-Calls/curl möglich sind.

Kernnutzen dieses Blocks:
- Schnell einloggen
- Systemstatus sehen
- Nutzer finden und Rollen verwalten
- Plugins sichtbar schalten und aktiv/deaktivieren

Annahmen:
- Fokus bleibt auf lokal/LAN-Betrieb.
- Bestehende Bot-Logik und Python-Stack bleiben erhalten.
- OpenClaw ist explizit nicht Teil des Produkts.

---

## 2) Entscheidungsvorschlag: Flask vs. bestehende FastAPI weiterverwenden

### Option A: Flask für WebUI neu aufsetzen

Pro:
- Sehr schlank für serverseitiges HTML mit Jinja2.
- Viele Beispiele für klassische Login-/Form-Workflows.
- Geringe Einstiegshürde für „klassische“ Weboberfläche.

Contra:
- Doppelter Stack (FastAPI + Flask) erhöht Wartungsaufwand.
- Auth/Rollen/Abhängigkeiten müssten teilweise neu gebaut oder adaptiert werden.
- Zusätzliche Integrations- und Testkomplexität.

### Option B: FastAPI beibehalten und serverseitige UI ergänzen

Pro:
- Vorhandene Auth-, Rollen- und Plugin-API direkt wiederverwendbar.
- Kein zweites Framework, weniger Betriebs- und Testaufwand.
- Schnellerer Weg zu nutzbarer UI im bestehenden MVP-Kontext.

Contra:
- Teamwunsch „Flask prüfen“ wird nicht 1:1 umgesetzt.
- Für klassische Form-Flows muss konsequente Struktur (Templates/CSRF) sauber nachgezogen werden.

### Empfehlung für AMO-telegram-bot

Empfehlung: **FastAPI weiterverwenden** und die WebUI als serverseitige HTML-Schicht (Templates + Sessions + Form-Handling) auf den bestehenden Modulen aufbauen.

Begründung: Der aktuelle MVP hat bereits tragende API-Bausteine (Auth, Dashboard, Rollen, Plugins). Für diese Ausbaustufe ist Time-to-Usable wichtiger als Framework-Wechsel. Flask sollte als „geprüft“ dokumentiert werden, aber nicht als Primärpfad für diesen Block.

---

## 3) MVP-WebUI-Funktionen für den nächsten Block

### Login-Seite
- Login-Form (Benutzername/Passwort oder bestehendes Token-Flow-Äquivalent).
- Session-Cookie nach erfolgreichem Login.
- Klare Fehlermeldungen ohne Informationsleck.
- Logout-Funktion.

### Dashboard
- Kompakte Übersicht: App-Version, Laufzeitstatus, Anzahl User, aktive Plugins.
- Letzte wichtige Ereignisse (read-only, falls vorhanden).
- Schnelllinks zu User, Rollen, Plugins, Health.

### User suchen/anzeigen
- Suchfeld (Name/ID) + Ergebnisliste.
- Detailansicht mit Basisinformationen und aktueller Rolle.
- Read-only-Ansichten zuerst, Mutationen separat absichern.

### Rollen setzen
- Rollenwechsel pro User über klaren Formular-Flow.
- Sicherheitsabfrage vor Änderung (Confirm-Schritt).
- Änderungsprotokoll (wer/wann/welche Rolle alt→neu).

### Plugins anzeigen/aktivieren/deaktivieren
- Liste aller bekannten Plugins mit Status.
- Toggle-Aktion nur für berechtigte Rollen.
- Deutliche Statusrückmeldung nach Aktion.

### Status/Health
- Health-Endpunkt über UI sichtbar.
- Anzeigen: API erreichbar, Datenpfade/Config plausibel, letzte Fehlerindikatoren.
- Reiner Status, keine tiefen Diagnose-Tools in diesem Block.

---

## 4) Nicht in diesem Block

- Produktiv-Deployment (Docker/Reverse Proxy/Hardening auf Internetniveau)
- Internet-Exposure (öffentliche Erreichbarkeit)
- Komplexes Rollen-/Rechtemanagement über UI (feingranular, policy-basiert)
- Echte Plugin-Ausführung innerhalb der UI (nur Status/Enable/Disable)

---

## 5) Sicherheitsrahmen

- Betriebsmodus: **lokal/LAN**, kein offenes Internet.
- Passwort/Session:
  - gehashte Passwörter (kein Klartext),
  - sichere Session-Cookies (HttpOnly, SameSite, optional Secure bei HTTPS),
  - Session-Timeout.
- CSRF-Schutz für alle mutierenden Form-Aktionen.
- Audit-Log für mutierende Aktionen (Rollenänderung, Plugin-Statuswechsel, Owner-Setup).
- `WEBUI_OWNER_TELEGRAM_ID` als klare Owner-Verankerung (Bootstrap + Recovery-Regel dokumentieren).

---

## 6) Migrationsplan (falls Flask empfohlen/gewählt würde)

Empfohlener Betriebsmodus bei Flask: **parallel statt sofort ersetzen**.

### Ersetzen oder parallel?
- Kurzfristig: Flask-UI parallel zur bestehenden FastAPI-API.
- Mittelfristig: Entscheidung, ob FastAPI nur API bleibt oder vollständig abgelöst wird.
- Kein Big-Bang-Rewrite im laufenden MVP.

### Betroffene Dateien/Module (typisch)
- Neuer Flask-App-Einstiegspunkt (z. B. `webui/app.py`).
- Template-Ordner (`webui/templates/*`) und statische Assets (`webui/static/*`).
- Adapter-Schicht zu bestehender Auth/Rollen/Plugin-Logik (Service-Layer statt Duplikation).
- Session-/CSRF-Konfiguration.
- Routing für Login, Dashboard, User, Rollen, Plugins, Health.

### Testanpassungen
- UI-Integrationstests für Kernflüsse (Login, Rollenwechsel, Plugin-Toggle).
- Bestehende API-Tests bleiben, ergänzend UI-Tests als eigener Testblock.
- Security-Checks: CSRF, Session-Timeout, Rechteprüfung auf mutierenden Routen.
- Regression-Gate: API-Verhalten unverändert trotz neuer UI.

Hinweis: Dieser Migrationspfad wird nur aktiv, falls Matze explizit Flask priorisiert.

---

## 7) Kleine Backend-Arbeitspakete mit QA-Gates

### Paket A – Auth/Session-Basis für WebUI
Scope:
- Login/Logout-Route, Session-Handling, Owner-Guard.

QA-Gate:
- Login erfolgreich/fehlerhaft getestet,
- Session läuft ab wie definiert,
- Unauthentifizierter Zugriff auf UI-Adminseiten blockiert.

### Paket B – Dashboard + Health-Ansicht
Scope:
- Dashboard-Datenaggregation + Health-Anzeige.

QA-Gate:
- Datenquellen robust bei Teilausfall,
- UI fällt kontrolliert auf „unbekannt“ statt 500.

### Paket C – User-Suche + Detailansicht
Scope:
- Suchroute, Ergebnisliste, Detailansicht.

QA-Gate:
- Suche mit leeren/ungültigen Werten stabil,
- Berechtigungen für Einsicht korrekt.

### Paket D – Rollen setzen inkl. Audit
Scope:
- Rollenänderungs-Flow mit Confirm + Audit-Write.

QA-Gate:
- Nur berechtigte Rolle darf ändern,
- Audit-Event vollständig (actor, target, old/new, timestamp),
- CSRF-Test für POST-Route bestanden.

### Paket E – Plugin-Liste + Enable/Disable
Scope:
- Plugin-Statuslisten und Toggle-Endpunkte in UI.

QA-Gate:
- Toggle nur mit Berechtigung,
- Status nach Aktion konsistent,
- Fehlermeldungen sauber und ohne Traceback-Leak.

### Paket F – First-Run Owner Bootstrap + Token-Handling vereinfachen
Scope:
- Guided First-Run-Flow,
- klare Owner-Initialisierung über `WEBUI_OWNER_TELEGRAM_ID`,
- vereinfachte Token-/Session-Nutzung für Admin-Alltag.

QA-Gate:
- Frischer Start ohne Owner führt in sicheren Bootstrap-Flow,
- Doppelter Bootstrap verhindert,
- Token-Handling für Nutzer nachvollziehbar und dokumentiert.

### Paket G – requirements + Release-Check
Scope:
- `requirements.txt` konsolidieren,
- lokal reproduzierbares Setup für Stand `2026.05.06-Beta` + WebUI-Block.

QA-Gate:
- Clean-Setup in neuer venv erfolgreich,
- Startanleitung funktioniert Schritt-für-Schritt,
- Smoke-Test der Kernseiten grün.

---

## 8) Offene Entscheidungen für Matze (max. 3)

1. Soll für den nächsten Block verbindlich **FastAPI+Templates** als primärer Weg gelten, mit Flask nur als dokumentierte Alternativbewertung?
2. Wie soll der First-Run-Owner konkret gesetzt werden: strikt über `WEBUI_OWNER_TELEGRAM_ID` (Pflicht) oder zusätzlich ein einmaliger UI-Bootstrap-Assistent?
3. Soll die Rollenverwaltung im MVP nur feste Rollen (z. B. owner/admin/user) erlauben, oder bereits jetzt erweiterbare Rollen vorbereiten (ohne UI-Komplexität)?
