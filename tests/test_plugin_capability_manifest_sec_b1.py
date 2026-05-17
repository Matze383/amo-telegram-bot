import pytest

from amo_bot.plugins.capability_manifest import (
    CapabilityClass,
    CapabilityDescriptor,
    CapabilityManifestRegistry,
    CapabilityRiskLevel,
    ManifestVersionMode,
)


def test_default_manifest_contains_expected_ids_and_default_deny() -> None:
    registry = CapabilityManifestRegistry()
    ids = {d.id for d in registry.list_capabilities()}

    assert {
        "respond",
        "analyze_image",
        "suggest_memory",
        "request_plugin",
        "network",
        "filesystem_read",
        "filesystem_write",
        "database",
        "git",
        "shell",
        "rss",
        "web_search",
        "web_scrape",
        "api_call",
        "secrets_access",
    }.issubset(ids)

    assert all(d.default_enabled is False for d in registry.list_capabilities())


def test_classification_separates_ki_minimal_and_plugin_sandbox() -> None:
    registry = CapabilityManifestRegistry()

    assert registry.classify("respond") is CapabilityClass.KI_MINIMAL
    assert registry.classify("request_plugin") is CapabilityClass.KI_MINIMAL

    assert registry.classify("network") is CapabilityClass.PLUGIN_SANDBOX
    assert registry.classify("shell") is CapabilityClass.PLUGIN_SANDBOX


def test_ki_direct_use_denied_for_plugin_capability() -> None:
    registry = CapabilityManifestRegistry()

    allowed = registry.ensure_ki_direct_allowed("respond")
    denied = registry.ensure_ki_direct_allowed("shell")

    assert allowed.valid is True
    assert allowed.reason_code == "ki_minimal_capability"

    assert denied.valid is False
    assert denied.reason_code == "ki_plugin_capability_requires_policy_gate"


def test_unknown_capability_returns_clean_denial() -> None:
    registry = CapabilityManifestRegistry()
    denied = registry.ensure_ki_direct_allowed("not_known")

    assert denied.valid is False
    assert denied.reason_code == "unknown_capability"


def test_version_compatibility_exact_and_major_modes() -> None:
    registry = CapabilityManifestRegistry()

    exact_ok = registry.is_version_compatible("network", "1.0.0", mode=ManifestVersionMode.EXACT)
    exact_fail = registry.is_version_compatible("network", "2.0.0", mode=ManifestVersionMode.EXACT)

    major_ok = registry.is_version_compatible("network", "1.5.9", mode=ManifestVersionMode.MAJOR)
    major_fail = registry.is_version_compatible("network", "3.0.0", mode=ManifestVersionMode.MAJOR)

    assert exact_ok.valid is True
    assert exact_fail.valid is False
    assert exact_fail.reason_code == "capability_version_mismatch"

    assert major_ok.valid is True
    assert major_fail.valid is False
    assert major_fail.reason_code == "capability_version_mismatch"


def test_invalid_descriptor_unknown_plugin_capability_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid_plugin_sandbox_capability"):
        CapabilityManifestRegistry(
            descriptors=(
                CapabilityDescriptor(
                    id="freeform_sql",
                    version="1.0.0",
                    risk_level=CapabilityRiskLevel.CRITICAL,
                    scopes=("topic",),
                    default_enabled=False,
                    capability_class=CapabilityClass.PLUGIN_SANDBOX,
                ),
            )
        )


def test_invalid_descriptor_high_risk_default_enabled_is_rejected() -> None:
    with pytest.raises(ValueError, match="high_risk_capability_must_not_be_default_enabled"):
        CapabilityManifestRegistry(
            descriptors=(
                CapabilityDescriptor(
                    id="shell",
                    version="1.0.0",
                    risk_level=CapabilityRiskLevel.HIGH,
                    scopes=("topic",),
                    default_enabled=True,
                    capability_class=CapabilityClass.PLUGIN_SANDBOX,
                ),
            )
        )
