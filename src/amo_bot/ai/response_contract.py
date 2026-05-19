from __future__ import annotations

from dataclasses import dataclass


class AIResponseContractError(RuntimeError):
    """Raised when provider data does not match the internal response contract."""


@dataclass(frozen=True, slots=True)
class AIResponseChunk:
    text: str
    is_final: bool = False


@dataclass(frozen=True, slots=True)
class AIResponseEnvelope:
    chunks: tuple[AIResponseChunk, ...]

    @property
    def final_text(self) -> str:
        return "".join(chunk.text for chunk in self.chunks).strip()


def envelope_from_full_response_text(text: str) -> AIResponseEnvelope:
    if not isinstance(text, str):
        raise AIResponseContractError("invalid provider response contract")
    normalized = text.strip()
    if not normalized:
        raise AIResponseContractError("empty response")
    return AIResponseEnvelope(chunks=(AIResponseChunk(text=normalized, is_final=True),))


def envelope_from_provider_chunk(chunk_like: object) -> AIResponseChunk:
    if not isinstance(chunk_like, dict):
        raise AIResponseContractError("invalid provider response contract")

    text = chunk_like.get("text")
    is_final = chunk_like.get("is_final")
    if not isinstance(text, str) or not isinstance(is_final, bool):
        raise AIResponseContractError("invalid provider response contract")

    if not text.strip() and is_final:
        raise AIResponseContractError("empty response")

    return AIResponseChunk(text=text, is_final=is_final)
