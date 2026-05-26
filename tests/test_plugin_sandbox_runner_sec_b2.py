from __future__ import annotations

import os

import pytest

from amo_bot.plugins.sandbox.runner import PluginSandboxRunner
from amo_bot.plugins.sandbox.types import SandboxErrorCode, SandboxRequest, SandboxRunnerError


def _request(timeout_ms: int = 200, *, action: str = "run", payload: dict | None = None) -> SandboxRequest:
    return SandboxRequest(
        request_id="req-1",
        plugin_id="demo",
        action=action,
        payload=payload or {"x": 1},
        timeout_ms=timeout_ms,
    )


def test_child_process_runner_success() -> None:
    runner = PluginSandboxRunner(base_timeout_ms=1000)
    response = runner.run(_request(timeout_ms=600))

    assert response.ok is True
    assert response.request_id == "req-1"
    assert response.result == {"plugin_id": "demo", "action": "run", "echo": {"x": 1}}


def test_reject_request_timeout_over_base_limit() -> None:
    runner = PluginSandboxRunner(base_timeout_ms=100)

    with pytest.raises(SandboxRunnerError) as exc:
        runner.run(_request(timeout_ms=200))

    assert exc.value.code == SandboxErrorCode.INVALID_REQUEST
    assert exc.value.message == "timeout_exceeds_base_limit"


def test_runner_accepts_explicitly_bounded_max_timeout_override() -> None:
    runner = PluginSandboxRunner(base_timeout_ms=100, max_timeout_ms=300)
    response = runner.run(_request(timeout_ms=250))

    assert response.ok is True
    assert response.request_id == "req-1"


def test_malformed_worker_response_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = PluginSandboxRunner(base_timeout_ms=500)

    class _Proc:
        returncode = 0
        pid = 42

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    class _StdIn:
        def write(self, _data):
            return None

        def flush(self):
            return None

        def close(self):
            return None

    proc = _Proc()
    proc.stdin = _StdIn()
    proc.stdout = None
    proc.stderr = None

    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: proc)
    monkeypatch.setattr(runner, "_read_streams_with_limits", lambda *_a, **_k: ("not-json", ""))

    with pytest.raises(SandboxRunnerError) as exc:
        runner.run(_request())

    assert exc.value.code == SandboxErrorCode.PROTOCOL_ERROR
    assert exc.value.message == "invalid_worker_json"


def test_timeout_kills_worker_deterministically(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = PluginSandboxRunner(base_timeout_ms=500)

    class _Proc:
        returncode = None
        pid = 43

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    class _StdIn:
        def write(self, _data):
            return None

        def flush(self):
            return None

        def close(self):
            return None

    proc = _Proc()
    proc.stdin = _StdIn()
    proc.stdout = None
    proc.stderr = None

    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: proc)
    monkeypatch.setattr(
        runner,
        "_read_streams_with_limits",
        lambda *_a, **_k: (_ for _ in ()).throw(
            SandboxRunnerError(SandboxErrorCode.WORKER_TIMEOUT, "worker_timeout")
        ),
    )

    terminated = {"called": False}

    def _fake_terminate(_proc):
        terminated["called"] = True

    monkeypatch.setattr(runner, "_terminate_process_group", _fake_terminate)

    with pytest.raises(SandboxRunnerError) as exc:
        runner.run(_request(timeout_ms=50))

    assert terminated["called"] is True
    assert exc.value.code == SandboxErrorCode.WORKER_TIMEOUT
    assert exc.value.message == "worker_timeout"


def test_capability_default_deny_at_sandbox_boundary() -> None:
    runner = PluginSandboxRunner(base_timeout_ms=1000)

    response = runner.run(_request(timeout_ms=600, action="audit.dump"))

    assert response.ok is False
    assert response.error_code == SandboxErrorCode.INVALID_REQUEST.value
    assert response.error_message == "capability_denied"


def test_sensitive_payload_fields_are_redacted_in_result() -> None:
    runner = PluginSandboxRunner(base_timeout_ms=1000)
    response = runner.run(
        _request(
            timeout_ms=600,
            payload={
                "token": "abc",
                "nested": {
                    "password": "pw",
                    "items": [{"api_key": "k1"}, {"x": 1}],
                },
                "x": 1,
            },
        )
    )

    assert response.ok is True
    assert response.result is not None
    assert response.result["echo"] == {
        "token": "[redacted]",
        "nested": {"password": "[redacted]", "items": [{"api_key": "[redacted]"}, {"x": 1}]},
        "x": 1,
    }


def test_preexec_limit_setup_failure_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = PluginSandboxRunner(base_timeout_ms=500)

    def _explode() -> None:
        raise OSError("setrlimit failed")

    monkeypatch.setattr(runner, "_preexec_limits", _explode)

    with pytest.raises(SandboxRunnerError) as exc:
        runner.run(_request())

    assert exc.value.code == SandboxErrorCode.WORKER_ERROR
    assert exc.value.message == "worker_spawn_failed"


def test_worker_environment_is_minimal_default_deny() -> None:
    os.environ["PATH"] = "/tmp/custom"
    os.environ["LD_PRELOAD"] = "evil.so"
    os.environ["PYTHONPATH"] = "/tmp/pythonpath"

    runner = PluginSandboxRunner()
    env = runner._build_env()

    assert env["PATH"] == "/usr/bin:/bin"
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PYTHONSAFEPATH"] == "1"
    assert "LD_PRELOAD" not in env
    assert "PYTHONPATH" not in env


def test_output_cap_enforced_during_streaming_read(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = PluginSandboxRunner(base_timeout_ms=500, max_output_bytes=1024)

    class _Proc:
        returncode = None
        pid = 44

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

    class _StdIn:
        def write(self, _data):
            return None

        def flush(self):
            return None

        def close(self):
            return None

    proc = _Proc()
    proc.stdin = _StdIn()
    proc.stdout = None
    proc.stderr = None

    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: proc)
    monkeypatch.setattr(
        runner,
        "_read_streams_with_limits",
        lambda *_a, **_k: (_ for _ in ()).throw(
            SandboxRunnerError(SandboxErrorCode.WORKER_ERROR, "worker_output_limit_exceeded")
        ),
    )

    with pytest.raises(SandboxRunnerError) as exc:
        runner.run(_request())

    assert exc.value.code == SandboxErrorCode.WORKER_ERROR
    assert exc.value.message == "worker_output_limit_exceeded"
