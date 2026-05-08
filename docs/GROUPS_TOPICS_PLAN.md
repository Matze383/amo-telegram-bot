# GROUPS_TOPICS_PLAN

## Ist-Analyse (realer Stand)

### 1) Flask-Struktur
Vorhanden:
- `src/amo_bot/webui/flask_blueprints/auth.py`
- `src/amo_bot/webui/flask_blueprints/health.py`
- `src/amo_bot/webui/flask_blueprints/ui.py`
- `src/amo_bot/webui/templates/base.html`
- `src/amo_bot/webui/templates/dashboard.html`
- `src/amo_bot/webui/templates/login.html`
- `src/amo_bot/webui/templates/users.html`

Aktuell gibt es **keine** Route und **kein** Template für `/groups`.

`ui.py` hat bereits:
- `login_required`-Decorator
- GET `/dashboard`
- GET `/users`
- POST `/users/<telegram_user_id>/role`
- CSRF via `FlaskForm`
- Owner-Gate für Mutationen über `settings.webui_owner_telegram_id`

### 2) DB-Stand
Dateien:
- `src/amo_bot/db/base.py` (SQLAlchemy Base + session factory)
- `src/amo_bot/db/models.py`
- `src/amo_bot/db/repositories.py`
- `src/amo_bot/db/init_db.py`

Vorhandene Modelle in `models.py`:
- `DbRole`, `User`, `UpdateOffset`, `Plugin`, `AuditEvent`

Es existieren aktuell **keine** Tabellen für Telegram-Chats/Topics.

`repositories.py` enthält:
- `UserRoleRepository`
- `PluginRepository`

Es gibt aktuell **kein** Repository für Chats/Topics.

`init_db.py` initialisiert aktuell Rollen + `UpdateOffset` (source=telegram).

### 3) Telegram-Update-Flow
Dateien:
- `src/amo_bot/telegram/polling.py`
- `src/amo_bot/telegram/dispatcher.py`
- zusätzlich relevant: `src/amo_bot/telegram/update_parser.py`

Ist-Zustand:
- `polling.run_polling()` holt Updates und ruft `dispatcher.handle_raw_update(update)`.
- `dispatcher.py` parst über `parse_update(raw_update)`.
- `update_parser.py` verarbeitet nur `message` mit Basisfeldern (`from`, `chat`, `text`).
- Topic-bezogene Telegram-Felder (z. B. `message_thread_id`) werden derzeit nicht gemappt/persistiert.

### 4) Tests / Pattern
Vorhanden:
- WebUI-Auth-Tests: `tests/test_webui_flask_auth.py`
- Users-WebUI-Tests inkl. CSRF/Owner-Gate: `tests/test_webui_flask_users.py`
- Dispatcher/Parsing-Tests: `tests/test_dispatcher.py`, `tests/test_command_parsing.py`

Pattern:
- Flask `test_client()`
- CSRF-Token aus HTML extrahieren und im POST mitsenden
- sqlite tmp DB pro Test
- Seed über bestehende Repositories

Es gibt derzeit keine Tests für Gruppen/Topics-Persistenz und keine `/groups`-Tests.

---

## Minimal-Vorschlag: DB-Modelle

Ziel: nur lokale Verwaltung + Anzeige, keine Telegram-Topic-Erstellung/Löschung.

### Neues Modell `TelegramChat`
Empfohlenes Minimum:
- `id` (PK, int)
- `telegram_chat_id` (BigInteger, unique, index, not null)
- `chat_type` (String, not null)
- `title` (String nullable)
- `username` (String nullable)
- `enabled` (Integer/bool-like, default 1, not null)
- `created_at`, `updated_at`

### Neues Modell `TelegramTopic`
Empfohlenes Minimum:
- `id` (PK, int)
- `chat_id` (FK -> telegram_chats.id, not null)
- `telegram_thread_id` (BigInteger, not null)
- `display_name` (String nullable, lokal)
- `notes` (Text nullable, lokal)
- `enabled` (Integer/bool-like, default 1, not null)
- `created_at`, `updated_at`
- Unique-Constraint: (`chat_id`, `telegram_thread_id`)

Hinweis: `display_name` + `notes` sind rein lokal und überschreiben nichts bei Telegram.

---

## Minimal-Vorschlag: Repository-Methoden

Neue Repository-Klasse (z. B. `ChatTopicRepository`) in `db/repositories.py`:

1. `upsert_chat_from_update(telegram_chat_id, chat_type, title, username) -> TelegramChat`
2. `upsert_topic_from_update(chat_row_id, telegram_thread_id) -> TelegramTopic`
3. `list_groups_with_topics() -> list[...]` (für `/groups` Rendering)
4. `update_topic_meta(topic_id, display_name, notes, enabled) -> bool`
5. Optional: `set_chat_enabled(chat_id, enabled)` falls Gruppen deaktivierbar sein sollen

Audit (optional aber konsistent):
- Bei Topic-Meta-Änderungen `AuditEvent(event_type="topic_meta_update", payload_json=...)`

---

## Einbaupunkt Persistenz im Update-Flow

Empfohlener minimal-invasiver Pfad:
1. `update_parser.py` erweitert um optionales Feld `message_thread_id` in `TelegramMessage`.
2. In `dispatcher.handle_raw_update()` direkt nach erfolgreichem Parse:
   - Chat upserten (immer)
   - Topic upserten nur wenn `message_thread_id` vorhanden
3. Persistenz via `session_factory` + neues `ChatTopicRepository`.

Alternative wäre in `polling.py` vor Dispatcher; empfohlen ist Dispatcher, weil dort bereits validierte Message-Struktur existiert.

---

## Flask-Routen/Templates für `/groups`

### Routen in `webui/flask_blueprints/ui.py`
- `GET /groups`
  - login_required
  - lädt Gruppen + Topics
  - rendert neues Template `groups.html`

- `POST /groups/topics/<int:topic_id>`
  - login_required
  - CSRF geschützt (`FlaskForm`)
  - Felder: `display_name`, `notes`, `enabled`
  - Owner-Gate analog Users-Mutation (`WEBUI_OWNER_TELEGRAM_ID` erforderlich)

### Templates
- neu: `src/amo_bot/webui/templates/groups.html`
  - Tabelle/Liste pro Group
  - darunter Topics
  - Inline-Form pro Topic (display_name, notes, enabled)

- `dashboard.html` Link ergänzen: `/groups`

---

## Topic-Verwaltung (Scope-Fixierung)

Nur lokal:
- `display_name` bearbeiten
- `notes` bearbeiten
- `enabled` toggeln

Nicht in Scope:
- Telegram Topic erstellen
- Telegram Topic löschen
- Telegram Topic umbenennen via API

---

## Owner-Gate / CSRF / Audit

Entscheidungsvorschlag (konsistent mit existierendem Verhalten):
- Lesen (`GET /groups`): jeder eingeloggte WebUI-User
- Mutationen (`POST /groups/topics/<id>`): nur wenn `WEBUI_OWNER_TELEGRAM_ID` gesetzt, sonst 403
- CSRF: verpflichtend via Flask-WTF (`validate_on_submit`)
- Audit: empfohlen für Topic-Metadaten-Änderung

---

## Testplan (minimal)

Neue Tests analog `test_webui_flask_users.py`:
1. `GET /groups` ohne Login -> 302 /login
2. `GET /groups` mit Login -> 200 + leerer Zustand
3. Persistierte Chat/Topic-Daten werden gelistet
4. Topic-Meta-Update mit Owner-ID + CSRF -> 302 + DB geändert
5. Topic-Meta-Update ohne Owner-ID -> 403
6. Topic-Meta-Update ohne CSRF -> 400

Telegram-seitig:
7. Parser akzeptiert `message_thread_id` optional
8. Dispatcher persistiert Chat
9. Dispatcher persistiert Topic bei `message_thread_id`

---

## Kleine Umsetzungsschritte (Reihenfolge)

1. `models.py`: `TelegramChat`, `TelegramTopic` hinzufügen (+ Constraints/Indices)
2. `repositories.py`: `ChatTopicRepository` minimal hinzufügen
3. `update_parser.py`: `message_thread_id` ergänzen
4. Dispatcher-Persistenz-Hook ergänzen (chat/topic upsert)
5. `ui.py`: `/groups` GET + Topic-Update POST ergänzen
6. `templates/groups.html` neu + Dashboard-Link
7. Tests für WebUI + Parser/Dispatcher ergänzen

---

## Risiken / offene Fragen (max 3)

1. Soll `enabled` auf Chat-Ebene ebenfalls im UI mutierbar sein oder nur Topic-Ebene?
2. Sollen Topic-Einträge auch ohne jemals empfangenes Topic-Update manuell anlegbar sein (aktuell nein, nur aus Telegram-Updates)?
3. Reicht SQLite-kompatible Migration über `create_all`, oder ist eine echte Migration (Alembic) bereits im Projektstandard vorgesehen?
