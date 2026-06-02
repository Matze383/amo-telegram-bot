# Context & Memory Architecture (A5)

> **Language / Sprache:** English (EN-only) – Technical architecture specification using Lingua Franca.
> **Rationale:** Architecture and specification documents are maintained in English only, as per [LANGUAGE_CONVENTIONS.md](LANGUAGE_CONVENTIONS.md) and [i18n-inventory.md](i18n-inventory.md) GH-DOCS-13 decision.

Status: approved architecture/spec (A5 implementation pending; no production code changes in this docs-only review).

## 1) Context layers

| Layer | Purpose | TTL | Storage | Access |
|---|---|---|---|---|
| Turn Context | Process current incoming message/update and immediate tool/runtime state | request-lifetime | in-memory only | request handler only |
| Request Context Window | Build short conversational window for answer quality | bounded window (e.g. last N messages / short time window) | transient cache or reconstructed from recent history source | scoped to same Telegram identity tuple |
| Session/Chat Context | Preserve current chat continuity without becoming long-term memory | configurable short/medium retention (hours/days) | chat-scoped persistence | only same chat/topic scope; never cross-chat/topic by default |
| Persistent Memory Layer | Durable facts/preferences explicitly accepted for long-term reuse | policy-based retention; explicit review/deactivation support | persistent memory store with audit trail | read only via policy + identity scope checks |
| Audit Layer | Trace why/how memory changed and who initiated it | long enough for compliance/debug needs | append-only audit records | internal/admin diagnostics; redacted views |

Rules:
- No implicit long-term memory promotion.
- Context lookup must always enforce identity scope first, then policy.
- Group/topic boundaries are hard isolation boundaries.

## 2) Telegram identity schema

Canonical identity fields used for retrieval, isolation, and audit linkage:

- `platform` (fixed `telegram`)
- `chat_id`
- `topic_id` (nullable; required for topic-aware isolation in forum/group threads)
- `user_id`
- `message_id`
- `role` (e.g. user/assistant/system/tool)
- `timestamp` (UTC ISO-8601 or epoch, consistently normalized)

Guidance:
- Primary context partition key: (`platform`, `chat_id`, `topic_id`).
- Actor attribution key includes `user_id` and `role`.
- Source linkage for memory/audit must retain originating `message_id`.

## 3) Memory promotion policy

Promotion decision states:

- `auto`: allow automatic promotion only for explicitly safe, non-sensitive, policy-approved categories.
- `confirm`: require explicit user confirmation before promotion.
- `deny`: never promote.

Default recommendation:
- DM: default `confirm` (selective `auto` only for clearly safe classes).
- Group/topic: default `deny` or strict `confirm` depending on sensitivity profile.
- Sensitive-content classifier/patterns should force `deny` unless explicit override flow exists.

Mandatory rule:
- “Remember this” intent is an explicit promotion path and must create auditable records.

## 4) Audit model for memory changes

Each memory mutation (create/update/deactivate) must include:

- `who` (actor id/system component)
- `when` (timestamp)
- `why` (reason/policy/intent classification)
- `source_message_id` (origin message reference)

Recommended additional metadata:
- action type (`create|update|deactivate`)
- policy decision (`auto|confirm|deny`)
- scope key (`platform/chat_id/topic_id`)
- redaction flag/classification marker

Audit requirements:
- Append-only entries.
- No raw secret leakage in audit payloads.
- Traceability from memory record back to source decision.

## 5) Privacy & retention by conversation type

### DM
- Higher personalization allowed, but still no implicit sensitive auto-promotion.
- Retention can be longer for approved durable preferences.
- Default sensitive handling: `confirm`/`deny`.

### Group
- Minimize persistence; prefer ephemeral/session context.
- Strong anti-leak policy: no cross-group retrieval.
- Personal facts from one user should not become globally reusable group memory.

### Topic (forum thread)
- Topic is isolated sub-scope under group (`chat_id` + `topic_id`).
- No retrieval from sibling topics unless explicit, auditable policy says so.
- Retention typically shorter than DM; conservative promotion defaults.

## 6) Reference scenarios / QA test anchors

1. Chat context only, no persist:
   - Given normal conversation without remember intent,
   - context window is used for response,
   - no persistent memory record is created.

2. Explicit remember intent:
   - Given user says equivalent of “remember this”,
   - system follows `confirm`/`auto` policy path,
   - persistent record + audit entry with `source_message_id` are created.

3. Group + topic separation:
   - Given two topics in same group,
   - memory/context from topic A is not retrievable in topic B by default.

4. Wrong-context non-retrieval:
   - Given same user in different chat/group,
   - memory scoped to prior chat/topic is not returned.

5. Sensitive message not auto-promoted:
   - Given sensitive content,
   - automatic promotion is blocked (`deny` or forced `confirm`),
   - audit records policy reason without exposing sensitive raw content.

## 7) Acceptance mapping (A5)

- Context layers with purpose/TTL/storage/access: covered in section 1.
- Telegram identity schema: covered in section 2.
- Promotion policy `auto|confirm|deny`: covered in section 3.
- Audit model `who|when|why|source_message_id`: covered in section 4.
- DM/group/topic privacy-retention: covered in section 5.
- Reference scenarios/tests: covered in section 6.

## 8) Internal maintenance: retrievable memory backfill

`python -m amo_bot.db.retrievable_memory_backfill --dry-run` reports a metadata-only preview for seeding `retrievable_memories` from existing summarized stores (`topic_daily_memories`, `topic_long_memories`). Use `--apply` to write rows. The backfill must not read or import `topic_recent_messages`, and output must remain counts/scopes/types only, never memory text.
