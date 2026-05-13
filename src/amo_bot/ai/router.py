from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AIRouterReasonCode(StrEnum):
    """Machine-readable reason codes for deterministic router decisions."""

    DEFAULT_NOOP = "default_noop"


@dataclass(frozen=True, slots=True)
class AIRouterDecision:
    """Deterministic KI-B1/KI-B2 routing decision metadata."""

    passthrough: bool = True
    reason_code: AIRouterReasonCode = AIRouterReasonCode.DEFAULT_NOOP


class AIRouter:
    """Minimal router seam for future KI routing logic."""

    def decide(self, *, prompt: str) -> AIRouterDecision:
        _ = prompt
        return AIRouterDecision()


__all__ = ["AIRouter", "AIRouterDecision", "AIRouterReasonCode"]
