from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class AIResponseContractError(RuntimeError):
    """Raised when provider data does not match the internal response contract."""


@dataclass(frozen=True, slots=True)
class AIResponseChunk:
    text: str
    is_final: bool = False


@dataclass(frozen=True, slots=True)
class AIResponseEnvelope:
    chunks: tuple[AIResponseChunk, ...]
    finish_reason: str | None = None
    partial: bool = False

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


def envelope_from_provider_chunks(chunks_like: object) -> AIResponseEnvelope:
    if not isinstance(chunks_like, list) or not chunks_like:
        raise AIResponseContractError("invalid provider response contract")

    parsed: list[AIResponseChunk] = []
    finish_reason: str | None = None
    saw_final = False

    for raw_chunk in chunks_like:
        if not isinstance(raw_chunk, dict):
            raise AIResponseContractError("invalid provider response contract")
        chunk = envelope_from_provider_chunk(raw_chunk)
        parsed.append(chunk)

        if chunk.is_final:
            saw_final = True
            reason = raw_chunk.get("finish_reason")
            if reason is not None and not isinstance(reason, str):
                raise AIResponseContractError("invalid provider response contract")
            finish_reason = reason

    if not saw_final:
        raise AIResponseContractError("invalid provider response contract")

    if not any(c.text.strip() for c in parsed):
        raise AIResponseContractError("empty response")

    return AIResponseEnvelope(chunks=tuple(parsed), finish_reason=finish_reason, partial=False)


def envelope_from_provider_chat_response(response_like: object) -> AIResponseEnvelope:
    if not isinstance(response_like, dict):
        raise AIResponseContractError("invalid provider response contract")

    done = response_like.get("done")
    if not isinstance(done, bool):
        raise AIResponseContractError("invalid provider response contract")

    message = response_like.get("message")
    content = ""
    if message is not None:
        if not isinstance(message, dict):
            raise AIResponseContractError("invalid provider response contract")
        raw_content = message.get("content")
        if not isinstance(raw_content, str):
            raise AIResponseContractError("invalid provider response contract")
        content = raw_content

    finish_reason = response_like.get("done_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise AIResponseContractError("invalid provider response contract")

    if done and not content.strip():
        raise AIResponseContractError("empty response")

    return AIResponseEnvelope(
        chunks=(AIResponseChunk(text=content, is_final=done),),
        finish_reason=finish_reason,
        partial=not done,
    )
