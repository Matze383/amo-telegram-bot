from amo_bot.ai import (
    APICapabilityRegistry,
    APIEndpointDescriptor,
    APIServiceDescriptor,
    APIServiceSecretRef,
    build_default_api_capability_registry,
)


def _registry() -> APICapabilityRegistry:
    return build_default_api_capability_registry()


def test_lookup_endpoint_by_service_id_and_endpoint_key() -> None:
    registry = _registry()

    lookup = registry.get_endpoint(service_id="CRM", endpoint_key="CREATE_CONTACT")

    assert lookup.allowed is True
    assert lookup.reason_code == "ok"
    assert lookup.service is not None
    assert lookup.endpoint is not None
    assert lookup.service.service_id == "crm"
    assert lookup.endpoint.endpoint_key == "create_contact"


def test_unknown_endpoint_denied_safely() -> None:
    registry = _registry()

    lookup = registry.get_endpoint(service_id="crm", endpoint_key="does_not_exist")

    assert lookup.allowed is False
    assert lookup.reason_code == "unknown_endpoint"


def test_request_schema_validation_allows_only_configured_payload_shape() -> None:
    registry = _registry()

    ok = registry.validate_request(
        service_id="crm",
        endpoint_key="create_contact",
        payload={"email": "alice@example.org", "tags": ["lead"]},
    )
    assert ok.allowed is True
    assert ok.reason_code == "ok"

    missing_required = registry.validate_request(
        service_id="crm",
        endpoint_key="create_contact",
        payload={"tags": ["lead"]},
    )
    assert missing_required.allowed is False
    assert missing_required.reason_code == "invalid_payload"

    extra_field = registry.validate_request(
        service_id="crm",
        endpoint_key="create_contact",
        payload={"email": "alice@example.org", "unexpected": "x"},
    )
    assert extra_field.allowed is False
    assert extra_field.reason_code == "invalid_payload"


def test_raw_url_mode_rejected() -> None:
    registry = _registry()

    result = registry.validate_request(
        service_id="crm",
        endpoint_key="create_contact",
        payload={"email": "alice@example.org"},
        raw_url="https://evil.example.com/anything",
    )

    assert result.allowed is False
    assert result.reason_code == "raw_url_mode_forbidden"


def test_service_secret_binding_is_reference_only() -> None:
    registry = APICapabilityRegistry(
        services=(
            APIServiceDescriptor(
                service_id="billing",
                display_name="Billing",
                base_url_ref="services.billing.base_url",
                auth=APIServiceSecretRef(
                    header_name="Authorization",
                    secret_ref="secrets.billing.token",
                ),
            ),
        ),
        endpoints=(
            APIEndpointDescriptor(
                service_id="billing",
                endpoint_key="create_invoice",
                method="POST",
                path_template="/v1/invoices",
                description="Create invoice",
                required_payload_keys=("customer_id", "amount"),
            ),
        ),
    )

    lookup = registry.get_endpoint(service_id="billing", endpoint_key="create_invoice")
    assert lookup.allowed is True
    assert lookup.service is not None
    assert lookup.service.auth is not None
    assert lookup.service.auth.secret_ref == "secrets.billing.token"
    assert "token" not in lookup.service.auth.header_name.lower()
