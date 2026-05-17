from __future__ import annotations

from amo_bot.ai.image_analyze_coreplugin_cp_b4 import (
    ImageAnalyzePolicyContext,
    ImageAnalyzeRequest,
    ImageAnalyzeSettings,
    execute_image_analyze_stub,
    get_image_analyze_manifest,
)


def _req() -> ImageAnalyzeRequest:
    return ImageAnalyzeRequest(image_ref="media://img-1", prompt="what is visible", locale="en")


def _policy(*, consent_granted: bool = True, actor_role: str = "admin") -> ImageAnalyzePolicyContext:
    return ImageAnalyzePolicyContext(consent_granted=consent_granted, actor_role=actor_role)


def test_img_b4_manifest_discoverable_but_not_auto_active() -> None:
    manifest = get_image_analyze_manifest()

    assert manifest.capability_name == "ki.image.analyze"
    assert manifest.capability_version == "1.0.0"
    assert manifest.enabled_by_default is False
    assert manifest.requires_consent is True
    assert manifest.min_role == "admin"


def test_img_b4_activation_gate_default_denies_when_disabled() -> None:
    result = execute_image_analyze_stub(
        request=_req(),
        settings=ImageAnalyzeSettings(enabled=False),
        policy=_policy(),
    )

    assert result.result.value == "deny"
    assert result.reason_code == "not_enabled"
    assert result.message == "image analysis not configured"


def test_img_b4_policy_matrix_enforces_consent_and_role() -> None:
    settings = ImageAnalyzeSettings(enabled=True, consent_required=True, min_role="vip")

    no_consent = execute_image_analyze_stub(
        request=_req(),
        settings=settings,
        policy=_policy(consent_granted=False, actor_role="owner"),
    )
    assert no_consent.result.value == "deny"
    assert no_consent.reason_code == "consent_required"
    assert no_consent.message == "image analysis not configured"

    low_role = execute_image_analyze_stub(
        request=_req(),
        settings=settings,
        policy=_policy(consent_granted=True, actor_role="normal"),
    )
    assert low_role.result.value == "deny"
    assert low_role.reason_code == "role_forbidden"
    assert low_role.message == "image analysis not configured"

    allowed_policy_but_stub = execute_image_analyze_stub(
        request=_req(),
        settings=settings,
        policy=_policy(consent_granted=True, actor_role="vip"),
    )
    assert allowed_policy_but_stub.result.value == "deny"
    assert allowed_policy_but_stub.reason_code == "not_configured"
    assert allowed_policy_but_stub.message == "image analysis not configured"


def test_img_b4_no_provider_or_network_path_available() -> None:
    network_on = execute_image_analyze_stub(
        request=_req(),
        settings=ImageAnalyzeSettings(enabled=True, allow_network=True),
        policy=_policy(),
    )
    assert network_on.result.value == "deny"
    assert network_on.reason_code == "network_not_allowed"

    provider_set = execute_image_analyze_stub(
        request=_req(),
        settings=ImageAnalyzeSettings(enabled=True, provider_name="vision-x", model_name="m1"),
        policy=_policy(),
    )
    assert provider_set.result.value == "deny"
    assert provider_set.reason_code == "provider_not_allowed"
