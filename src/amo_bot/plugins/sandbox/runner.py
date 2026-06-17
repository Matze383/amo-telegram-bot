from __future__ import annotations

import json
import os
import re
import selectors
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from amo_bot.plugins.sandbox.types import SandboxErrorCode, SandboxRequest, SandboxResponse, SandboxRunnerError


class PluginSandboxRunner:
    HIGH_RISK_PLUGIN_ONLY_CAPABILITIES = {"network", "git", "shell", "secrets"}
    _SECRET_KEY_PATTERN = re.compile(
        r"(?:api[_-]?key|token|secret|password|passwd|authorization|cookie|session|private[_-]?key)",
        re.IGNORECASE,
    )
    _SECRET_VALUE_PATTERN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{20,})\b")
    _SHELL_META_PATTERN = re.compile(r"[;&|`$<>\n\r]")
    _SHELL_ALLOWLIST_RULES: dict[str, re.Pattern[str]] = {
        "echo": re.compile(r"^echo(?:\s+[A-Za-z0-9._,:@/+=-]+){0,32}$"),
        "ls": re.compile(r"^ls(?:\s+-[A-Za-zA-Z]{1,6})*(?:\s+[A-Za-z0-9._/:-]+)*$"),
        "pwd": re.compile(r"^pwd$"),
        "whoami": re.compile(r"^whoami$"),
        "date": re.compile(r"^date$"),
    }
    _GIT_READONLY_ALLOWED_SUBCOMMANDS = {
        "status",
        "log",
        "show",
        "diff",
        "branch",
        "rev-parse",
        "describe",
        "remote",
        "ls-files",
        "cat-file",
        "blame",
        "grep",
    }

    @staticmethod
    def is_capability_allowed(
        requested_capability: str,
        manifest_capabilities: set[str],
        sandbox_allowed_capabilities: set[str],
    ) -> bool:
        normalized_requested = requested_capability.strip().lower() if isinstance(requested_capability, str) else ""
        if not normalized_requested:
            return False

        normalized_manifest = {
            c.strip().lower()
            for c in manifest_capabilities
            if isinstance(c, str) and c.strip()
        }
        normalized_sandbox = {
            c.strip().lower()
            for c in sandbox_allowed_capabilities
            if isinstance(c, str) and c.strip()
        }

        # Default deny; capability must be explicitly declared and policy-allowed.
        return normalized_requested in normalized_manifest and normalized_requested in normalized_sandbox

    def run_plugin_capability(self, *, plugin_id: str, capability_id: str, params: dict[str, object]) -> dict[str, object]:
        origin = str(params.get("_origin", "")).strip().lower() if isinstance(params, dict) else ""
        if capability_id.strip().lower() in self.HIGH_RISK_PLUGIN_ONLY_CAPABILITIES and origin == "ki":
            raise PermissionError("high_risk_direct_request_denied")
        return {"status": "ok", "plugin_id": plugin_id, "capability_id": capability_id}

    def enforce_network_policy(
        self,
        *,
        url: str,
        policy: dict[str, object],
        counters: dict[str, int],
        response_size_bytes: int | None = None,
    ) -> dict[str, object]:
        host = urlparse(url).hostname or ""
        allowed_domains = {str(x).strip().lower() for x in (policy.get("allowed_domains") or []) if str(x).strip()}
        if not host or not any(host == d or host.endswith(f".{d}") for d in allowed_domains):
            return {"allowed": False, "reason_code": "network_domain_not_allowlisted"}

        timeout_ms = int(policy.get("timeout_ms") or self._base_timeout_ms)
        if timeout_ms > self._base_timeout_ms:
            return {"allowed": False, "reason_code": "network_timeout_exceeded"}

        max_calls = int(policy.get("max_calls") or 0)
        key = "network.calls"
        current_calls = counters.get(key, 0)
        if max_calls > 0 and current_calls >= max_calls:
            return {"allowed": False, "reason_code": "network_rate_limited"}

        max_response_bytes = int(policy.get("max_response_bytes") or 0)
        if response_size_bytes is not None and max_response_bytes > 0 and response_size_bytes > max_response_bytes:
            return {"allowed": False, "reason_code": "network_response_cap_exceeded"}

        counters[key] = current_calls + 1
        return {"allowed": True, "reason_code": "policy_allow", "timeout_ms": timeout_ms}

    def enforce_git_policy(self, *, command: str, policy: dict[str, object]) -> dict[str, object]:
        if not bool(policy.get("enabled", False)):
            return {"allowed": False, "reason_code": "git_disabled"}

        stripped = command.strip()
        if not stripped:
            return {"allowed": False, "reason_code": "git_only_commands_allowed"}
        try:
            tokens = shlex.split(stripped)
        except ValueError:
            return {"allowed": False, "reason_code": "git_invalid_command"}

        if not tokens or tokens[0] != "git":
            return {"allowed": False, "reason_code": "git_only_commands_allowed"}

        read_only = bool(policy.get("read_only", True))
        if not read_only:
            return {"allowed": True, "reason_code": "policy_allow"}

        if len(tokens) < 2:
            return {"allowed": False, "reason_code": "git_read_only_command_not_allowlisted"}

        second = tokens[1]
        if second.startswith("-"):
            # Deny command-shaping options such as -c/--exec-path in read-only mode.
            return {"allowed": False, "reason_code": "git_read_only_command_not_allowlisted"}

        subcommand = second.lower()
        if subcommand not in self._GIT_READONLY_ALLOWED_SUBCOMMANDS:
            return {"allowed": False, "reason_code": "git_write_operation_denied"}

        for arg in tokens[2:]:
            lowered = arg.lower()
            if lowered.startswith("-"):
                continue
            if lowered in {"--", "."}:
                continue
            if lowered.startswith("refs/") or lowered.startswith("origin/"):
                continue

        return {"allowed": True, "reason_code": "policy_allow"}

    def enforce_shell_policy(self, *, command: str, policy: dict[str, object]) -> dict[str, object]:
        if not bool(policy.get("enabled", False)):
            return {"allowed": False, "reason_code": "shell_disabled"}

        stripped = command.strip()
        if not stripped:
            return {"allowed": False, "reason_code": "shell_command_not_allowlisted"}
        if self._SHELL_META_PATTERN.search(stripped):
            return {"allowed": False, "reason_code": "shell_meta_syntax_denied"}

        try:
            tokens = shlex.split(stripped)
        except ValueError:
            return {"allowed": False, "reason_code": "shell_invalid_command"}

        allowlist = {str(x).strip() for x in (policy.get("allowlist") or []) if str(x).strip()}
        if not tokens:
            return {"allowed": False, "reason_code": "shell_command_not_allowlisted"}

        command_name = tokens[0]
        if command_name not in allowlist:
            return {"allowed": False, "reason_code": "shell_command_not_allowlisted"}

        allow_pattern = self._SHELL_ALLOWLIST_RULES.get(command_name)
        if allow_pattern is None or not allow_pattern.fullmatch(stripped):
            return {"allowed": False, "reason_code": "shell_command_form_denied"}

        timeout_ms = int(policy.get("timeout_ms") or self._base_timeout_ms)
        if timeout_ms > self._base_timeout_ms:
            return {"allowed": False, "reason_code": "shell_timeout_exceeded"}

        return {"allowed": True, "reason_code": "policy_allow", "timeout_ms": timeout_ms}

    def enforce_secrets_reference_only(self, payload: object) -> object:
        if isinstance(payload, dict):
            out: dict[str, object] = {}
            for k, v in payload.items():
                key_is_string = isinstance(k, str)
                lowered_key = k.strip().lower() if key_is_string else ""
                if lowered_key == "secret_ref":
                    out[k] = v if isinstance(v, str) else "[redacted]"
                elif key_is_string and self._SECRET_KEY_PATTERN.search(k):
                    out[k] = "[redacted]"
                elif self._looks_like_secret_value(v):
                    out[k] = "[redacted]"
                else:
                    out[k] = self.enforce_secrets_reference_only(v)
            return out
        if isinstance(payload, list):
            return [self.enforce_secrets_reference_only(x) for x in payload]
        if self._looks_like_secret_value(payload):
            return "[redacted]"
        return payload

    def _looks_like_secret_value(self, value: object) -> bool:
        if not isinstance(value, str):
            return False
        stripped = value.strip()
        if not stripped:
            return False
        if self._SECRET_VALUE_PATTERN.search(stripped):
            return True
        if len(stripped) >= 24 and re.fullmatch(r"[A-Za-z0-9_\-./+=]+", stripped):
            return True
        return False
    def __init__(
        self,
        worker_module: str = "amo_bot.plugins.sandbox.worker",
        *,
        base_timeout_ms: int = 3000,
        max_timeout_ms: int | None = None,
        max_memory_bytes: int = 128 * 1024 * 1024,
        max_file_descriptors: int = 64,
        max_processes: int = 32,
        max_cpu_seconds: int = 2,
        max_output_bytes: int = 64 * 1024,
        worker_timeout_grace_ms: int = 500,
        plugins_dir: Path | str | None = None,
    ) -> None:
        self._worker_module = worker_module
        self._base_timeout_ms = base_timeout_ms
        self._max_timeout_ms = max_timeout_ms if max_timeout_ms is not None else base_timeout_ms
        self._max_memory_bytes = max_memory_bytes
        self._max_file_descriptors = max_file_descriptors
        self._max_processes = max_processes
        self._max_cpu_seconds = max_cpu_seconds
        self._max_output_bytes = max_output_bytes
        self._worker_timeout_grace_ms = worker_timeout_grace_ms
        self._plugins_dir = Path(plugins_dir).expanduser().resolve() if plugins_dir else None

    def _validate_request(self, request: SandboxRequest) -> None:
        if request.timeout_ms > self._max_timeout_ms:
            raise SandboxRunnerError(
                code=SandboxErrorCode.INVALID_REQUEST,
                message="timeout_exceeds_base_limit",
            )

    def _build_env(self) -> dict[str, str]:
        # Default-deny environment for worker process.
        # Keep only strict minimum for deterministic runtime.
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "PYTHONSAFEPATH": "1",
            "PYTHONNOUSERSITE": "1",
            "AMO_SANDBOX_ALLOWED_CAPABILITIES": "plugin.execute,plugin.runtime.worker.execute,plugin.runtime.schedule.execute,run",
            "AMO_PLUGIN_SANDBOX": "1",
            "AMO_SANDBOX_PLUGIN_DIR": str(self._plugins_dir or self._resolve_plugin_dir()),
        }
        # Make the installed amo_bot package reachable by the worker subprocess.
        # When installed via `pip install -e .` (as in CI), the package lives under
        # sys.prefix/site-packages.  We add it to PYTHONPATH so that the worker can
        # `import amo_bot.ai` without relying on the current working directory.
        python_paths: list[str] = []
        site_packages = Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        if site_packages.exists():
            python_paths.append(str(site_packages))
        for path in sys.path:
            if not path:
                continue
            candidate = Path(path)
            if candidate.exists():
                python_paths.append(str(candidate.resolve()))
        if python_paths:
            env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(python_paths))
        return env

    @staticmethod
    def _resolve_plugin_dir() -> Path:
        cwd = Path.cwd()
        direct = cwd / "plugins"
        if direct.exists():
            return direct.resolve()
        for parent in cwd.parents:
            candidate = parent / "plugins"
            if candidate.exists():
                return candidate.resolve()
        return direct.resolve()

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
        stream_event_handler=None,
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
                    chunk = stream.readline()
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
                        if stream_event_handler is not None:
                            self._handle_stream_chunk(chunk, stream_event_handler)
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

    @staticmethod
    def _handle_stream_chunk(chunk: str, stream_event_handler) -> None:
        for line in chunk.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("type") == "op":
                stream_event_handler(parsed)

    def _parse_worker_response(self, stdout: str, request: SandboxRequest) -> SandboxResponse:
        stdout = self._extract_final_response_json(stdout)
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

    @staticmethod
    def _extract_final_response_json(stdout: str) -> str:
        stripped = stdout.strip()
        if not stripped:
            return stripped
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("type") == "op":
                continue
            return line
        return stripped

    def run(self, request: SandboxRequest, stream_event_handler=None) -> SandboxResponse:
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
                env={**self._build_env(), "AMO_SANDBOX_REQUEST_PLUGIN_ID": request.plugin_id},
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

        effective_timeout_ms = max(
            1,
            min(request.timeout_ms, self._max_timeout_ms) + self._worker_timeout_grace_ms,
        )

        try:
            stdout, stderr = self._read_streams_with_limits(
                proc,
                timeout_s=effective_timeout_ms / 1000,
                stream_event_handler=stream_event_handler,
            )
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
