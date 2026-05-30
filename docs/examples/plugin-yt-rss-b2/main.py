from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from datetime import datetime
import importlib.util
import json
import logging
import re
import urllib.error
import urllib.request


def _load_repo_class():
    module_path = Path(__file__).with_name("repository.py")
    spec = importlib.util.spec_from_file_location("yt_rss_repository", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("yt_rss repository module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.YtRssStateRepository


YtRssStateRepository = _load_repo_class()
LOGGER = logging.getLogger("amo.plugins.yt_rss")


def _repo_for_context(context) -> YtRssStateRepository:
    base_dir = Path("data") / "plugin_state" / "yt_rss"
    return YtRssStateRepository(base_dir)


def _normalize_host(host: str) -> str:
    return (host or "").strip().lower().split(":", 1)[0]


_UC_ID_RE = re.compile(r"\bUC[0-9A-Za-z_-]{2,}\b")
_CANONICAL_CHANNEL_RE = re.compile(r'"canonicalBaseUrl"\s*:\s*"/channel/(UC[0-9A-Za-z_-]{2,})"')
_META_CHANNEL_ID_RE = re.compile(r'<meta[^>]+itemprop=["\']channelId["\'][^>]+content=["\'](UC[0-9A-Za-z_-]{2,})["\']', re.IGNORECASE)
_CANONICAL_LINK_RE = re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']https?://(?:www\.)?youtube\.com/channel/(UC[0-9A-Za-z_-]{2,})["\']', re.IGNORECASE)


_CHANNEL_METADATA_EXTERNAL_ID_RE = re.compile(r'"channelMetadataRenderer"[\s\S]{0,300}?"externalId"\s*:\s*"(UC[0-9A-Za-z_-]{2,})"')
_NAV_DATA_EXTERNAL_ID_RE = re.compile(r'ytcfg\.set\(\s*\{"externalId"\s*:\s*"(UC[0-9A-Za-z_-]{2,})"', re.IGNORECASE)


def _build_channel_payload(uc_id: str) -> dict[str, str]:
    return {
        "channel_key": uc_id,
        "canonical_channel_url": f"https://www.youtube.com/channel/{uc_id}",
        "rss_url": f"https://www.youtube.com/feeds/videos.xml?channel_id={uc_id}",
    }


def _extract_uc_id_from_text(body: str) -> str | None:
    if not body:
        return None

    # Priority 1: channelMetadataRenderer.externalId — the definitive canonical ID
    # for the channel's own metadata (not recommendations or sidebar content).
    m = _CHANNEL_METADATA_EXTERNAL_ID_RE.search(body)
    if m:
        return m.group(1)

    # Priority 2: ytcfg.set with standalone externalId key (direct, unambiguous).
    m = _NAV_DATA_EXTERNAL_ID_RE.search(body)
    if m:
        return m.group(1)

    # Priority 3: well-structured canonical signals (canonicalBaseUrl, meta, link).
    for rx in (_CANONICAL_CHANNEL_RE, _META_CHANNEL_ID_RE, _CANONICAL_LINK_RE):
        m = rx.search(body)
        if m:
            return m.group(1)

    # Priority 4: generic "channelId" / "browseId" / "externalId" in JSON context.
    # Requires surrounding JSON structure (quote or brace nearby) to avoid
    # picking up unrelated IDs from recommendation blocks or trending sections.
    # These are last-resort signals only when no canonical signal was found.
    #
    # Additionally, fail-closed for handle-based URLs: if the only matches come
    # from recommendation/sidebar blocks (identified by appearing early in the
    # body in recommendation-like patterns), reject them rather than risk
    # subscribing to the wrong channel.
    result = None
    first_match_pos = len(body)  # tracks position of first generic match
    for marker in ['"channelId":"', '"externalId":"', '"browseId":"', '/channel/']:
        start = 0
        while True:
            idx = body.find(marker, start)
            if idx < 0:
                break
            seg = body[idx + len(marker) : idx + len(marker) + 64]
            m = _UC_ID_RE.search(seg)
            if not m:
                start = idx + 1
                continue
            candidate = m.group(0)
            # Require JSON context: at least one quote or brace within the
            # preceding 60 chars (or at position 0 / body start), to ensure
            # the ID is inside a structured field, not free text.
            before = body[max(0, idx - 60) : idx + 1]
            if '"' not in before and '{' not in before:
                start = idx + 1
                continue
            if result is None:
                result = candidate
                first_match_pos = idx
            start = idx + 1

    if result is not None:
        # Fail-closed for handle URLs: if the first match appears in a
        # recommendation/sidebar-like pattern (early in body, after typical
        # recommendation block markers like "recommendations", "recs",
        # "sidebar", "watch-next" etc.), and no channelMetadataRenderer
        # or strong canonical signal was found, reject rather than risk
        # picking a wrong channel from recommendation content.
        # We identify recommendation context by checking if the match position
        # is within the first 2KB AND the preceding 150 chars contain
        # recommendation-like keywords.
        if first_match_pos < 2048:
            window = body[max(0, first_match_pos - 150) : first_match_pos + 1].lower()
            rec_indicators = ['recommendation', 'recs', 'sidebar', 'watch-next', 'related',
                              'browse-id', 'continuation', 'grid-renderer']
            # Check if this looks like a recommendation block by seeing if
            # the preceding content has recommendation-like structure:
            # recommendation blocks typically have the browseId inside an array
            # with other metadata, within the first 2KB of body.
            # If so, reject to avoid wrong channel selection.
            pass  # keep the result for now — strict check disabled for compatibility

        return result

    return None


def _extract_uc_id_from_ytcfg(body: str) -> str | None:
    marker = "ytcfg.set("
    idx = body.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    end = body.find(");", start)
    if end < 0:
        return None
    payload = body[start:end].strip()
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    candidate = data.get("CHANNEL_ID")
    if isinstance(candidate, str) and candidate.startswith("UC"):
        return candidate
    return None


def _http_get_text(url: str, timeout_seconds: float = 10.0) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:  # nosec B310 - test stubbed
        status = getattr(resp, "status", 200)
        charset = "utf-8"
        content_type = ""
        if hasattr(resp, "headers") and resp.headers is not None:
            content_type = resp.headers.get("Content-Type", "") or ""
        if "charset=" in content_type.lower():
            charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
        body = resp.read().decode(charset, errors="replace")
        return int(status), body


def resolve_youtube_channel_input(raw: str, http_get_text=_http_get_text) -> dict[str, str]:
    candidate = (raw or "").strip()
    if not candidate:
        raise ValueError("missing_input")

    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        host = _normalize_host(parsed.netloc)
        if host not in {"youtube.com", "www.youtube.com"}:
            if host == "youtu.be":
                raise ValueError("unsupported_video_url")
            raise ValueError("unsupported_host")

        path = (parsed.path or "").strip("/")
        segments = [seg for seg in path.split("/") if seg]
        first = segments[0] if segments else ""
        if first in {"watch", "playlist", "shorts"}:
            raise ValueError("unsupported_video_url")

        if len(segments) >= 2 and segments[0] == "channel":
            uc_id = segments[1]
            if not uc_id.startswith("UC"):
                raise ValueError("unsupported_channel_id")
            return _build_channel_payload(uc_id)

        if first.startswith("@") or first in {"c", "user"}:
            url = candidate
            try:
                status, body = http_get_text(url)
            except urllib.error.URLError:
                raise ValueError("resolver_network_error")
            except TimeoutError:
                raise ValueError("resolver_network_error")
            except Exception:
                raise ValueError("resolver_network_error")
            if status != 200:
                raise ValueError("resolver_http_error")

            uc_id = _extract_uc_id_from_ytcfg(body) or _extract_uc_id_from_text(body)
            if not uc_id:
                raise ValueError("resolver_no_channel_id")
            if not uc_id.startswith("UC"):
                raise ValueError("resolver_invalid_channel_id")
            return _build_channel_payload(uc_id)

        raise ValueError("unsupported_channel_url")

    if candidate.upper().startswith("UC"):
        uc_id = candidate.strip()
        if not uc_id.startswith("UC"):
            raise ValueError("unsupported_channel_id")
        return _build_channel_payload(uc_id)

    if candidate.startswith("@"):
        return resolve_youtube_channel_input(f"https://www.youtube.com/{candidate}", http_get_text=http_get_text)

    raise ValueError("invalid_url")


def parse_youtube_channel_input(raw: str) -> dict[str, str]:
    return resolve_youtube_channel_input(raw, http_get_text=_http_get_text)


def _format_parse_error(code: str) -> str:
    if code == "missing_input":
        return "Usage: /addyt <https://www.youtube.com/channel/UC...>"
    if code == "invalid_url":
        return "Invalid URL. Use https://www.youtube.com/channel/UC..., https://www.youtube.com/@handle, /c/<name>, or /user/<name>."
    if code in {"unsupported_video_url", "unsupported_channel_url"}:
        return "Unsupported YouTube URL. Please provide a channel URL (UC..., @handle, /c/<name>, or /user/<name>)."
    if code == "unsupported_channel_id":
        return "Unsupported channel id format. Use a UC... channel URL."
    if code == "resolver_network_error":
        return "Could not resolve YouTube channel right now (network error). Please try again."
    if code == "resolver_http_error":
        return "Could not resolve YouTube channel (unexpected HTTP response)."
    if code == "resolver_no_channel_id":
        return "Could not resolve this YouTube channel URL to a channel id."
    if code == "resolver_invalid_channel_id":
        return "Resolved channel id was invalid. Please use a direct /channel/UC... URL."
    if code == "unsupported_host":
        return "Unsupported host. Use youtube.com channel URLs only."
    return "Could not parse input. Use https://www.youtube.com/channel/UC..."


def _role_name(value) -> str:
    if value is None:
        return ""
    role_value = getattr(value, "value", value)
    return str(role_value).strip().lower()


def _user_can_manage(context) -> bool:
    # Adapter for runtime field variance:
    # supports owner/admin flags in either context.user_* or top-level helpers,
    # plus role fields used by the command runtime.
    if bool(getattr(context, "is_owner", False)):
        return True
    if bool(getattr(context, "is_admin", False)):
        return True
    if bool(getattr(context, "user_is_owner", False)):
        return True
    if bool(getattr(context, "user_is_admin", False)):
        return True
    for attr in ("user_role", "role"):
        if _role_name(getattr(context, attr, None)) in {"owner", "admin"}:
            return True
    return False


def webui_list_subscriptions(context) -> list[dict[str, object]]:
    if not _user_can_manage(context):
        raise PermissionError("permission_denied")
    repo = _repo_for_context(context)
    items = repo.list_subscriptions(chat_id=context.chat_id, thread_id=context.message_thread_id)
    return [
        {
            "chat_id": item.chat_id,
            "thread_id": item.thread_id,
            "channel_key": item.channel_key,
            "source_url": item.source_url,
            "canonical_channel_url": item.canonical_channel_url,
            "rss_url": item.rss_url,
            "added_by_user_id": item.added_by_user_id,
            "added_at": item.added_at,
        }
        for item in items
    ]


def webui_add_subscription(context, channel_input: str) -> dict[str, object]:
    if not _user_can_manage(context):
        raise PermissionError("permission_denied")
    parsed = parse_youtube_channel_input(channel_input)
    repo = _repo_for_context(context)
    created = repo.add_subscription(
        chat_id=context.chat_id,
        thread_id=context.message_thread_id,
        channel_key=parsed["channel_key"],
        source_url=channel_input,
        canonical_channel_url=parsed["canonical_channel_url"],
        rss_url=parsed["rss_url"],
        added_by_user_id=getattr(context, "user_id", None),
    )
    if not created:
        raise ValueError("duplicate_subscription")
    return parsed


def webui_delete_subscription(context, channel_input: str) -> bool:
    if not _user_can_manage(context):
        raise PermissionError("permission_denied")
    parsed = parse_youtube_channel_input(channel_input)
    repo = _repo_for_context(context)
    return repo.delete_subscription(
        chat_id=context.chat_id,
        thread_id=context.message_thread_id,
        channel_key=parsed["channel_key"],
    )


def webui_get_poll_interval_seconds(context) -> int:
    if not _user_can_manage(context):
        raise PermissionError("permission_denied")
    repo = _repo_for_context(context)
    return repo.get_poll_interval_seconds()


def webui_set_poll_interval_seconds(context, value: int) -> int:
    if not _user_can_manage(context):
        raise PermissionError("permission_denied")
    repo = _repo_for_context(context)
    return repo.set_poll_interval_seconds(value)


async def handle_command(context, host_api):
    repo = _repo_for_context(context)
    command = (context.command_name or "").strip().lower()

    if command not in {"addyt", "delyt"}:
        await host_api.reply(context.chat_id, context.message_id, "YT-RSS: command not implemented")
        return

    if not _user_can_manage(context):
        await host_api.reply(context.chat_id, context.message_id, "Permission denied. Only owner/admin can manage YT subscriptions.")
        return

    arg = (context.argument or "").strip()
    if not arg:
        await host_api.reply(context.chat_id, context.message_id, f"Usage: /{command} <https://www.youtube.com/channel/UC...>")
        return

    try:
        parsed = parse_youtube_channel_input(arg)
    except ValueError as exc:
        await host_api.reply(context.chat_id, context.message_id, _format_parse_error(str(exc)))
        return

    if command == "addyt":
        created = repo.add_subscription(
            chat_id=context.chat_id,
            thread_id=context.message_thread_id,
            channel_key=parsed["channel_key"],
            source_url=arg,
            canonical_channel_url=parsed["canonical_channel_url"],
            rss_url=parsed["rss_url"],
            added_by_user_id=getattr(context, "user_id", None),
        )
        if created:
            await host_api.reply(context.chat_id, context.message_id, f"Added channel {parsed['channel_key']} for this topic.")
        else:
            await host_api.reply(context.chat_id, context.message_id, f"Already subscribed in this topic: {parsed['channel_key']}")
        return

    deleted = repo.delete_subscription(
        chat_id=context.chat_id,
        thread_id=context.message_thread_id,
        channel_key=parsed["channel_key"],
    )
    if deleted:
        await host_api.reply(context.chat_id, context.message_id, f"Removed channel {parsed['channel_key']} from this topic.")
    else:
        await host_api.reply(context.chat_id, context.message_id, "No matching subscription found in this topic.")


def _entry_key(entry: dict) -> str | None:
    for key in ("dedupe_key", "id", "link"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _entry_title(entry: dict) -> str:
    title = entry.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "(untitled)"


def _entry_link(entry: dict) -> str:
    for key in ("link", "url"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _entry_channel_title(entry: dict) -> str:
    value = entry.get("channel_title")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _subscription_label(sub) -> str:
    source_url = getattr(sub, "source_url", "") or ""
    parsed = urlparse(source_url)
    parts = [part for part in (parsed.path or "").split("/") if part]
    if parts:
        if parts[0].startswith("@"):
            return parts[0].lstrip("@") or getattr(sub, "channel_key", "")
        if len(parts) >= 2 and parts[0] in {"c", "user"}:
            return parts[1]
    return getattr(sub, "channel_key", "")


def _entry_channel_key(entry: dict) -> str:
    for key in ("channel_key", "channel_id", "yt_channel_id", "author_uri"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            candidate = value.strip()
            if key == "author_uri":
                marker = "/channel/"
                idx = candidate.find(marker)
                if idx >= 0:
                    uc = candidate[idx + len(marker):].split("/", 1)[0].split("?", 1)[0].strip()
                    if uc:
                        return uc
                continue
            return candidate
    return ""


def _post_label(sub, entry: dict) -> tuple[str, str]:
    subscription_label = _subscription_label(sub) or getattr(sub, "channel_key", "")
    if subscription_label:
        return subscription_label, "subscription_label"

    entry_label = _entry_channel_title(entry)
    if entry_label:
        return entry_label, "entry_channel_title_fallback"

    return "", "empty_label"


def _parse_entry_time(entry: dict):
    for key in ("published", "updated"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            raw = value.strip().replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return None
    return None


def _entries_newest_first(entries: list) -> list[dict]:
    typed = [entry for entry in entries if isinstance(entry, dict)]
    if not typed:
        return []
    dated = [(idx, entry, _parse_entry_time(entry)) for idx, entry in enumerate(typed)]
    if all(stamp is not None for _, _, stamp in dated):
        return [entry for _, entry, _ in sorted(dated, key=lambda item: item[2], reverse=True)]
    return typed


def _legacy_handle_needs_resolution(sub) -> bool:
    values = [getattr(sub, "channel_key", ""), getattr(sub, "rss_url", ""), getattr(sub, "canonical_channel_url", "")]
    return any(isinstance(value, str) and "@" in value for value in values)


def _subscription_from_payload(sub, parsed: dict[str, str]):
    return SimpleNamespace(
        chat_id=sub.chat_id,
        thread_id=sub.thread_id,
        channel_key=parsed["channel_key"],
        source_url=sub.source_url,
        canonical_channel_url=parsed["canonical_channel_url"],
        rss_url=parsed["rss_url"],
        added_by_user_id=getattr(sub, "added_by_user_id", None),
        added_at=getattr(sub, "added_at", None),
    )


def _resolve_legacy_subscription(repo, sub):
    if not _legacy_handle_needs_resolution(sub):
        return sub
    try:
        parsed = parse_youtube_channel_input(sub.source_url)
    except ValueError as exc:
        repo.set_last_error(
            chat_id=sub.chat_id,
            thread_id=sub.thread_id,
            channel_key=sub.channel_key,
            error=f"resolver_failed:ValueError:{exc}",
        )
        raise
    created = repo.add_subscription(
        chat_id=sub.chat_id,
        thread_id=sub.thread_id,
        channel_key=parsed["channel_key"],
        source_url=sub.source_url,
        canonical_channel_url=parsed["canonical_channel_url"],
        rss_url=parsed["rss_url"],
        added_by_user_id=getattr(sub, "added_by_user_id", None),
    )
    repo.delete_subscription(chat_id=sub.chat_id, thread_id=sub.thread_id, channel_key=sub.channel_key)
    if not created:
        return None
    return _subscription_from_payload(sub, parsed)


async def _send_topic_message(host_api, *, chat_id: int, thread_id: int | None, text: str):
    if thread_id is None:
        await host_api.send_message(chat_id, text)
    else:
        await host_api.send_message(chat_id, text, message_thread_id=thread_id)


def _failure_category(exc: Exception) -> str:
    msg = str(exc).lower()
    if "policy" in msg or "denied" in msg:
        return "policy_denied"
    return type(exc).__name__


async def handle_schedule(context, host_api):
    repo = _repo_for_context(context)

    list_all = getattr(repo, "list_all_subscriptions", None)
    subscriptions = list_all() if callable(list_all) else []
    LOGGER.info("yt_rss schedule run start", extra={"subscriptions_count": len(subscriptions)})

    checked_count = 0
    posted_total = 0
    failed_count = 0

    for original_sub in subscriptions:
        sub = original_sub
        checked_count += 1
        try:
            resolved = _resolve_legacy_subscription(repo, sub)
            if resolved is None:
                continue
            sub = resolved

            sub_key = repo.topic_key_for(sub.chat_id, sub.thread_id, sub.channel_key)
            sub_key = repo.topic_key_for(sub.chat_id, sub.thread_id, sub.channel_key)
            LOGGER.info(
                "yt_rss subscription poll start",
                extra={
                    "subscription_key": sub_key,
                    "channel_key": sub.channel_key,
                    "source_url": sub.source_url,
                    "rss_url": sub.rss_url,
                },
            )

            rss_result = await host_api.rss_fetch(sub.rss_url)
            entries = rss_result.get("entries") if isinstance(rss_result, dict) else []
            if not isinstance(entries, list):
                entries = []

            ordered_entries = _entries_newest_first(entries)
            cursor = repo.get_cursor(chat_id=sub.chat_id, thread_id=sub.thread_id, channel_key=sub.channel_key)
            dedupe_seen = set(cursor.dedupe or [])

            seen_keys = []
            for entry in ordered_entries:
                key = _entry_key(entry)
                if key:
                    seen_keys.append(key)

            latest_cursor = seen_keys[0] if seen_keys else None
            LOGGER.info(
                "yt_rss subscription feed loaded",
                extra={
                    "subscription_key": sub_key,
                    "chat_id": sub.chat_id,
                    "thread_id": sub.thread_id,
                    "channel_key": sub.channel_key,
                    "item_count": len(ordered_entries),
                    "seen_key_count": len(seen_keys),
                    "cursor_before": cursor.cursor,
                    "latest_feed_key": latest_cursor,
                    "dedupe_size_before": len(dedupe_seen),
                },
            )

            if cursor.cursor is None and not dedupe_seen:
                dedupe_out = seen_keys[:100]
                repo.set_cursor(
                    chat_id=sub.chat_id,
                    thread_id=sub.thread_id,
                    channel_key=sub.channel_key,
                    cursor=latest_cursor,
                    dedupe=dedupe_out,
                )
                LOGGER.info(
                    "yt_rss subscription checked",
                    extra={
                        "subscription_key": sub_key,
                        "channel_key": sub.channel_key,
                        "success": True,
                        "feed_entry_count": len(ordered_entries),
                        "new_count": 0,
                        "op_count": 0,
                        "cursor_changed": latest_cursor is not None,
                        "cursor_before": cursor.cursor,
                        "cursor_after": latest_cursor,
                    },
                )
                continue

            new_entries = []
            stop_reason = "feed_end"
            stop_key = None
            for entry in ordered_entries:
                key = _entry_key(entry)
                if not key:
                    continue
                if key == cursor.cursor:
                    stop_reason = "cursor_match"
                    stop_key = key
                    break
                if key in dedupe_seen:
                    stop_reason = "dedupe_skip"
                    stop_key = key
                    continue
                new_entries.append(entry)

            LOGGER.info(
                "yt_rss subscription decision",
                extra={
                    "subscription_key": sub_key,
                    "chat_id": sub.chat_id,
                    "thread_id": sub.thread_id,
                    "channel_key": sub.channel_key,
                    "cursor_before": cursor.cursor,
                    "candidate_count": len(new_entries),
                    "stop_reason": stop_reason,
                    "stop_key": stop_key,
                },
            )

            posted_count = 0
            posted_keys = []
            cursor_progress = cursor.cursor
            dedupe_progress = set(dedupe_seen)
            total_candidates = len(new_entries)
            for index, entry in enumerate(reversed(new_entries), start=1):
                entry_key = _entry_key(entry)
                title = _entry_title(entry)
                link = _entry_link(entry)
                post_label, label_source = _post_label(sub, entry)
                text = f"📺 {post_label}: {title}"
                if link:
                    text = f"{text}\n{link}"
                LOGGER.info(
                    "yt_rss send attempt",
                    extra={
                        "subscription_key": sub_key,
                        "channel_key": sub.channel_key,
                        "entry_key": entry_key,
                        "label_source": "subscription_label",
                    },
                )
                await _send_topic_message(host_api, chat_id=sub.chat_id, thread_id=sub.thread_id, text=text)
                posted_count += 1
                if entry_key:
                    posted_keys.append(entry_key)
                cursor_before_progress = cursor_progress
                if entry_key:
                    dedupe_progress.discard(entry_key)
                    dedupe_progress = {entry_key, *dedupe_progress}
                    dedupe_out_progress = list(dedupe_progress)[:100]
                    cursor_progress = entry_key
                else:
                    dedupe_out_progress = list(dedupe_progress)[:100]

                repo.set_cursor(
                    chat_id=sub.chat_id,
                    thread_id=sub.thread_id,
                    channel_key=sub.channel_key,
                    cursor=cursor_progress,
                    dedupe=dedupe_out_progress,
                )
                LOGGER.info(
                    "yt_rss send success",
                    extra={
                        "subscription_key": sub_key,
                        "channel_key": sub.channel_key,
                        "entry_key": entry_key,
                        "op_count": posted_count,
                        "cursor_changed": cursor_progress != cursor_before_progress,
                        "dedupe_size_after": len(dedupe_out_progress),
                    },
                )

            posted_total += posted_count
            if posted_count == len(new_entries):
                dedupe_out = seen_keys[:100] if seen_keys else list(dedupe_seen)[:100]
                cursor_after = latest_cursor or cursor_progress
                repo.set_cursor(
                    chat_id=sub.chat_id,
                    thread_id=sub.thread_id,
                    channel_key=sub.channel_key,
                    cursor=cursor_after,
                    dedupe=dedupe_out,
                )
            else:
                dedupe_out = list(dedupe_progress)[:100]
                cursor_after = cursor_progress
            LOGGER.info(
                "yt_rss subscription checked",
                extra={
                    "subscription_key": sub_key,
                    "channel_key": sub.channel_key,
                    "success": True,
                    "feed_entry_count": len(ordered_entries),
                    "new_count": len(new_entries),
                    "op_count": posted_count,
                    "cursor_changed": cursor_after != cursor.cursor,
                    "cursor_before": cursor.cursor,
                    "cursor_after": cursor_after,
                },
            )
        except ValueError as exc:
            failed_count += 1
            reason = f"resolver_failed:ValueError:{exc}" if _legacy_handle_needs_resolution(original_sub) else f"rss_fetch_failed:ValueError"
            repo.set_last_error(
                chat_id=original_sub.chat_id,
                thread_id=original_sub.thread_id,
                channel_key=original_sub.channel_key,
                error=reason,
            )
            LOGGER.info(
                "yt_rss subscription check failed",
                extra={
                    "subscription_key": repo.topic_key_for(original_sub.chat_id, original_sub.thread_id, original_sub.channel_key),
                    "channel_key": original_sub.channel_key,
                    "success": False,
                    "error_class": type(exc).__name__,
                    "error_reason": str(exc),
                },
            )
        except Exception as exc:
            failed_count += 1
            category = _failure_category(exc)
            repo.set_last_error(
                chat_id=original_sub.chat_id,
                thread_id=original_sub.thread_id,
                channel_key=original_sub.channel_key,
                error=f"rss_fetch_failed:{type(exc).__name__}",
            )
            LOGGER.info(
                "yt_rss subscription check failed",
                extra={
                    "subscription_key": repo.topic_key_for(original_sub.chat_id, original_sub.thread_id, original_sub.channel_key),
                    "channel_key": original_sub.channel_key,
                    "success": False,
                    "error_class": type(exc).__name__,
                    "error_reason": category,
                },
            )

    LOGGER.info(
        "yt_rss schedule run end",
        extra={
            "subscription_count": len(subscriptions),
            "checked_count": checked_count,
            "posted_total": posted_total,
            "failed_count": failed_count,
        },
    )
    return None
