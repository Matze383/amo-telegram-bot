from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
from pathlib import Path

import yaml
import pytest


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
    assert sorted(manifest["commands"]) == ["addyt", "delyt"]
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


def test_resolver_ignores_unrelated_uc_ids_on_handle_page() -> None:
    plugin_main = _load_plugin_main()

    expected = "UCKREIS123456"
    unrelated = "UCOTyb6e_v9cz2Bu7VzFW7tg"

    html = (
        "<html><head>"
        '<link rel="canonical" href="https://www.youtube.com/@kreisverkehr">'
        "</head><body>"
        f'<script>var junk = {{"browseId":"{unrelated}"}};</script>'
        f'<script>ytcfg.set({{"CHANNEL_ID":"{expected}"}});</script>'
        "</body></html>"
    )

    def fake_http(_url: str):
        return 200, html

    out = plugin_main.resolve_youtube_channel_input(
        "https://youtube.com/@kreisverkehr?si=DhDHWMwe3ZvS6wdL",
        http_get_text=fake_http,
    )
    assert out["channel_key"] == expected

def test_resolver_prefers_canonical_channel_over_unrelated_page_uc_fallback() -> None:
    plugin_main = _load_plugin_main()

    expected = "UCREALCANON123"
    unrelated = "UCOTyb6e_v9cz2Bu7VzFW7tg"

    html = (
        "<html><head>"
        '<link rel="canonical" href="https://www.youtube.com/@kreisverkehr">'
        "</head><body>"
        f'<script>var nav = {{"browseId":"{unrelated}"}};</script>'
        f'<meta itemprop="channelId" content="{expected}">'
        "</body></html>"
    )

    def fake_http(_url: str):
        return 200, html

    out = plugin_main.resolve_youtube_channel_input(
        "https://youtube.com/@kreisverkehr?si=DhDHWMwe3ZvS6wdL",
        http_get_text=fake_http,
    )
    assert out["channel_key"] == expected


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
        role=None,
    ):
        self.command_name = command_name
        self.argument = argument
        self.chat_id = chat_id
        self.message_id = message_id
        self.message_thread_id = message_thread_id
        self.user_id = user_id
        self.user_is_admin = user_is_admin
        self.user_is_owner = user_is_owner
        self.role = role


class _Host:
    def __init__(self) -> None:
        self.replies: list[tuple[int, int, str]] = []
        self.sent: list[tuple[int, int | None, str]] = []
        self.feed_map: dict[str, object] = {}
        self.callback_answers: list[tuple[object, str]] = []

    async def reply(self, chat_id: int, message_id: int, text: str):
        self.replies.append((chat_id, message_id, text))

    async def send_message(self, chat_id: int, text: str, message_thread_id: int | None = None):
        self.sent.append((chat_id, message_thread_id, text))

    async def answer_callback(self, callback_query_id, text: str):
        self.callback_answers.append((callback_query_id, text))

    async def answer_callback_query(self, callback_query_id, text: str | None = None):
        self.callback_answers.append((callback_query_id, text or ""))

    async def rss_fetch(self, rss_url: str):
        value = self.feed_map[rss_url]
        if isinstance(value, Exception):
            raise value
        return value


def test_command_add_delete_duplicate_and_topic_isolation(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    test_repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: test_repo)

    host = _Host()

    ctx_t1 = _Context(command_name="addyt", argument="https://www.youtube.com/channel/UC111", message_thread_id=1)
    asyncio.run(plugin_main.handle_command(ctx_t1, host))
    assert "Added channel UC111" in host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(ctx_t1, host))
    assert "Already subscribed in this topic" in host.replies[-1][2]

    ctx_t2 = _Context(command_name="addyt", argument="https://www.youtube.com/channel/UC111", message_thread_id=2)
    asyncio.run(plugin_main.handle_command(ctx_t2, host))
    assert "Added channel UC111" in host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(_Context(command_name="delyt", argument="UC111", message_thread_id=1), host))
    assert "Removed channel UC111 from this topic." == host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(_Context(command_name="delyt", argument="UC111", message_thread_id=1), host))
    assert "No matching subscription found in this topic." == host.replies[-1][2]

    remaining_t2 = test_repo.list_subscriptions(chat_id=1, thread_id=2)
    assert [x.channel_key for x in remaining_t2] == ["UC111"]


def test_command_delyt_without_argument_lists_topic_subscriptions(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    test_repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: test_repo)

    test_repo.add_subscription(
        chat_id=-100,
        thread_id=10,
        channel_key="UC111",
        source_url="https://www.youtube.com/@one",
        canonical_channel_url="https://www.youtube.com/channel/UC111",
        rss_url="rss://one",
        added_by_user_id=99,
    )
    test_repo.add_subscription(
        chat_id=-100,
        thread_id=10,
        channel_key="UC222",
        source_url="https://www.youtube.com/channel/UC222",
        canonical_channel_url="https://www.youtube.com/channel/UC222",
        rss_url="rss://two",
        added_by_user_id=99,
    )
    test_repo.add_subscription(
        chat_id=-100,
        thread_id=11,
        channel_key="UC333",
        source_url="https://www.youtube.com/channel/UC333",
        canonical_channel_url="https://www.youtube.com/channel/UC333",
        rss_url="rss://three",
        added_by_user_id=99,
    )

    host = _Host()
    ctx = _Context(command_name="delyt", argument=None, chat_id=-100, message_thread_id=10)
    asyncio.run(plugin_main.handle_command(ctx, host))

    payload = host.replies[-1][2]
    assert isinstance(payload, dict)
    assert payload["text"] == "Welchen YouTube-RSS-Kanal moechtest du aus diesem Topic entfernen?"
    rows = payload["reply_markup"]["inline_keyboard"]
    assert rows == [
        [{"text": "Entfernen: one", "callback_data": "yt_rss:delyt:UC111"}],
        [{"text": "Entfernen: UC222", "callback_data": "yt_rss:delyt:UC222"}],
    ]


def test_command_delyt_without_argument_reports_empty_topic(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    test_repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: test_repo)

    host = _Host()
    ctx = _Context(command_name="delyt", argument="", chat_id=-100, message_thread_id=10)
    asyncio.run(plugin_main.handle_command(ctx, host))

    assert host.replies[-1][2] == "No active YouTube-RSS subscriptions found in this topic."


def test_delyt_callback_removes_subscription_only_from_current_topic(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    test_repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: test_repo)

    for thread_id in (10, 11):
        test_repo.add_subscription(
            chat_id=-100,
            thread_id=thread_id,
            channel_key="UC111",
            source_url="https://www.youtube.com/channel/UC111",
            canonical_channel_url="https://www.youtube.com/channel/UC111",
            rss_url=f"rss://{thread_id}",
            added_by_user_id=99,
        )

    host = _Host()
    ctx = _Context(command_name="callback", argument=None, chat_id=-100, message_id=55, message_thread_id=10)
    ctx.callback_data = "yt_rss:delyt:UC111"
    ctx.callback_query_id = "cb-1"
    handled = asyncio.run(plugin_main.handle_callback(ctx, host))

    assert handled is True
    assert host.callback_answers == [("cb-1", "Subscription removed")]
    assert host.replies[-1][2] == "Removed channel UC111 from this topic."
    assert test_repo.list_subscriptions(chat_id=-100, thread_id=10) == []
    assert [item.channel_key for item in test_repo.list_subscriptions(chat_id=-100, thread_id=11)] == ["UC111"]


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
    asyncio.run(plugin_main.handle_command(_Context(command_name="addyt", argument="https://www.youtube.com/@handle", message_thread_id=10), host))
    assert "Added channel UCRES1" in host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(_Context(command_name="addyt", argument="https://www.youtube.com/c/name", message_thread_id=10), host))
    assert "Added channel UCRES2" in host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(_Context(command_name="addyt", argument="https://www.youtube.com/user/name", message_thread_id=11), host))
    assert "Added channel UCRES3" in host.replies[-1][2]

    asyncio.run(plugin_main.handle_command(_Context(command_name="delyt", argument="https://www.youtube.com/@handle", message_thread_id=10), host))
    assert host.replies[-1][2] == "Removed channel UCRES1 from this topic."

    asyncio.run(plugin_main.handle_command(_Context(command_name="delyt", argument="https://www.youtube.com/@handle", message_thread_id=11), host))
    assert host.replies[-1][2] == "No matching subscription found in this topic."

    asyncio.run(plugin_main.handle_command(_Context(command_name="addyt", argument="https://www.youtube.com/@bad", message_thread_id=10), host))
    assert "Could not resolve this YouTube channel URL to a channel id." == host.replies[-1][2]


def test_command_permission_denied_and_invalid_url(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    test_repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: test_repo)
    host = _Host()

    denied = _Context(command_name="addyt", argument="https://www.youtube.com/channel/UC1", user_is_admin=False, user_is_owner=False)
    asyncio.run(plugin_main.handle_command(denied, host))
    assert "Permission denied" in host.replies[-1][2]

    bad = _Context(command_name="addyt", argument="notaurl")
    asyncio.run(plugin_main.handle_command(bad, host))
    assert "Invalid URL." in host.replies[-1][2]


def test_command_permission_allows_context_role_owner_admin(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    test_repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: test_repo)
    host = _Host()

    owner_ctx = _Context(
        command_name="addyt",
        argument="https://www.youtube.com/channel/UCOWNER1",
        user_is_admin=False,
        user_is_owner=False,
        role="owner",
    )
    asyncio.run(plugin_main.handle_command(owner_ctx, host))
    assert "Added channel UCOWNER1" in host.replies[-1][2]

    class _RoleLike:
        value = "ADMIN"

    admin_ctx = _Context(
        command_name="addyt",
        argument="https://www.youtube.com/channel/UCADMIN1",
        user_is_admin=False,
        user_is_owner=False,
        role=_RoleLike(),
    )
    asyncio.run(plugin_main.handle_command(admin_ctx, host))
    assert "Added channel UCADMIN1" in host.replies[-1][2]

    denied_ctx = _Context(
        command_name="addyt",
        argument="https://www.youtube.com/channel/UCDENIED1",
        user_is_admin=False,
        user_is_owner=False,
        role="normal",
    )
    asyncio.run(plugin_main.handle_command(denied_ctx, host))
    assert "Permission denied" in host.replies[-1][2]


def test_schedule_first_poll_baselines_without_posting(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=100,
        thread_id=200,
        channel_key="UC1",
        source_url="https://www.youtube.com/channel/UC1",
        canonical_channel_url="https://www.youtube.com/channel/UC1",
        rss_url="rss://uc1",
        added_by_user_id=1,
    )

    host = _Host()
    host.feed_map["rss://uc1"] = {
        "entries": [
            {"id": "v3", "title": "new", "link": "https://x/3"},
            {"id": "v2", "title": "mid", "link": "https://x/2"},
            {"id": "v1", "title": "old", "link": "https://x/1"},
        ]
    }

    asyncio.run(plugin_main.handle_schedule(object(), host))
    assert host.sent == []
    cursor = repo.get_cursor(chat_id=100, thread_id=200, channel_key="UC1")
    assert cursor.cursor == "v3"
    assert cursor.dedupe == ["v3", "v2", "v1"]


def test_schedule_second_poll_posts_only_new_and_correct_topic(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=100,
        thread_id=321,
        channel_key="UC1",
        source_url="https://www.youtube.com/channel/UC1",
        canonical_channel_url="https://www.youtube.com/channel/UC1",
        rss_url="rss://uc1",
        added_by_user_id=1,
    )

    host = _Host()
    host.feed_map["rss://uc1"] = {"entries": [{"id": "v2", "title": "two", "link": "https://x/2"}, {"id": "v1", "title": "one", "link": "https://x/1"}]}
    asyncio.run(plugin_main.handle_schedule(object(), host))

    host.feed_map["rss://uc1"] = {"entries": [{"id": "v3", "title": "three", "link": "https://x/3"}, {"id": "v2", "title": "two", "link": "https://x/2"}]}
    asyncio.run(plugin_main.handle_schedule(object(), host))

    assert len(host.sent) == 1
    chat_id, thread_id, text = host.sent[0]
    assert chat_id == 100
    assert thread_id == 321
    assert text.startswith("📺 UC1: three")
    assert "https://x/3" in text


def test_schedule_posts_new_entry_and_advances_cursor_even_when_feed_order_is_oldest_first(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=100,
        thread_id=321,
        channel_key="UC1",
        source_url="https://www.youtube.com/channel/UC1",
        canonical_channel_url="https://www.youtube.com/channel/UC1",
        rss_url="rss://uc1",
        added_by_user_id=1,
    )

    host = _Host()
    host.feed_map["rss://uc1"] = {
        "entries": [
            {"id": "v1", "title": "one", "link": "https://x/1", "published": "2026-05-26T09:00:00+00:00"},
            {"id": "v2", "title": "two", "link": "https://x/2", "published": "2026-05-26T09:10:00+00:00"},
        ]
    }
    asyncio.run(plugin_main.handle_schedule(object(), host))

    cursor = repo.get_cursor(chat_id=100, thread_id=321, channel_key="UC1")
    assert cursor.cursor == "v2"

    host.feed_map["rss://uc1"] = {
        "entries": [
            {"id": "v1", "title": "one", "link": "https://x/1", "published": "2026-05-26T09:00:00+00:00"},
            {"id": "v2", "title": "two", "link": "https://x/2", "published": "2026-05-26T09:10:00+00:00"},
            {"id": "v3", "title": "three", "link": "https://x/3", "published": "2026-05-26T09:20:00+00:00"},
        ]
    }
    asyncio.run(plugin_main.handle_schedule(object(), host))

    assert len(host.sent) == 1
    assert "three" in host.sent[0][2]
    cursor = repo.get_cursor(chat_id=100, thread_id=321, channel_key="UC1")
    assert cursor.cursor == "v3"


def test_schedule_post_header_uses_subscription_label_for_unverified_entry_channel_title(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=100,
        thread_id=7,
        channel_key="UCRAWID999",
        source_url="https://youtube.com/@friendly-channel",
        canonical_channel_url="https://www.youtube.com/channel/UCRAWID999",
        rss_url="rss://uc-title",
        added_by_user_id=1,
    )

    host = _Host()
    host.feed_map["rss://uc-title"] = {
        "entries": [
            {"id": "v2", "title": "two", "link": "https://x/2", "channel_title": "Readable Feed Name"},
            {"id": "v1", "title": "one", "link": "https://x/1", "channel_title": "Readable Feed Name"},
        ]
    }
    asyncio.run(plugin_main.handle_schedule(object(), host))

    host.feed_map["rss://uc-title"] = {
        "entries": [
            {"id": "v3", "title": "three", "link": "https://x/3", "channel_title": "Readable Feed Name"},
            {"id": "v2", "title": "two", "link": "https://x/2", "channel_title": "Readable Feed Name"},
        ]
    }
    asyncio.run(plugin_main.handle_schedule(object(), host))

    assert len(host.sent) == 1
    _, _, text = host.sent[0]
    assert text.startswith("📺 friendly-channel: three")
    assert "UCRAWID999" not in text


def test_schedule_duplicate_entries_not_reposted(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=100,
        thread_id=None,
        channel_key="UC1",
        source_url="https://www.youtube.com/channel/UC1",
        canonical_channel_url="https://www.youtube.com/channel/UC1",
        rss_url="rss://uc1",
        added_by_user_id=1,
    )

    host = _Host()
    host.feed_map["rss://uc1"] = {"entries": [{"id": "v2", "title": "two", "link": "https://x/2"}, {"id": "v1", "title": "one", "link": "https://x/1"}]}
    asyncio.run(plugin_main.handle_schedule(object(), host))

    host.feed_map["rss://uc1"] = {"entries": [{"id": "v2", "title": "two", "link": "https://x/2"}, {"id": "v1", "title": "one", "link": "https://x/1"}]}
    asyncio.run(plugin_main.handle_schedule(object(), host))

    assert host.sent == []


def test_webui_list_add_delete_topic_local_and_permissions(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    def fake_http(url: str):
        if url == "https://www.youtube.com/@handle":
            return 200, '"channelId":"UCWEB1"'
        raise AssertionError(url)

    monkeypatch.setattr(plugin_main, "_http_get_text", fake_http)

    ctx_t1 = _Context(command_name="addyt", argument=None, message_thread_id=11, user_is_admin=True)
    ctx_t2 = _Context(command_name="addyt", argument=None, message_thread_id=22, user_is_admin=True)

    added = plugin_main.webui_add_subscription(ctx_t1, "https://www.youtube.com/@handle")
    assert added["channel_key"] == "UCWEB1"

    items_t1 = plugin_main.webui_list_subscriptions(ctx_t1)
    assert [x["channel_key"] for x in items_t1] == ["UCWEB1"]

    items_t2 = plugin_main.webui_list_subscriptions(ctx_t2)
    assert items_t2 == []

    try:
        plugin_main.webui_add_subscription(ctx_t1, "https://www.youtube.com/@handle")
        assert False, "expected duplicate"
    except ValueError as exc:
        assert str(exc) == "duplicate_subscription"

    assert plugin_main.webui_delete_subscription(ctx_t2, "https://www.youtube.com/@handle") is False
    assert plugin_main.webui_delete_subscription(ctx_t1, "https://www.youtube.com/@handle") is True

    denied = _Context(command_name="addyt", argument=None, message_thread_id=11, user_is_admin=False, user_is_owner=False)
    for fn, args in [
        (plugin_main.webui_list_subscriptions, (denied,)),
        (plugin_main.webui_add_subscription, (denied, "https://www.youtube.com/channel/UCx")),
        (plugin_main.webui_delete_subscription, (denied, "https://www.youtube.com/channel/UCx")),
        (plugin_main.webui_get_poll_interval_seconds, (denied,)),
        (plugin_main.webui_set_poll_interval_seconds, (denied, 600)),
    ]:
        try:
            fn(*args)
            assert False, "expected permission error"
        except PermissionError as exc:
            assert str(exc) == "permission_denied"


def test_webui_poll_interval_get_set_validation_and_persistence(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    ctx = _Context(command_name="addyt", argument=None, user_is_admin=True)
    assert plugin_main.webui_get_poll_interval_seconds(ctx) == 300
    assert plugin_main.webui_set_poll_interval_seconds(ctx, 900) == 900
    assert plugin_main.webui_get_poll_interval_seconds(ctx) == 900

    repo2 = repo_module.YtRssStateRepository(tmp_path / "state")
    assert repo2.get_poll_interval_seconds() == 900

    for bad in [0, 29, 86401, -1, "abc"]:
        try:
            plugin_main.webui_set_poll_interval_seconds(ctx, bad)  # type: ignore[arg-type]
            assert False, "expected invalid_interval"
        except ValueError as exc:
            assert str(exc) == "invalid_interval"


def test_schedule_failure_isolated_per_subscription(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=100,
        thread_id=1,
        channel_key="UCFAIL",
        source_url="https://www.youtube.com/channel/UCFAIL",
        canonical_channel_url="https://www.youtube.com/channel/UCFAIL",
        rss_url="rss://fail",
        added_by_user_id=1,
    )
    repo.add_subscription(
        chat_id=100,
        thread_id=2,
        channel_key="UCGOOD",
        source_url="https://www.youtube.com/channel/UCGOOD",
        canonical_channel_url="https://www.youtube.com/channel/UCGOOD",
        rss_url="rss://good",
        added_by_user_id=1,
    )

    host = _Host()
    host.feed_map["rss://fail"] = RuntimeError("boom")
    host.feed_map["rss://good"] = {"entries": [{"id": "g1", "title": "good", "link": "https://x/g1"}]}

    asyncio.run(plugin_main.handle_schedule(object(), host))
    assert host.sent == []  # first good poll baselines

    host.feed_map["rss://fail"] = RuntimeError("boom")
    host.feed_map["rss://good"] = {"entries": [{"id": "g2", "title": "good2", "link": "https://x/g2"}, {"id": "g1", "title": "good", "link": "https://x/g1"}]}
    asyncio.run(plugin_main.handle_schedule(object(), host))

    assert len(host.sent) == 1
    assert host.sent[0][0] == 100 and host.sent[0][1] == 2


def test_schedule_migrates_legacy_subscription_shape_and_baselines(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    state_path = tmp_path / "state" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{\n'
        '  "subscriptions": {\n'
        '    "100:root:UCLEGACY": {\n'
        '      "chat_id": 100,\n'
        '      "thread_id": null,\n'
        '      "channel_key": "UCLEGACY",\n'
        '      "source_url": "https://www.youtube.com/channel/UCLEGACY",\n'
        '      "added_at": "2026-05-23T00:00:00+00:00"\n'
        '    }\n'
        '  },\n'
        '  "cursors": {},\n'
        '  "errors": {}\n'
        '}',
        encoding="utf-8",
    )

    host = _Host()
    host.feed_map["https://www.youtube.com/feeds/videos.xml?channel_id=UCLEGACY"] = {
        "entries": [
            {"id": "v2", "title": "two", "link": "https://x/2"},
            {"id": "v1", "title": "one", "link": "https://x/1"},
        ]
    }

    asyncio.run(plugin_main.handle_schedule(object(), host))
    assert host.sent == []

    migrated = repo.list_all_subscriptions()
    assert len(migrated) == 1
    assert migrated[0].canonical_channel_url == "https://www.youtube.com/channel/UCLEGACY"
    assert migrated[0].rss_url == "https://www.youtube.com/feeds/videos.xml?channel_id=UCLEGACY"
    assert migrated[0].added_by_user_id is None

    cursor = repo.get_cursor(chat_id=100, thread_id=None, channel_key="UCLEGACY")
    assert cursor.cursor == "v2"


def test_schedule_resolves_legacy_handle_subscription_and_posts_new_only(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    state_path = tmp_path / "state" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{\n'
        '  "subscriptions": {\n'
        '    "100:root:@ark-analytik": {\n'
        '      "chat_id": 100,\n'
        '      "thread_id": null,\n'
        '      "channel_key": "@ark-analytik",\n'
        '      "source_url": "https://youtube.com/@ark-analytik",\n'
        '      "canonical_channel_url": "https://www.youtube.com/channel/@ark-analytik",\n'
        '      "rss_url": "https://www.youtube.com/feeds/videos.xml?channel_id=@ark-analytik",\n'
        '      "added_at": "2026-05-23T00:00:00+00:00"\n'
        '    }\n'
        '  },\n'
        '  "cursors": {},\n'
        '  "errors": {}\n'
        '}',
        encoding="utf-8",
    )

    def fake_http(url: str):
        assert url == "https://youtube.com/@ark-analytik"
        return 200, '"channelId":"UCARK123"'

    monkeypatch.setattr(plugin_main, "_http_get_text", fake_http)

    host = _Host()
    host.feed_map["https://www.youtube.com/feeds/videos.xml?channel_id=UCARK123"] = {
        "entries": [
            {"id": "v2", "title": "two", "link": "https://x/2"},
            {"id": "v1", "title": "one", "link": "https://x/1"},
        ]
    }

    asyncio.run(plugin_main.handle_schedule(object(), host))
    assert host.sent == []

    subs = repo.list_all_subscriptions()
    assert len(subs) == 1
    assert subs[0].channel_key == "UCARK123"
    assert subs[0].canonical_channel_url == "https://www.youtube.com/channel/UCARK123"
    assert subs[0].rss_url == "https://www.youtube.com/feeds/videos.xml?channel_id=UCARK123"

    assert json.loads(state_path.read_text(encoding="utf-8"))["subscriptions"].get("100:root:@ark-analytik") is None

    host.feed_map["https://www.youtube.com/feeds/videos.xml?channel_id=UCARK123"] = {
        "entries": [
            {"id": "v3", "title": "three", "link": "https://x/3"},
            {"id": "v2", "title": "two", "link": "https://x/2"},
        ]
    }
    asyncio.run(plugin_main.handle_schedule(object(), host))
    assert len(host.sent) == 1
    assert host.sent[0][2].startswith("📺 ark-analytik: three")




def test_schedule_emits_safe_diagnostic_logs_success_and_error(tmp_path, monkeypatch, caplog) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=101,
        thread_id=11,
        channel_key="UCLOGOK",
        source_url="https://www.youtube.com/channel/UCLOGOK",
        canonical_channel_url="https://www.youtube.com/channel/UCLOGOK",
        rss_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCLOGOK",
        added_by_user_id=5,
    )
    repo.add_subscription(
        chat_id=202,
        thread_id=22,
        channel_key="UCLOGERR",
        source_url="https://www.youtube.com/channel/UCLOGERR",
        canonical_channel_url="https://www.youtube.com/channel/UCLOGERR",
        rss_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCLOGERR",
        added_by_user_id=6,
    )

    class Host:
        def __init__(self):
            self.sent = []

        async def rss_fetch(self, url):
            if "UCLOGERR" in url:
                raise RuntimeError("rss_fetch denied: policy:write")
            return {
                "entries": [
                    {
                        "id": "id-new",
                        "title": "Safe title should not be logged",
                        "link": "https://youtube.example/watch?v=abc123&token=secret",
                    }
                ]
            }

        async def send_message(self, chat_id, text, message_thread_id=None):
            self.sent.append((chat_id, message_thread_id, text))

    host = Host()

    repo.set_cursor(chat_id=101, thread_id=11, channel_key="UCLOGOK", cursor="id-old", dedupe=["id-old"])

    with caplog.at_level(logging.INFO, logger="amo.plugins.yt_rss"):
        asyncio.run(plugin_main.handle_schedule(object(), host))

    start_logs = [r for r in caplog.records if r.msg == "yt_rss schedule run start"]
    end_logs = [r for r in caplog.records if r.msg == "yt_rss schedule run end"]
    sub_ok_logs = [r for r in caplog.records if r.msg == "yt_rss subscription checked" and getattr(r, "channel_key", "") == "UCLOGOK"]
    sub_fail_logs = [r for r in caplog.records if r.msg == "yt_rss subscription check failed" and getattr(r, "channel_key", "") == "UCLOGERR"]

    assert start_logs and getattr(start_logs[0], "subscriptions_count", None) == 2
    assert end_logs and getattr(end_logs[-1], "checked_count", None) == 2

    assert sub_ok_logs
    ok = sub_ok_logs[-1]
    assert getattr(ok, "success", None) is True
    assert getattr(ok, "feed_entry_count", None) == 1
    assert getattr(ok, "new_count", None) == 1
    assert getattr(ok, "op_count", None) == 1
    assert getattr(ok, "cursor_changed", None) is True

    assert sub_fail_logs
    fail = sub_fail_logs[-1]
    assert getattr(fail, "success", None) is False
    assert getattr(fail, "error_class", None) == "RuntimeError"
    assert getattr(fail, "error_reason", None) == "policy_denied"

    rendered = "\n".join(caplog.messages)
    assert "Safe title should not be logged" not in rendered
    assert "token=secret" not in rendered
    for record in caplog.records:
        assert not hasattr(record, "subscription_key")
        assert not hasattr(record, "chat_id")
        assert not hasattr(record, "thread_id")
        assert hasattr(record, "subscription_ref") or record.msg in {
            "yt_rss schedule run start",
            "yt_rss schedule run end",
        }

def test_schedule_resolver_failure_records_error_and_retries_later(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=100,
        thread_id=10,
        channel_key="@badhandle",
        source_url="https://youtube.com/@badhandle",
        canonical_channel_url="https://www.youtube.com/channel/@badhandle",
        rss_url="https://www.youtube.com/feeds/videos.xml?channel_id=@badhandle",
        added_by_user_id=1,
    )

    def failing_http(_url: str):
        raise OSError("network")

    monkeypatch.setattr(plugin_main, "_http_get_text", failing_http)
    host = _Host()
    asyncio.run(plugin_main.handle_schedule(object(), host))
    assert host.sent == []

    raw = (tmp_path / "state" / "state.json").read_text(encoding="utf-8")
    assert "resolver_failed:ValueError:resolver_network_error" in raw

    def ok_http(_url: str):
        return 200, '"channelId":"UCOK123"'

    monkeypatch.setattr(plugin_main, "_http_get_text", ok_http)
    host.feed_map["https://www.youtube.com/feeds/videos.xml?channel_id=UCOK123"] = {"entries": [{"id": "a1", "title": "one", "link": "https://x/a1"}]}
    asyncio.run(plugin_main.handle_schedule(object(), host))
    assert host.sent == []
    subs = repo.list_subscriptions(chat_id=100, thread_id=10)
    assert [s.channel_key for s in subs] == ["UCOK123"]


def test_schedule_resolved_handle_dedupes_when_uc_subscription_exists(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    state_path = tmp_path / "state" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{\n'
        '  "subscriptions": {\n'
        '    "100:root:@ark-analytik": {\n'
        '      "chat_id": 100,\n'
        '      "thread_id": null,\n'
        '      "channel_key": "@ark-analytik",\n'
        '      "source_url": "https://youtube.com/@ark-analytik",\n'
        '      "canonical_channel_url": "https://www.youtube.com/channel/@ark-analytik",\n'
        '      "rss_url": "https://www.youtube.com/feeds/videos.xml?channel_id=@ark-analytik",\n'
        '      "added_at": "2026-05-23T00:00:00+00:00"\n'
        '    },\n'
        '    "100:root:UCARK123": {\n'
        '      "chat_id": 100,\n'
        '      "thread_id": null,\n'
        '      "channel_key": "UCARK123",\n'
        '      "source_url": "https://www.youtube.com/channel/UCARK123",\n'
        '      "canonical_channel_url": "https://www.youtube.com/channel/UCARK123",\n'
        '      "rss_url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCARK123",\n'
        '      "added_at": "2026-05-23T00:00:00+00:00"\n'
        '    }\n'
        '  },\n'
        '  "cursors": {},\n'
        '  "errors": {}\n'
        '}',
        encoding="utf-8",
    )

    def fake_http(_url: str):
        return 200, '"channelId":"UCARK123"'

    monkeypatch.setattr(plugin_main, "_http_get_text", fake_http)

    host = _Host()
    host.feed_map["https://www.youtube.com/feeds/videos.xml?channel_id=UCARK123"] = {"entries": [{"id": "v1", "title": "one", "link": "https://x/1"}]}

    asyncio.run(plugin_main.handle_schedule(object(), host))
    keys = set(json.loads(state_path.read_text(encoding="utf-8"))["subscriptions"].keys())
    assert "100:root:@ark-analytik" not in keys
    assert "100:root:UCARK123" in keys


def test_schedule_partial_failure_does_not_repost_already_sent_entry(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=100,
        thread_id=7,
        channel_key="UCPARTIAL",
        source_url="https://www.youtube.com/channel/UCPARTIAL",
        canonical_channel_url="https://www.youtube.com/channel/UCPARTIAL",
        rss_url="rss://partial",
        added_by_user_id=1,
    )

    class FlakyHost(_Host):
        def __init__(self):
            super().__init__()
            self.fail_on_title = "four"
            self.attempts: list[tuple[int, int | None, str]] = []

        async def send_message(self, chat_id: int, text: str, message_thread_id: int | None = None):
            self.attempts.append((chat_id, message_thread_id, text))
            if self.fail_on_title and f": {self.fail_on_title}" in text:
                raise RuntimeError("send failed")
            self.sent.append((chat_id, message_thread_id, text))

    host = FlakyHost()

    host.feed_map["rss://partial"] = {
        "entries": [
            {"id": "v2", "title": "two", "link": "https://x/2"},
            {"id": "v1", "title": "one", "link": "https://x/1"},
        ]
    }
    asyncio.run(plugin_main.handle_schedule(object(), host))
    assert host.sent == []

    host.feed_map["rss://partial"] = {
        "entries": [
            {"id": "v4", "title": "four", "link": "https://x/4"},
            {"id": "v3", "title": "three", "link": "https://x/3"},
            {"id": "v2", "title": "two", "link": "https://x/2"},
            {"id": "v1", "title": "one", "link": "https://x/1"},
        ]
    }
    asyncio.run(plugin_main.handle_schedule(object(), host))

    sent_titles = [text for _, _, text in host.sent]
    assert len(sent_titles) == 1
    assert any(": three" in text for text in sent_titles)
    assert not any(": four" in text for text in sent_titles)

    cursor_after_failure = repo.get_cursor(chat_id=100, thread_id=7, channel_key="UCPARTIAL")
    assert cursor_after_failure.cursor == "v3"
    assert "v3" in (cursor_after_failure.dedupe or [])
    assert "v4" not in (cursor_after_failure.dedupe or [])

    host.fail_on_title = None
    asyncio.run(plugin_main.handle_schedule(object(), host))

    assert len(host.sent) == 2
    assert ": four" in host.sent[-1][2]
    assert ": three" not in host.sent[-1][2]

    cursor_final = repo.get_cursor(chat_id=100, thread_id=7, channel_key="UCPARTIAL")
    assert cursor_final.cursor == "v4"


def test_schedule_skips_malformed_legacy_subscription_and_records_error(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    state_path = tmp_path / "state" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        '{\n'
        '  "subscriptions": {\n'
        '    "bad:key": {\n'
        '      "thread_id": null,\n'
        '      "source_url": "https://www.youtube.com/channel/UCBAD",\n'
        '      "added_at": "2026-05-23T00:00:00+00:00"\n'
        '    }\n'
        '  },\n'
        '  "cursors": {},\n'
        '  "errors": {}\n'
        '}',
        encoding="utf-8",
    )

    host = _Host()
    asyncio.run(plugin_main.handle_schedule(object(), host))
    assert host.sent == []

    raw = state_path.read_text(encoding="utf-8")
    assert "invalid_subscription_record:chat_id" in raw
    assert "bad:key" in raw


def test_schedule_post_header_uses_subscription_label_when_entry_channel_title_mismatches_subscription(tmp_path, monkeypatch) -> None:
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=100,
        thread_id=8,
        channel_key="UC_KLAUS",
        source_url="https://youtube.com/@klausgrillt",
        canonical_channel_url="https://www.youtube.com/channel/UC_KLAUS",
        rss_url="rss://klaus",
        added_by_user_id=1,
    )

    host = _Host()
    host.feed_map["rss://klaus"] = {
        "entries": [
            {
                "id": "v2",
                "title": "older",
                "link": "https://www.youtube.com/watch?v=older",
                "channel_title": "thebbqbear01",
                "channel_key": "UC_KLAUS",
            },
            {
                "id": "v1",
                "title": "oldest",
                "link": "https://www.youtube.com/watch?v=oldest",
                "channel_title": "thebbqbear01",
                "channel_key": "UC_KLAUS",
            },
        ]
    }
    asyncio.run(plugin_main.handle_schedule(object(), host))

    host.feed_map["rss://klaus"] = {
        "entries": [
            {
                "id": "v3",
                "title": "CURRYWURSTDÖNER grillen --- Klaus grillt",
                "link": "https://www.youtube.com/watch?v=ycD9wSWzymY",
                "channel_title": "thebbqbear01",
                "channel_key": "UC_KLAUS",
            },
            {
                "id": "v2",
                "title": "older",
                "link": "https://www.youtube.com/watch?v=older",
                "channel_title": "thebbqbear01",
                "channel_key": "UC_KLAUS",
            },
        ]
    }
    asyncio.run(plugin_main.handle_schedule(object(), host))

    assert len(host.sent) == 1
    _, _, text = host.sent[0]
    assert text.startswith("📺 klausgrillt: CURRYWURSTDÖNER grillen --- Klaus grillt")
    assert "https://www.youtube.com/watch?v=ycD9wSWzymY" in text
    assert "thebbqbear01" not in text


def test_resolver_prefers_channelmetadatarenderer_externalid_over_generic_browseid() -> None:
    """When a page contains channelMetadataRenderer.externalId alongside unrelated
    browseId/channelId values (e.g. recommendation blocks), the resolver must prefer
    the canonical channelMetadataRenderer signal and NOT pick a UC ID from recommendations.

    This reproduces the @kreisverkehr bug where a wrong UCfES... was selected
    from recommendation sidebars instead of the correct UCOT... from the channel's
    own metadata.
    """
    plugin_main = _load_plugin_main()

    # The correct channel ID (what channelMetadataRenderer.externalId would contain)
    correct_id = "UCOTyb6e_v9cz2Bu7VzFW7tg"
    # A UC ID that appears in recommendations/sidebar content
    wrong_id = "UCfES0_A2udbABm81ZpOiMxQ"

    html = (
        '<html><head>'
        f'<link rel="canonical" href="https://www.youtube.com/@kreisverkehr">'
        "</head><body>"
        # Recommendation sidebar with a UC ID (simulates "Videos from this creator" sidebar)
        f'<script>var recommendations = [{{"browseId":"{wrong_id}","title":"Some Video"}}];</script>'
        # Genuine channel metadata block containing the CORRECT externalId
        f'<script>var meta = {{"channelMetadataRenderer":{{"externalId":"{correct_id}","title":"KeysJore"}}}};</script>'
        # ytcfg with CHANNEL_ID set to correct ID (fallback)
        f'<script>ytcfg.set({{"CHANNEL_ID":"{correct_id}"}});</script>'
        "</body></html>"
    )

    def fake_http(_url: str):
        return 200, html

    out = plugin_main.resolve_youtube_channel_input(
        "https://youtube.com/@kreisverkehr",
        http_get_text=fake_http,
    )
    assert out["channel_key"] == correct_id, (
        f"Resolver picked wrong UC ID {out['channel_key']!r} "
        f"(expected {correct_id!r}). "
        f"channelMetadataRenderer.externalId was not preferred over recommendation browseId."
    )


def test_resolver_falls_back_to_structured_match_when_no_canonical_signal(tmp_path) -> None:
    """When the page has no channelMetadataRenderer.externalId, no ytcfg.set CHANNEL_ID,
    no canonicalBaseUrl, and no meta channelId — but does contain structured browseId
    entries in non-recommendation JSON context — the resolver falls back to the first
    structured match.

    The recommendation-only case (recommendation/sidebar/watch-next context) is handled
    by test_resolver_fails_closed_on_recommendation_only_page and correctly returns
    resolver_no_channel_id. This test uses browseId/channelId in a neutral context
    (no recommendation indicators in the preceding 200 chars), so fail-closed does not
    trigger and the first structured match is accepted.
    """
    plugin_main = _load_plugin_main()

    # HTML where UC IDs appear in structured JSON but NOT in recommendation/sidebar
    # patterns — no rec_indicators in the preceding window. The resolver accepts
    # the first structured match as a fallback since there is no canonical signal
    # AND no recommendation context to trigger fail-closed.
    html = (
        '<html><body>'
        '<script>var config = {"channelId":"UCSTRUCTURED1"};</script>'
        '<script>var nav = {"browseId":"UCSTRUCTURED2"};</script>'
        "</body></html>"
    )

    def fake_http(_url: str):
        return 200, html

    result = plugin_main.resolve_youtube_channel_input(
        "https://youtube.com/@some_handle",
        http_get_text=fake_http,
    )
    # Accepts the first structured match as fallback (no canonical signal, no rec context).
    assert result["channel_key"] == "UCSTRUCTURED1"


def test_resolver_fails_closed_on_recommendation_only_page() -> None:
    """When the page contains UC IDs only in recommendation/sidebar/watch-next
    contexts and has NO strong canonical signal (no channelMetadataRenderer.externalId,
    no ytcfg.set CHANNEL_ID, no canonicalBaseUrl, no meta channelId), the resolver
    must fail with resolver_no_channel_id rather than pick a wrong channel ID.

    This is the fail-closed guarantee for ambiguous/generic HTML where only
    recommendation-block browseIds are found.
    """
    plugin_main = _load_plugin_main()

    # HTML simulating a recommendation-only page (e.g. watch-next sidebar)
    # with UC IDs only in videoRenderer/browseId contexts — no canonical signal.
    html = (
        '<html><body>'
        '<script>var recs = [{"browseId":"UCSIDEBAR1","title":"Video A"},'
        '{"browseId":"UCSIDEBAR2","title":"Video B"}];</script>'
        '<script>var renderer = {"videoRenderer":{"videoId":"abc","browseId":"UCWRONGGG"}};</script>'
        '<script>var itemSection = {"itemSectionRenderer":{"contents":[{"compact-video-renderer":{"browseId":"UCRECOMEND3"}}]}};</script>'
        '</body></html>'
    )

    def fake_http(_url: str):
        return 200, html

    try:
        plugin_main.resolve_youtube_channel_input(
            "https://youtube.com/@some_handle",
            http_get_text=fake_http,
        )
        assert False, "expected ValueError 'resolver_no_channel_id'"
    except ValueError as exc:
        assert str(exc) == "resolver_no_channel_id", (
            f"Expected resolver_no_channel_id for recommendation-only page, got {exc!r}"
        )


def test_schedule_poll_start_log_excludes_source_url_and_rss_url(tmp_path, monkeypatch, caplog) -> None:
    """yt_rss subscription poll start must not include source_url or rss_url fields.
    Only safe metadata fields (subscription_key, channel_key) are permitted.
    """
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state_poll")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=600,
        thread_id=60,
        channel_key="UC_POLLSTART",
        source_url="https://youtube.com/@secret_channel",
        canonical_channel_url="https://www.youtube.com/channel/UC_POLLSTART",
        rss_url="https://www.youtube.com/feeds/videos.xml?channel_id=UC_POLLSTART",
        added_by_user_id=1,
    )

    class Host:
        def __init__(self):
            self.sent = []

        async def rss_fetch(self, url):
            return {"entries": [{"id": "p1", "title": "posted", "link": "https://x/p1"}]}

        async def send_message(self, chat_id, text, message_thread_id=None):
            self.sent.append((chat_id, message_thread_id, text))

    host = Host()
    repo.set_cursor(chat_id=600, thread_id=60, channel_key="UC_POLLSTART", cursor="old", dedupe=["old"])

    with caplog.at_level(logging.INFO, logger="amo.plugins.yt_rss"):
        asyncio.run(plugin_main.handle_schedule(object(), host))

    poll_start_logs = [r for r in caplog.records if r.msg == "yt_rss subscription poll start"]
    assert poll_start_logs, "No 'yt_rss subscription poll start' log emitted"
    rec = poll_start_logs[0]

    # Allowed fields only
    assert hasattr(rec, "subscription_ref"), "poll start must have subscription_ref"
    assert hasattr(rec, "channel_key"), "poll start must have channel_key"

    # Forbidden fields must not exist on the record
    assert not hasattr(rec, "subscription_key")
    assert not hasattr(rec, "chat_id")
    assert not hasattr(rec, "thread_id")
    extra_dict = getattr(rec, "extra", {})
    assert "source_url" not in extra_dict, (
        f"source_url must not appear in poll start log. "
        f"Fields: {list(extra_dict.keys())}"
    )
    assert "rss_url" not in extra_dict, (
        f"rss_url must not appear in poll start log. "
        f"Fields: {list(extra_dict.keys())}"
    )

    # Verify no URL strings leaked into any log message
    rendered = "\n".join(caplog.messages)
    assert "@secret_channel" not in rendered, "source URL leaked into logs"
    assert "feeds/videos.xml" not in rendered, "rss URL leaked into logs"



    """Per-subscription log entries must include channel_key, thread, status,
    feed_entry_count, new_count, op_count, cursor_changed, and error_class/reason.
    No video titles, URLs, message text, or secrets must appear.
    """
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state_diag")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=300,
        thread_id=10,
        channel_key="UC_DIAG1",
        source_url="https://www.youtube.com/channel/UC_DIAG1",
        canonical_channel_url="https://www.youtube.com/channel/UC_DIAG1",
        rss_url="https://www.youtube.com/feeds/videos.xml?channel_id=UC_DIAG1",
        added_by_user_id=1,
    )
    repo.add_subscription(
        chat_id=400,
        thread_id=20,
        channel_key="UC_DIAG2",
        source_url="https://www.youtube.com/channel/UC_DIAG2",
        canonical_channel_url="https://www.youtube.com/channel/UC_DIAG2",
        rss_url="https://www.youtube.com/feeds/videos.xml?channel_id=UC_DIAG2",
        added_by_user_id=1,
    )

    class Host:
        def __init__(self):
            self.sent = []

        async def rss_fetch(self, url):
            if "UC_DIAG1" in url:
                return {
                    "entries": [
                        {
                        "id": "diag1_new",
                        "title": "Diag video should not appear in logs",
                        "link": "https://youtube.com/watch?v=secret_token",
                        },
                    ]
                }
            if "UC_DIAG2" in url:
                return {"entries": []}  # no new entries
            raise AssertionError(f"unexpected URL {url}")

        async def send_message(self, chat_id, text, message_thread_id=None):
            self.sent.append((chat_id, message_thread_id, text))

    host = Host()

    # Baseline: UC_DIAG1 has cursor at diag1_old, UC_DIAG2 has no cursor
    repo.set_cursor(chat_id=300, thread_id=10, channel_key="UC_DIAG1", cursor="diag1_old", dedupe=["diag1_old"])

    with caplog.at_level(logging.INFO, logger="amo.plugins.yt_rss"):
        asyncio.run(plugin_main.handle_schedule(object(), host))

    # UC_DIAG1: one new entry → posted, cursor advanced
    sub1_logs = [r for r in caplog.records if getattr(r, "channel_key", "") == "UC_DIAG1"]
    assert sub1_logs, "No log records for UC_DIAG1"

    checked = [r for r in sub1_logs if r.msg == "yt_rss subscription checked"]
    assert checked, "No 'yt_rss subscription checked' log for UC_DIAG1"
    rec = checked[-1]
    assert getattr(rec, "success", None) is True
    assert getattr(rec, "feed_entry_count", None) == 1
    assert getattr(rec, "new_count", None) == 1
    assert getattr(rec, "op_count", None) == 1
    assert getattr(rec, "cursor_changed", None) is True
    assert getattr(rec, "error_class", None) is None

    # UC_DIAG2: no new entries → success, zero posts
    sub2_logs = [r for r in caplog.records if getattr(r, "channel_key", "") == "UC_DIAG2"]
    assert sub2_logs, "No log records for UC_DIAG2"
    checked2 = [r for r in sub2_logs if r.msg == "yt_rss subscription checked"]
    assert checked2, "No 'yt_rss subscription checked' log for UC_DIAG2"
    rec2 = checked2[-1]
    assert getattr(rec2, "success", None) is True
    assert getattr(rec2, "feed_entry_count", None) == 0
    assert getattr(rec2, "new_count", None) == 0
    assert getattr(rec2, "op_count", None) == 0
    assert getattr(rec2, "cursor_changed", None) is False

    # Run-level log
    run_end = [r for r in caplog.records if r.msg == "yt_rss schedule run end"]
    assert run_end
    assert getattr(run_end[-1], "subscription_count", None) == 2
    assert getattr(run_end[-1], "checked_count", None) == 2
    assert getattr(run_end[-1], "posted_total", None) == 1
    assert getattr(run_end[-1], "failed_count", None) == 0

    # Verify no titles / URLs / tokens leak into log messages
    rendered = "\n".join(caplog.messages)
    assert "Diag video should not appear in logs" not in rendered
    assert "secret_token" not in rendered
    # Verify channel_key (not a secret) appears in log records, not message strings
    channel_keys_in_records = {getattr(r, "channel_key", "") for r in caplog.records}
    assert "UC_DIAG1" in channel_keys_in_records, f"UC_DIAG1 not in log records: {channel_keys_in_records}"
    assert "UC_DIAG2" in channel_keys_in_records, f"UC_DIAG2 not in log records: {channel_keys_in_records}"


def test_schedule_emits_error_class_and_reason_on_exception(tmp_path, monkeypatch, caplog) -> None:
    """On failure, log must include error_class (type name) and error_reason (category).
    """
    plugin_main = _load_plugin_main()
    repo_module = _load_repo_module()
    repo = repo_module.YtRssStateRepository(tmp_path / "state_err")
    monkeypatch.setattr(plugin_main, "_repo_for_context", lambda context: repo)

    repo.add_subscription(
        chat_id=500,
        thread_id=5,
        channel_key="UC_ERR",
        source_url="https://www.youtube.com/channel/UC_ERR",
        canonical_channel_url="https://www.youtube.com/channel/UC_ERR",
        rss_url="rss://ucerr",
        added_by_user_id=1,
    )

    class Host:
        def __init__(self):
            self.sent = []

        async def rss_fetch(self, url):
            raise RuntimeError("simulated failure")

        async def send_message(self, chat_id, text, message_thread_id=None):
            self.sent.append((chat_id, message_thread_id, text))

    host = Host()

    with caplog.at_level(logging.INFO, logger="amo.plugins.yt_rss"):
        asyncio.run(plugin_main.handle_schedule(object(), host))

    failed = [r for r in caplog.records if r.msg == "yt_rss subscription check failed"]
    assert failed, "No failed log emitted"
    rec = failed[-1]
    assert getattr(rec, "success", None) is False
    assert getattr(rec, "error_class", None) == "RuntimeError"
    assert getattr(rec, "error_reason", None) == "RuntimeError"
    assert getattr(rec, "channel_key", None) == "UC_ERR"
