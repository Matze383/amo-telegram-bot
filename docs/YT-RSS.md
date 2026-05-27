# YT-RSS Plugin — User Guide / Nutzeranleitung

> **Language / Sprache:** Bilingual (EN-primary with DE headers) – Siehe Abschnitte mit 🇩🇪 / See sections marked with 🇩🇪
> **Scope:** Topic-scoped YouTube channel RSS subscriptions for AMO Telegram Bot.

**Version:** 0.1.0 (example plugin — local use)
**Example path:** `docs/examples/plugin-yt-rss-b2/`

---

## Overview

The YT-RSS plugin allows owners and admins to subscribe Telegram topics (threads) to YouTube channels via RSS. New videos are automatically posted to the subscribed topic when detected.

**Key Features:**
- Subscribe topics to YouTube channels using various URL formats
- Automatic polling every 5 minutes (300 seconds)
- Topic-scoped subscriptions (isolated per thread)
- Duplicate detection and no-backfill behavior

---

## Requirements

| Requirement | Value |
|-------------|-------|
| Minimum Role | `admin` or `owner` |
| Required Permissions | `rss.fetch`, `send_message` |
| Schedule Interval | 300 seconds (5 minutes) |
| Scope | Topic-scoped (per `message_thread_id`) |

---

## Telegram Commands

### `/addyt <channel_url>`

Subscribe the current topic to a YouTube channel.

**Supported URL formats:**
- Direct channel ID: `https://www.youtube.com/channel/UC...`
- Handle URL: `https://www.youtube.com/@handle`
- Custom URL: `https://www.youtube.com/c/name` or `https://www.youtube.com/user/name`
- Handle shorthand: `@handle` (without full URL)
- Channel ID shorthand: `UC...` (without URL)

**Examples:**
```
/addyt https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw
/addyt https://www.youtube.com/@GoogleDevelopers
/addyt @GoogleDevelopers
/addyt UC_x5XG1OV2P6uZZ5FSM9Ttw
```

**Responses:**
- `Added channel UC... for this topic.` — Subscription created successfully
- `Already subscribed in this topic: UC...` — Duplicate subscription (not added again)
- `Permission denied. Only owner/admin can manage YT subscriptions.` — Insufficient role
- Resolver/network error messages for URL resolution failures

### `/delyt <channel_url>`

Remove a YouTube channel subscription from the current topic.

**Usage:**
```
/delyt https://www.youtube.com/channel/UC...
/delyt @handle
/delyt UC...
```

**Responses:**
- `Removed channel UC... from this topic.` — Subscription removed
- `No matching subscription found in this topic.` — Channel was not subscribed
- `Permission denied...` — Insufficient role

---

## WebUI Usage

The YT-RSS plugin provides WebUI functions for managing subscriptions:

### Listing Subscriptions

View all subscriptions for the current context via the plugin's WebUI interface (if exposed by the host).

### Managing Poll Interval

The default poll interval is 300 seconds (5 minutes). This can be adjusted via the WebUI within bounds:
- Minimum: 30 seconds
- Maximum: 86400 seconds (24 hours)

---

## Constraints & Limitations

### Input Validation

| Constraint | Behavior |
|------------|----------|
| Video URLs | Rejected (`/watch`, `/shorts`, youtu.be links) |
| Playlist URLs | Rejected (`/playlist`) |
| Non-YouTube hosts | Rejected |
| Invalid channel IDs | Rejected (must start with `UC`) |

### Resolver Behavior

- **Network errors:** Returns user-friendly error message; retry later
- **HTTP errors:** Returns `resolver_http_error`
- **No channel ID found:** Returns `resolver_no_channel_id`

### Duplicate Detection

- Subscriptions are keyed by `(chat_id, thread_id, channel_key)`
- Adding the same channel twice in the same topic is silently rejected (returns "Already subscribed")

---

## Auth & Topic Behavior

### Authorization

- Only users with role `admin` or `owner` can execute `/addyt` and `/delyt`
- Normal users, VIPs, and ignored users receive "Permission denied"
- The check uses both `context.role` and legacy `is_owner`/`is_admin` flags

### Topic Scoping

- Subscriptions are **strictly scoped** to the topic/thread where they were added
- The same channel can be subscribed independently in different topics
- Each topic has its own cursor and deduplication state
- Private chats (no `message_thread_id`) use `None` as the thread identifier

### Cross-Topic Isolation

- Poll results are never cross-posted between topics
- Each topic maintains independent deduplication history (last 100 entry keys)
- Cursor resets do not affect other topics

---

## Polling & Notifications

### Poll Schedule

- Fixed interval: 300 seconds (5 minutes)
- Triggered by AMO's scheduler mechanism
- Runs across all subscribed channels globally

### Notification Format

When new videos are detected:
```
📺 UCxxxxxxxxx: Video Title Here
https://www.youtube.com/watch?v=...
```

### No-Backfill Behavior

- **First poll:** Initializes cursor to the most recent entry; no messages sent
- **Subsequent polls:** Only entries **newer** than the cursor are notified
- **Dedup:** Entries matching the last 100 seen keys are skipped

This means you will **not** receive historical videos when first subscribing—only new uploads after the subscription is active.

---

## Error Messages Reference

| Error Code | User-Facing Message |
|------------|---------------------|
| `missing_input` | "Usage: /addyt <https://www.youtube.com/channel/UC...>" |
| `invalid_url` | "Invalid URL. Use https://www.youtube.com/channel/UC..., https://www.youtube.com/@handle, /c/<name>, or /user/<name>." |
| `unsupported_video_url` | "Unsupported YouTube URL. Please provide a channel URL (UC..., @handle, /c/<name>, or /user/<name>)." |
| `unsupported_channel_url` | Same as above |
| `unsupported_channel_id` | "Unsupported channel id format. Use a UC... channel URL." |
| `resolver_network_error` | "Could not resolve YouTube channel right now (network error). Please try again." |
| `resolver_http_error` | "Could not resolve YouTube channel (unexpected HTTP response)." |
| `resolver_no_channel_id` | "Could not resolve this YouTube channel URL to a channel id." |
| `resolver_invalid_channel_id` | "Resolved channel id was invalid. Please use a direct /channel/UC... URL." |
| `unsupported_host` | "Unsupported host. Use youtube.com channel URLs only." |

---

## Data Storage

- Subscriptions stored in: `data/plugin_state/yt_rss/state.json`
- Format: JSON with `subscriptions`, `cursors`, `errors`, and `config` sections
- Each subscription includes: `chat_id`, `thread_id`, `channel_key`, `source_url`, `canonical_channel_url`, `rss_url`, `added_by_user_id`, `added_at`

---

## Troubleshooting

### "Permission denied" when adding subscriptions
- Verify your role is `admin` or `owner` in the group
- Check that the bot recognizes your role (may need to re-grant consent if role changed)

### Channel URL not resolving
- Use the direct `/channel/UC...` URL format for most reliable results
- Ensure the channel is public (unlisted/private channels cannot be subscribed)
- YouTube custom URLs (`@handle`, `/c/`, `/user/`) require a web fetch to resolve

### No notifications after adding subscription
- This is expected for the first poll (no-backfill behavior)
- Wait for a new video to be published after subscription

### Duplicate subscriptions
- The same channel cannot be added twice in the same topic
- To re-add after deletion, use `/delyt` first, then `/addyt`

---

## Related Documentation

- [Userplugin Development Guide](USERPLUGINS.md) — Plugin architecture and security rules
- [Setup Guide](SETUP_EN.md) — Bot installation and configuration

---

<p align="center">
  <sub>YT-RSS Plugin — Topic-Scoped YouTube Subscriptions</sub>
</p>
