from __future__ import annotations

import json
import os
import signal
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    cmdline: tuple[str, ...]
    cwd: str | None
    start_time_ticks: int | None


@dataclass(frozen=True)
class StopResult:
    ok: bool
    message: str


def _read_proc_start_time(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        after_comm = stat.rsplit(")", 1)[1].strip()
        fields = after_comm.split()
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def _read_process_snapshot(pid: int) -> ProcessSnapshot | None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass

    cmdline: tuple[str, ...] = ()
    try:
        raw_cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        cmdline = tuple(part.decode("utf-8", errors="replace") for part in raw_cmdline.split(b"\0") if part)
    except OSError:
        pass

    cwd: str | None = None
    try:
        cwd = str(Path(f"/proc/{pid}/cwd").resolve())
    except OSError:
        pass

    return ProcessSnapshot(
        pid=pid,
        cmdline=cmdline,
        cwd=cwd,
        start_time_ticks=_read_proc_start_time(pid),
    )


def _current_process_snapshot() -> ProcessSnapshot:
    return ProcessSnapshot(
        pid=os.getpid(),
        cmdline=tuple(sys.argv),
        cwd=str(Path.cwd()),
        start_time_ticks=_read_proc_start_time(os.getpid()),
    )


def _pid_file_path(pid_file: str | os.PathLike[str]) -> Path:
    path = Path(pid_file)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def write_pid_file(pid_file: str | os.PathLike[str]) -> Path:
    path = _pid_file_path(pid_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = _current_process_snapshot()
    payload = {
        "pid": snapshot.pid,
        "start_time_ticks": snapshot.start_time_ticks,
        "cmdline": list(snapshot.cmdline),
        "cwd": snapshot.cwd,
        "project_root": str(PROJECT_ROOT),
    }
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)
    return path


def _load_pid_file(path: Path) -> tuple[int, int | None, str | None]:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError("PID file is empty")

    if raw.startswith("{"):
        payload = json.loads(raw)
        pid = int(payload["pid"])
        start_time_ticks = payload.get("start_time_ticks")
        expected_project_root = payload.get("project_root")
        return (
            pid,
            int(start_time_ticks) if start_time_ticks is not None else None,
            str(expected_project_root) if expected_project_root else None,
        )

    return int(raw), None, None


def _is_bot_process(snapshot: ProcessSnapshot, expected_project_root: str | None) -> bool:
    cmdline = snapshot.cmdline
    joined = "\0".join(cmdline)
    project_root = expected_project_root or str(PROJECT_ROOT)
    project_main = str(Path(project_root) / "main.py")
    package_main = str(Path(project_root) / "src" / "amo_bot" / "main.py")

    if "amo_bot.main" in cmdline:
        return True
    if project_main in cmdline or package_main in cmdline:
        return True
    if "amo_bot/main.py" in joined:
        return True

    if snapshot.cwd == project_root and any(Path(arg).name == "main.py" for arg in cmdline):
        return True

    return False


def _pid_file_matches_current_process(path: Path) -> bool:
    try:
        pid, expected_start_time, _expected_project_root = _load_pid_file(path)
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
        return False
    current = _current_process_snapshot()
    if pid != current.pid:
        return False
    if expected_start_time is not None and current.start_time_ticks is not None:
        return expected_start_time == current.start_time_ticks
    return True


def remove_pid_file_if_current(pid_file: str | os.PathLike[str]) -> None:
    path = _pid_file_path(pid_file)
    if _pid_file_matches_current_process(path):
        path.unlink(missing_ok=True)


@contextmanager
def pid_file(pid_file: str | os.PathLike[str]) -> Iterator[Path]:
    path = write_pid_file(pid_file)
    try:
        yield path
    finally:
        remove_pid_file_if_current(path)


def stop_running_bot(pid_file: str | os.PathLike[str], *, timeout_seconds: float = 5.0) -> StopResult:
    path = _pid_file_path(pid_file)
    if not path.exists():
        return StopResult(False, f"No PID file found at {path}; nothing to stop.")

    try:
        pid, expected_start_time, expected_project_root = _load_pid_file(path)
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return StopResult(False, f"PID file at {path} is invalid: {exc}.")
    except OSError as exc:
        return StopResult(False, f"Could not read PID file at {path}: {exc}.")

    if pid <= 0:
        return StopResult(False, f"PID file at {path} contains invalid PID {pid}.")
    if pid == os.getpid():
        return StopResult(False, "Refusing to stop the current CLI process.")

    snapshot = _read_process_snapshot(pid)
    if snapshot is None:
        return StopResult(False, f"PID file at {path} is stale; process {pid} is not running.")

    if expected_start_time is not None and snapshot.start_time_ticks is not None:
        if snapshot.start_time_ticks != expected_start_time:
            return StopResult(False, f"PID file at {path} is stale; PID {pid} now belongs to a different process.")

    if not _is_bot_process(snapshot, expected_project_root):
        return StopResult(False, f"Refusing to stop PID {pid}: process command does not match AMO bot startup.")

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return StopResult(False, f"PID file at {path} is stale; process {pid} disappeared before SIGTERM.")
    except PermissionError as exc:
        return StopResult(False, f"Permission denied sending SIGTERM to PID {pid}: {exc}.")

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        if _read_process_snapshot(pid) is None:
            return StopResult(True, f"Sent SIGTERM to AMO bot process {pid}; process exited.")
        time.sleep(0.1)

    if _read_process_snapshot(pid) is None:
        return StopResult(True, f"Sent SIGTERM to AMO bot process {pid}; process exited.")

    return StopResult(
        False,
        f"Sent SIGTERM to AMO bot process {pid}, but it is still running after {timeout_seconds:g}s.",
    )
