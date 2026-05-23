from pathlib import Path
from urllib.parse import parse_qs, urlparse
import importlib.util


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


def parse_youtube_channel_input(raw: str) -> dict[str, str]:
    candidate = (raw or "").strip()
    if not candidate:
        raise ValueError("missing_input")

    if candidate.upper().startswith("UC"):
        uc_id = candidate.strip()
        if not uc_id.startswith("UC"):
            raise ValueError("unsupported_channel_id")
        return {
            "channel_key": uc_id,
            "canonical_channel_url": f"https://www.youtube.com/channel/{uc_id}",
            "rss_url": f"https://www.youtube.com/feeds/videos.xml?channel_id={uc_id}",
        }

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid_url")

    host = _normalize_host(parsed.netloc)
    if host not in {"youtube.com", "www.youtube.com"}:
        if host == "youtu.be":
            raise ValueError("unsupported_video_url")
        raise ValueError("unsupported_host")

    path = (parsed.path or "").strip("/")
    segments = [seg for seg in path.split("/") if seg]
    if len(segments) >= 2 and segments[0] == "channel":
        uc_id = segments[1]
        if not uc_id.startswith("UC"):
            raise ValueError("unsupported_channel_id")
        return {
            "channel_key": uc_id,
            "canonical_channel_url": f"https://www.youtube.com/channel/{uc_id}",
            "rss_url": f"https://www.youtube.com/feeds/videos.xml?channel_id={uc_id}",
        }

    first = segments[0] if segments else ""
    if first in {"watch", "playlist", "shorts"}:
        raise ValueError("unsupported_video_url")
    if first.startswith("@") or first in {"c", "user"}:
        raise ValueError("unsupported_vanity_url")
    raise ValueError("unsupported_channel_url")


def _format_parse_error(code: str) -> str:
    if code == "missing_input":
        return "Usage: /addYT <https://www.youtube.com/channel/UC...>"
    if code == "invalid_url":
        return "Invalid URL. Use direct channel URLs: https://www.youtube.com/channel/UC..."
    if code in {"unsupported_video_url", "unsupported_channel_url"}:
        return "Unsupported YouTube URL. Please provide a channel URL: https://www.youtube.com/channel/UC..."
    if code == "unsupported_vanity_url":
        return "Handle/vanity URLs are not supported yet. Use https://www.youtube.com/channel/UC..."
    if code == "unsupported_channel_id":
        return "Unsupported channel id format. Use a UC... channel URL."
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
