from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    id: str
    version: str
    risk_level: str
    actor_types: tuple[str, ...]
    scopes: tuple[str, ...]
    default_enabled: bool = False


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    allowed: bool
    reason_code: str


_DEFAULT_CAPABILITY_DESCRIPTORS: tuple[CapabilityDescriptor, ...] = (
    CapabilityDescriptor(
        id="ki.memory.read",
        version="1.0.0",
        risk_level="low",
        actor_types=("ki",),
        scopes=("topic", "user"),
        default_enabled=False,
    ),
    CapabilityDescriptor(
        id="ki.rss.fetch",
        version="1.0.0",
        risk_level="medium",
        actor_types=("ki",),
        scopes=("topic", "user"),
        default_enabled=False,
    ),
    CapabilityDescriptor(
        id="ki.websearch.query",
        version="1.0.0",
        risk_level="medium",
        actor_types=("ki",),
        scopes=("topic", "user"),
        default_enabled=False,
    ),
)


class CapabilityRegistry:
    """Deterministic in-memory capability descriptor registry (default deny)."""

    def __init__(self, descriptors: Iterable[CapabilityDescriptor] | None = None) -> None:
        self._by_id: dict[str, CapabilityDescriptor] = {}
        source = _DEFAULT_CAPABILITY_DESCRIPTORS if descriptors is None else descriptors
        for descriptor in source:
            self.register(descriptor)

    def register(self, descriptor: CapabilityDescriptor) -> None:
        key = _normalize_id(descriptor.id)
        if not key:
            raise ValueError("capability id must not be empty")
        if key in self._by_id:
            raise ValueError(f"capability already registered: {descriptor.id}")
        self._by_id[key] = descriptor

    def get(self, capability_id: str) -> CapabilityDescriptor | None:
        return self._by_id.get(_normalize_id(capability_id))

    def list_capabilities(self) -> list[CapabilityDescriptor]:
        return [self._by_id[key] for key in sorted(self._by_id.keys())]

    def evaluate(self, capability_id: str) -> CapabilityDecision:
        descriptor = self.get(capability_id)
        if descriptor is None:
            return CapabilityDecision(allowed=False, reason_code="unknown_capability")
        if descriptor.default_enabled:
            return CapabilityDecision(allowed=True, reason_code="enabled_by_default")
        return CapabilityDecision(allowed=False, reason_code="default_deny")


def _normalize_id(value: str) -> str:
    return value.strip().lower() if isinstance(value, str) else ""
