from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Literal, TypedDict

import httpx

from amo_bot.ai.response_contract import (
    AIResponseContractError,
    envelope_from_full_response_text,
    envelope_from_provider_chat_response,
)


class OllamaError(RuntimeError):
    pass


class OllamaHTTPStatusError(OllamaError):
    def __init__(self, status_code: int, message: str | None = None) -> None:
        super().__init__(message or f"http {status_code}")
        self.status_code = status_code


logger = logging.getLogger(__name__)


class StreamMetadata(TypedDict):
    phase: Literal["primary", "retry", "fallback"]
    live_edit_enabled: bool
    prompt_len: int
    model: str
    done: bool
    done_reason: str | None
    error_category: str | None
    content_len: int
    fallback_used: bool
    cancelled: bool
    timed_out: bool


@dataclass(slots=True)
class OllamaClient:
    base_url: str
    model: str
    timeout_seconds: float
    max_prompt_chars: int = 4000
    max_predict_tokens: int = 512
    max_response_chars: int = 1500
    request_endpoint: str = "generate"
    streaming_mode: Literal["off", "collect_only", "live_edit"] = "off"
    stream_phase: Literal["primary", "retry", "fallback"] = "primary"
    last_stream_events: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.last_stream_events = []
        if self.max_prompt_chars <= 0:
            raise ValueError("max_prompt_chars must be > 0")
        if self.max_predict_tokens <= 0:
            raise ValueError("max_predict_tokens must be > 0")
        if self.request_endpoint not in {"generate", "chat"}:
            raise ValueError("request_endpoint must be one of: generate, chat")
        if self.streaming_mode not in {"off", "collect_only", "live_edit"}:
            raise ValueError("streaming_mode must be one of: off, collect_only, live_edit")

    async def generate(self, prompt: str) -> str:
        self.last_stream_events = []
        request_prompt = prompt[: self.max_prompt_chars]
        if self.request_endpoint == "chat":
            stream_enabled = self.streaming_mode == "collect_only"
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": request_prompt}],
                "stream": stream_enabled,
                "options": {"num_predict": self.max_predict_tokens},
            }
            endpoint_path = "/api/chat"
        else:
            payload = {
                "model": self.model,
                "prompt": request_prompt,
                "stream": False,
                "options": {"num_predict": self.max_predict_tokens},
            }
            endpoint_path = "/api/generate"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url}{endpoint_path}", json=payload)
            response.raise_for_status()
            if self.request_endpoint == "chat" and payload["stream"] is True:
                data = self._collect_chat_stream_response(response, prompt_len=len(request_prompt))
            else:
                data = response.json()
        except httpx.TimeoutException as exc:
            raise OllamaError("request timed out") from exc
        except httpx.HTTPStatusError as exc:
            endpoint = str(exc.request.url) if exc.request is not None else f"{self.base_url}{endpoint_path}"
            response_preview = (exc.response.text or "")[:300]
            logger.error(
                "ollama http error endpoint=%s status_code=%s prompt_len=%s response_preview=%r",
                endpoint,
                exc.response.status_code,
                len(prompt),
                response_preview,
            )
            raise OllamaHTTPStatusError(exc.response.status_code) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise OllamaError("invalid ollama response") from exc

        if not isinstance(data, dict):
            raise OllamaError("invalid ollama response")

        try:
            if self.request_endpoint == "chat":
                envelope = envelope_from_provider_chat_response(data)
            else:
                response_text = data.get("response")
                if not isinstance(response_text, str):
                    raise OllamaError("invalid ollama response")
                envelope = envelope_from_full_response_text(response_text)
        except AIResponseContractError as exc:
            raise OllamaError(str(exc)) from exc

        text = envelope.final_text
        return text[: self.max_response_chars]

    def _collect_chat_stream_response(self, response: httpx.Response, *, prompt_len: int) -> dict[str, object]:
        events = self.iter_chat_stream_events(
            response=response,
            prompt_len=prompt_len,
            fallback_used=self.stream_phase == "fallback",
        )
        self.last_stream_events = list(events)

        final_chunk: dict[str, object] | None = None
        for event in events:
            if event["event"] == "done":
                payload = event.get("payload")
                if isinstance(payload, dict):
                    final_chunk = payload
                break
        if final_chunk is None:
            raise OllamaError("invalid ollama response")
        return final_chunk

    def iter_chat_stream_events(self, *, response: httpx.Response, prompt_len: int, fallback_used: bool = False) -> list[dict[str, Any]]:
        events: list[dict[str, object]] = []
        final_chunk: dict[str, object] | None = None
        accumulated_content_parts: list[str] = []

        events.append(
            {
                "event": "start",
                "metadata": self._event_meta(
                    prompt_len=prompt_len,
                    done=False,
                    done_reason=None,
                    error_category=None,
                    content_len=0,
                    fallback_used=fallback_used,
                    cancelled=False,
                    timed_out=False,
                ),
            }
        )

        for raw_line in response.iter_lines():
            line = raw_line.strip() if isinstance(raw_line, str) else ""
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError as exc:
                events.append(
                    {
                        "event": "error",
                        "metadata": self._event_meta(
                            prompt_len=prompt_len,
                            done=False,
                            done_reason=None,
                            error_category="invalid_response",
                            content_len=len("".join(accumulated_content_parts)),
                            fallback_used=fallback_used,
                            cancelled=False,
                            timed_out=False,
                        ),
                    }
                )
                raise OllamaError("invalid ollama response") from exc
            if not isinstance(chunk, dict):
                events.append(
                    {
                        "event": "error",
                        "metadata": self._event_meta(
                            prompt_len=prompt_len,
                            done=False,
                            done_reason=None,
                            error_category="invalid_response",
                            content_len=len("".join(accumulated_content_parts)),
                            fallback_used=fallback_used,
                            cancelled=False,
                            timed_out=False,
                        ),
                    }
                )
                raise OllamaError("invalid ollama response")

            message = chunk.get("message")
            if message is not None:
                if not isinstance(message, dict):
                    raise OllamaError("invalid ollama response")
                content = message.get("content")
                if content is not None:
                    if not isinstance(content, str):
                        raise OllamaError("invalid ollama response")
                    if content:
                        accumulated_content_parts.append(content)
                        events.append(
                            {
                                "event": "delta",
                                "delta": content,
                                "metadata": self._event_meta(
                                    prompt_len=prompt_len,
                                    done=False,
                                    done_reason=None,
                                    error_category=None,
                                    content_len=len(content),
                                    fallback_used=fallback_used,
                                    cancelled=False,
                                    timed_out=False,
                                ),
                            }
                        )

            if chunk.get("done") is True:
                final_chunk = chunk

        if final_chunk is None:
            events.append(
                {
                    "event": "error",
                    "metadata": self._event_meta(
                        prompt_len=prompt_len,
                        done=False,
                        done_reason=None,
                        error_category="invalid_response",
                        content_len=len("".join(accumulated_content_parts)),
                        fallback_used=fallback_used,
                        cancelled=False,
                        timed_out=False,
                    ),
                }
            )
            raise OllamaError("invalid ollama response")

        final_message = final_chunk.get("message")
        if not isinstance(final_message, dict):
            raise OllamaError("invalid ollama response")

        final_content = final_message.get("content")
        if final_content is None:
            final_content = ""
        if not isinstance(final_content, str):
            raise OllamaError("invalid ollama response")

        combined = "".join(accumulated_content_parts)
        if combined and not final_content:
            final_message["content"] = combined
        elif combined and final_content and not final_content.startswith(combined):
            final_message["content"] = combined

        events.append(
            {
                "event": "done",
                "metadata": self._event_meta(
                    prompt_len=prompt_len,
                    done=True,
                    done_reason=str(final_chunk.get("done_reason")) if final_chunk.get("done_reason") is not None else None,
                    error_category=None,
                    content_len=len(final_message.get("content", "")) if isinstance(final_message.get("content"), str) else 0,
                    fallback_used=fallback_used,
                    cancelled=bool(final_chunk.get("done_reason") == "cancelled"),
                    timed_out=bool(final_chunk.get("done_reason") == "timeout"),
                ),
                "payload": final_chunk,
            }
        )

        return events

    def _event_meta(
        self,
        *,
        prompt_len: int,
        done: bool,
        done_reason: str | None,
        error_category: str | None,
        content_len: int,
        fallback_used: bool,
        cancelled: bool,
        timed_out: bool,
    ) -> StreamMetadata:
        return {
            "phase": self.stream_phase,
            "live_edit_enabled": False,
            "prompt_len": max(0, int(prompt_len)),
            "model": self.model,
            "done": done,
            "done_reason": done_reason,
            "error_category": error_category,
            "content_len": max(0, int(content_len)),
            "fallback_used": fallback_used,
            "cancelled": cancelled,
            "timed_out": timed_out,
        }
