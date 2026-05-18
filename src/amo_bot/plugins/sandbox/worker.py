from __future__ import annotations

import json
import os
import sys

from amo_bot.plugins.sandbox.types import SandboxErrorCode, SandboxRequest, SandboxResponse


def _safe_error(request_id: str, code: SandboxErrorCode, message: str) -> dict[str, object]:
    return SandboxResponse(
        request_id=request_id,
        ok=False,
        error_code=code.value,
        error_message=message,
    ).to_dict()


def _allowed_capabilities() -> set[str]:
    raw = os.environ.get("AMO_SANDBOX_ALLOWED_CAPABILITIES", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _resolve_capability(action: str) -> str:
    if action == "run":
        return "plugin.execute"
    return "plugin.unknown"


def _sanitize_value(value: object) -> object:
    if isinstance(value, dict):
        return _sanitize_payload(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _sanitize_payload(payload: dict[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        if any(token in key.lower() for token in ("secret", "token", "password", "key")):
            redacted[key] = "[redacted]"
        else:
            redacted[key] = _sanitize_value(value)
    return redacted


def main() -> int:
    raw = sys.stdin.read()
    request_id = "unknown"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        sys.stdout.write(json.dumps(_safe_error(request_id, SandboxErrorCode.INVALID_REQUEST, "invalid_json")))
        sys.stdout.flush()
        return 1

    if not isinstance(parsed, dict):
        sys.stdout.write(json.dumps(_safe_error(request_id, SandboxErrorCode.INVALID_REQUEST, "invalid_shape")))
        sys.stdout.flush()
        return 1

    if isinstance(parsed.get("request_id"), str):
        request_id = parsed["request_id"]

    try:
        request = SandboxRequest.from_dict(parsed)
    except ValueError:
        sys.stdout.write(json.dumps(_safe_error(request_id, SandboxErrorCode.INVALID_REQUEST, "invalid_fields")))
        sys.stdout.flush()
        return 1

    capability = _resolve_capability(request.action)
    if capability not in _allowed_capabilities():
        sys.stdout.write(
            json.dumps(_safe_error(request.request_id, SandboxErrorCode.INVALID_REQUEST, "capability_denied"))
        )
        sys.stdout.flush()
        return 1

    payload = _sanitize_payload(request.payload)
    response = SandboxResponse(
        request_id=request.request_id,
        ok=True,
        result={
            "plugin_id": request.plugin_id,
            "action": request.action,
            "echo": payload,
        },
    )
    sys.stdout.write(json.dumps(response.to_dict()))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
