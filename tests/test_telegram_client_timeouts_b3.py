from __future__ import annotations

import asyncio

import httpx

from amo_bot.telegram.client import TelegramClient


class _DummyResponse:
    status_code = 200

    @staticmethod
    def json() -> dict[str, object]:
        return {"ok": True, "result": []}


class _CaptureClient:
    def __init__(self, *, timeout, seen: list[object]) -> None:  # noqa: ANN001
        self._seen = seen
        self._timeout = timeout

    async def __aenter__(self):
        self._seen.append(self._timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None

    async def post(self, url: str, json: dict[str, object]) -> _DummyResponse:  # noqa: ARG002
        return _DummyResponse()


def test_get_updates_uses_read_timeout_above_poll_timeout(monkeypatch) -> None:
    seen: list[object] = []

    def _factory(*, timeout):  # noqa: ANN001
        return _CaptureClient(timeout=timeout, seen=seen)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    client = TelegramClient(token="t", timeout_seconds=30.0)
    asyncio.run(client.get_updates(offset=0, timeout=30, limit=1))

    assert len(seen) == 1
    timeout = seen[0]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 40.0


def test_non_poll_calls_keep_shared_timeout_float(monkeypatch) -> None:
    seen: list[object] = []

    def _factory(*, timeout):  # noqa: ANN001
        return _CaptureClient(timeout=timeout, seen=seen)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)

    client = TelegramClient(token="t", timeout_seconds=7.0)
    asyncio.run(client.send_message(chat_id=1, text="hello"))

    assert seen == [7.0]
