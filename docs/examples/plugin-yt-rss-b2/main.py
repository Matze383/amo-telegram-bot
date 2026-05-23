from pathlib import Path
from urllib.parse import urlparse
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


def _normalize_channel_url(raw: str) -> str:
    candidate = (raw or "").strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid_url")
    if "youtube.com" not in parsed.netloc and "youtu.be" not in parsed.netloc:
        raise ValueError("unsupported_host")
    return candidate


def _extract_channel_key(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    path = (parsed.path or "").strip("/")
    if not path:
        raise ValueError("missing_channel_path")
    return path.replace("/", "_").lower()


async def handle_command(context, host_api):
    repo = _repo_for_context(context)
    command = (context.command_name or "").strip().lower()

    if command == "addyt":
        arg = (context.argument or "").strip()
        if not arg:
            await host_api.reply(context.chat_id, context.message_id, "B2 skeleton: usage /addYT <youtube-channel-url>")
            return
        try:
            normalized = _normalize_channel_url(arg)
            channel_key = _extract_channel_key(normalized)
        except ValueError:
            await host_api.reply(
                context.chat_id,
                context.message_id,
                "B2 skeleton: only direct YouTube channel URLs are accepted. Resolver comes in B3.",
            )
            return
        created = repo.add_subscription(
            chat_id=context.chat_id,
            thread_id=context.message_thread_id,
            channel_key=channel_key,
            source_url=normalized,
        )
        if created:
            await host_api.reply(context.chat_id, context.message_id, f"Added (skeleton): {channel_key}")
        else:
            await host_api.reply(context.chat_id, context.message_id, f"Already subscribed (skeleton): {channel_key}")
        return

    if command == "delyt":
        arg = (context.argument or "").strip()
        if not arg:
            await host_api.reply(context.chat_id, context.message_id, "B2 skeleton: usage /delYT <youtube-channel-url>")
            return
        try:
            normalized = _normalize_channel_url(arg)
            channel_key = _extract_channel_key(normalized)
        except ValueError:
            await host_api.reply(
                context.chat_id,
                context.message_id,
                "B2 skeleton: only direct YouTube channel URLs are accepted. Resolver comes in B3.",
            )
            return
        deleted = repo.delete_subscription(
            chat_id=context.chat_id,
            thread_id=context.message_thread_id,
            channel_key=channel_key,
        )
        if deleted:
            await host_api.reply(context.chat_id, context.message_id, f"Removed (skeleton): {channel_key}")
        else:
            await host_api.reply(context.chat_id, context.message_id, f"Not found (skeleton): {channel_key}")
        return

    await host_api.reply(context.chat_id, context.message_id, "B2 skeleton: command not implemented")


async def handle_schedule(context, host_api):
    # B2 scope guard: no real polling/send implementation yet.
    return None
