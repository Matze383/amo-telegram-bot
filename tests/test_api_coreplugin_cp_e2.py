from __future__ import annotations

import io
import json
import urllib.error
from contextlib import contextmanager
from dataclasses import dataclass

from amo_bot.ai import (
    APICapabilityRegistry,
    APICorepluginExecutionInput,
    APIEndpointDescriptor,
    APIServiceDescriptor,
    APIServiceSecretRef,
    execute_api_request_mvp,
)


@dataclass
class _FakeResponse:
    status: int
    payload: str

    def read(self) -> bytes:
        return self.payload.encode("utf-8")


@contextmanager
def _fake_context_response(response: _FakeResponse):
    yield response


def _registry(method: str = "POST") -> APICapabilityRegistry:
    return APICapabilityRegistry(
        services=(
            APIServiceDescriptor(
                service_id="crm",
                display_name="CRM",
                base_url_ref="services.crm.base_url",
                auth=APIServiceSecretRef(
                    header_name="Authorization",
                    secret_ref="secrets.crm.token",
                ),
            ),
        ),
        endpoints=(
            APIEndpointDescriptor(
                service_id="crm",
                endpoint_key="create_contact",
                method=method,
                path_template="/v1/contacts",
                description="Create contact",
                required_payload_keys=("email",),
                optional_payload_keys=("name",),
            ),
        ),
    )


def test_cp_e2_schema_fail_returns_safe_envelope() -> None:
    result = execute_api_request_mvp(
        registry=_registry(),
        request=APICorepluginExecutionInput(
            service_id="crm",
            endpoint_key="create_contact",
            payload={"name": "Alice"},
        ),
        config_values={"services.crm.base_url": "https://crm.example"},
        secret_values={"secrets.crm.token": "super-secret"},
    )

    assert result.allowed is False
    assert result.reason_code == "invalid_payload"
    assert result.data is None


def test_cp_e2_timeout_and_failure_envelope() -> None:
    def _timeout(_request, _timeout):
        raise TimeoutError("timed out")

    result = execute_api_request_mvp(
        registry=_registry(),
        request=APICorepluginExecutionInput(
            service_id="crm",
            endpoint_key="create_contact",
            payload={"email": "a@example.org"},
        ),
        config_values={"services.crm.base_url": "https://crm.example"},
        secret_values={"secrets.crm.token": "super-secret"},
        max_retries=1,
        backoff_seconds=0,
        opener=_timeout,
    )

    assert result.allowed is False
    assert result.reason_code == "network_timeout"
    assert result.http_status is None


def test_cp_e2_redaction_never_returns_or_audits_secret_values() -> None:
    def _ok(_request, _timeout):
        payload = json.dumps(
            {
                "ok": True,
                "api_key": "do-not-leak",
                "nested": {"token": "do-not-leak"},
            }
        )
        return _fake_context_response(_FakeResponse(status=200, payload=payload))

    result = execute_api_request_mvp(
        registry=_registry(),
        request=APICorepluginExecutionInput(
            service_id="crm",
            endpoint_key="create_contact",
            payload={"email": "a@example.org"},
        ),
        config_values={"services.crm.base_url": "https://crm.example"},
        secret_values={"secrets.crm.token": "super-secret-value"},
        opener=_ok,
    )

    assert result.allowed is True
    assert result.data is not None
    assert result.data["api_key"] == "***REDACTED***"
    assert result.data["nested"]["token"] == "***REDACTED***"
    assert "super-secret-value" not in json.dumps(result.audit_summary)


def test_cp_e2_response_cap_applies_with_safe_reason() -> None:
    def _ok(_request, _timeout):
        large_text = "x" * 120
        return _fake_context_response(_FakeResponse(status=200, payload=large_text))

    result = execute_api_request_mvp(
        registry=_registry(),
        request=APICorepluginExecutionInput(
            service_id="crm",
            endpoint_key="create_contact",
            payload={"email": "a@example.org"},
        ),
        config_values={"services.crm.base_url": "https://crm.example"},
        secret_values={"secrets.crm.token": "super-secret"},
        max_response_chars=40,
        opener=_ok,
    )

    assert result.allowed is True
    assert result.reason_code == "response_too_large"
    assert result.data is not None
    assert result.data["truncated"] is True
    assert len(result.data["text"]) == 40


def test_cp_e2_method_denial_for_non_get_post() -> None:
    registry = _registry(method="DELETE")

    result = execute_api_request_mvp(
        registry=registry,
        request=APICorepluginExecutionInput(
            service_id="crm",
            endpoint_key="create_contact",
            payload={"email": "a@example.org"},
        ),
        config_values={"services.crm.base_url": "https://crm.example"},
        secret_values={"secrets.crm.token": "super-secret"},
    )

    assert result.allowed is False
    assert result.reason_code == "method_not_allowed"


def test_cp_e2_raw_url_denied() -> None:
    result = execute_api_request_mvp(
        registry=_registry(),
        request=APICorepluginExecutionInput(
            service_id="crm",
            endpoint_key="create_contact",
            payload={"email": "a@example.org"},
            raw_url="https://evil.example/raw",
        ),
        config_values={"services.crm.base_url": "https://crm.example"},
        secret_values={"secrets.crm.token": "super-secret"},
    )

    assert result.allowed is False
    assert result.reason_code == "raw_url_mode_forbidden"


def test_cp_e2_plaintext_non_json_secret_redaction() -> None:
    def _ok(_request, _timeout):
        text = "token= abc123 password: abc123 api_key = abc123 Authorization: Bearer abc123"
        return _fake_context_response(_FakeResponse(status=200, payload=text))

    result = execute_api_request_mvp(
        registry=_registry(),
        request=APICorepluginExecutionInput(
            service_id="crm",
            endpoint_key="create_contact",
            payload={"email": "a@example.org"},
        ),
        config_values={"services.crm.base_url": "https://crm.example"},
        secret_values={"secrets.crm.token": "super-secret"},
        opener=_ok,
    )

    assert result.allowed is True
    assert result.data is not None
    assert "abc123" not in result.data["text"]
    assert result.data["text"].count("***REDACTED***") >= 4


def test_cp_e2_plaintext_truncated_secret_redaction() -> None:
    def _ok(_request, _timeout):
        text = "A" * 80 + " token= abc123 password: abc123 api_key = abc123 Authorization: Bearer abc123 " + "B" * 80
        return _fake_context_response(_FakeResponse(status=200, payload=text))

    result = execute_api_request_mvp(
        registry=_registry(),
        request=APICorepluginExecutionInput(
            service_id="crm",
            endpoint_key="create_contact",
            payload={"email": "a@example.org"},
        ),
        config_values={"services.crm.base_url": "https://crm.example"},
        secret_values={"secrets.crm.token": "super-secret"},
        max_response_chars=120,
        opener=_ok,
    )

    assert result.allowed is True
    assert result.reason_code == "response_too_large"
    assert result.data is not None
    assert "abc123" not in result.data["text"]


def test_cp_e2_http_error_returns_bounded_sanitized_body() -> None:
    def _http_error(_request, _timeout):
        payload = b"Authorization: Bearer abc123 token= abc123 password: abc123 api_key = abc123"
        raise urllib.error.HTTPError(
            url="https://crm.example/v1/contacts",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(payload),
        )

    result = execute_api_request_mvp(
        registry=_registry(),
        request=APICorepluginExecutionInput(
            service_id="crm",
            endpoint_key="create_contact",
            payload={"email": "a@example.org"},
        ),
        config_values={"services.crm.base_url": "https://crm.example"},
        secret_values={"secrets.crm.token": "super-secret"},
        max_response_chars=40,
        opener=_http_error,
    )

    assert result.allowed is False
    assert result.reason_code == "network_error"
    assert result.http_status == 401
    assert result.data is not None
    assert result.data["truncated"] is True
    assert "abc123" not in result.data["text"]
