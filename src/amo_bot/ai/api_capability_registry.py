from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

_SAFE_REASON_CODES = {
    "ok",
    "unknown_service",
    "unknown_endpoint",
    "raw_url_mode_forbidden",
    "invalid_payload",
}


@dataclass(frozen=True, slots=True)
class APIServiceSecretRef:
    """Server-side secret reference metadata only (never secret values)."""

    header_name: str
    secret_ref: str


@dataclass(frozen=True, slots=True)
class APIEndpointDescriptor:
    """Descriptor for an allowed endpoint (metadata + payload schema only)."""

    service_id: str
    endpoint_key: str
    method: str
    path_template: str
    description: str
    required_payload_keys: tuple[str, ...] = ()
    optional_payload_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class APIServiceDescriptor:
    """Descriptor for an API service (no runtime execution details)."""

    service_id: str
    display_name: str
    base_url_ref: str
    auth: APIServiceSecretRef | None


@dataclass(frozen=True, slots=True)
class APIEndpointLookupResult:
    allowed: bool
    reason_code: str
    service: APIServiceDescriptor | None = None
    endpoint: APIEndpointDescriptor | None = None


@dataclass(frozen=True, slots=True)
class APIPayloadValidationResult:
    allowed: bool
    reason_code: str


def _normalize_key(value: str) -> str:
    return value.strip().lower()


def _sanitize_reason_code(value: str) -> str:
    normalized = _normalize_key(value).replace("-", "_")
    if normalized in _SAFE_REASON_CODES:
        return normalized
    return "invalid_payload"


class APICapabilityRegistry:
    """Descriptor-only API endpoint registry.

    Security constraints:
    - No network/API execution
    - No raw URL mode
    - Secret binding by reference only
    """

    def __init__(
        self,
        *,
        services: tuple[APIServiceDescriptor, ...] = (),
        endpoints: tuple[APIEndpointDescriptor, ...] = (),
    ) -> None:
        self._services: dict[str, APIServiceDescriptor] = {}
        self._endpoints: dict[tuple[str, str], APIEndpointDescriptor] = {}

        for service in services:
            self.register_service(service)
        for endpoint in endpoints:
            self.register_endpoint(endpoint)

    def register_service(self, descriptor: APIServiceDescriptor) -> None:
        service_key = _normalize_key(descriptor.service_id)
        if not service_key:
            raise ValueError("service_id must not be empty")
        if service_key in self._services:
            raise ValueError(f"service already registered: {descriptor.service_id}")
        self._services[service_key] = descriptor

    def register_endpoint(self, descriptor: APIEndpointDescriptor) -> None:
        service_key = _normalize_key(descriptor.service_id)
        endpoint_key = _normalize_key(descriptor.endpoint_key)
        if not service_key:
            raise ValueError("service_id must not be empty")
        if not endpoint_key:
            raise ValueError("endpoint_key must not be empty")
        if service_key not in self._services:
            raise ValueError("endpoint service_id is not registered")
        key = (service_key, endpoint_key)
        if key in self._endpoints:
            raise ValueError(
                f"endpoint already registered: {descriptor.service_id}/{descriptor.endpoint_key}"
            )
        self._endpoints[key] = descriptor

    def get_endpoint(self, *, service_id: str, endpoint_key: str) -> APIEndpointLookupResult:
        service_key = _normalize_key(service_id)
        endpoint_norm = _normalize_key(endpoint_key)

        service = self._services.get(service_key)
        if service is None:
            return APIEndpointLookupResult(allowed=False, reason_code="unknown_service")

        endpoint = self._endpoints.get((service_key, endpoint_norm))
        if endpoint is None:
            return APIEndpointLookupResult(
                allowed=False,
                reason_code="unknown_endpoint",
                service=service,
            )

        return APIEndpointLookupResult(
            allowed=True,
            reason_code="ok",
            service=service,
            endpoint=endpoint,
        )

    def validate_request(
        self,
        *,
        service_id: str,
        endpoint_key: str,
        payload: Mapping[str, Any] | None,
        raw_url: str | None = None,
    ) -> APIPayloadValidationResult:
        if isinstance(raw_url, str) and raw_url.strip():
            return APIPayloadValidationResult(allowed=False, reason_code="raw_url_mode_forbidden")

        lookup = self.get_endpoint(service_id=service_id, endpoint_key=endpoint_key)
        if not lookup.allowed or lookup.endpoint is None:
            return APIPayloadValidationResult(allowed=False, reason_code=_sanitize_reason_code(lookup.reason_code))

        body = payload if isinstance(payload, Mapping) else {}
        required = set(lookup.endpoint.required_payload_keys)
        optional = set(lookup.endpoint.optional_payload_keys)

        missing = [key for key in required if key not in body]
        if missing:
            return APIPayloadValidationResult(allowed=False, reason_code="invalid_payload")

        allowed_keys = required | optional
        unknown = [key for key in body.keys() if key not in allowed_keys]
        if unknown:
            return APIPayloadValidationResult(allowed=False, reason_code="invalid_payload")

        return APIPayloadValidationResult(allowed=True, reason_code="ok")


def build_default_api_capability_registry() -> APICapabilityRegistry:
    """Build a deterministic default registry with descriptor-only entries."""

    registry = APICapabilityRegistry(
        services=(
            APIServiceDescriptor(
                service_id="crm",
                display_name="CRM Service",
                base_url_ref="services.crm.base_url",
                auth=APIServiceSecretRef(
                    header_name="Authorization",
                    secret_ref="secrets.crm.api_token",
                ),
            ),
        ),
        endpoints=(
            APIEndpointDescriptor(
                service_id="crm",
                endpoint_key="create_contact",
                method="POST",
                path_template="/v1/contacts",
                description="Create CRM contact",
                required_payload_keys=("email",),
                optional_payload_keys=("name", "tags"),
            ),
            APIEndpointDescriptor(
                service_id="crm",
                endpoint_key="upsert_note",
                method="POST",
                path_template="/v1/notes/upsert",
                description="Create or update a contact note",
                required_payload_keys=("contact_id", "note"),
                optional_payload_keys=("source",),
            ),
        ),
    )
    return registry
