from __future__ import annotations

import pytest

from amo_bot.ai.response_contract import (
    AIResponseChunk,
    AIResponseContractError,
    envelope_from_full_response_text,
    envelope_from_provider_chat_response,
    envelope_from_provider_chunk,
    envelope_from_provider_chunks,
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


def test_provider_chunks_accumulate_and_capture_finish_reason() -> None:
    envelope = envelope_from_provider_chunks(
        [
            {"text": "Hel", "is_final": False},
            {"text": "lo", "is_final": True, "finish_reason": "stop"},
        ]
    )

    assert envelope.chunks == (
        AIResponseChunk(text="Hel", is_final=False),
        AIResponseChunk(text="lo", is_final=True),
    )
    assert envelope.final_text == "Hello"
    assert envelope.finish_reason == "stop"
    assert envelope.partial is False


def test_provider_chunks_fail_closed_when_no_final_chunk() -> None:
    with pytest.raises(AIResponseContractError) as exc:
        envelope_from_provider_chunks([{"text": "Hello", "is_final": False}])

    assert str(exc.value) == "invalid provider response contract"


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        ["bad"],
        [{"text": "x", "is_final": True, "finish_reason": 1}],
    ],
)
def test_provider_chunks_invalid_shapes_fail_closed(value: object) -> None:
    with pytest.raises(AIResponseContractError) as exc:
        envelope_from_provider_chunks(value)

    assert str(exc.value) == "invalid provider response contract"


def test_provider_chunks_fail_closed_on_whitespace_only() -> None:
    with pytest.raises(AIResponseContractError) as exc:
        envelope_from_provider_chunks(
            [
                {"text": "   ", "is_final": False},
                {"text": "\n", "is_final": True, "finish_reason": "stop"},
            ]
        )

    assert str(exc.value) == "empty response"


def test_provider_chat_response_partial_chunk_contract() -> None:
    envelope = envelope_from_provider_chat_response(
        {
            "message": {"role": "assistant", "content": "Hel"},
            "done": False,
        }
    )

    assert envelope.chunks == (AIResponseChunk(text="Hel", is_final=False),)
    assert envelope.partial is True
    assert envelope.finish_reason is None


def test_provider_chat_response_final_with_done_reason() -> None:
    envelope = envelope_from_provider_chat_response(
        {
            "message": {"role": "assistant", "content": "Hello"},
            "done": True,
            "done_reason": "stop",
        }
    )

    assert envelope.final_text == "Hello"
    assert envelope.partial is False
    assert envelope.finish_reason == "stop"


def test_provider_chat_response_invalid_shapes_fail_closed() -> None:
    for value in (
        [],
        {},
        {"done": True, "message": "x"},
        {"done": True, "message": {"content": 1}},
        {"done": "yes", "message": {"content": "ok"}},
        {"done": True, "message": {"content": "ok"}, "done_reason": 3},
    ):
        with pytest.raises(AIResponseContractError) as exc:
            envelope_from_provider_chat_response(value)
        assert str(exc.value) == "invalid provider response contract"


def test_provider_chat_response_done_blank_content_fails_closed() -> None:
    with pytest.raises(AIResponseContractError) as exc:
        envelope_from_provider_chat_response({"done": True, "message": {"content": "   "}})

    assert str(exc.value) == "empty response"
