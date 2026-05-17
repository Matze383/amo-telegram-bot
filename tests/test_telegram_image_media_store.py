from __future__ import annotations

from pathlib import Path

import asyncio

import httpx


from amo_bot.telegram.image_media_store import ImageDownloadPolicy, TelegramImageMediaStore
from amo_bot.telegram.update_parser import TelegramAttachment


class _FakeStreamResponse:
    def __init__(self, *, status_code: int = 200, headers: dict[str, str] | None = None, chunks: list[bytes] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []

    async def __aenter__(self) -> "_FakeStreamResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def aiter_bytes(self, chunk_size: int = 65536):
        for chunk in self._chunks:
            yield chunk


class _FakeStreamContext:
    def __init__(self, response: _FakeStreamResponse | Exception) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeStreamResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeClient:
    def __init__(self, *, post_result: object | Exception, stream_result: _FakeStreamResponse | Exception) -> None:
        self._post_result = post_result
        self._stream_result = stream_result

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, json: dict[str, object]):
        if isinstance(self._post_result, Exception):
            raise self._post_result
        return self._post_result

    def stream(self, method: str, url: str):
        return _FakeStreamContext(self._stream_result)


class _JsonResponse:
    def __init__(self, *, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


def _image_attachment(*, size: int | None = None) -> TelegramAttachment:
    return TelegramAttachment(
        source_kind="photo",
        type_hint="image",
        file_id="abc123",
        file_unique_id="u1",
        size=size,
    )


def test_deny_oversize_from_attachment_metadata(tmp_path: Path) -> None:
    store = TelegramImageMediaStore(
        bot_token="token",
        policy=ImageDownloadPolicy(max_bytes=10),
        base_dir=tmp_path,
    )

    result = asyncio.run(store.download_image(attachment=_image_attachment(size=11)))

    assert result.ok is False
    assert result.reason_code == "deny_size_limit"
    assert list(tmp_path.iterdir()) == []


def test_deny_oversize_while_streaming(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    post = _JsonResponse(
        status_code=200,
        payload={"ok": True, "result": {"file_path": "img.bin", "file_size": 5}},
    )
    stream = _FakeStreamResponse(
        status_code=200,
        headers={"content-type": "image/png"},
        chunks=[b"12345", b"67890"],
    )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda timeout=None: _FakeClient(post_result=post, stream_result=stream),
    )

    store = TelegramImageMediaStore(
        bot_token="token",
        policy=ImageDownloadPolicy(max_bytes=8),
        base_dir=tmp_path,
    )

    result = asyncio.run(store.download_image(attachment=_image_attachment(size=5)))

    assert result.ok is False
    assert result.reason_code == "deny_size_limit"
    assert list(tmp_path.iterdir()) == []


def test_deny_mime_not_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    post = _JsonResponse(
        status_code=200,
        payload={"ok": True, "result": {"file_path": "img.bin"}},
    )
    stream = _FakeStreamResponse(
        status_code=200,
        headers={"content-type": "application/pdf"},
        chunks=[b"%PDF-1.4"],
    )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda timeout=None: _FakeClient(post_result=post, stream_result=stream),
    )

    store = TelegramImageMediaStore(bot_token="token", base_dir=tmp_path)
    result = asyncio.run(store.download_image(attachment=_image_attachment()))

    assert result.ok is False
    assert result.reason_code == "deny_mime_not_allowed"
    assert list(tmp_path.iterdir()) == []


def test_timeout_maps_to_deterministic_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda timeout=None: _FakeClient(
            post_result=httpx.ReadTimeout("timeout"),
            stream_result=_FakeStreamResponse(status_code=200, headers={"content-type": "image/png"}, chunks=[]),
        ),
    )

    store = TelegramImageMediaStore(bot_token="token", base_dir=tmp_path)
    result = asyncio.run(store.download_image(attachment=_image_attachment()))

    assert result.ok is False
    assert result.reason_code == "error_timeout"


def test_successful_bounded_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"\x89PNG\r\n\x1a\nabc"
    post = _JsonResponse(
        status_code=200,
        payload={"ok": True, "result": {"file_path": "img.bin", "file_size": len(content)}},
    )
    stream = _FakeStreamResponse(
        status_code=200,
        headers={"content-type": "image/png", "content-length": str(len(content))},
        chunks=[content],
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda timeout=None: _FakeClient(post_result=post, stream_result=stream),
    )

    store = TelegramImageMediaStore(
        bot_token="token",
        policy=ImageDownloadPolicy(max_bytes=1024),
        base_dir=tmp_path,
    )
    result = asyncio.run(store.download_image(attachment=_image_attachment(size=len(content))))

    assert result.ok is True
    assert result.reason_code == "ok"
    assert result.mime_type == "image/png"
    assert result.bytes_stored == len(content)
    assert result.file_path is not None

    saved = Path(result.file_path)
    assert saved.exists()
    assert saved.read_bytes() == content


def test_cleanup_expired_respects_ttl(tmp_path: Path) -> None:
    policy = ImageDownloadPolicy(ttl_seconds=10)
    store = TelegramImageMediaStore(bot_token="token", policy=policy, base_dir=tmp_path)

    old_file = tmp_path / "old.png"
    old_file.write_bytes(b"old")
    fresh_file = tmp_path / "fresh.png"
    fresh_file.write_bytes(b"fresh")

    now = 2_000.0
    old_mtime = now - 100.0
    fresh_mtime = now - 1.0
    old_file.touch()
    fresh_file.touch()

    import os

    os.utime(old_file, (old_mtime, old_mtime))
    os.utime(fresh_file, (fresh_mtime, fresh_mtime))

    removed = store.cleanup_expired(now_ts=now)

    assert removed == 1
    assert old_file.exists() is False
    assert fresh_file.exists() is True


def test_no_raw_bytes_in_result_or_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = b"VERY-PRIVATE-BYTES"
    post = _JsonResponse(
        status_code=200,
        payload={"ok": True, "result": {"file_path": "img.bin", "file_size": len(secret)}},
    )
    stream = _FakeStreamResponse(
        status_code=200,
        headers={"content-type": "image/png"},
        chunks=[secret],
    )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda timeout=None: _FakeClient(post_result=post, stream_result=stream),
    )

    store = TelegramImageMediaStore(bot_token="token", base_dir=tmp_path)
    result = asyncio.run(store.download_image(attachment=_image_attachment()))

    assert result.ok is True
    assert "VERY-PRIVATE" not in result.reason_code
    assert result.file_path is not None
    assert "VERY-PRIVATE" not in result.file_path
