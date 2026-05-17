from __future__ import annotations

from dataclasses import dataclass

from .capability_policy import CapabilityDecisionResult

_MAX_LOCALE_LENGTH = 16
_MAX_PROMPT_LENGTH = 512
_DEFAULT_NOT_CONFIGURED_MESSAGE = "image analysis not configured"


@dataclass(frozen=True, slots=True)
class ImageAnalyzeRequest:
    image_ref: str
    prompt: str = ""
    locale: str = "en"


@dataclass(frozen=True, slots=True)
class ImageAnalyzeInputValidationResult:
    ok: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class ImageAnalyzeSettings:
    enabled: bool = False
    allow_network: bool = False
    provider_name: str = ""
    model_name: str = ""
    consent_required: bool = True
    min_role: str = "admin"


@dataclass(frozen=True, slots=True)
class ImageAnalyzePolicyContext:
    consent_granted: bool
    actor_role: str


@dataclass(frozen=True, slots=True)
class ImageAnalyzeExecutionResult:
    result: CapabilityDecisionResult
    reason_code: str
    message: str


@dataclass(frozen=True, slots=True)
class ImageAnalyzeManifest:
    capability_name: str
    capability_version: str
    enabled_by_default: bool
    requires_consent: bool
    min_role: str


def get_image_analyze_manifest(settings: ImageAnalyzeSettings | None = None) -> ImageAnalyzeManifest:
    safe_settings = settings or ImageAnalyzeSettings()
    return ImageAnalyzeManifest(
        capability_name="ki.image.analyze",
        capability_version="1.0.0",
        enabled_by_default=False,
        requires_consent=safe_settings.consent_required,
        min_role=_normalize_role(safe_settings.min_role),
    )


def validate_image_analyze_input(request: ImageAnalyzeRequest) -> ImageAnalyzeInputValidationResult:
    if not isinstance(request.image_ref, str):
        return ImageAnalyzeInputValidationResult(ok=False, reason_code="invalid_image_ref")
    image_ref = request.image_ref.strip()
    if not image_ref:
        return ImageAnalyzeInputValidationResult(ok=False, reason_code="invalid_image_ref")

    if not isinstance(request.prompt, str):
        return ImageAnalyzeInputValidationResult(ok=False, reason_code="invalid_prompt")
    if len(request.prompt.strip()) > _MAX_PROMPT_LENGTH:
        return ImageAnalyzeInputValidationResult(ok=False, reason_code="invalid_prompt")

    if not isinstance(request.locale, str):
        return ImageAnalyzeInputValidationResult(ok=False, reason_code="invalid_locale")
    locale = request.locale.strip().lower()
    if not locale or len(locale) > _MAX_LOCALE_LENGTH:
        return ImageAnalyzeInputValidationResult(ok=False, reason_code="invalid_locale")
    if not all(ch.isalpha() or ch in {"-", "_"} for ch in locale):
        return ImageAnalyzeInputValidationResult(ok=False, reason_code="invalid_locale")

    return ImageAnalyzeInputValidationResult(ok=True, reason_code="ok")


def execute_image_analyze_stub(
    *,
    request: ImageAnalyzeRequest,
    settings: ImageAnalyzeSettings,
    policy: ImageAnalyzePolicyContext,
) -> ImageAnalyzeExecutionResult:
    validation = validate_image_analyze_input(request)
    if not validation.ok:
        return ImageAnalyzeExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code=validation.reason_code,
            message=_DEFAULT_NOT_CONFIGURED_MESSAGE,
        )

    if not settings.enabled:
        return _deny("not_enabled")

    if settings.allow_network:
        return _deny("network_not_allowed")

    if settings.provider_name.strip() or settings.model_name.strip():
        return _deny("provider_not_allowed")

    if settings.consent_required and not policy.consent_granted:
        return _deny("consent_required")

    required_role = _normalize_role(settings.min_role)
    actor_role = _normalize_role(policy.actor_role)
    if _role_rank(actor_role) < _role_rank(required_role):
        return _deny("role_forbidden")

    return _deny("not_configured")


def _deny(reason_code: str) -> ImageAnalyzeExecutionResult:
    return ImageAnalyzeExecutionResult(
        result=CapabilityDecisionResult.DENY,
        reason_code=reason_code,
        message=_DEFAULT_NOT_CONFIGURED_MESSAGE,
    )


def _normalize_role(value: str) -> str:
    if not isinstance(value, str):
        return "admin"
    normalized = value.strip().lower()
    if normalized not in {"owner", "admin", "vip", "normal", "ignore"}:
        return "admin"
    return normalized


def _role_rank(role: str) -> int:
    ranks = {
        "ignore": 0,
        "normal": 1,
        "vip": 2,
        "admin": 3,
        "owner": 4,
    }
    return ranks.get(role, 0)
