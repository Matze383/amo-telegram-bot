# Versionshinweise 2026.06.17

---

## Übersicht

Dieses Release bündelt Popgun/Userplugin-Arbeit, robustere Web-Recherche und die neue Current-Info-Pipeline. Der Fokus liegt auf besser belegten aktuellen Antworten, kontrollierter optionaler Vector-Suche und reproduzierbarer Eval-/QA-Abdeckung.

### Neu

- **Popgun Userplugin:** Neues Userplugin für überwachte Markt-Signale mit persistiertem SQL-State, deduplizierten Fetches, Observability-Logging und engerem Standardsymbol-Set.
- **Exchange-Candles:** Popgun nutzt öffentliche Exchange-Daten für Candles und wurde über mehrere Commits auf stabilere Symbol-/Marktquellen eingegrenzt.
- **Bot Process Stop CLI:** Neuer lokaler Prozess-Stop-Befehl für kontrollierte Betriebsabläufe.
- **Current-Info Pipeline:** Neuer Current-Info-Service mit Search Broker, Kandidaten-Ranking, Dokument-Fetching, MariaDB-Dokumentcache, Evidence Assembly und Telegram-Integration vor dem Legacy-Webtool-Fallback.
- **Optionale Vector-Suche:** Qdrant-basierte semantische Retrieval-Schicht für Current-Info-Chunks. MariaDB bleibt Source of Truth; Qdrant speichert nur Vektoren, Chunk-Zeiger und Quellenmetadaten.
- **Eval CLI:** Deterministisches Current-Info Eval-Harness mit stabiler JSON/JSONL-Ausgabe und lokalem Fixture-Modus via `--local-only`.

### Web-Recherche & Evidenz

- Verbesserte Evidence-Gates für Web-Recherche, Sport-/Finanzprofile und News-Korroboration.
- Dynamischere Research-Planung mit Follow-up-Recherche, Source-Health und konservativerem Fail-Closed-Verhalten.
- Bounded Browser Provider für stärker kontrollierte Browser-Evidence.

### Sicherheit & Privacy

- Privacy-/Secret-Audit vor Release: keine echten Secrets, Tokens, privaten Chat-/User-IDs, lokalen Pfade, Logs, Dumps oder Archive im Push-Bereich gefunden.
- Neue Secret-/API-Key-Dokumentation bleibt bei Platzhaltern und weist auf Env/Secret-Speicherung hin.
- Current-Info Vector-Suche speichert keine privaten User-Queries als Vektoren.

### OpenSearch-Entscheidung

- OpenSearch wurde als Spike bewertet und für jetzt zurückgestellt. Empfehlung: erst MariaDB + Qdrant messen; OpenSearch nur bei später nachgewiesenem Bedarf für stärkere Volltext-/Analyzer-Funktionen.

### QA-Status

- Großer segmentierter QA-Lauf grün nach Backend-Fixture-Fix.
- Zusätzlich Privacy-/Secret-Audit grün.
- Build-Pakettool `build` ist lokal nicht installiert; `compileall src` und Importcheck `amo_bot` sind grün.

### Upgrade-Hinweise für Admins

1. **Version:** Paketversion ist `2026.6.17`.
2. **Current-Info:** `AMO_CURRENT_INFO_ENABLED=false` bleibt Standard. Aktivierung erst nach Provider-/Timeout-Konfiguration.
3. **Qdrant optional:** `AMO_VECTOR_ENABLED=false` bleibt Standard. Für semantisches Retrieval Qdrant-URL/API-Key nur über Env/Secrets konfigurieren.
4. **Popgun:** Plugin-Konfiguration und Runtime-Policy vor Aktivierung prüfen.

### Bekannte Einschränkungen

- OpenSearch ist nicht Teil dieses Release-Pfads.
- Vector-Suche ist optional und ergänzt MariaDB, ersetzt sie aber nicht.
- Live-Provider-Qualität muss weiter mit Eval-Fällen gemessen werden.

---

*Letzte Aktualisierung: 2026-06-17*
