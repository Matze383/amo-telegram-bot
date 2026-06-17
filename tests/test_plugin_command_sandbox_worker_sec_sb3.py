from __future__ import annotations

import asyncio
import json

from amo_bot.plugins.sandbox.command_worker import execute_command_request


def _request(plugin_entry: str, *, argument: str = "x", message_thread_id: int | None = None) -> dict[str, object]:
    return {
        "action": "command.execute.v1",
        "request_id": "req-1",
        "plugin_entry": plugin_entry,
        "command_name": "delyt",
        "argument": argument,
        "context": {
            "chat_id": 123,
            "message_id": 456,
            "message_thread_id": message_thread_id,
            "user_id": 42,
            "role": "admin",
            "trigger_type": "command",
            "run_id": "run-1",
            "attachments": [],
            "reply_to_image": None,
        },
        "permissions": ["send_message"],
        "limits": {"timeout_ms": 1000, "max_ops": 2, "max_text_len": 100},
    }


def test_worker_success_records_allowed_ops(tmp_path) -> None:
    pdir = tmp_path / "plugins" / "demo"
    pdir.mkdir(parents=True)
    (pdir / "main.py").write_text(
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "ok")
    await host_api.reply(context.chat_id, context.message_id, "ack")
""",
        encoding="utf-8",
    )

    response = asyncio.run(
        execute_command_request(_request("demo/main.py"), plugins_root=tmp_path / "plugins")
    )

    assert response["ok"] is True
    assert response["error"] is None
    assert response["ops"] == [
        {"op": "send_message", "text": "ok", "chat_id": 123, "message_id": None},
        {"op": "reply", "text": "ack", "chat_id": 123, "message_id": 456},
    ]


def test_worker_splits_long_send_message_ops_without_protocol_error(tmp_path) -> None:
    pdir = tmp_path / "plugins" / "longsend"
    pdir.mkdir(parents=True)
    (pdir / "main.py").write_text(
        """
async def handle_command(context, host_api):
    await host_api.send_message(context.chat_id, "x" * 4200)
""",
        encoding="utf-8",
    )
    request = _request("longsend/main.py")
    request["limits"] = {"timeout_ms": 1000, "max_ops": 3, "max_text_len": 4000}

    response = asyncio.run(execute_command_request(request, plugins_root=tmp_path / "plugins"))

    assert response["ok"] is True
    assert response["error"] is None
    assert [len(op["text"]) for op in response["ops"]] == [4000, 200]
    assert "".join(op["text"] for op in response["ops"]) == "x" * 4200


def test_worker_disallowed_host_op_is_sanitized(tmp_path) -> None:
    pdir = tmp_path / "plugins" / "badop"
    pdir.mkdir(parents=True)
    (pdir / "main.py").write_text(
        """
async def handle_command(context, host_api):
    await host_api.exec("echo nope")
""",
        encoding="utf-8",
    )

    response = asyncio.run(
        execute_command_request(_request("badop/main.py"), plugins_root=tmp_path / "plugins")
    )

    assert response["ok"] is False
    assert response["ops"] == []
    assert response["error"]["code"] == "runtime_error"
    assert "traceback" not in response["error"]["message"].lower()


def test_worker_plugin_exception_sanitized_no_traceback(tmp_path) -> None:
    pdir = tmp_path / "plugins" / "boom"
    pdir.mkdir(parents=True)
    (pdir / "main.py").write_text(
        """
async def handle_command(context, host_api):
    raise RuntimeError("Traceback: secret leak")
""",
        encoding="utf-8",
    )

    response = asyncio.run(
        execute_command_request(_request("boom/main.py"), plugins_root=tmp_path / "plugins")
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "runtime_error"
    assert response["error"]["message"] == "command execution failed"


def test_worker_allows_dict_send_message_payload_with_reply_markup_for_delyt_menu(tmp_path) -> None:
    pdir = tmp_path / "plugins" / "yt_rss"
    pdir.mkdir(parents=True)
    (pdir / "main.py").write_text(
        '''
async def handle_command(context, host_api):
    if context.command_name.lower() != "delyt" or (context.argument or "").strip():
        await host_api.send_message(context.chat_id, "unexpected")
        return
    await host_api.send_message(
        context.chat_id,
        {
            "text": "🗑 Welches Abo möchtest du löschen?",
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "Channel One", "callback_data": "yt_rss:delyt:UC111"}],
                    [{"text": "❌ Abbrechen", "callback_data": "yt_rss:delyt:cancel"}],
                ]
            },
            "message_thread_id": context.message_thread_id,
        },
    )
''',
        encoding="utf-8",
    )

    response = asyncio.run(
        execute_command_request(
            _request("yt_rss/main.py", argument="", message_thread_id=9936),
            plugins_root=tmp_path / "plugins",
        )
    )

    assert response["ok"] is True
    assert response["error"] is None
    assert response["ops"] == [
        {
            "op": "send_message",
            "text": "🗑 Welches Abo möchtest du löschen?",
            "chat_id": 123,
            "message_id": None,
            "message_thread_id": 9936,
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "Channel One", "callback_data": "yt_rss:delyt:UC111"}],
                    [{"text": "❌ Abbrechen", "callback_data": "yt_rss:delyt:cancel"}],
                ]
            },
        }
    ]


def test_worker_rejects_path_traversal_and_absolute_entry(tmp_path) -> None:
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir(parents=True)

    traversal = asyncio.run(execute_command_request(_request("../evil/main.py"), plugins_root=plugins_root))
    absolute = asyncio.run(execute_command_request(_request("/tmp/evil/main.py"), plugins_root=plugins_root))

    assert traversal["ok"] is False
    assert traversal["error"]["code"] in {"invalid_request", "invalid_plugin_entry"}
    assert absolute["ok"] is False
    assert absolute["error"]["code"] in {"invalid_request", "invalid_plugin_entry"}
