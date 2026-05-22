from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import mimetypes
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from amo_bot.auth.roles import Role
from amo_bot.db.models import ImageAnalyzeAuditEvent, ImageAnalyzeQuotaCounter
from amo_bot.db.repositories import TopicAgentMemoryRepository


@dataclass(frozen=True, slots=True)
class ImageAnalyzeOrchestratorRequest:
    user_id: int
    role: Role
    chat_id: int
    message_thread_id: int | None
    command: str
    provider: str = "fake"
    reply_to_image: dict[str, Any] | None = None
    image_ok: bool | None = None
    image_reason_code: str | None = None
    prompt: str = ""


@dataclass(frozen=True, slots=True)
class ImageAnalyzeProviderRequest:
    image_ref: str
    prompt: str
    user_id: int
    chat_id: int
    message_thread_id: int | None
    image_path: str | None = None


@dataclass(frozen=True, slots=True)
class ImageAnalyzeProviderResult:
    provider: str
    summary: str


@dataclass(frozen=True, slots=True)
class ImageAnalyzeOrchestratorResult:
    allowed: bool
    outcome: str
    provider_called: bool = False
    provider_result: ImageAnalyzeProviderResult | None = None
    day: str = ""
    count: int = 0


class ImageAnalyzeProvider(Protocol):
    name: str

    def analyze(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult: ...


class AsyncImageAnalyzeProvider(Protocol):
    name: str

    async def analyze_async(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult: ...


class ImageAnalyzeProviderError(RuntimeError):
    pass


class ImageAnalyzeDenyHook(Protocol):
    def __call__(self, request: ImageAnalyzeOrchestratorRequest) -> str | None: ...


class FakeImageAnalyzeRuntimeProvider:
    name = "fake"

    def analyze(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult:
        prompt = request.prompt.strip() or "describe image"
        return ImageAnalyzeProviderResult(provider=self.name, summary=f"fake image analysis for {request.image_ref}: {prompt}")

    async def analyze_async(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult:
        return self.analyze(request)


class ImageAnalyzeOrchestrator:
    def __init__(
        self,
        *,
        provider: ImageAnalyzeProvider | None = None,
        deny_hook: ImageAnalyzeDenyHook | None = None,
        session_factory: sessionmaker | None = None,
        role_daily_quota: dict[Role, int | None] | None = None,
        max_image_bytes: int | None = None,
        allowed_mime_types: set[str] | None = None,
    ) -> None:
        self._provider = provider or FakeImageAnalyzeRuntimeProvider()
        self._deny_hook = deny_hook
        self._session_factory = session_factory
        self._role_daily_quota = role_daily_quota or {}
        self._max_image_bytes = max_image_bytes
        self._allowed_mime_types = {item.strip().lower() for item in (allowed_mime_types or set()) if isinstance(item, str) and item.strip()} or None

    def evaluate_and_maybe_invoke_provider(
        self,
        *,
        request: ImageAnalyzeOrchestratorRequest,
        provider_call: ImageAnalyzeProvider | None = None,
        now: datetime | None = None,
    ) -> ImageAnalyzeOrchestratorResult:
        day = (now or datetime.now(UTC)).date().isoformat()
        image = request.reply_to_image or {}
        image_ok = request.image_ok if request.image_ok is not None else bool(image.get("ok") is True)
        if not image_ok:
            reason = request.image_reason_code or _safe_reason(image.get("reason_code")) or "invalid_image"
            return self._audit_and_result(request=request, day=day, count=0, outcome=reason, allowed=False)

        deny_reason = self._deny_hook(request) if self._deny_hook is not None else None
        if deny_reason:
            return self._audit_and_result(request=request, day=day, count=0, outcome=_safe_reason(deny_reason) or "denied", allowed=False)

        if self._session_factory is not None:
            topic_mode = self._resolve_topic_image_analysis_mode(request=request)
            if topic_mode != "enabled":
                return self._audit_and_result(request=request, day=day, count=0, outcome="topic_disabled", allowed=False)

        quota = self._role_daily_quota.get(request.role)
        if quota == 0:
            return self._audit_and_result(request=request, day=day, count=0, outcome="role_disabled", allowed=False)

        count_before = self._current_count(request=request, day=day)
        if isinstance(quota, int) and quota > 0 and count_before >= quota:
            return self._audit_and_result(request=request, day=day, count=count_before, outcome="quota_exceeded", allowed=False)

        image_guard_reason = self._guard_image_payload(image=image)
        if image_guard_reason is not None:
            return self._audit_and_result(request=request, day=day, count=count_before, outcome=image_guard_reason, allowed=False)

        provider = provider_call or self._provider
        try:
            provider_result = provider.analyze(
                ImageAnalyzeProviderRequest(
                    image_ref=_image_ref(image),
                    prompt=request.prompt,
                    user_id=request.user_id,
                    chat_id=request.chat_id,
                    message_thread_id=request.message_thread_id,
                    image_path=_image_path(image),
                )
            )
        except TimeoutError:
            return self._audit_and_result(request=request, day=day, count=count_before, outcome="provider_timeout", allowed=False)
        except ImageAnalyzeProviderError:
            return self._audit_and_result(request=request, day=day, count=count_before, outcome="provider_error", allowed=False)
        except Exception:
            return self._audit_and_result(request=request, day=day, count=count_before, outcome="provider_error", allowed=False)

        if not provider_result.summary.strip():
            return self._audit_and_result(request=request, day=day, count=count_before, outcome="provider_empty", allowed=False)

        new_count = self._increment_counter(request=request, day=day) if self._session_factory is not None else (count_before + 1)
        self._audit(request=request, day=day, count=new_count, outcome="allowed", provider=provider_result.provider)
        return ImageAnalyzeOrchestratorResult(
            allowed=True,
            outcome="allowed",
            provider_called=True,
            provider_result=provider_result,
            day=day,
            count=new_count,
        )


    async def evaluate_and_maybe_invoke_provider_async(
        self,
        *,
        request: ImageAnalyzeOrchestratorRequest,
        provider_call: ImageAnalyzeProvider | AsyncImageAnalyzeProvider | None = None,
        now: datetime | None = None,
    ) -> ImageAnalyzeOrchestratorResult:
        day = (now or datetime.now(UTC)).date().isoformat()
        image = request.reply_to_image or {}
        image_ok = request.image_ok if request.image_ok is not None else bool(image.get("ok") is True)
        if not image_ok:
            reason = request.image_reason_code or _safe_reason(image.get("reason_code")) or "invalid_image"
            return self._audit_and_result(request=request, day=day, count=0, outcome=reason, allowed=False)

        deny_reason = self._deny_hook(request) if self._deny_hook is not None else None
        if deny_reason:
            return self._audit_and_result(request=request, day=day, count=0, outcome=_safe_reason(deny_reason) or "denied", allowed=False)

        if self._session_factory is not None:
            topic_mode = self._resolve_topic_image_analysis_mode(request=request)
            if topic_mode != "enabled":
                return self._audit_and_result(request=request, day=day, count=0, outcome="topic_disabled", allowed=False)

        quota = self._role_daily_quota.get(request.role)
        if quota == 0:
            return self._audit_and_result(request=request, day=day, count=0, outcome="role_disabled", allowed=False)

        count_before = self._current_count(request=request, day=day)
        if isinstance(quota, int) and quota > 0 and count_before >= quota:
            return self._audit_and_result(request=request, day=day, count=count_before, outcome="quota_exceeded", allowed=False)

        image_guard_reason = self._guard_image_payload(image=image)
        if image_guard_reason is not None:
            return self._audit_and_result(request=request, day=day, count=count_before, outcome=image_guard_reason, allowed=False)

        provider = provider_call or self._provider
        provider_request = ImageAnalyzeProviderRequest(
            image_ref=_image_ref(image),
            prompt=request.prompt,
            user_id=request.user_id,
            chat_id=request.chat_id,
            message_thread_id=request.message_thread_id,
            image_path=_image_path(image),
        )
        try:
            analyze_async = getattr(provider, "analyze_async", None)
            if callable(analyze_async):
                provider_result = await analyze_async(provider_request)
            else:
                provider_result = provider.analyze(provider_request)
        except TimeoutError:
            return self._audit_and_result(request=request, day=day, count=count_before, outcome="provider_timeout", allowed=False)
        except ImageAnalyzeProviderError:
            return self._audit_and_result(request=request, day=day, count=count_before, outcome="provider_error", allowed=False)
        except Exception:
            return self._audit_and_result(request=request, day=day, count=count_before, outcome="provider_error", allowed=False)

        if not provider_result.summary.strip():
            return self._audit_and_result(request=request, day=day, count=count_before, outcome="provider_empty", allowed=False)

        new_count = self._increment_counter(request=request, day=day) if self._session_factory is not None else (count_before + 1)
        self._audit(request=request, day=day, count=new_count, outcome="allowed", provider=provider_result.provider)
        return ImageAnalyzeOrchestratorResult(
            allowed=True,
            outcome="allowed",
            provider_called=True,
            provider_result=provider_result,
            day=day,
            count=new_count,
        )

    def _resolve_topic_image_analysis_mode(self, *, request: ImageAnalyzeOrchestratorRequest) -> str:
        with self._session_factory() as session:
            repo = TopicAgentMemoryRepository(session)
            config = repo.get_config(scope_type="topic", chat_id=request.chat_id, topic_id=request.message_thread_id)
            mode = (config.image_analysis_mode if config is not None else "inherit")
            normalized = (mode or "inherit").strip().lower()
            if normalized not in {"enabled", "disabled", "inherit"}:
                return "inherit"
            return normalized

    def _current_count(self, *, request: ImageAnalyzeOrchestratorRequest, day: str) -> int:
        if self._session_factory is None:
            return 0
        with self._session_factory() as session:
            row = session.scalar(
                select(ImageAnalyzeQuotaCounter).where(
                    ImageAnalyzeQuotaCounter.user_id == request.user_id,
                    ImageAnalyzeQuotaCounter.role == request.role.value,
                    ImageAnalyzeQuotaCounter.chat_id == request.chat_id,
                    ImageAnalyzeQuotaCounter.message_thread_id == request.message_thread_id,
                    ImageAnalyzeQuotaCounter.day == day,
                )
            )
            return 0 if row is None else int(row.count)

    def _increment_counter(self, *, request: ImageAnalyzeOrchestratorRequest, day: str) -> int:
        with self._session_factory() as session:
            row = session.scalar(
                select(ImageAnalyzeQuotaCounter).where(
                    ImageAnalyzeQuotaCounter.user_id == request.user_id,
                    ImageAnalyzeQuotaCounter.role == request.role.value,
                    ImageAnalyzeQuotaCounter.chat_id == request.chat_id,
                    ImageAnalyzeQuotaCounter.message_thread_id == request.message_thread_id,
                    ImageAnalyzeQuotaCounter.day == day,
                )
            )
            if row is None:
                row = ImageAnalyzeQuotaCounter(
                    user_id=request.user_id,
                    role=request.role.value,
                    chat_id=request.chat_id,
                    message_thread_id=request.message_thread_id,
                    day=day,
                    count=1,
                )
                session.add(row)
            else:
                row.count = int(row.count) + 1
            session.commit()
            return int(row.count)

    def _audit(self, *, request: ImageAnalyzeOrchestratorRequest, day: str, count: int, outcome: str, provider: str | None = None) -> None:
        if self._session_factory is None:
            return
        with self._session_factory() as session:
            session.add(
                ImageAnalyzeAuditEvent(
                    user_id=request.user_id,
                    role=request.role.value,
                    chat_id=request.chat_id,
                    message_thread_id=request.message_thread_id,
                    day=day,
                    count=count,
                    command=request.command,
                    provider=provider or request.provider,
                    outcome=outcome,
                )
            )
            session.commit()

    def _audit_and_result(self, *, request: ImageAnalyzeOrchestratorRequest, day: str, count: int, outcome: str, allowed: bool) -> ImageAnalyzeOrchestratorResult:
        self._audit(request=request, day=day, count=count, outcome=outcome)
        return ImageAnalyzeOrchestratorResult(allowed=allowed, outcome=outcome, day=day, count=count)

    def _guard_image_payload(self, *, image: dict[str, Any]) -> str | None:
        media_ref = image.get("media_ref")
        media_ref_dict = media_ref if isinstance(media_ref, dict) else {}
        mime_type = _safe_mime(media_ref_dict.get("mime_type"))
        if mime_type is None:
            mime_type = _safe_mime(image.get("mime_type"))
        if mime_type is None:
            mime_type = _mime_from_type_hint(image.get("type_hint"))
        if self._allowed_mime_types is not None:
            if mime_type is None or mime_type not in self._allowed_mime_types:
                return "invalid_type"

        if self._max_image_bytes is not None and self._max_image_bytes > 0:
            image_size = _safe_non_negative_int(media_ref_dict.get("bytes_stored"))
            if image_size is None:
                image_size = _safe_non_negative_int(image.get("size"))
            if image_size is not None and image_size > self._max_image_bytes:
                return "oversize"
        return None


def _image_ref(image: dict[str, Any]) -> str:
    media_ref = image.get("media_ref")
    if isinstance(media_ref, dict):
        unique = media_ref.get("file_unique_id") or image.get("file_unique_id")
        if isinstance(unique, str) and unique.strip():
            return f"telegram-file:{unique.strip()}"
    type_hint = image.get("type_hint")
    if isinstance(type_hint, str) and type_hint.strip():
        return f"telegram-{type_hint.strip()}"
    return "telegram-image"


def _safe_reason(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_")
    if not normalized or len(normalized) > 64:
        return None
    if not all(ch.isalnum() or ch == "_" for ch in normalized):
        return None
    return normalized


def _safe_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return None


def _safe_mime(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 128:
        return None
    if "/" not in normalized:
        return None
    return normalized


def _mime_from_type_hint(type_hint: object) -> str | None:
    if not isinstance(type_hint, str):
        return None
    normalized = type_hint.strip().lower()
    if not normalized:
        return None
    if normalized in {"image", "image_document", "photo"}:
        return "image/*"
    suffix = Path(normalized).suffix
    if suffix:
        guessed, _ = mimetypes.guess_type(f"x{suffix}")
        if guessed:
            return guessed.lower()
    return None


def _image_path(image: dict[str, Any]) -> str | None:
    file_path = image.get("_file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        media_ref = image.get("media_ref")
        if not isinstance(media_ref, dict):
            return None
        file_path = media_ref.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return None
    return file_path.strip()
