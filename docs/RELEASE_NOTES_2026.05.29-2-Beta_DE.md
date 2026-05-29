# Release Notes 2026.05.29-2-Beta / Versionshinweise 2026.05.29-2-Beta

---

## 🇩🇪 Deutsch

### Übersicht

Dieses Release veröffentlicht die Arbeiten seit `2026.05.29`: scoped User-Profile-Memory, Bot-Peer-Approval, Webtool-Ausführung über einen separaten quota-geprüften Pfad und das YT-RSS-Userplugin als getracktes Repository-Plugin.

### Neu

- **Scoped User-Profile-Memory:** Nutzerprofil-Memory ist kontext-/scope-bewusst eingebunden und vermeidet ungewollte Vermischung zwischen privaten, Gruppen- und Topic-Kontexten.
- **Bot-Peer-Approval:** Andere Bots werden nicht mehr pauschal bedient. Bot-Peers benötigen Owner-Freigabe; erlaubte Bots bekommen nur die vorgesehenen Interaktionen.
- **Webtool-Dispatcher/Subagent-Pfad:** Websearch und Webscraping laufen über einen separaten Dispatcher/Provider-Adapter-Pfad statt direkt im normalen Antwortpfad.
- **`/webtoolquota`:** Neue Verwaltung für Webtool-Quotas pro Rolle mit Modi `disabled`, `limited` und `unlimited`.
- **YT-RSS-Userplugin im Repository:** Das vorhandene `plugins/yt_rss` Userplugin ist jetzt getrackt und auf GitHub verfügbar.

### Verbessert

- **WebUI-Quota-Konfiguration:** Webtool-Quotas können über die WebUI im Nutzer-/Rollenbereich verwaltet werden.
- **Provider-Fail-Closed:** Webtool-Provider schlagen kontrolliert fehl, wenn Provider/Konfiguration nicht verfügbar sind; kein direkter Fallback.
- **Plugin-Repository-Regel:** Userplugins werden künftig standardmäßig mitversioniert, außer ein Plugin wird explizit als privat/lokal markiert.

### Sicherheit & Privacy

- **Quota vor Ausführung:** Webtool-Rollenquotas werden vor Provider-/Subagent-Ausführung geprüft.
- **Sanitized Output:** Webtool-Ergebnisse werden kompakt und gegen Prompt-Injection bereinigt an den Hauptpfad zurückgegeben.
- **Metadata-only Logging/Audit:** Logs und Audit-Einträge enthalten keine Prompts, Nachrichtentexte, Queries, URLs, Secrets, Tokens, `.env`-Inhalte oder private Kontexte.
- **Bot-Peer-Schutz:** Bot-Peers bleiben ohne explizite Owner-Freigabe blockiert bzw. still.
- **Keine Secrets im YT-RSS-Plugin:** Das getrackte Userplugin enthält keine Runtime-State-Dateien, Caches, Tokens oder lokalen Secrets.

### Architektur / Interna

- Neuer Webtool-Facade-/Dispatcher-Pfad mit Provider-Adaptern für bestehende Websearch-/Webscraping-Coreplugins.
- Webtool-Nutzung ist getrennt von normalen `/ask` AI-Antworten; es gibt kein allgemeines AI-Antwort-Quota als finalen Scope.
- `plugins/` ist nicht mehr global ignoriert; Cache-/Runtime-Artefakte bleiben über bestehende Ignore-Regeln ausgeschlossen.

### Qualitätssicherung

- Issue #48 Main-Verifikation: `git diff --check` sauber; gezielte Webtool/Quota/Dispatcher/i18n/db Tests: `131 passed`.
- YT-RSS/Userplugin-Verifikation: relevante Plugin/Userplugin Tests: `95 passed`; zusätzliche YT-RSS-Tests: `30 passed`.
- QA-Gates: PASS für Webtool-Architektur, Docs und getracktes Userplugin.

### Betriebsnotizen

- GitHub CI bleibt gemäß Maintainer-Entscheidung manuell deaktiviert.
- Normale `/ask` AI-Antworten sind nicht durch Webtool-Quotas limitiert.
- Keine Breaking Changes für Endnutzer erwartet.

---

*Letzte Aktualisierung: 2026-05-29*
