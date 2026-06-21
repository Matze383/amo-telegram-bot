# Current Autoreply Context Pipeline Audit

> **Language / Sprache:** English (EN-only) - Technical audit document using Lingua Franca.
> **Scope:** GitHub issue #86. This is a current-state audit only and does not define a new resolver contract.

Status: current implementation audit. No functional autoreply behavior changes are part of this issue.

Note: the current `main` branch already contains the diagnostic structured context snapshot from later resolver work. This audit documents the pipeline as it now stands, including that snapshot, because it is present before the synthesis call today.

## 1) Entry Point And Trigger Gates

Autoreply assembly starts in `Dispatcher._maybe_handle_ai_autoreply()` in `src/amo_bot/telegram/dispatcher.py`.

The incoming Telegram message contributes:

- `message.text` as raw current text.
- `message.chat.id`, `message.chat.type`, `message.message_id`, and `message.message_thread_id`.
- `message.from_user.id`.
- reply metadata through `_is_reply_to_current_bot()` and `_resolve_reply_context()`.

Routing is delegated to `AIRouter.decide()` in `src/amo_bot/ai/router.py`. Eligible group/topic replies require an explicit bot mention or reply-to-bot. Private chats can use `SCOPE_ENABLED` when the private scope is enabled and the role policy allows it.

Denied paths write `ai_autoreply_denied` audit rows when a database is configured. Successful answer generation later writes `ai_autoreply_sent`; generation failures write `ai_autoreply_error`.

## 2) Prompt Assembly Order

When the router decision is eligible, the normal LLM prompt is assembled in this order:

1. Identity instruction.
2. Default response language rule.
3. Current time context from `build_current_time_context()`.
4. A guard telling the model to focus on the current message and treat background as possibly stale or irrelevant.
5. `Current message:` with the mention-stripped normalized text.
6. Structured runtime context snapshot JSON.
7. `Background context:` sections, if any.
8. Final `User message:` with the normalized text.

Background sections are appended in this order:

1. Telegram reply context.
2. Relevant recent chat context.
3. Known coarse user profile context for current participants.
4. Operator-authored prompt context docs.
5. Assistant behavior context (`main_soul_text`, then `topic_soul_text`).
6. Daily memory context.
7. Long-term memory context.
8. Retrieved memory context.

This order gives the current turn two explicit prompt placements before and after background context, but there is no hard resolver that removes unrelated background content from synthesis.

## 3) Current Context Sources

| Source | Origin | Scope/filtering | Sorting/priority | Window/caps | Audit trace |
|---|---|---|---|---|---|
| Current message | Incoming Telegram update, normalized by `_sanitize_prompt_for_autoreply()` | Current request only; bot mention removed for prompt text | Highest practical priority by prompt wording and duplicate placement | Request lifetime | `ai.context_snapshot`, `ai_context_snapshot`, `ai_autoreply_sent/error` metadata include message identifiers and snapshot |
| Telegram reply context | `topic_recent_messages` lookup by replied-to message | Same resolved stored recent-message record; missing records are ignored | Added before recent chat context | One replied-to record | No dedicated lookup audit; only snapshot/source counts show reply context existed |
| Recent topic/chat messages | `TopicAgentMemoryRepository.list_recent()` from `topic_recent_messages` | Same `scope_type`, `chat_id`, `topic_id`, `user_id`; max age 14 days; bot-authored and obvious meta-status rows filtered out | Repository returns oldest-first after fetching newest rows; router then takes the last configured rows after filtering | Config `recent_context_window_size`, default 20, max 50; DB read max 50; prompt text max 2000 chars | `ai_router_recent_context_filter` logs candidate/selected/excluded counts, not IDs or content |
| Daily memory | `topic_daily_memories` through `get_daily_memory()` | Exact current scope | Checks today first, then yesterday | First found summary; sanitized and capped to 2000 chars | Read errors are folded into `context_error`; no positive per-read audit |
| Long-term memory | `topic_long_memories` through `list_long_memories()` | Exact current scope; active only; `answer_status == approved` | Repository returns newest-first; router reverses to oldest-first chronology before prompt injection | Up to 100 records read; joined text capped to 2000 chars | Read errors are folded into `context_error`; no positive per-read audit |
| Lexical active recall | Reuses daily memory, long memory, and recent message text already loaded by the router | Only topic and private scopes; sanitized with secret/path/contact filters; requires lexical overlap with the prompt unless prompt tokens are empty/weak | Keeps source iteration order: daily, long, recent; stops at first 20 records | 150 ms budget; max 20 records; max 1200 chars | `ai_router_recall` logs decision, reason, counts, truncation, timeout, and error class |
| Retrievable memory | `retrievable_memories` through `RetrievableMemoryRepository.recall_memories()` | Visibility union of global plus matching chat, topic, and/or user rows; active and unexpired only | MySQL FULLTEXT preselect when available; final score sort by score, confidence, id descending | Max 5 records; formatted block capped to 1200 chars; `mark_used=True` updates use counters | `ai_router_retrievable_memory` logs decision, records, chars, max score, error class; content and IDs are not logged |
| User profile context | `user_memory_profiles` through `list_profiles_for_users()` | Same scope; candidate users from current user, reply target, scope user, and recent authors | Preserves participant discovery order, then caps to matching non-empty profiles | Max 5 users, 5 bullets per user, 1200 chars | Read errors folded into `context_error`; no positive per-profile audit |
| Prompt context docs | `prompt_context_docs` through `resolve_docs()` | Enabled docs; topic doc overrides global doc per kind | Repository kind order, then router accepts `AGENT`, `SOUL`, `PLUGINS`, `AUFGABE` | 2000 chars per doc; 6000 chars total | Read errors folded into `context_error`; no positive doc IDs in audit |
| Assistant behavior context | Topic agent config `main_soul_text` and `topic_soul_text` | Current resolved scope config only | Main soul first, topic soul second | Combined cap 2000 chars after suspicious-marker filtering | No dedicated audit beyond snapshot source count and final audit payload |
| Current-Info/Web research | `decide_auto_research()`, Current-Info service, legacy webtool dispatcher, and research orchestrator | Triggered by current normalized text; Current-Info request carries user/chat/topic/role metadata | Current-Info can short-circuit and send its own answer before legacy research or normal LLM synthesis | Telegram Current-Info defaults: timeout 8s, max 5 search results, max 3 fetched docs; synthesis evidence max 4500 chars and 5 sources | Current-Info logs query/provider/fetch/evidence/synthesis events and Telegram sent/fallback events |
| Qdrant/vector retrieval | Optional Current-Info retrieval layer when `AMO_VECTOR_ENABLED=true` | Only Current-Info document chunks; not conversation memory | Hybrid provider fuses MariaDB keyword chunks and vector chunks using reciprocal-rank style scoring | Request `max_results`; vector timeout/settings from `AMO_VECTOR_*` | Vector failures are warnings only; Current-Info query/fetch runs are DB-recorded, but vector hit IDs are not included in Telegram autoreply audit |

## 4) Current-Info And Websearch Branching

After the normal prompt has already been assembled and the snapshot has been built, dispatcher checks explicit webtool chat triggers. Explicit `websearch:`, browser, or scraping triggers can return a tool response directly.

If there is no explicit trigger and a webtool dispatcher exists:

1. `_maybe_handle_current_info_autoreply()` runs first when `AMO_CURRENT_INFO_ENABLED` and the Current-Info service are available.
2. It uses `decide_auto_research(normalized_text)` and only proceeds for `websearch`, `browser`, or `webscraping` capabilities.
3. It builds a `CurrentInfoRequest` with current query, locale, domain hint, result/document caps, user/chat/topic IDs, role, Telegram message ID, auto-research reason, and direct URL.
4. If Current-Info returns `empty_evidence` or `unverified_evidence`, the dispatcher sends an insufficiency answer and returns.
5. If Current-Info answers, it synthesizes a concise answer using only checked evidence, appends sources, sends it, and returns.
6. If Current-Info times out, errors, or returns not answered, dispatcher falls back to the legacy research orchestrator and then possibly normal LLM synthesis.

Current-Info search/fetch/retrieval details:

- Search provider output is normalized, deduplicated, ranked, and optionally adjusted by source preferences.
- Fetched documents are capped by `max_documents`; fetch priority comes from the ranked search results.
- Stored document chunks are retrieved from MariaDB keyword search. If vector search is enabled, keyword and vector chunks are fused.
- Evidence assembly classifies confidence, freshness, source landscape, stale/snippet-only gaps, and warnings.

## 5) Structured Context Snapshot

`build_context_snapshot()` in `src/amo_bot/ai/context_snapshot.py` creates a diagnostic snapshot before synthesis. It is inserted into the prompt and also written to logs/audit:

- Log event: `ai.context_snapshot`.
- DB audit event: `ai_context_snapshot`.
- Included again in `ai_autoreply_sent` and `ai_autoreply_error` payloads.

The snapshot records schema version, detected current intent, extracted active subject, frame candidates, assumptions, conflicts, uncertainty, whether Current-Info appears required, and normalized context source counts.

Important limitation: this is diagnostic metadata, not a resolver. It can flag a low-overlap boundary between the current turn and background context, but it does not remove, reorder, or suppress context sections.

## 6) Topic 2246 Diagnostic Fixture

The observed failure case is:

- Chat: `-1003997137641`.
- Topic/message thread: `2246`.
- User message: `14776`.
- Bot answer: `14777`.
- Failure shape: current user asks for a current real fact, while recent topic context contains Fantasy/simulation content. The answer can blend the current factual question with background roleplay/simulation framing.

Reproduction fixture shape:

1. Enable AI for scope `topic`, `chat_id=-1003997137641`, `topic_id=2246`.
2. Store recent topic messages containing unrelated Fantasy/simulation content, for example tavern, orcs, magic, quest, kingdom.
3. Send a mention-triggered current-fact message such as `@AmoBot Was ist der aktuelle echte Kurs von BTC?`.
4. Keep Current-Info/Websearch unavailable, disabled, or falling back to normal answer synthesis if the aim is to exercise the mixed-context LLM path.
5. Observe that the prompt includes the current BTC question plus recent Fantasy context. Current code also includes a context snapshot with `source_frame_boundary` when lexical overlap is low.

Existing fixture: `tests/test_context_snapshot.py::test_topic_2246_fixture_structures_current_turn_background_boundary` covers the diagnostic boundary behavior for this shape. It does not replay Telegram update `14776` against production databases or assert final LLM answer content.

## 7) Logging Coverage

Existing logs are enough to tell that multiple context sources were available and that the diagnostic snapshot saw a boundary conflict:

- `ai.context_snapshot` and `ai_context_snapshot` include frame candidates, conflict count, active subject, current-info flag, and source counts.
- `ai.autoreply.sent` includes router reason, duration, snapshot conflict count, and Current-Info requirement flag.
- `ai_autoreply_sent/error` audit payloads include the full structured snapshot.
- `ai_router_recent_context_filter` reports recent-message candidate, selected, bot-excluded, and meta-excluded counts.
- `ai_router_recall` reports lexical recall decision metadata.
- `ai_router_retrievable_memory` reports retrievable-memory decision metadata.
- Current-Info logs provider/search/fetch/evidence/synthesis and Telegram sent/fallback outcomes.

## 8) Logging Gaps For Future Resolver Decisions

The existing logs are not sufficient to fully reconstruct later Context-Resolver decisions without replaying DB state:

- No stable context bundle ID links router context reads, context snapshot, Current-Info branch, and final send as one immutable context package.
- Recent-message audit logs counts but not selected `topic_recent_messages.id`, Telegram `message_id`, author IDs, or timestamps.
- Daily and long memory reads have no positive audit entries with selected row IDs, dates, status, or truncation metadata.
- Prompt context docs have no positive audit entries with selected kind/scope/doc identifiers or truncation metadata.
- User profile context has no positive audit entries with selected user IDs/profile scopes.
- The final normal LLM prompt text is intentionally not logged, and there is no redacted source manifest that reconstructs exact section order and truncation.
- Current-Info vector retrieval failures are warning logs, but selected vector/keyword chunk IDs are not linked back to the Telegram autoreply audit.
- Current-Info direct-send answers and normal LLM fallback paths are logged separately; there is no single field saying which branch won for a given incoming message.
- The snapshot stores source counts and conflict metadata, but it does not store source row identities or a resolver decision explaining why each source was kept, downranked, or ignored.

## 9) Residual Risk

Until a real resolver suppresses or explicitly labels stale/unrelated background, the model still receives all assembled background sections on the normal LLM path. The current snapshot improves diagnosis and prompt guidance but cannot guarantee separation between Fantasy/simulation context, user claims, bot claims, and checked current facts.
