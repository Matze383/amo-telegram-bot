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
        channel_key="UCabc",
        source_url="https://www.youtube.com/channel/UCabc",
        canonical_channel_url="https://www.youtube.com/channel/UCabc",
        rss_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCabc",
        added_by_user_id=7,
    )
    assert not repo.add_subscription(
        chat_id=1,
        thread_id=None,
        channel_key="UCabc",
        source_url="https://www.youtube.com/channel/UCabc",
        canonical_channel_url="https://www.youtube.com/channel/UCabc",
        rss_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCabc",
        added_by_user_id=7,
    )

    assert repo.add_subscription(
        chat_id=1,
        thread_id=22,
        channel_key="UCabc",
        source_url="https://www.youtube.com/channel/UCabc",
        canonical_channel_url="https://www.youtube.com/channel/UCabc",
        rss_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCabc",
        added_by_user_id=7,
    )

    root_items = repo.list_subscriptions(chat_id=1, thread_id=None)
    topic_items = repo.list_subscriptions(chat_id=1, thread_id=22)

    assert [item.channel_key for item in root_items] == ["UCabc"]
    assert [item.channel_key for item in topic_items] == ["UCabc"]

    assert repo.delete_subscription(chat_id=1, thread_id=None, channel_key="UCabc")
    assert not repo.delete_subscription(chat_id=1, thread_id=None, channel_key="UCabc")


def test_parser_accepts_direct_channel_urls_and_uc_id() -> None:
    plugin_main = _load_plugin_main()

    parsed = plugin_main.parse_youtube_channel_input("https://www.youtube.com/channel/UC123")
    assert parsed["channel_key"] == "UC123"
    assert parsed["canonical_channel_url"] == "https://www.youtube.com/channel/UC123"
    assert parsed["rss_url"] == "https://www.youtube.com/feeds/videos.xml?channel_id=UC123"

    parsed2 = plugin_main.parse_youtube_channel_input("https://youtube.com/channel/UCXYZ")
    assert parsed2["channel_key"] == "UCXYZ"

    parsed3 = plugin_main.parse_youtube_channel_input("UCraw")
    assert parsed3["channel_key"] == "UCraw"


def test_parser_rejects_unsupported_inputs() -> None:
    plugin_main = _load_plugin_main()

    for value in [
        "https://youtu.be/abc",
        "https://www.youtube.com/watch?v=abc",
        "https://www.youtube.com/playlist?list=abc",
        "https://www.youtube.com/shorts/abc",
    ]:
        try:
            plugin_main.parse_youtube_channel_input(value)
            assert False, f"expected ValueError for {value}"
        except ValueError:
            pass


def test_parser_resolves_handle_c_and_user_inputs_with_fake_http() -> None:
    plugin_main = _load_plugin_main()

    def fake_http(url: str):
        if "/@handle" in url:
            return 200, '<script>ytcfg.set({"CHANNEL_ID":"UC_HANDLE_123"});</script>'
        if "/c/name" in url:
            return 200, '"canonicalBaseUrl":"/channel/UC_CANON_456"'
        if "/user/name" in url:
            return 200, '<meta itemprop="channelId" content="UC_USER_789">'
        raise AssertionError(f"unexpected url {url}")

    out1 = plugin_main.resolve_youtube_channel_input("https://www.youtube.com/@handle", http_get_text=fake_http)
    out2 = plugin_main.resolve_youtube_channel_input("https://www.youtube.com/c/name", http_get_text=fake_http)
    out3 = plugin_main.resolve_youtube_channel_input("https://www.youtube.com/user/name", http_get_text=fake_http)
    out4 = plugin_main.resolve_youtube_channel_input("@handle", http_get_text=fake_http)

    assert out1["channel_key"] == "UC_HANDLE_123"
    assert out2["channel_key"] == "UC_CANON_456"
    assert out3["channel_key"] == "UC_USER_789"
    assert out4["channel_key"] == "UC_HANDLE_123"


def test_resolver_failures_no_channel_id_non_200_network_error() -> None:
    plugin_main = _load_plugin_main()

    def no_channel(_url: str):
        return 200, "<html>nothing useful</html>"

    def non_200(_url: str):
        return 404, "missing"

    def network_error(_url: str):
        raise OSError("boom")

    for fn, code in [
        (no_channel, "resolver_no_channel_id"),
        (non_200, "resolver_http_error"),
        (network_error, "resolver_network_error"),
    ]:
        try:
            plugin_main.resolve_youtube_channel_input("https://www.youtube.com/@handle", http_get_text=fn)
            assert False, "expected ValueError"
        except ValueError as exc:
            assert str(exc) == code


class _Context:
    def __init__(
        self,
        *,
        command_name: str,
        argument: str | None,
        chat_id: int = 1,
        message_id: int = 10,
        message_thread_id: int | None = None,
        user_id: int = 99,
        user_is_admin: bool = True,
        user_is_owner: bool = False,
    ):
        self.command_name = command_name
        self.argument = argument
        self.chat_id = chat_id
        self.message_id = message_id
        self.message_thread_id = message_thread_id
        self.user_id = user_id
        self.user_is_admin = user_is_admin
        self.user_is_owner = user_is_owner


class _Host:
    def __init__(self) -> None:
        self.replies: list[tuple[int, int, str]] = []

    async def reply(self, chat_id: int, message_id: int, text: str):
        self.replies.append((chat_id, message_id, text))


def test_command_add_delete_duplicate_and_topic_isolation(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    test_repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: test_repo)

    host = _Host()

    ctx_t1 = _Context(command_name="addYT", argument="https://www.youtube.com/channel/UC111", message_thread_id=1)
    asyncio.run(plugin_main.handle_command(ctx_t1, host))
    assert "Added channel UC111" in host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(ctx_t1, host))
    assert "Already subscribed in this topic" in host.replies[-1][2]

    ctx_t2 = _Context(command_name="addYT", argument="https://www.youtube.com/channel/UC111", message_thread_id=2)
    asyncio.run(plugin_main.handle_command(ctx_t2, host))
    assert "Added channel UC111" in host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(_Context(command_name="delYT", argument="UC111", message_thread_id=1), host))
    assert "Removed channel UC111 from this topic." == host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(_Context(command_name="delYT", argument="UC111", message_thread_id=1), host))
    assert "No matching subscription found in this topic." == host.replies[-1][2]

    remaining_t2 = test_repo.list_subscriptions(chat_id=1, thread_id=2)
    assert [x.channel_key for x in remaining_t2] == ["UC111"]


def test_command_add_delete_resolved_inputs_and_failures(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    test_repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: test_repo)

    def fake_http(url: str):
        mapping = {
            "https://www.youtube.com/@handle": (200, '"channelId":"UCRES1"'),
            "https://www.youtube.com/c/name": (200, '"channelId":"UCRES2"'),
            "https://www.youtube.com/user/name": (200, '"channelId":"UCRES3"'),
            "https://www.youtube.com/@bad": (200, "no id here"),
        }
        return mapping[url]

    monkeypatch.setattr(plugin_main, "_http_get_text", fake_http)

    host = _Host()
    asyncio.run(plugin_main.handle_command(_Context(command_name="addYT", argument="https://www.youtube.com/@handle", message_thread_id=10), host))
    assert "Added channel UCRES1" in host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(_Context(command_name="addYT", argument="https://www.youtube.com/c/name", message_thread_id=10), host))
    assert "Added channel UCRES2" in host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(_Context(command_name="addYT", argument="https://www.youtube.com/user/name", message_thread_id=11), host))
    assert "Added channel UCRES3" in host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(_Context(command_name="delYT", argument="https://www.youtube.com/@handle", message_thread_id=10), host))
    assert host.replies[-1][2] == "Removed channel UCRES1 from this topic."

    asyncio.run(plugin_main.handle_command(_Context(command_name="delYT", argument="https://www.youtube.com/@handle", message_thread_id=11), host))
    assert host.replies[-1][2] == "No matching subscription found in this topic."

    asyncio.run(plugin_main.handle_command(_Context(command_name="addYT", argument="https://www.youtube.com/@bad", message_thread_id=10), host))
    assert "Could not resolve this YouTube channel URL to a channel id." == host.replies[-1][2]


def test_command_permission_denied_and_invalid_url(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    test_repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: test_repo)
    host = _Host()

    denied = _Context(command_name="addYT", argument="https://www.youtube.com/channel/UC1", user_is_admin=False, user_is_owner=False)
    asyncio.run(plugin_main.handle_command(denied, host))
    assert "Permission denied" in host.replies[-1][2]

    bad = _Context(command_name="addYT", argument="notaurl")
    asyncio.run(plugin_main.handle_command(bad, host))
    assert "Invalid URL." in host.replies[-1][2]
