from __future__ import annotations

import pytest

from amo_bot.ai.response_contract import (
    AIResponseChunk,
    AIResponseContractError,
    envelope_from_full_response_text,
    envelope_from_provider_chunk,
)


def test_full_response_text_normalizes_into_final_chunk() -> None:
    envelope = envelope_from_full_response_text("  hello world  ")

    assert envelope.final_text == "hello world"
    assert envelope.chunks == (AIResponseChunk(text="hello world", is_final=True),)


@pytest.mark.parametrize(
    "value",
    [None, 123, [], {}, {"text": "ok"}, {"is_final": True}, {"text": 1, "is_final": True}, {"text": "x", "is_final": "yes"}],
)
def test_chunk_like_invalid_shapes_fail_closed(value: object) -> None:
    with pytest.raises(AIResponseContractError) as exc:
        envelope_from_provider_chunk(value)

    assert str(exc.value) == "invalid provider response contract"


def test_final_chunk_with_blank_text_fails_closed() -> None:
    with pytest.raises(AIResponseContractError) as exc:
        envelope_from_provider_chunk({"text": "   ", "is_final": True})

    assert str(exc.value) == "empty response"


def test_non_final_blank_chunk_is_allowed() -> None:
    out = envelope_from_provider_chunk({"text": "   ", "is_final": False})
    assert out == AIResponseChunk(text="   ", is_final=False)
