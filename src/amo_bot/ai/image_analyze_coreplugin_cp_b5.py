from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time

from .capability_policy import CapabilityDecisionResult
from .image_analyze_coreplugin_cp_b4 import (
    ImageAnalyzeRequest,
    _DEFAULT_NOT_CONFIGURED_MESSAGE,
    validate_image_analyze_input,
)

_DEFAULT_TIMEOUT_SECONDS = 2.0
_DEFAULT_MAX_OUTPUT_CHARS = 240
_ALLOWED_FAKE_PROVIDER = "fake"


class ImageAnalyzeProviderErrorKind(str, Enum):
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ImageAnalyzeProviderResult:
    summary: str


@dataclass(frozen=True, slots=True)
class ImageAnalyzeProviderError(Exception):
    kind: ImageAnalyzeProviderErrorKind
    message: str = ""


class ImageAnalyzeProvider:
    def analyze(self, *, request: ImageAnalyzeRequest, timeout_seconds: float) -> ImageAnalyzeProviderResult:
        raise NotImplementedError


class FakeImageAnalyzeProvider(ImageAnalyzeProvider):
    def analyze(self, *, request: ImageAnalyzeRequest, timeout_seconds: float) -> ImageAnalyzeProviderResult:
        prompt = request.prompt.strip() or "no prompt"
        base = f"fake-analysis ({request.locale}): {prompt}"
        return ImageAnalyzeProviderResult(summary=base)


@dataclass(frozen=True, slots=True)
class ImageAnalyzeAdapterConfig:
    provider_name: str = _ALLOWED_FAKE_PROVIDER
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_output_chars: int = _DEFAULT_MAX_OUTPUT_CHARS

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.max_output_chars < 1:
            raise ValueError("max_output_chars must be >= 1")


@dataclass(frozen=True, slots=True)
class ImageAnalyzeAuditMetadata:
    provider: str
    reason_code: str
    request_id: str
    prompt_length: int
    output_length: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class ImageAnalyzeAdapterResult:
    result: CapabilityDecisionResult
    reason_code: str
    message: str
    output_text: str = ""
    audit: ImageAnalyzeAuditMetadata | None = None


def execute_image_analyze_adapter(
    *,
    request: ImageAnalyzeRequest,
    config: ImageAnalyzeAdapterConfig,
    provider: ImageAnalyzeProvider,
) -> ImageAnalyzeAdapterResult:
    validation = validate_image_analyze_input(request)
    if not validation.ok:
        return ImageAnalyzeAdapterResult(
            result=CapabilityDecisionResult.DENY,
            reason_code=validation.reason_code,
            message=_DEFAULT_NOT_CONFIGURED_MESSAGE,
            audit=_build_audit(provider_name=_normalize_provider_name(config.provider_name), reason_code=validation.reason_code, request=request),
        )

    provider_name = _normalize_provider_name(config.provider_name)
    if provider_name != _ALLOWED_FAKE_PROVIDER:
        return ImageAnalyzeAdapterResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="provider_not_allowed",
            message=_DEFAULT_NOT_CONFIGURED_MESSAGE,
            audit=_build_audit(provider_name=provider_name, reason_code="provider_not_allowed", request=request),
        )

    started = time.monotonic()
    try:
        provider_result = provider.analyze(request=request, timeout_seconds=config.timeout_seconds)
        if (time.monotonic() - started) > config.timeout_seconds:
            raise TimeoutError("provider timeout")
    except (TimeoutError, ImageAnalyzeProviderError) as exc:
        reason = "provider_timeout" if isinstance(exc, TimeoutError) or (isinstance(exc, ImageAnalyzeProviderError) and exc.kind == ImageAnalyzeProviderErrorKind.TIMEOUT) else "provider_error"
        return ImageAnalyzeAdapterResult(
            result=CapabilityDecisionResult.DENY,
            reason_code=reason,
            message=_DEFAULT_NOT_CONFIGURED_MESSAGE,
            audit=_build_audit(provider_name=provider_name, reason_code=reason, request=request),
        )
    except Exception:
        return ImageAnalyzeAdapterResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="provider_error",
            message=_DEFAULT_NOT_CONFIGURED_MESSAGE,
            audit=_build_audit(provider_name=provider_name, reason_code="provider_error", request=request),
        )

    bounded_output = provider_result.summary.strip()
    truncated = len(bounded_output) > config.max_output_chars
    if truncated:
        bounded_output = bounded_output[: config.max_output_chars]

    return ImageAnalyzeAdapterResult(
        result=CapabilityDecisionResult.DENY,
        reason_code="not_configured",
        message=_DEFAULT_NOT_CONFIGURED_MESSAGE,
        output_text=bounded_output,
        audit=_build_audit(
            provider_name=provider_name,
            reason_code="not_configured",
            request=request,
            output_length=len(bounded_output),
            truncated=truncated,
        ),
    )


def _normalize_provider_name(value: str) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _build_audit(*, provider_name: str, reason_code: str, request: ImageAnalyzeRequest, output_length: int = 0, truncated: bool = False) -> ImageAnalyzeAuditMetadata:
    safe_provider = provider_name if provider_name else "unknown"
    request_id = f"image_analyze_{safe_provider}_r{len(request.image_ref.strip())}_p{len(request.prompt.strip())}"[:64]
    return ImageAnalyzeAuditMetadata(
        provider=safe_provider,
        reason_code=reason_code,
        request_id=request_id,
        prompt_length=len(request.prompt.strip()),
        output_length=output_length,
        truncated=truncated,
    )
