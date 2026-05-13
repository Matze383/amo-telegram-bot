from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AIRouterDecision:
    """Deterministic KI-B1 routing decision (default: passthrough/no-op)."""

    passthrough: bool = True
    reason: str = "default_noop"


class AIRouter:
    """Minimal router seam for future KI routing logic."""

    def decide(self, *, prompt: str) -> AIRouterDecision:
        _ = prompt
        return AIRouterDecision()


__all__ = ["AIRouter", "AIRouterDecision"]
