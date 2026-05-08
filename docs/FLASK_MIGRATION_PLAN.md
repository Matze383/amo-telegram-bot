# Flask-Migrationplan WebUI (AMO-telegram-bot)

## 1) Zielbild: Flask-only WebUI
- Die WebUI läuft ausschließlich auf Flask.
- Keine FastAPI-App, kein FastAPI-Lifecycle, keine FastAPI-Router mehr im Projekt.
- Keine FastAPI/Uvicorn-Abhängigkeiten in Runtime oder Tests.
- Bestehende Funktionen bleiben fachlich erhalten (Health, Login/Logout, Dashboard, Users, Plugins, Mutationen mit Owner-Actor, Audit).
- Betriebsziel bleibt lokal/LAN, nicht internet-exponiert.

## 2) Bestehende FastAPI-Komponenten, die entfernt/ersetzt werden müssen
- Entfernen/ersetzen:
  - `src/amo_bot/webui/app.py` (aktuelle FastAPI-App inkl. `create_app`, `Depends`, `Header`, `HTTPException` etc.)
  - FastAPI-spezifische Request/Response-Modelle auf Pydantic-Basis für reine WebUI-Requests (sofern nur dort genutzt)
  - Startup-Hook (`@app.on_event("startup")`) und Router-Montage-Logik
- Ersetzen durch Flask-Äquivalente:
  - Auth-Prüfung via Flask-Session/Cookie statt `Authorization: Bearer`
  - Fehlerantworten via Flask `abort`/Error-Handler
  - JSON-Endpoints über `jsonify`
- Entfernen in Dependencies:
  - `fastapi`
  - `uvicorn[standard]`

## 3) Flask-Zielarchitektur
- App Factory
  - `create_app(settings, session_store, plugin_service)` bleibt als zentrale Fabrik erhalten (Flask-Variante).
  - Konfiguration ausschließlich aus `Settings`, keine globalen Seiteneffekte.
- Blueprints/Routes
  - `auth` Blueprint: `/auth/login`, `/auth/logout`
  - `ui` Blueprint: `/dashboard`, `/users/...`, `/plugins...`
  - `system` Blueprint: `/health`
- Templates
  - Serverseitige HTML-Seiten für Login + Dashboard + Listen/Forms (Users, Plugins).
  - Klare Trennung: HTML-Routen (Browser) vs. JSON-Routen (optional für Automatisierung/Tests).
- Sessions/Login/Logout
  - Login mit `WEBUI_PASSWORD`.
  - Erfolgreicher Login setzt signierte Flask-Session (httpOnly, sameSite=Lax, secure konfigurierbar).
  - Session-TTL explizit über `PERMANENT_SESSION_LIFETIME` und Sliding/Fixed-Strategie festlegen.
  - Logout invalidiert Session serverseitig.
- CSRF
  - Entscheidung Matze: **Flask-WTF**.
  - Für alle mutierenden Form-POSTs ist Flask-WTF-CSRF verpflichtend.
  - JSON-Mutationsendpunkte, falls später nötig, nur mit explizitem CSRF-Header/Token und identischer Policy.
- JSON-Endpunkte vs HTML-Seiten
  - Entscheidung Matze: **HTML-first**.
  - `/health` bleibt als JSON.
  - Interaktive Admin-Funktionen primär als HTML + Form-POST.
  - Optional: kleine JSON-Endpunkte nur für Health, Tests und spätere Automatisierung mit identischer Policy.

## 4) Funktionsparität zur aktuellen API
- `/health`
  - Muss `{"status":"ok"}` (oder gleichwertig stabil) liefern.
- Login/Logout
  - Login prüft `WEBUI_PASSWORD`; bei ungültig: 401/Fehlermeldung.
  - Wenn Passwort unsicher/nicht gesetzt (`change_me`/leer): Mutationen deaktivieren (wie bisher), Login-Policy sauber dokumentieren.
  - Logout beendet Session zuverlässig.
- Dashboard
  - Zeigt Basisstatus (Host/Port, Local-only-Hinweis, MVP-Warnung).
- Users anzeigen/Rollen setzen
  - User-Rolle anzeigen (Default `normal`, falls nicht explizit gesetzt).
  - Rollenänderung nur erlaubt bei gesetzter `WEBUI_OWNER_TELEGRAM_ID`.
  - Actor für Änderung bleibt serverseitig `WEBUI_OWNER_TELEGRAM_ID`.
- Plugins anzeigen/activate/deactivate
  - Liste aktiver/verfügbarer Plugins unverändert nutzbar.
  - Activate/Deactivate nur mit gesetzter Owner-ID und bestehender Policy-Prüfung.
  - Fehlerabbildung: Policy-Verletzung (403), unbekanntes Plugin (404), Konfigurationsfehler (503).

## 5) Sicherheitsanforderungen
- `WEBUI_PASSWORD`
  - Pflicht für sicheren Betrieb; unsicher/leer => Mutationen gesperrt.
- `WEBUI_OWNER_TELEGRAM_ID`
  - Pflicht für mutierende Admin-Aktionen (Rollen/Plugins).
- Session TTL
  - Entscheidung Matze: **Sliding Session TTL**.
  - Die Session-Laufzeit verlängert sich bei Aktivität.
  - Nach Inaktivität über `webui_session_ttl_seconds` hinaus ist erneuter Login erforderlich.
- Serverseitiger Actor
  - Mutationen laufen weiterhin mit serverseitigem Actor (`WEBUI_OWNER_TELEGRAM_ID`), nie aus Browser-Input ableiten.
- Audit
  - Bestehende Audit-Pfade in Repositories/Services müssen unverändert greifen.
  - Migration darf keine Audit-Events verlieren.
- Lokal/LAN, kein Internet
  - Default Bind auf Loopback/LAN gemäß Projektvorgabe.
  - Keine Empfehlung/Unterstützung für öffentliche Exponierung.

## 6) Dependency-Änderungen
- Entfernen:
  - `fastapi`
  - `uvicorn[standard]`
- Hinzufügen (minimal):
  - `Flask`
- Optional (empfohlen je nach Umsetzung):
  - `Flask-WTF` für CSRF/Form-Handling
  - `itsdangerous` (indirekt via Flask, ggf. explizit bei eigener Token-Strategie)
  - `Werkzeug` (kommt über Flask, nur explizit pinnen wenn nötig)
- Startkommando/Runbook an Flask-Werkzeug/Gunicorn-ähnlichen lokalen Betrieb anpassen (ohne Uvicorn).

### Status je Arbeitspunkt
- ✅ Punkt 1 erledigt: Flask-Dependencies (`Flask`, `Flask-WTF`) in `pyproject.toml` ergänzt.
- ✅ Punkt 2 erledigt: FastAPI/Uvicorn Runtime-Dependencies aus `pyproject.toml` entfernt.
- ✅ Punkt 3 erledigt: Flask App-Factory (`src/amo_bot/webui/flask_app.py`) + Blueprint-Grundstruktur (`src/amo_bot/webui/flask_blueprints/__init__.py`) angelegt, inklusive CSRF-Init, Session-Config und vorbereiteter Sliding-TTL-Hook.
- ✅ Punkt 5 erledigt: Login/Logout mit Flask-Session umgesetzt (`src/amo_bot/webui/flask_blueprints/auth.py`), minimale HTML-Templates (`src/amo_bot/webui/templates/base.html`, `src/amo_bot/webui/templates/login.html`) ergänzt, unsicheres Passwort (`change_me`/leer) blockiert und gezielte Flask-Login/Logout-Tests inkl. CSRF-Verhalten ergänzt (`tests/test_webui_flask_auth.py`).

## 7) Test-Migration
- Entfernen:
  - FastAPI `TestClient`-basierte Tests
  - FastAPI-spezifische Assertions (Dependency Overrides, response model assumptions)
- Neu mit Flask:
  - `app.test_client()`-basierte Integrationstests
  - Fixture für `create_app(test_settings, fake session/plugin service)`
- Tests, die umgeschrieben werden müssen (inhaltlich):
  - Health-Endpoint
  - Login/Logout inkl. Session-Cookie/TTL-Verhalten
  - Dashboard-Zugriff nur authentifiziert
  - Users lesen + Rolle setzen (inkl. Owner-ID fehlt => 503)
  - Plugins listen/activate/deactivate inkl. 403/404/503-Fälle
  - CSRF-Schutz auf mutierenden Routen
- Zusätzliche Regressionen:
  - Keine FastAPI-Imports mehr im gesamten Testbaum
  - Audit-Nachweise bei Mutationen vorhanden

## 8) Kleine Arbeitspakete mit QA-Gates
- Paket 1: Ist-Aufnahme und Schnittstellenvertrag fixieren
  - Ergebnis: dokumentierter Route-/Policy-Vertrag (alt -> neu).
  - QA-Gate: Checkliste mit allen Pflichtfunktionen abgehakt.
- Paket 2: Flask-Basis (App Factory, Config, Blueprints, Error-Handling)
  - Ergebnis: Flask-App bootet, `/health` läuft.
  - QA-Gate: Tests `health` grün.
- Paket 3: Auth + Session + Logout + TTL
  - Ergebnis: Login/Logout stabil, Session erzwungen.
  - QA-Gate: Auth-Tests inkl. TTL grün.
- Paket 4: Dashboard + Users (read/write) mit Owner-Guard
  - Ergebnis: Dashboard-Grundlage nach Login umgesetzt (`/` -> Login/Dashboard Redirect, `/dashboard` mit Login-Guard, minimales `dashboard.html` inkl. Logout/Health/Platzhalter).
  - QA-Gate: Dashboard-Auth-Tests grün; Users folgt im nächsten Block.
- Paket 5: Plugins (list/activate/deactivate) mit Policy-Fehlerabbildung
  - Ergebnis: Funktionsparität Plugins erreicht.
  - QA-Gate: Plugin-Tests inkl. 403/404/503 grün, Audit geprüft.
- Paket 6: CSRF-Härtung + HTML/JSON-Konsolidierung
  - Ergebnis: Mutationen CSRF-geschützt.
  - QA-Gate: Negative CSRF-Tests grün.
  - Status: ⏳ In Arbeit. Erst als erledigt markieren, wenn Default-`pytest` ohne installierte FastAPI/Uvicorn grün läuft und aktive Tests keine FastAPI-Imports mehr enthalten.
- Paket 7: Dependency-Cleanup + FastAPI-Reste entfernen
  - Ergebnis: Keine FastAPI/Uvicorn-Abhängigkeit, keine FastAPI-Dateien/Tests.
  - QA-Gate: `pytest` grün, Import-Scan ohne FastAPI-Treffer, Runbook aktualisiert.

## 9) Risiken und Rollback-Hinweise
- Risiken
  - Verdeckte Funktionsabweichungen durch Wechsel Bearer-Token -> Cookie-Session.
  - CSRF falsch/inkonsistent umgesetzt und dadurch 403-Spikes oder Lücken.
  - Audit-Verlust, falls Service-Aufrufe beim Refactor umgangen werden.
  - Testlücken bei Fehlerfällen (403/404/503) führen zu Regressionen.
- Rollback-Hinweise
  - Migration in kleinen, isolierten Commits pro Arbeitspaket.
  - Vor Entfernen von FastAPI: vollständige Flask-Testparität herstellen.
  - Fallback-Tag/Branch vor Dependency-Removal setzen.
  - Bei kritischen Regressionen: Rücksprung auf letzten grünen Paket-Stand statt Big-Bang-Revert.

## 10) Offene Fragen für Matze (max. 3)
1. Soll die Flask-WebUI weiterhin JSON-first bleiben (wie aktuell API-ähnlich) oder klar HTML-first mit nur wenigen JSON-Admin-Endpunkten?
2. Session-TTL: gewünschtes Verhalten bei Aktivität — feste Ablaufzeit (fixed) oder bei Aktivität verlängern (sliding)?
3. CSRF-Strategie: bevorzugt `Flask-WTF` (konventionell) oder schlanke Eigenlösung mit `itsdangerous`?
