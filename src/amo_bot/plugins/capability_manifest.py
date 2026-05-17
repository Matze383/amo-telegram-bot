from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class CapabilityRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CapabilityClass(StrEnum):
    KI_MINIMAL = "ki_minimal"
    PLUGIN_SANDBOX = "plugin_sandbox"


class ManifestVersionMode(StrEnum):
    EXACT = "exact"
    MAJOR = "major"


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    id: str
    version: str
    risk_level: CapabilityRiskLevel
    scopes: tuple[str, ...]
    default_enabled: bool = False
    capability_class: CapabilityClass = CapabilityClass.PLUGIN_SANDBOX


@dataclass(frozen=True, slots=True)
class CapabilityValidationResult:
    valid: bool
    reason_code: str


KI_MINIMAL_CAPABILITY_IDS: tuple[str, ...] = (
    "respond",
    "analyze_image",
    "suggest_memory",
    "request_plugin",
)

PLUGIN_SANDBOX_CAPABILITY_IDS: tuple[str, ...] = (
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
)

HIGH_RISK_PLUGIN_IDS: tuple[str, ...] = (
    "filesystem_write",
    "database",
    "git",
    "shell",
    "api_call",
    "secrets_access",
)

_DEFAULT_PLUGIN_SCOPES: tuple[str, ...] = ("topic", "group", "user")


def default_capability_manifest() -> tuple[CapabilityDescriptor, ...]:
    descriptors: list[CapabilityDescriptor] = []

    for capability_id in KI_MINIMAL_CAPABILITY_IDS:
        descriptors.append(
            CapabilityDescriptor(
                id=capability_id,
                version="1.0.0",
                risk_level=CapabilityRiskLevel.LOW,
                scopes=("topic", "group", "user"),
                default_enabled=False,
                capability_class=CapabilityClass.KI_MINIMAL,
            )
        )

    for capability_id in PLUGIN_SANDBOX_CAPABILITY_IDS:
        risk = CapabilityRiskLevel.HIGH if capability_id in HIGH_RISK_PLUGIN_IDS else CapabilityRiskLevel.MEDIUM
        descriptors.append(
            CapabilityDescriptor(
                id=capability_id,
                version="1.0.0",
                risk_level=risk,
                scopes=_DEFAULT_PLUGIN_SCOPES,
                default_enabled=False,
                capability_class=CapabilityClass.PLUGIN_SANDBOX,
            )
        )

    return tuple(descriptors)


class CapabilityManifestRegistry:
    """SEC-B1 descriptor/policy foundation only. No execution paths."""

    def __init__(self, descriptors: Iterable[CapabilityDescriptor] | None = None) -> None:
        self._by_id: dict[str, CapabilityDescriptor] = {}
        source = default_capability_manifest() if descriptors is None else tuple(descriptors)
        for descriptor in source:
            self.register(descriptor)

    def register(self, descriptor: CapabilityDescriptor) -> None:
        key = _normalize_id(descriptor.id)
        if not key:
            raise ValueError("invalid_capability_id")
        if key in self._by_id:
            raise ValueError(f"duplicate_capability:{key}")
        _validate_descriptor(descriptor)
        self._by_id[key] = descriptor

    def list_capabilities(self) -> list[CapabilityDescriptor]:
        return [self._by_id[key] for key in sorted(self._by_id)]

    def get(self, capability_id: str) -> CapabilityDescriptor | None:
        return self._by_id.get(_normalize_id(capability_id))

    def classify(self, capability_id: str) -> CapabilityClass:
        descriptor = self.get(capability_id)
        if descriptor is None:
            raise ValueError("unknown_capability")
        return descriptor.capability_class

    def ensure_ki_direct_allowed(self, capability_id: str) -> CapabilityValidationResult:
        descriptor = self.get(capability_id)
        if descriptor is None:
            return CapabilityValidationResult(valid=False, reason_code="unknown_capability")
        if descriptor.capability_class is not CapabilityClass.KI_MINIMAL:
            return CapabilityValidationResult(valid=False, reason_code="ki_plugin_capability_requires_policy_gate")
        return CapabilityValidationResult(valid=True, reason_code="ki_minimal_capability")

    def is_version_compatible(
        self,
        capability_id: str,
        requested_version: str,
        *,
        mode: ManifestVersionMode = ManifestVersionMode.EXACT,
    ) -> CapabilityValidationResult:
        descriptor = self.get(capability_id)
        if descriptor is None:
            return CapabilityValidationResult(valid=False, reason_code="unknown_capability")

        req = requested_version.strip()
        if not req:
            return CapabilityValidationResult(valid=False, reason_code="invalid_requested_version")

        if mode is ManifestVersionMode.EXACT:
            if req == descriptor.version:
                return CapabilityValidationResult(valid=True, reason_code="version_compatible")
            return CapabilityValidationResult(valid=False, reason_code="capability_version_mismatch")

        req_major = req.split(".", 1)[0]
        reg_major = descriptor.version.split(".", 1)[0]
        if req_major and req_major == reg_major:
            return CapabilityValidationResult(valid=True, reason_code="version_compatible")
        return CapabilityValidationResult(valid=False, reason_code="capability_version_mismatch")


def _validate_descriptor(descriptor: CapabilityDescriptor) -> None:
    if not descriptor.version.strip():
        raise ValueError("invalid_capability_version")

    if not descriptor.scopes:
        raise ValueError("invalid_capability_scopes")

    normalized_id = _normalize_id(descriptor.id)

    if descriptor.capability_class is CapabilityClass.KI_MINIMAL:
        if normalized_id not in KI_MINIMAL_CAPABILITY_IDS:
            raise ValueError("invalid_ki_minimal_capability")
        if descriptor.risk_level in {CapabilityRiskLevel.HIGH, CapabilityRiskLevel.CRITICAL}:
            raise ValueError("invalid_ki_minimal_risk")
    else:
        if normalized_id not in PLUGIN_SANDBOX_CAPABILITY_IDS:
            raise ValueError("invalid_plugin_sandbox_capability")

    if descriptor.default_enabled and descriptor.risk_level in {CapabilityRiskLevel.HIGH, CapabilityRiskLevel.CRITICAL}:
        raise ValueError("high_risk_capability_must_not_be_default_enabled")


def _normalize_id(value: str) -> str:
    return value.strip().lower() if isinstance(value, str) else ""
