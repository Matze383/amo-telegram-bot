from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "docs" / "examples" / "plugin-yt-rss-b2"


def _load_module(module_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_plugin_main():
    return _load_module(PLUGIN_DIR / "main.py", "yt_rss_plugin_main")


def _load_repo_module():
    return _load_module(PLUGIN_DIR / "repository.py", "yt_rss_plugin_repository")


def test_yt_rss_manifest_has_expected_skeleton_contract() -> None:
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))

    assert manifest["name"] == "yt_rss"
    assert sorted(manifest["commands"]) == ["addYT", "delYT"]
    assert manifest["schedule"]["interval_seconds"] == 300
    assert "rss.fetch" in manifest["required_permissions"]


def test_state_repository_add_delete_duplicate_and_topic_isolation(tmp_path) -> None:
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")

    assert repo.add_subscription(
        chat_id=1,
        thread_id=None,
        channel_key="@alpha",
        source_url="https://www.youtube.com/@Alpha",
    )
    assert not repo.add_subscription(
        chat_id=1,
        thread_id=None,
        channel_key="@alpha",
        source_url="https://www.youtube.com/@Alpha",
    )

    assert repo.add_subscription(
        chat_id=1,
        thread_id=22,
        channel_key="@alpha",
        source_url="https://www.youtube.com/@Alpha",
    )
    assert repo.add_subscription(
        chat_id=2,
        thread_id=None,
        channel_key="@alpha",
        source_url="https://www.youtube.com/@Alpha",
    )

    root_items = repo.list_subscriptions(chat_id=1, thread_id=None)
    topic_items = repo.list_subscriptions(chat_id=1, thread_id=22)
    other_chat_items = repo.list_subscriptions(chat_id=2, thread_id=None)

    assert [item.channel_key for item in root_items] == ["@alpha"]
    assert [item.channel_key for item in topic_items] == ["@alpha"]
    assert [item.channel_key for item in other_chat_items] == ["@alpha"]

    repo.set_cursor(chat_id=1, thread_id=None, channel_key="@alpha", cursor="etag:1", dedupe=["a", "b"])
    cursor = repo.get_cursor(chat_id=1, thread_id=None, channel_key="@alpha")
    assert cursor.cursor == "etag:1"
    assert cursor.dedupe == ["a", "b"]

    assert repo.delete_subscription(chat_id=1, thread_id=None, channel_key="@alpha")
    assert not repo.delete_subscription(chat_id=1, thread_id=None, channel_key="@alpha")


class _Context:
    def __init__(self, *, command_name: str, argument: str | None, chat_id: int = 1, message_id: int = 10, message_thread_id: int | None = None):
        self.command_name = command_name
        self.argument = argument
        self.chat_id = chat_id
        self.message_id = message_id
        self.message_thread_id = message_thread_id


class _Host:
    def __init__(self) -> None:
        self.replies: list[tuple[int, int, str]] = []

    async def reply(self, chat_id: int, message_id: int, text: str):
        self.replies.append((chat_id, message_id, text))


def test_command_handler_skeleton_responses_and_routing(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    test_repo = repo_module.YtRssStateRepository(tmp_path / "state")

    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: test_repo)

    host = _Host()
    asyncio.run(plugin_main.handle_command(_Context(command_name="addYT", argument=None), host))
    assert "usage /addYT" in host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(_Context(command_name="addYT", argument="not-a-url"), host))
    assert "Resolver comes in B3" in host.replies[-1][2]

    asyncio.run(
        plugin_main.handle_command(
            _Context(command_name="addYT", argument="https://www.youtube.com/@OpenAI"),
            host,
        )
    )
    assert "Added (skeleton)" in host.replies[-1][2]

    asyncio.run(
        plugin_main.handle_command(
            _Context(command_name="addYT", argument="https://www.youtube.com/@OpenAI"),
            host,
        )
    )
    assert "Already subscribed" in host.replies[-1][2]

    asyncio.run(
        plugin_main.handle_command(
            _Context(command_name="delYT", argument="https://www.youtube.com/@OpenAI"),
            host,
        )
    )
    assert "Removed (skeleton)" in host.replies[-1][2]

    asyncio.run(
        plugin_main.handle_command(
            _Context(command_name="unknown", argument="x"),
            host,
        )
    )
    assert "command not implemented" in host.replies[-1][2]
