from __future__ import annotations

import pytest

from amo_bot.plugins.sandbox.command_protocol import (
    CommandExecuteRequestV1,
    CommandExecuteResponseV1,
    CommandLimits,
    CommandProtocolError,
)


def _valid_request_payload() -> dict[str, object]:
    return {
        "action": "command.execute.v1",
        "request_id": "req-1",
        "plugin_id": "weather_plugin",
        "command_name": "weather",
        "argument": "Berlin",
        "context": {
            "chat_id": 123,
            "message_id": 456,
            "message_thread_id": 789,
            "user_id": 42,
            "role": "admin",
            "trigger_type": "command",
            "run_id": "run-1",
            "attachments": [{"type": "image", "url": "https://example.org/a.jpg"}],
            "reply_to_image": {"file_id": "abc"},
        },
        "permissions": ["send_message"],
        "limits": {"timeout_ms": 1000, "max_ops": 2, "max_text_len": 100},
    }


def test_request_and_response_roundtrip() -> None:
    req = CommandExecuteRequestV1.from_dict(_valid_request_payload())
    req_roundtrip = CommandExecuteRequestV1.from_dict(req.to_dict())
    assert req_roundtrip == req

    response_payload = {
        "action": "command.execute.v1",
        "request_id": req.request_id,
        "ok": True,
        "ops": [
            {"op": "send_message", "chat_id": 123, "text": "Hallo"},
            {"op": "reply", "chat_id": 123, "message_id": 456, "text": "Antwort"},
        ],
        "metrics": {"duration_ms": 12},
    }
    rsp = CommandExecuteResponseV1.from_dict(response_payload, limits=req.limits)
    rsp_roundtrip = CommandExecuteResponseV1.from_dict(rsp.to_dict(), limits=req.limits)
    assert rsp_roundtrip == rsp


def test_rejects_absolute_and_traversal_plugin_entry() -> None:
    base = _valid_request_payload()
    base.pop("plugin_id")
    base["plugin_entry"] = "/tmp/plugin.py"
    with pytest.raises(CommandProtocolError):
        CommandExecuteRequestV1.from_dict(base)

    base2 = _valid_request_payload()
    base2.pop("plugin_id")
    base2["plugin_entry"] = "../plugin.py"
    with pytest.raises(CommandProtocolError):
        CommandExecuteRequestV1.from_dict(base2)


def test_op_allowlist_enforced() -> None:
    req = CommandExecuteRequestV1.from_dict(_valid_request_payload())
    payload = {
        "action": "command.execute.v1",
        "request_id": req.request_id,
        "ok": True,
        "ops": [{"op": "exec", "text": "rm -rf /"}],
    }
    with pytest.raises(CommandProtocolError):
        CommandExecuteResponseV1.from_dict(payload, limits=req.limits)


def test_max_ops_and_max_text_len_enforced() -> None:
    req_payload = _valid_request_payload()
    req_payload["limits"] = {"timeout_ms": 1000, "max_ops": 1, "max_text_len": 5}
    req = CommandExecuteRequestV1.from_dict(req_payload)

    too_many_ops = {
        "action": "command.execute.v1",
        "request_id": req.request_id,
        "ok": True,
        "ops": [
            {"op": "send_message", "chat_id": 123, "text": "Hallo"},
            {"op": "reply", "chat_id": 123, "message_id": 456, "text": "Hi"},
        ],
    }
    with pytest.raises(CommandProtocolError):
        CommandExecuteResponseV1.from_dict(too_many_ops, limits=req.limits)

    text_too_long = {
        "action": "command.execute.v1",
        "request_id": req.request_id,
        "ok": True,
        "ops": [{"op": "send_message", "chat_id": 123, "text": "123456"}],
    }
    with pytest.raises(CommandProtocolError):
        CommandExecuteResponseV1.from_dict(text_too_long, limits=req.limits)


def test_sanitized_error_shape_and_no_traceback_required() -> None:
    limits = CommandLimits(timeout_ms=1000, max_ops=1, max_text_len=100)
    ok_error = {
        "action": "command.execute.v1",
        "request_id": "req-err",
        "ok": False,
        "error": {"code": "runtime_error", "message": "Failed safely"},
    }
    parsed = CommandExecuteResponseV1.from_dict(ok_error, limits=limits)
    assert parsed.error is not None
    assert parsed.error.code == "runtime_error"

    malformed = {
        "action": "command.execute.v1",
        "request_id": "req-err",
        "ok": False,
        "error": {
            "code": "runtime_error",
            "message": "Traceback: stack...",
            "traceback": "boom",
        },
    }
    with pytest.raises(CommandProtocolError):
        CommandExecuteResponseV1.from_dict(malformed, limits=limits)


def test_action_version_must_match_command_execute_v1() -> None:
    bad_req = _valid_request_payload()
    bad_req["action"] = "command.execute.v2"
    with pytest.raises(CommandProtocolError):
        CommandExecuteRequestV1.from_dict(bad_req)

    limits = CommandLimits(timeout_ms=1000, max_ops=1, max_text_len=100)
    bad_rsp = {
        "action": "command.execute.v2",
        "request_id": "req-x",
        "ok": True,
        "ops": [{"op": "send_message", "chat_id": 1, "text": "ok"}],
    }
    with pytest.raises(CommandProtocolError):
        CommandExecuteResponseV1.from_dict(bad_rsp, limits=limits)
