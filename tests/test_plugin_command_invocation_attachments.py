from __future__ import annotations

import asyncio
from pathlib import Path

from amo_bot.auth.roles import Role
from amo_bot.plugins.command_runtime import (
    CommandActor,
    CommandInvocation,
    PluginCommandExecutor,
)
from amo_bot.plugins.loader import PluginLoader
from amo_bot.telegram.update_parser import TelegramAttachment


class _DummySessionFactory:
    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _mk_executor(enable_image_attachments: bool = True) -> PluginCommandExecutor:
    return PluginCommandExecutor(
        loader=PluginLoader("/tmp"),
        session_factory=_DummySessionFactory(),
        send_message=lambda *_args, **_kwargs: None,
        reply=lambda *_args, **_kwargs: None,
        enable_image_attachments=enable_image_attachments,
    )


def _mk_invocation() -> CommandInvocation:
    return CommandInvocation(
        command_name="analyze_image",
        argument=None,
        chat_id=1,
        message_id=2,
        message_thread_id=None,
        attachments=(
            TelegramAttachment(
                source_kind="photo",
                type_hint="image",
                file_id="f1",
                file_unique_id="u1",
                width=100,
                height=80,
                size=123,
            ),
            TelegramAttachment(
                source_kind="document",
                type_hint="image_document",
                file_id="f2",
                file_unique_id="u2",
                width=50,
                height=40,
                size=22,
            ),
        ),
    )


def test_build_attachment_context_default_off_returns_empty() -> None:
    executor = _mk_executor(enable_image_attachments=False)
    result = asyncio.run(executor._build_attachment_context(invocation=_mk_invocation()))
    assert result == ()


def test_build_attachment_context_only_safe_fields_without_media_ref() -> None:
    executor = _mk_executor(enable_image_attachments=True)
    result = asyncio.run(executor._build_attachment_context(invocation=_mk_invocation()))

    assert len(result) == 2
    assert result[0] == {
        "source_kind": "photo",
        "type_hint": "image",
        "file_id": "f1",
        "file_unique_id": "u1",
        "width": 100,
        "height": 80,
        "size": 123,
    }
    assert "media_ref" not in result[0]


class _MediaResult:
    def __init__(self, ok: bool, reason_code: str = "ok") -> None:
        self.ok = ok
        self.reason_code = reason_code
        self.mime_type = "image/png"
        self.bytes_stored = 99
        self.file_path = "/private/path/should-not-leak.png"


class _MediaStoreOk:
    async def download_image(self, *, attachment: TelegramAttachment):
        return _MediaResult(ok=True)


class _MediaStoreError:
    async def download_image(self, *, attachment: TelegramAttachment):
        raise RuntimeError("download failed")


def test_build_attachment_context_includes_safe_media_ref_without_file_path() -> None:
    executor = _mk_executor(enable_image_attachments=True)
    executor._image_media_store = _MediaStoreOk()

    result = asyncio.run(executor._build_attachment_context(invocation=_mk_invocation()))
    assert "media_ref" in result[0]
    assert result[0]["media_ref"] == {
        "reason_code": "ok",
        "mime_type": "image/png",
        "bytes_stored": 99,
    }
    assert "file_path" not in result[0]["media_ref"]


def test_build_attachment_context_isolates_media_errors() -> None:
    executor = _mk_executor(enable_image_attachments=True)
    executor._image_media_store = _MediaStoreError()

    result = asyncio.run(executor._build_attachment_context(invocation=_mk_invocation()))
    assert len(result) == 2
    assert "media_ref" not in result[0]
    assert "media_ref" not in result[1]
