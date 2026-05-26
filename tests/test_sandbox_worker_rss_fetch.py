from __future__ import annotations

import json

from amo_bot.plugins.sandbox import worker as sandbox_worker


def _base_request(*, permissions: list[str]) -> dict[str, object]:
    return {
        "request_id": "req-1",
        "plugin_id": "yt_rss",
        "action": "run",
        "timeout_ms": 1000,
        "payload": {
            "trigger": "schedule",
            "run_id": "run-1",
            "permissions": permissions,
            "plugin_entry": "yt_rss/main.py",
        },
    }


def test_schedule_rss_fetch_allowed(monkeypatch, tmp_path) -> None:
    plugin_root = tmp_path / "plugins" / "yt_rss"
    plugin_root.mkdir(parents=True)
    (plugin_root / "main.py").write_text(
        """
async def handle_schedule(context, host_api):
    result = await host_api.rss_fetch("https://www.youtube.com/feeds/videos.xml?channel_id=UC123")
    entries = result.get("entries") or []
    await host_api.send_message(123, f"entries:{len(entries)}")
    return {
        "schedule_diagnostics": [{"event": "rss.checked", "entries": len(entries)}],
        "secret": "must-not-leak",
        "url": "https://example.invalid",
    }
""",
        encoding="utf-8",
    )

    original_execute_rss_fetch = sandbox_worker.execute_rss_fetch

    def _fake_fetch(*, request, http_get, now_monotonic_seconds, last_fetch_monotonic_seconds):
        return original_execute_rss_fetch(
            request=request,
            http_get=lambda _url, _timeout: sandbox_worker.RSSHTTPResponse(
                status_code=200,
                body=b"<?xml version='1.0'?><rss><channel><item><guid>1</guid><title>A</title><link>https://x</link></item></channel></rss>",
                redirects=0,
            ),
            now_monotonic_seconds=now_monotonic_seconds,
            last_fetch_monotonic_seconds=last_fetch_monotonic_seconds,
        )

    monkeypatch.setenv("AMO_SANDBOX_ALLOWED_CAPABILITIES", "plugin.execute")
    monkeypatch.setenv("AMO_SANDBOX_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setattr(sandbox_worker, "execute_rss_fetch", _fake_fetch)

    monkeypatch.setattr(
        sandbox_worker.sys,
        "stdin",
        type("_S", (), {"read": lambda self: json.dumps(_base_request(permissions=["rss.fetch", "send_message"]))})(),
    )
    out: dict[str, str] = {}
    monkeypatch.setattr(sandbox_worker.sys, "stdout", type("_O", (), {"write": lambda self, s: out.setdefault("text", s), "flush": lambda self: None})())

    code = sandbox_worker.main()
    payload = json.loads(out["text"])
    assert code == 0
    assert payload["ok"] is True
    ops = payload["result"]["ops"]
    assert ops and ops[0]["op"] == "send_message"
    assert ops[0]["text"] == "entries:1"
    assert payload["result"]["schedule_diagnostics"] == [{"event": "rss.checked", "entries": 1}]
    assert "secret" not in payload["result"]
    assert "url" not in payload["result"]


def test_schedule_rss_fetch_permission_denied(monkeypatch, tmp_path) -> None:
    plugin_root = tmp_path / "plugins" / "yt_rss"
    plugin_root.mkdir(parents=True)
    (plugin_root / "main.py").write_text(
        """
async def handle_schedule(context, host_api):
    await host_api.rss_fetch("https://www.youtube.com/feeds/videos.xml?channel_id=UC123")
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("AMO_SANDBOX_ALLOWED_CAPABILITIES", "plugin.execute")
    monkeypatch.setenv("AMO_SANDBOX_PLUGIN_DIR", str(tmp_path / "plugins"))
    monkeypatch.setattr(
        sandbox_worker.sys,
        "stdin",
        type("_S", (), {"read": lambda self: json.dumps(_base_request(permissions=["send_message"]))})(),
    )
    out: dict[str, str] = {}
    monkeypatch.setattr(sandbox_worker.sys, "stdout", type("_O", (), {"write": lambda self, s: out.setdefault("text", s), "flush": lambda self: None})())

    code = sandbox_worker.main()
    payload = json.loads(out["text"])
    assert code == 1
    assert payload["ok"] is False
    assert payload["error_message"] == "operation 'rss_fetch' requires capability 'rss.fetch'"


def test_schedule_rss_fetch_policy_denied_reason_remains_denied(monkeypatch, tmp_path) -> None:
    plugin_root = tmp_path / "plugins" / "yt_rss"
    plugin_root.mkdir(parents=True)
    (plugin_root / "main.py").write_text(
        """
async def handle_schedule(context, host_api):
    await host_api.rss_fetch("https://www.youtube.com/feeds/videos.xml?channel_id=UC123")
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("AMO_SANDBOX_ALLOWED_CAPABILITIES", "plugin.execute")
    monkeypatch.setenv("AMO_SANDBOX_PLUGIN_DIR", str(tmp_path / "plugins"))

    def _deny_policy(*, request, http_get, now_monotonic_seconds, last_fetch_monotonic_seconds):
        return sandbox_worker.CapabilityDecisionResult.DENY and type("_R", (), {"result": sandbox_worker.CapabilityDecisionResult.DENY, "reason_code": "host_not_allowlisted", "entries": [], "audit": {}})()

    monkeypatch.setattr(sandbox_worker, "execute_rss_fetch", _deny_policy)
    monkeypatch.setattr(
        sandbox_worker.sys,
        "stdin",
        type("_S", (), {"read": lambda self: json.dumps(_base_request(permissions=["rss.fetch"]))})(),
    )
    out: dict[str, str] = {}
    monkeypatch.setattr(sandbox_worker.sys, "stdout", type("_O", (), {"write": lambda self, s: out.setdefault("text", s), "flush": lambda self: None})())

    code = sandbox_worker.main()
    payload = json.loads(out["text"])
    assert code == 1
    assert payload["ok"] is False
    assert payload["error_message"] == "rss_fetch denied: host_not_allowlisted"


def test_schedule_rss_fetch_operational_reason_is_error_not_denied(monkeypatch, tmp_path) -> None:
    plugin_root = tmp_path / "plugins" / "yt_rss"
    plugin_root.mkdir(parents=True)
    (plugin_root / "main.py").write_text(
        """
async def handle_schedule(context, host_api):
    await host_api.rss_fetch("https://www.youtube.com/feeds/videos.xml?channel_id=UC123")
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("AMO_SANDBOX_ALLOWED_CAPABILITIES", "plugin.execute")
    monkeypatch.setenv("AMO_SANDBOX_PLUGIN_DIR", str(tmp_path / "plugins"))

    def _deny_fetch(*, request, http_get, now_monotonic_seconds, last_fetch_monotonic_seconds):
        return sandbox_worker.CapabilityDecisionResult.DENY and type("_R", (), {"result": sandbox_worker.CapabilityDecisionResult.DENY, "reason_code": "fetch_failed", "entries": [], "audit": {}})()

    monkeypatch.setattr(sandbox_worker, "execute_rss_fetch", _deny_fetch)
    monkeypatch.setattr(
        sandbox_worker.sys,
        "stdin",
        type("_S", (), {"read": lambda self: json.dumps(_base_request(permissions=["rss.fetch"]))})(),
    )
    out: dict[str, str] = {}
    monkeypatch.setattr(sandbox_worker.sys, "stdout", type("_O", (), {"write": lambda self, s: out.setdefault("text", s), "flush": lambda self: None})())

    code = sandbox_worker.main()
    payload = json.loads(out["text"])
    assert code == 1
    assert payload["ok"] is False
    assert payload["error_message"] == "rss_fetch_error:fetch_failed"
