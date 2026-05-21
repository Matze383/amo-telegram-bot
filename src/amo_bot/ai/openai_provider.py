from __future__ import annotations

from dataclasses import dataclass


class OpenAIProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OpenAIProviderConfig:
    api_key: str
    model: str
    timeout_seconds: float

    def redacted_dict(self) -> dict[str, object]:
        return {
            "provider": "openai",
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "api_key_present": bool(self.api_key),
            "api_key_preview": "***",
        }
