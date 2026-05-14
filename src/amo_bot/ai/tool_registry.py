from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AIToolCapability(str, Enum):
    """High-level capability labels for AI-usable tools."""

    READ = "read"
    QUERY = "query"
    COMPUTE = "compute"
    NOTIFY = "notify"


@dataclass(frozen=True, slots=True)
class AIToolDescriptor:
    """Metadata-only descriptor for a tool that could be AI-usable later."""

    name: str
    capability: AIToolCapability
    description: str


class AIToolRegistry:
    """In-memory registry for AI tool descriptors.

    Registry is descriptor-only and performs no tool execution.
    """

    def __init__(self) -> None:
        self._tools_by_name: dict[str, AIToolDescriptor] = {}

    def register(self, descriptor: AIToolDescriptor) -> None:
        key = descriptor.name.strip().lower()
        if not key:
            raise ValueError("tool name must not be empty")
        if key in self._tools_by_name:
            raise ValueError(f"tool already registered: {descriptor.name}")
        self._tools_by_name[key] = descriptor

    def get(self, name: str) -> AIToolDescriptor | None:
        return self._tools_by_name.get(name.strip().lower())

    def list_tools(self) -> list[AIToolDescriptor]:
        return [self._tools_by_name[k] for k in sorted(self._tools_by_name.keys())]

    def list_by_capability(self, capability: AIToolCapability) -> list[AIToolDescriptor]:
        return [tool for tool in self.list_tools() if tool.capability == capability]


class AIToolPolicy:
    """Policy gate for AI tool usage.

    Default policy is deny-all for every tool, regardless of registration.
    """

    def is_allowed(self, *, tool_name: str) -> bool:
        _ = tool_name
        return False
