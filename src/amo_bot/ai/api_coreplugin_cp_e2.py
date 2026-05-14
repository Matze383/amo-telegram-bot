from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .api_capability_registry import APICapabilityRegistry

_SAFE_REASON_CODES = {
    "ok",
    "unknown_service",
    "unknown_endpoint",
    "raw_url_mode_forbidden",
    "invalid_payload",
    "method_not_allowed",
    "network_timeout",
    "network_error",
    "response_too_large",
}


@dataclass(frozen=True, slots=True)
class APICorepluginExecutionInput:
    service_id: str
    endpoint_key: str
    payload: Mapping[str, Any] | None = None
    raw_url: str | None = None


@dataclass(frozen=True, slots=True)
class APICorepluginExecutionResult:
    allowed: bool
    reason_code: str
    http_status: int | None
    data: dict[str, Any] | None
    audit_summary: dict[str, Any]


def _sanitize_reason_code(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in _SAFE_REASON_CODES:
        return normalized
    return "network_error"


def _sanitize_json_like(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, inner in value.items():
            lower = str(key).lower()
            if any(token in lower for token in ("secret", "token", "password", "api_key", "apikey", "authorization")):
                out[str(key)] = "***REDACTED***"
            else:
                out[str(key)] = _sanitize_json_like(inner)
        return out
    if isinstance(value, list):
        return [_sanitize_json_like(item) for item in value]
    return value




def _sanitize_plaintext_secrets(text: str) -> str:
    patterns = [
        re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+\-/=]+)"),
        re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)([^\s,;]+)"),
        re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)"),
        re.compile(r"(?i)(token\s*[:=]\s*)([^\s,;]+)"),
        re.compile(r"(?i)(password\s*[:=]\s*)([^\s,;]+)"),
    ]
    out = text
    for pattern in patterns:
        out = pattern.sub(lambda m: f"{m.group(1)}***REDACTED***", out)
    return out


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _build_query(payload: Mapping[str, Any]) -> str:
    items: list[tuple[str, str]] = []
    for key, value in payload.items():
        if isinstance(value, list):
            for item in value:
                items.append((str(key), str(item)))
        else:
            items.append((str(key), str(value)))
    return urllib.parse.urlencode(items, doseq=True)


def execute_api_request_mvp(
    *,
    registry: APICapabilityRegistry,
    request: APICorepluginExecutionInput,
    config_values: Mapping[str, str],
    secret_values: Mapping[str, str],
    timeout_seconds: float = 3.0,
    max_retries: int = 1,
    backoff_seconds: float = 0.2,
    max_response_chars: int = 3000,
    opener: Callable[[urllib.request.Request, float], Any] | None = None,
) -> APICorepluginExecutionResult:
    payload = request.payload if isinstance(request.payload, Mapping) else {}

    validation = registry.validate_request(
        service_id=request.service_id,
        endpoint_key=request.endpoint_key,
        payload=payload,
        raw_url=request.raw_url,
    )
    if not validation.allowed:
        reason = _sanitize_reason_code(validation.reason_code)
        return APICorepluginExecutionResult(
            allowed=False,
            reason_code=reason,
            http_status=None,
            data=None,
            audit_summary={
                "service_id": request.service_id,
                "endpoint_key": request.endpoint_key,
                "reason_code": reason,
                "raw_url_used": bool(request.raw_url),
            },
        )

    lookup = registry.get_endpoint(service_id=request.service_id, endpoint_key=request.endpoint_key)
    if not lookup.allowed or lookup.service is None or lookup.endpoint is None:
        reason = _sanitize_reason_code(lookup.reason_code)
        return APICorepluginExecutionResult(
            allowed=False,
            reason_code=reason,
            http_status=None,
            data=None,
            audit_summary={
                "service_id": request.service_id,
                "endpoint_key": request.endpoint_key,
                "reason_code": reason,
            },
        )

    method = lookup.endpoint.method.strip().upper()
    if method not in {"GET", "POST"}:
        return APICorepluginExecutionResult(
            allowed=False,
            reason_code="method_not_allowed",
            http_status=None,
            data=None,
            audit_summary={
                "service_id": lookup.service.service_id,
                "endpoint_key": lookup.endpoint.endpoint_key,
                "reason_code": "method_not_allowed",
                "method": method,
            },
        )

    base_url = config_values.get(lookup.service.base_url_ref, "").strip()
    if not base_url:
        return APICorepluginExecutionResult(
            allowed=False,
            reason_code="network_error",
            http_status=None,
            data=None,
            audit_summary={
                "service_id": lookup.service.service_id,
                "endpoint_key": lookup.endpoint.endpoint_key,
                "reason_code": "network_error",
                "detail": "missing_base_url_ref",
            },
        )

    endpoint_path = lookup.endpoint.path_template
    url = base_url.rstrip("/") + endpoint_path

    headers = {"Accept": "application/json"}
    if lookup.service.auth is not None:
        secret = secret_values.get(lookup.service.auth.secret_ref)
        if secret:
            headers[lookup.service.auth.header_name] = secret

    body_bytes: bytes | None = None
    if method == "GET":
        query = _build_query(payload)
        if query:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{query}"
    else:
        headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(payload).encode("utf-8")

    request_obj = urllib.request.Request(url=url, method=method, headers=headers, data=body_bytes)

    call = opener
    if call is None:
        def _default_open(req: urllib.request.Request, timeout: float) -> Any:
            return urllib.request.urlopen(req, timeout=timeout)

        call = _default_open

    attempts = 0
    while True:
        attempts += 1
        try:
            with call(request_obj, timeout_seconds) as response:
                status = getattr(response, "status", None)
                raw_text = response.read().decode("utf-8", errors="replace")
                sanitized_text = _sanitize_plaintext_secrets(raw_text)
                truncated_text, was_truncated = _truncate_text(sanitized_text, max_response_chars)
                if was_truncated:
                    parsed_data: dict[str, Any] = {"text": truncated_text, "truncated": True}
                    reason = "response_too_large"
                else:
                    try:
                        parsed = json.loads(truncated_text)
                        parsed_data = _sanitize_json_like(parsed)
                    except json.JSONDecodeError:
                        parsed_data = {"text": truncated_text}
                    reason = "ok"

                return APICorepluginExecutionResult(
                    allowed=True,
                    reason_code=reason,
                    http_status=status,
                    data=parsed_data,
                    audit_summary={
                        "service_id": lookup.service.service_id,
                        "endpoint_key": lookup.endpoint.endpoint_key,
                        "reason_code": reason,
                        "method": method,
                        "http_status": status,
                        "attempts": attempts,
                        "response_truncated": was_truncated,
                        "auth_secret_ref": lookup.service.auth.secret_ref if lookup.service.auth else None,
                    },
                )
        except urllib.error.HTTPError as exc:
            status = getattr(exc, "code", None)
            body_text = ""
            try:
                if exc.fp is not None:
                    body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body_text = ""
            sanitized_body = _sanitize_plaintext_secrets(body_text)
            bounded_body, was_truncated = _truncate_text(sanitized_body, max_response_chars)
            return APICorepluginExecutionResult(
                allowed=False,
                reason_code="network_error",
                http_status=status,
                data={"text": bounded_body, "truncated": was_truncated} if bounded_body else None,
                audit_summary={
                    "service_id": lookup.service.service_id,
                    "endpoint_key": lookup.endpoint.endpoint_key,
                    "reason_code": "network_error",
                    "method": method,
                    "attempts": attempts,
                    "http_status": status,
                },
            )
        except TimeoutError:
            if attempts <= max_retries:
                time.sleep(backoff_seconds * attempts)
                continue
            return APICorepluginExecutionResult(
                allowed=False,
                reason_code="network_timeout",
                http_status=None,
                data=None,
                audit_summary={
                    "service_id": lookup.service.service_id,
                    "endpoint_key": lookup.endpoint.endpoint_key,
                    "reason_code": "network_timeout",
                    "method": method,
                    "attempts": attempts,
                },
            )
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError) and attempts <= max_retries:
                time.sleep(backoff_seconds * attempts)
                continue
            return APICorepluginExecutionResult(
                allowed=False,
                reason_code="network_error",
                http_status=None,
                data=None,
                audit_summary={
                    "service_id": lookup.service.service_id,
                    "endpoint_key": lookup.endpoint.endpoint_key,
                    "reason_code": "network_error",
                    "method": method,
                    "attempts": attempts,
                },
            )
