from pathlib import Path
from urllib.parse import urlparse
import importlib.util
import json
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


def _repo_for_context(context) -> YtRssStateRepository:
    base_dir = Path("data") / "plugin_state" / "yt_rss"
    return YtRssStateRepository(base_dir)


def _normalize_host(host: str) -> str:
    return (host or "").strip().lower().split(":", 1)[0]


_UC_ID_RE = re.compile(r"\bUC[0-9A-Za-z_-]{2,}\b")


def _build_channel_payload(uc_id: str) -> dict[str, str]:
    return {
        "channel_key": uc_id,
        "canonical_channel_url": f"https://www.youtube.com/channel/{uc_id}",
        "rss_url": f"https://www.youtube.com/feeds/videos.xml?channel_id={uc_id}",
    }


def _extract_uc_id_from_text(body: str) -> str | None:
    if not body:
        return None

    for marker in [
        '"channelId":"',
        '"externalId":"',
        '"browseId":"',
        '/channel/',
        '"canonicalBaseUrl":"/channel/',
        '"urlCanonical":"https://www.youtube.com/channel/',
    ]:
        start = 0
        while True:
            idx = body.find(marker, start)
            if idx < 0:
                break
            seg = body[idx + len(marker) : idx + len(marker) + 64]
            m = _UC_ID_RE.search(seg)
            if m:
                return m.group(0)
            start = idx + 1

    m = _UC_ID_RE.search(body)
    return m.group(0) if m else None


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
        return "Usage: /addYT <https://www.youtube.com/channel/UC...>"
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


def _user_can_manage(context) -> bool:
    # Adapter for runtime field variance:
    # supports owner/admin flags in either context.user_* or top-level helpers.
    if bool(getattr(context, "is_owner", False)):
        return True
    if bool(getattr(context, "is_admin", False)):
        return True
    if bool(getattr(context, "user_is_owner", False)):
        return True
    if bool(getattr(context, "user_is_admin", False)):
        return True
    user_role = str(getattr(context, "user_role", "")).lower()
    return user_role in {"owner", "admin"}


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


async def handle_schedule(context, host_api):
    # B2/B3 scope guard: no real polling/send implementation yet.
    return None
