from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

from .capability_policy import CapabilityDecisionResult

_MAX_URL_LENGTH = 2048
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DENIED_LOCAL_HOSTS = frozenset({"localhost", "localhost.localdomain", "local"})


@dataclass(frozen=True, slots=True)
class WebscrapingInput:
    url: str


@dataclass(frozen=True, slots=True)
class WebscrapingValidationResult:
    ok: bool
    reason_code: str
    safe_host: str


@dataclass(frozen=True, slots=True)
class WebscrapingExecutionResult:
    result: CapabilityDecisionResult
    reason_code: str
    audit_payload: dict[str, str]



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



def execute_webscraping_noop(
    *,
    request: WebscrapingInput,
    policy_allow_webscraping: bool = False,
    policy_allow_local_hosts: bool = False,
) -> WebscrapingExecutionResult:
    validation = validate_webscraping_input(request, allow_local=policy_allow_local_hosts)
    if not validation.ok:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code=validation.reason_code,
            audit_payload={"reason": validation.reason_code, "host": validation.safe_host},
        )

    if not policy_allow_webscraping:
        return WebscrapingExecutionResult(
            result=CapabilityDecisionResult.DENY,
            reason_code="not_enabled",
            audit_payload={"reason": "not_enabled", "host": validation.safe_host},
        )

    return WebscrapingExecutionResult(
        result=CapabilityDecisionResult.DENY,
        reason_code="not_implemented",
        audit_payload={"reason": "not_implemented", "host": validation.safe_host},
    )
