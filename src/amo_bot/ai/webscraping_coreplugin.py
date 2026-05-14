from __future__ import annotations

import re
import time
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import urlparse

from .capability_policy import CapabilityDecisionResult

_MAX_URL_LENGTH = 2048
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DENIED_LOCAL_HOSTS = frozenset({"localhost", "localhost.localdomain", "local"})
_DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
_DEFAULT_MAX_OUTPUT_CHARS = 4000
_DEFAULT_TIMEOUT_SECONDS = 3.0
_DEFAULT_ALLOWED_MIME_PREFIXES = ("text/html", "application/xhtml+xml")
_DEFAULT_ROBOTS_PREFIXES = ("/",)


@dataclass(frozen=True, slots=True)
class WebscrapingInput:
    url: str


@dataclass(frozen=True, slots=True)
class WebscrapingValidationResult:
    ok: bool
    reason_code: str
    safe_host: str


@dataclass(frozen=True, slots=True)
class WebscrapingHTTPResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


class WebscrapingHTTPGet(Protocol):
    def __call__(self, url: str, timeout_seconds: float) -> WebscrapingHTTPResponse: ...


@dataclass(frozen=True, slots=True)
class WebscrapingPolicyConfig:
    enabled: bool = False
    allow_local_hosts: bool = False
    allowlist_hosts: frozenset[str] = frozenset()
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES
    max_output_chars: int = _DEFAULT_MAX_OUTPUT_CHARS
    allowed_mime_prefixes: tuple[str, ...] = _DEFAULT_ALLOWED_MIME_PREFIXES
    enforce_robots: bool = True
    robots_disallow_prefixes: tuple[str, ...] = _DEFAULT_ROBOTS_PREFIXES


@dataclass(frozen=True, slots=True)
class WebscrapingExecutionResult:
    result: CapabilityDecisionResult
    reason_code: str
    audit_payload: dict[str, str]
    extracted_text: str = ""


def _is_private_or_local_ip(host: str) -> bool:
    try:
        ip = ip_address(host)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _safe_host(parsed_host: str | None) -> str:
    if not parsed_host:
        return "unknown"
    host = parsed_host.strip().lower()
    if len(host) > 80:
        host = host[:80]
    return host


def _safe_path(path: str) -> str:
    cleaned = (path or "/").strip()
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    if cleaned == "/":
        return "/"
    return "/..."


def _safe_status_code(status_code: int) -> str:
    if 100 <= status_code <= 599:
        return str(status_code)
    return "unknown"


def _audit_payload(*, reason_code: str, host: str, path: str, status_code: int | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    payload: dict[str, str] = {"reason": reason_code, "host": host, "path": path}
    if status_code is not None:
        payload["status_code"] = _safe_status_code(status_code)
    if extra:
        payload.update(extra)
    return payload


def validate_webscraping_input(request: WebscrapingInput, *, allow_local: bool = False) -> WebscrapingValidationResult:
    if not isinstance(request.url, str):
        return WebscrapingValidationResult(ok=False, reason_code="invalid_url", safe_host="unknown")

    raw_url = request.url.strip()
    if not raw_url or len(raw_url) > _MAX_URL_LENGTH:
        return WebscrapingValidationResult(ok=False, reason_code="invalid_url", safe_host="unknown")

    parsed = urlparse(raw_url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip().lower()
    safe = _safe_host(parsed.hostname)

    if scheme not in _ALLOWED_SCHEMES:
        return WebscrapingValidationResult(ok=False, reason_code="scheme_not_allowed", safe_host=safe)

    if not host:
        return WebscrapingValidationResult(ok=False, reason_code="invalid_url", safe_host=safe)

    if not allow_local:
        if host in _DENIED_LOCAL_HOSTS:
            return WebscrapingValidationResult(ok=False, reason_code="host_not_allowed", safe_host=safe)
        if _is_private_or_local_ip(host):
            return WebscrapingValidationResult(ok=False, reason_code="host_not_allowed", safe_host=safe)

    return WebscrapingValidationResult(ok=True, reason_code="ok", safe_host=safe)


def _extract_visible_text(html: str) -> str:
    no_script = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    stripped = re.sub(r"(?s)<[^>]+>", " ", no_script)
    return " ".join(stripped.split())


def _is_allowed_mime(content_type: str | None, allowed_prefixes: tuple[str, ...]) -> bool:
    if not content_type:
        return False
    normalized = content_type.split(";", 1)[0].strip().lower()
    return any(normalized.startswith(prefix.lower()) for prefix in allowed_prefixes)


def _is_path_disallowed_by_robots(path: str, disallow_prefixes: tuple[str, ...]) -> bool:
    normalized_path = path or "/"
    return any(normalized_path.startswith(prefix) for prefix in disallow_prefixes if prefix)


def execute_webscraping_static_html(
    *,
    request: WebscrapingInput,
    policy: WebscrapingPolicyConfig,
    http_get: WebscrapingHTTPGet,
) -> WebscrapingExecutionResult:
    parsed = urlparse(request.url.strip() if isinstance(request.url, str) else "")
    safe_host = _safe_host(parsed.hostname)
    raw_path = parsed.path or "/"
    safe_path = _safe_path(raw_path)

    validation = validate_webscraping_input(request, allow_local=policy.allow_local_hosts)
    if not validation.ok:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code=validation.reason_code,
            audit_payload=_audit_payload(reason_code=validation.reason_code, host=safe_host, path=safe_path),
        )

    if not policy.enabled:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="not_enabled",
            audit_payload=_audit_payload(reason_code="not_enabled", host=safe_host, path=safe_path),
        )

    if validation.safe_host not in policy.allowlist_hosts:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="url_not_allowlisted",
            audit_payload=_audit_payload(reason_code="url_not_allowlisted", host=safe_host, path=safe_path),
        )

    if policy.enforce_robots and _is_path_disallowed_by_robots(raw_path, policy.robots_disallow_prefixes):
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="robots_disallowed",
            audit_payload=_audit_payload(reason_code="robots_disallowed", host=safe_host, path=safe_path),
        )

    if policy.timeout_seconds <= 0:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="invalid_timeout",
            audit_payload=_audit_payload(reason_code="invalid_timeout", host=safe_host, path=safe_path),
        )

    started = time.monotonic()
    try:
        response = http_get(request.url.strip(), policy.timeout_seconds)
    except TimeoutError:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="fetch_timeout",
            audit_payload=_audit_payload(reason_code="fetch_timeout", host=safe_host, path=safe_path),
        )
    except Exception:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="fetch_failed",
            audit_payload=_audit_payload(reason_code="fetch_failed", host=safe_host, path=safe_path),
        )

    if (time.monotonic() - started) > policy.timeout_seconds:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="fetch_timeout",
            audit_payload=_audit_payload(reason_code="fetch_timeout", host=safe_host, path=safe_path),
        )

    if len(response.body) > policy.max_response_bytes:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="response_too_large",
            audit_payload=_audit_payload(
                reason_code="response_too_large",
                host=safe_host,
                path=safe_path,
                status_code=response.status_code,
                extra={"bytes": str(len(response.body))},
            ),
        )

    content_type = response.headers.get("content-type") or response.headers.get("Content-Type")
    if not _is_allowed_mime(content_type, policy.allowed_mime_prefixes):
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="mime_not_allowed",
            audit_payload=_audit_payload(
                reason_code="mime_not_allowed",
                host=safe_host,
                path=safe_path,
                status_code=response.status_code,
            ),
        )

    if response.status_code < 200 or response.status_code >= 300:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="http_status_not_ok",
            audit_payload=_audit_payload(
                reason_code="http_status_not_ok",
                host=safe_host,
                path=safe_path,
                status_code=response.status_code,
            ),
        )

    text = _extract_visible_text(response.body.decode("utf-8", errors="replace"))
    capped_text = text[: policy.max_output_chars]
    return WebscrapingExecutionResult(
        result=CapabilityDecisionResult.ALLOW,
        reason_code="ok",
        extracted_text=capped_text,
        audit_payload=_audit_payload(reason_code="ok", host=safe_host, path=safe_path, status_code=response.status_code),
    )


def execute_webscraping_noop(
    *,
    request: WebscrapingInput,
    policy_allow_webscraping: bool = False,
    policy_allow_local_hosts: bool = False,
) -> WebscrapingExecutionResult:
    validation = validate_webscraping_input(request, allow_local=policy_allow_local_hosts)
    parsed = urlparse(request.url.strip() if isinstance(request.url, str) else "")
    safe_path = _safe_path(parsed.path or "/")
    if not validation.ok:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code=validation.reason_code,
            audit_payload=_audit_payload(reason_code=validation.reason_code, host=validation.safe_host, path=safe_path),
        )

    if not policy_allow_webscraping:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="not_enabled",
            audit_payload=_audit_payload(reason_code="not_enabled", host=validation.safe_host, path=safe_path),
        )

    return WebscrapingExecutionResult(
        result=CapabilityDecisionResult.DENY,
        reason_code="not_implemented",
        audit_payload=_audit_payload(reason_code="not_implemented", host=validation.safe_host, path=safe_path),
    )
