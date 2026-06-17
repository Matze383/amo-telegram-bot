# Release Notes 2026.06.17

---

## Overview

This release bundles Popgun/userplugin work, more robust web research, and the new Current-Info pipeline. The focus is better-evidenced current answers, controlled optional vector retrieval, and reproducible eval/QA coverage.

### New

- **Popgun Userplugin:** New userplugin for monitored market signals with persisted SQL state, deduplicated fetches, observability logging, and a narrower default symbol set.
- **Exchange Candles:** Popgun uses public exchange data for candles and was narrowed across commits toward more stable symbol/market sources.
- **Bot Process Stop CLI:** New local process stop command for controlled operations.
- **Current-Info Pipeline:** New Current-Info service with search broker, candidate ranking, document fetching, MariaDB document cache, evidence assembly, and Telegram integration before the legacy webtool fallback.
- **Optional Vector Search:** Qdrant-backed semantic retrieval layer for Current-Info chunks. MariaDB remains the source of truth; Qdrant stores only vectors, chunk pointers, and source metadata.
- **Eval CLI:** Deterministic Current-Info eval harness with stable JSON/JSONL output and local fixture mode via `--local-only`.

### Web Research & Evidence

- Improved evidence gates for web research, sports/finance profiles, and news corroboration.
- More dynamic research planning with follow-up research, source health, and more conservative fail-closed behavior.
- Bounded Browser Provider for more controlled browser evidence.

### Security & Privacy

- Pre-release privacy/secret audit: no real secrets, tokens, private chat/user IDs, local paths, logs, dumps, or archives found in the push range.
- New secret/API-key documentation remains placeholder-only and points to env/secret storage.
- Current-Info vector search does not store private user queries as vectors.

### OpenSearch Decision

- OpenSearch was evaluated as a spike and deferred for now. Recommendation: measure MariaDB + Qdrant first; consider OpenSearch later only if stronger full-text/analyzer capabilities are proven necessary.

### QA Status

- Large segmented QA run is green after a Backend-owned fixture fix.
- Privacy/secret audit is green.
- Local package build tool `build` is not installed; `compileall src` and `amo_bot` import check are green.

### Upgrade Notes for Admins

1. **Version:** Package version is `2026.6.17`.
2. **Current-Info:** `AMO_CURRENT_INFO_ENABLED=false` remains the default. Enable only after provider/timeout configuration.
3. **Qdrant optional:** `AMO_VECTOR_ENABLED=false` remains the default. Configure Qdrant URL/API key only through env/secrets for semantic retrieval.
4. **Popgun:** Review plugin configuration and runtime policy before enabling.

### Known Limitations

- OpenSearch is not part of this release path.
- Vector search is optional and complements MariaDB; it does not replace it.
- Live provider quality should continue to be measured with eval cases.

---

*Last updated: 2026-06-17*
