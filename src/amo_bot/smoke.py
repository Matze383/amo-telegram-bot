from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from amo_bot.config.settings import Settings
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.telegram.commands import create_builtin_registry
from amo_bot.telegram.dispatcher import Dispatcher
from amo_bot.telegram.role_resolver import DBRoleResolver


@dataclass(slots=True)
class SmokeResult:
    sent: list[tuple[int, str]]


class _FakeAIService:
    async def ask(self, prompt: str) -> str:
        return f"smoke-ai:{prompt.strip()}"


class _CaptureSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_text(self, chat_id: int, text: str) -> object:
        self.sent.append((chat_id, text))
        return {"ok": True}


def _fake_update(*, update_id: int, user_id: int, chat_id: int, text: str) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 100,
            "from": {"id": user_id, "is_bot": False, "first_name": "Smoke", "username": "smoke"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


async def run_smoke(settings: Settings) -> SmokeResult:
    init_db(settings.database_url)

    sender = _CaptureSender()
    registry = create_builtin_registry(database_url=settings.database_url, ai_service=_FakeAIService())
    dispatcher = Dispatcher(
        command_registry=registry,
        role_resolver=DBRoleResolver(create_session_factory(settings.database_url)),
        send_text=sender.send_text,
        bot_username=settings.bot_username,
    )

    for idx, cmd in enumerate(("/ping", "/help", "/role"), start=1):
        await dispatcher.handle_raw_update(_fake_update(update_id=idx, user_id=999001, chat_id=999001, text=cmd))

    return SmokeResult(sent=sender.sent)


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    parsed = urlparse(database_url)
    if parsed.scheme != "sqlite":
        return

    # sqlite:///:memory: and sqlite+pysqlite:///:memory:
    if parsed.path in {":memory:", "/:memory:"}:
        return

    raw_path = unquote(parsed.path or "")
    if not raw_path:
        return

    # SQLAlchemy URL forms:
    # - sqlite:///relative/path.db   -> parsed.path = "/relative/path.db" (actually relative)
    # - sqlite:////absolute/path.db  -> parsed.path = "//absolute/path.db"
    is_absolute_sqlite_path = raw_path.startswith("//")
    normalized_path = raw_path[1:] if raw_path.startswith("/") and not is_absolute_sqlite_path else raw_path

    db_path = Path(normalized_path)
    if is_absolute_sqlite_path:
        target = db_path
    else:
        target = Path.cwd() / db_path

    target.parent.mkdir(parents=True, exist_ok=True)


def _build_cli_settings(database_url: str, plugin_dir: str) -> Settings:
    return Settings(
        BOT_TOKEN="smoke-token",
        BOT_USERNAME="SmokeBot",
        WEBUI_PASSWORD="smoke-password",
        DATABASE_URL=database_url,
        AMO_PLUGIN_DIR=plugin_dir,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Local runtime smoke without real Telegram/Ollama calls")
    parser.add_argument("--db", default="sqlite:///./data/smoke.db", help="SQLAlchemy DATABASE_URL")
    parser.add_argument("--plugin-dir", default="./plugins", help="Plugin directory")
    args = parser.parse_args()

    _ensure_sqlite_parent_dir(args.db)
    settings = _build_cli_settings(database_url=args.db, plugin_dir=args.plugin_dir)
    result = asyncio.run(run_smoke(settings))
    print(json.dumps({"ok": True, "sent": result.sent}, ensure_ascii=False))


if __name__ == "__main__":
    main()
