from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

from amo_bot.plugins.sandbox.types import SandboxErrorCode, SandboxRequest, SandboxResponse, SandboxRunnerError


class PluginSandboxRunner:
    def __init__(
        self,
        worker_module: str = "amo_bot.plugins.sandbox.worker",
        *,
        base_timeout_ms: int = 3000,
        max_memory_bytes: int = 128 * 1024 * 1024,
        max_file_descriptors: int = 64,
        max_processes: int = 32,
        max_cpu_seconds: int = 2,
        max_output_bytes: int = 64 * 1024,
        worker_timeout_grace_ms: int = 500,
    ) -> None:
        self._worker_module = worker_module
        self._base_timeout_ms = base_timeout_ms
        self._max_memory_bytes = max_memory_bytes
        self._max_file_descriptors = max_file_descriptors
        self._max_processes = max_processes
        self._max_cpu_seconds = max_cpu_seconds
        self._max_output_bytes = max_output_bytes
        self._worker_timeout_grace_ms = worker_timeout_grace_ms

    def _validate_request(self, request: SandboxRequest) -> None:
        if request.timeout_ms > self._base_timeout_ms:
            raise SandboxRunnerError(
                code=SandboxErrorCode.INVALID_REQUEST,
                message="timeout_exceeds_base_limit",
            )

    def _build_env(self) -> dict[str, str]:
        # Default-deny environment for worker process.
        # Keep only strict minimum for deterministic runtime.
        return {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "PYTHONSAFEPATH": "1",
            "PYTHONNOUSERSITE": "1",
            "AMO_SANDBOX_ALLOWED_CAPABILITIES": "plugin.execute",
        }

    def _preexec_limits(self):
        def _apply() -> None:
            os.setsid()
            import resource

            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_CPU, (self._max_cpu_seconds, self._max_cpu_seconds))
            resource.setrlimit(resource.RLIMIT_AS, (self._max_memory_bytes, self._max_memory_bytes))
            resource.setrlimit(
                resource.RLIMIT_NOFILE,
                (self._max_file_descriptors, self._max_file_descriptors),
            )
            if self._max_processes > 0:
                resource.setrlimit(
                    resource.RLIMIT_NPROC,
                    (self._max_processes, self._max_processes),
                )

        return _apply

    def _terminate_process_group(self, proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except Exception:
            proc.kill()

    @staticmethod
    def _truncate_for_log(text: str, max_bytes: int) -> str:
        encoded = text.encode("utf-8", errors="ignore")
        if len(encoded) <= max_bytes:
            return text
        clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
        return f"{clipped}...[truncated]"

    @staticmethod
    def _utf8_len(text: str) -> int:
        return len(text.encode("utf-8", errors="ignore"))

    def _read_streams_with_limits(
        self,
        proc: subprocess.Popen[str],
        timeout_s: float,
    ) -> tuple[str, str]:
        assert proc.stdout is not None
        assert proc.stderr is not None

        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ, data="stdout")
        selector.register(proc.stderr, selectors.EVENT_READ, data="stderr")

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        stdout_size = 0
        stderr_size = 0

        deadline = time.monotonic() + timeout_s

        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SandboxRunnerError(SandboxErrorCode.WORKER_TIMEOUT, "worker_timeout")

                events = selector.select(remaining)
                if not events:
                    if proc.poll() is not None and not selector.get_map():
                        break
                    continue

                for key, _ in events:
                    stream = key.fileobj
                    chunk = stream.read(4096)
                    if chunk == "":
                        selector.unregister(stream)
                        continue

                    size = self._utf8_len(chunk)
                    if key.data == "stdout":
                        stdout_size += size
                        if stdout_size > self._max_output_bytes:
                            raise SandboxRunnerError(
                                SandboxErrorCode.WORKER_ERROR,
                                "worker_output_limit_exceeded",
                            )
                        stdout_chunks.append(chunk)
                    else:
                        stderr_size += size
                        if stderr_size > self._max_output_bytes:
                            raise SandboxRunnerError(
                                SandboxErrorCode.WORKER_ERROR,
                                "worker_output_limit_exceeded",
                            )
                        stderr_chunks.append(chunk)
        finally:
            selector.close()

        return "".join(stdout_chunks), "".join(stderr_chunks)

    def _parse_worker_response(self, stdout: str, request: SandboxRequest) -> SandboxResponse:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise SandboxRunnerError(SandboxErrorCode.PROTOCOL_ERROR, "invalid_worker_json") from exc

        if not isinstance(parsed, dict):
            raise SandboxRunnerError(SandboxErrorCode.INVALID_RESPONSE, "invalid_worker_shape")

        try:
            response = SandboxResponse.from_dict(parsed)
        except ValueError as exc:
            raise SandboxRunnerError(SandboxErrorCode.INVALID_RESPONSE, "invalid_worker_response") from exc

        if response.request_id != request.request_id:
            raise SandboxRunnerError(SandboxErrorCode.INVALID_RESPONSE, "request_id_mismatch")

        return response

    def run(self, request: SandboxRequest) -> SandboxResponse:
        self._validate_request(request)
        payload = json.dumps(request.to_dict())

        worker_file = Path(__file__).with_name("worker.py")
        command = [sys.executable, str(worker_file)]

        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._build_env(),
                preexec_fn=self._preexec_limits(),
            )
        except OSError as exc:
            raise SandboxRunnerError(SandboxErrorCode.WORKER_ERROR, "worker_spawn_failed") from exc

        assert proc.stdin is not None
        try:
            proc.stdin.write(f"{payload}\n")
            proc.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            proc.stdin.close()

        try:
            stdout, stderr = self._read_streams_with_limits(proc, timeout_s=request.timeout_ms / 1000)
        except SandboxRunnerError as exc:
            self._terminate_process_group(proc)
            try:
                proc.wait(timeout=self._worker_timeout_grace_ms / 1000)
            except subprocess.TimeoutExpired:
                pass
            raise exc

        if proc.returncode in (None,):
            try:
                proc.wait(timeout=self._worker_timeout_grace_ms / 1000)
            except subprocess.TimeoutExpired:
                self._terminate_process_group(proc)
                raise SandboxRunnerError(SandboxErrorCode.WORKER_TIMEOUT, "worker_timeout")

        if proc.returncode not in (0, None):
            if stdout.strip():
                response = self._parse_worker_response(stdout, request)
                if not response.ok and response.error_code:
                    return response

            _ = self._truncate_for_log(stderr, self._max_output_bytes)
            raise SandboxRunnerError(SandboxErrorCode.WORKER_ERROR, "worker_failed")

        return self._parse_worker_response(stdout, request)
