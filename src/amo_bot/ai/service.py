from __future__ import annotations

from dataclasses import dataclass

from amo_bot.ai.ollama import OllamaClient, OllamaError


@dataclass(slots=True)
class AIService:
    client: OllamaClient

    async def ask(self, prompt: str) -> str:
        cleaned = prompt.strip()
        if not cleaned:
            raise ValueError("empty prompt")

        return await self.client.generate(cleaned)


__all__ = ["AIService", "OllamaError"]
