from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import httpx

from amo_bot.telegram.update_parser import TelegramAttachment

_DEFAULT_ALLOWED_MIME_TYPES: Final[frozenset[str]] = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
})


@dataclass(frozen=True, slots=True)
class ImageDownloadPolicy:
    allowed_mime_types: frozenset[str] = _DEFAULT_ALLOWED_MIME_TYPES
    max_bytes: int = 5 * 1024 * 1024
    timeout_seconds: float = 10.0
    ttl_seconds: int = 30 * 60


@dataclass(frozen=True, slots=True)
class ImageDownloadResult:
    ok: bool
    reason_code: str
    file_path: str | None = None
    bytes_stored: int = 0
    mime_type: str | None = None


class TelegramImageMediaStore:
    """Secure temporary image download store for Telegram attachments.

    The service validates attachment metadata, enforces bounded download limits,
    and stores bytes in a short-lived temp directory.
    """

    def __init__(self, *, bot_token: str, policy: ImageDownloadPolicy | None = None, base_dir: str | Path | None = None) -> None:
        self._bot_token = bot_token
        self._policy = policy or ImageDownloadPolicy()
        if self._policy.max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")
        if self._policy.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self._policy.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")

        if base_dir is None:
            root = Path(tempfile.gettempdir()) / "amo-telegram-images"
        else:
            root = Path(base_dir)
        root.mkdir(parents=True, exist_ok=True)
        self._root = root

    async def download_image(self, *, attachment: TelegramAttachment) -> ImageDownloadResult:
        if attachment.type_hint not in {"image", "image_document"}:
            return ImageDownloadResult(ok=False, reason_code="deny_type_not_image")

        if attachment.size is not None and attachment.size > self._policy.max_bytes:
            return ImageDownloadResult(ok=False, reason_code="deny_size_limit")

        try:
            file_info = await self._telegram_call("getFile", {"file_id": attachment.file_id})
        except TimeoutError:
            return ImageDownloadResult(ok=False, reason_code="error_timeout")
        except Exception:
            return ImageDownloadResult(ok=False, reason_code="error_get_file")

        if not isinstance(file_info, dict):
            return ImageDownloadResult(ok=False, reason_code="error_get_file")

        file_path_raw = file_info.get("file_path")
        if not isinstance(file_path_raw, str) or not file_path_raw.strip():
            return ImageDownloadResult(ok=False, reason_code="error_missing_file_path")

        raw_file_size = file_info.get("file_size")
        file_size = self._safe_int(raw_file_size)
        if file_size is not None and file_size > self._policy.max_bytes:
            return ImageDownloadResult(ok=False, reason_code="deny_size_limit")

        remote_url = f"https://api.telegram.org/file/bot{self._bot_token}/{file_path_raw}"

        try:
            download = await self._bounded_download(remote_url)
        except TimeoutError:
            return ImageDownloadResult(ok=False, reason_code="error_timeout")
        except Exception:
            return ImageDownloadResult(ok=False, reason_code="error_download_failed")

        if not download.ok:
            return download

        return download

    def cleanup_expired(self, *, now_ts: float | None = None) -> int:
        now = time.time() if now_ts is None else now_ts
        removed = 0
        for entry in self._root.iterdir():
            try:
                stat = entry.stat()
            except FileNotFoundError:
                continue
            if now - stat.st_mtime <= self._policy.ttl_seconds:
                continue
            if entry.is_file():
                entry.unlink(missing_ok=True)
                removed += 1
        return removed

    async def _telegram_call(self, method: str, payload: dict[str, object]) -> object:
        url = f"https://api.telegram.org/bot{self._bot_token}/{method}"
        timeout = httpx.Timeout(self._policy.timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise TimeoutError from exc

        if response.status_code >= 400:
            raise RuntimeError(f"telegram_http_{response.status_code}")

        data = response.json()
        if not data.get("ok", False):
            raise RuntimeError("telegram_api_error")
        return data.get("result")

    async def _bounded_download(self, remote_url: str) -> ImageDownloadResult:
        timeout = httpx.Timeout(self._policy.timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", remote_url) as response:
                    if response.status_code >= 400:
                        return ImageDownloadResult(ok=False, reason_code="error_download_http")

                    content_type_raw = response.headers.get("content-type", "")
                    mime_type = content_type_raw.split(";", 1)[0].strip().casefold()
                    if mime_type not in self._policy.allowed_mime_types:
                        return ImageDownloadResult(ok=False, reason_code="deny_mime_not_allowed")

                    declared_len = self._safe_int(response.headers.get("content-length"))
                    if declared_len is not None and declared_len > self._policy.max_bytes:
                        return ImageDownloadResult(ok=False, reason_code="deny_size_limit")

                    suffix = self._suffix_for_mime(mime_type)
                    tmp_path = self._next_file_path(suffix=suffix)
                    total = 0

                    with tmp_path.open("wb") as fh:
                        async for chunk in response.aiter_bytes(64 * 1024):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > self._policy.max_bytes:
                                fh.close()
                                tmp_path.unlink(missing_ok=True)
                                return ImageDownloadResult(ok=False, reason_code="deny_size_limit")
                            fh.write(chunk)

                    return ImageDownloadResult(
                        ok=True,
                        reason_code="ok",
                        file_path=str(tmp_path),
                        bytes_stored=total,
                        mime_type=mime_type,
                    )
        except httpx.TimeoutException as exc:
            raise TimeoutError from exc
        except asyncio.TimeoutError as exc:
            raise TimeoutError from exc

    def _next_file_path(self, *, suffix: str) -> Path:
        token = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
        return self._root / f"{token}{suffix}"

    @staticmethod
    def _suffix_for_mime(mime_type: str) -> str:
        if mime_type == "image/jpeg":
            return ".jpg"
        if mime_type == "image/png":
            return ".png"
        if mime_type == "image/webp":
            return ".webp"
        if mime_type == "image/gif":
            return ".gif"
        return ".img"

    @staticmethod
    def _safe_int(raw: object) -> int | None:
        try:
            return int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
