# Release Notes 2026.06.03

---

## Overview

This release brings significant improvements to web research reliability, database scalability, and memory management. Notable additions include the new Learning Feedback Memory feature and improved Scoped Memory Recall capabilities.

### New

- **Auto Web Research/Reliability:** Clearer distinction between successful websearch and actually verifiable current values; fail-closed behavior when web results are unavailable.
- **Bounded Web Extraction:** Automatic web research limited to configured limits (SearXNG → static extraction → optional browser fallback).
- **Feedback-Driven Follow-up Research:** Users can trigger another research round with phrases like "search more", "other sources", "open/check the sources".
- **Broader Web Research Triggers:** More intent types trigger automatic web research (sports results, current external facts, generic current-data classification).
- **Retry on Empty:** One-time retry when auto websearch returns empty.

### Database

- **MariaDB/MySQL Support:** Full and robust MariaDB/MySQL support alongside SQLite. Prepared for future multi-instance/production deployments after completing backup, security, and load testing gates.
- **Migration Tooling:** Dry-run migration with table names, row counts, and status overview (no memory contents).
- **Legacy Null Handling:** Robust handling of legacy null values during migration.
- **Source-of-Truth:** SQLite remains the default for local instances; MariaDB recommended for future multi-instance/production deployments after completing hardening gates.

### Memory

- **Scoped Retrievable Memory:** Memory responses are strictly isolated by scope (topic/group/private).
- **Backfill from Daily Summaries:** Migration of existing daily summaries into retrievable memory.
- **Explicit `/remember` Command:** Manual saving of important preferences and facts.
- **Global Manual Memory v1 Disabled:** In v1, global manual memories are disabled; storage only upon explicit request.

**Important Limitation:** Memories are **scoped/untrusted context** and **cannot** override the requirement for current live web data.

### Learning Feedback Memory v1

- **Explicit Learning from Feedback:** Source preferences, corrections to chart analyses and results, approach preferences.
- **Scoped Learning:** Learning is limited to topic/chat/user — no global learning in v1.
- **Emoji Reactions as Weak Signals:** Telegram reactions/smileys are interpreted as weak engagement/feedback signals — low confidence, scope-limited.

**Opt-out:** If you do not want reaction-based learning, avoid reacting to bot messages with emoji or provide explicit corrective text.

### Security & Privacy

- No secrets in release documentation.
- Memory scope isolation: No cross-scope access.
- Daily Memory and Dreaming share the same night window (02:00–05:00 Europe/Berlin).
- **Owner restart:** `/restart` is documented as an owner-only operator command; AMO acknowledges the command before process exit and persists the polling offset to prevent restart loops.

### Upgrade Notes for Admins

1. **MariaDB Migration (optional):**
   ```bash
   pip install pymysql
   python -m amo_bot.db.migrate \
     --source-url sqlite:///./data/amo_bot.db \
     --target-url 'mysql+pymysql://amo_bot:<pass>@<host>:3306/amo_bot?charset=utf8mb4' \
     --dry-run
   ```
   After backup verification: remove `--dry-run`.

2. **Retrievable Memory Backfill (after migration):**
   ```bash
   python -m amo_bot.db.retrievable_memory_backfill --dry-run
   # After review:
   python -m amo_bot.db.retrievable_memory_backfill --apply
   ```

3. **SearXNG for Current Data:**
   - Configure `SEARXNG_BASE_URL` for Auto Web Research.
   - Only HTTPS URLs allowed for public endpoints.

4. **Learning Feedback Memory:**
   - Emoji reactions are weak, scope-limited signals.
   - For important preferences: use `/remember`.

### Known Limitations

- Memories cannot replace live web evidence (fail-closed for current data).
- No global memory learning in v1 (scoped only).
- Daily Memory and Dreaming share the night window — resource conflicts possible when both enabled.

### Operational Notes

- SQLite remains the recommended default for local instances.
- MariaDB is prepared for future production deployments.
- No breaking changes expected for end users.

---

*Last updated: 2026-06-03*
