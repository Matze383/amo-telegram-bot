# Release Notes 2026.05.26-Beta

> **HARD STOP:** No push/tag/release/publication without explicit Matze approval.
> **HARD STOP:** Kein push/tag/release/publication ohne explizite Matze-Freigabe.

---

## 🇬🇧 English

### Overview

This release hardens the YouTube-RSS plugin stack and sandbox runtime. New commands complement existing variants, the handle/channel ID resolver is more robust, and diagnostic outputs remain privacy-safe.

### New

- **YT-RSS commands:** `/addyt` and `/delyt` to add and remove YouTube RSS feeds.
- **YouTube handle/channel ID resolver hardening:** Improved resolution of YouTube handles and channel IDs with more robust error handling.
- **Scheduler cursor & backlog:** Improved cursor behavior and backlog processing for more reliable feed updates.
- **Legacy handle migration & deduplication:** Automatic migration and deduplication of legacy handles.

### Security & Privacy

- **Safe diagnostics & log redaction:** Diagnostic outputs contain no sensitive data; automatic redaction of tokens and personal identifiers.
- **No callback/UI reintro:** No re-introduction of callback or UI code; all interactions governed through sandbox runtime.
- **Sandbox/runtime RSS support:** RSS fetching runs entirely within the sandbox with capability gating.

### Architecture / Internal

- **Sandbox runtime tests:** Extended tests for sandbox runtime with RSS feed handling.
- **Capability gating:** All RSS operations subject to strict capability checking (`rss.fetch`).

### Operational Notes

- No breaking changes for end users.
- All RSS operations now run through the sandbox runtime.
- Legacy handles are automatically migrated and deduplicated.

---

*Last updated: 2026-05-26*
