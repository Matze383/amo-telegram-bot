from __future__ import annotations

import json
import signal
from pathlib import Path

from amo_bot import process_control
from amo_bot.process_control import ProcessSnapshot


def test_pid_file_context_writes_current_pid_and_removes_on_exit(tmp_path) -> None:
    pid_path = tmp_path / "amo_bot.pid"

    with process_control.pid_file(pid_path) as written_path:
        assert written_path == pid_path
        payload = json.loads(pid_path.read_text(encoding="utf-8"))
        assert payload["pid"] == process_control.os.getpid()
        assert payload["project_root"] == str(process_control.PROJECT_ROOT)

    assert not pid_path.exists()


def test_pid_file_context_resolves_relative_path_from_project_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(process_control, "PROJECT_ROOT", tmp_path)
    relative_pid_path = Path(".state") / "amo_bot.pid"
    expected_pid_path = tmp_path / relative_pid_path

    with process_control.pid_file(relative_pid_path) as written_path:
        assert written_path == expected_pid_path
        assert expected_pid_path.exists()

    assert not expected_pid_path.exists()


def test_stop_running_bot_missing_pid_file_returns_clean_error(tmp_path) -> None:
    result = process_control.stop_running_bot(tmp_path / "missing.pid")

    assert result.ok is False
    assert "No PID file found" in result.message


def test_stop_running_bot_refuses_current_cli_process(tmp_path) -> None:
    pid_path = tmp_path / "amo_bot.pid"

    with process_control.pid_file(pid_path):
        result = process_control.stop_running_bot(pid_path)

    assert result.ok is False
    assert "Refusing to stop the current CLI process" in result.message


def test_stop_running_bot_invalid_pid_file_returns_clean_error(tmp_path) -> None:
    pid_path = tmp_path / "amo_bot.pid"
    pid_path.write_text("not-a-pid", encoding="utf-8")

    result = process_control.stop_running_bot(pid_path)

    assert result.ok is False
    assert "invalid" in result.message


def test_stop_running_bot_stale_pid_does_not_signal(monkeypatch, tmp_path) -> None:
    pid_path = tmp_path / "amo_bot.pid"
    pid_path.write_text(json.dumps({"pid": 4242, "start_time_ticks": 10}), encoding="utf-8")
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr(process_control, "_read_process_snapshot", lambda pid: None)
    monkeypatch.setattr(process_control.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    result = process_control.stop_running_bot(pid_path)

    assert result.ok is False
    assert "stale" in result.message
    assert signals == []


def test_stop_running_bot_refuses_reused_pid_with_different_start_time(monkeypatch, tmp_path) -> None:
    pid_path = tmp_path / "amo_bot.pid"
    pid_path.write_text(
        json.dumps(
            {
                "pid": 4242,
                "start_time_ticks": 10,
                "project_root": str(process_control.PROJECT_ROOT),
            }
        ),
        encoding="utf-8",
    )
    snapshot = ProcessSnapshot(
        pid=4242,
        cmdline=("python", str(process_control.PROJECT_ROOT / "main.py")),
        cwd=str(process_control.PROJECT_ROOT),
        start_time_ticks=11,
    )
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr(process_control, "_read_process_snapshot", lambda pid: snapshot)
    monkeypatch.setattr(process_control.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    result = process_control.stop_running_bot(pid_path)

    assert result.ok is False
    assert "different process" in result.message
    assert signals == []


def test_stop_running_bot_refuses_process_that_does_not_match_bot(monkeypatch, tmp_path) -> None:
    pid_path = tmp_path / "amo_bot.pid"
    pid_path.write_text(json.dumps({"pid": 4242, "start_time_ticks": 10}), encoding="utf-8")
    snapshot = ProcessSnapshot(
        pid=4242,
        cmdline=("sleep", "300"),
        cwd="/tmp",
        start_time_ticks=10,
    )
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr(process_control, "_read_process_snapshot", lambda pid: snapshot)
    monkeypatch.setattr(process_control.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    result = process_control.stop_running_bot(pid_path)

    assert result.ok is False
    assert "Refusing to stop PID 4242" in result.message
    assert signals == []


def test_stop_running_bot_sends_sigterm_and_reports_exit(monkeypatch, tmp_path) -> None:
    pid_path = tmp_path / "amo_bot.pid"
    pid_path.write_text(
        json.dumps(
            {
                "pid": 4242,
                "start_time_ticks": 10,
                "project_root": str(process_control.PROJECT_ROOT),
            }
        ),
        encoding="utf-8",
    )
    snapshot = ProcessSnapshot(
        pid=4242,
        cmdline=("python", str(process_control.PROJECT_ROOT / "main.py"), "--serve"),
        cwd=str(process_control.PROJECT_ROOT),
        start_time_ticks=10,
    )
    snapshots = [snapshot, None]
    signals: list[tuple[int, int]] = []

    def _fake_snapshot(pid: int) -> ProcessSnapshot | None:
        return snapshots.pop(0)

    monkeypatch.setattr(process_control, "_read_process_snapshot", _fake_snapshot)
    monkeypatch.setattr(process_control.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    result = process_control.stop_running_bot(pid_path)

    assert result.ok is True
    assert "process exited" in result.message
    assert signals == [(4242, signal.SIGTERM)]


def test_stop_running_bot_timeout_does_not_escalate_to_sigkill(monkeypatch, tmp_path) -> None:
    pid_path = tmp_path / "amo_bot.pid"
    pid_path.write_text(json.dumps({"pid": 4242, "start_time_ticks": 10}), encoding="utf-8")
    snapshot = ProcessSnapshot(
        pid=4242,
        cmdline=("python", "-m", "amo_bot.main"),
        cwd=str(process_control.PROJECT_ROOT),
        start_time_ticks=10,
    )
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr(process_control, "_read_process_snapshot", lambda pid: snapshot)
    monkeypatch.setattr(process_control.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    result = process_control.stop_running_bot(pid_path, timeout_seconds=0)

    assert result.ok is False
    assert "still running" in result.message
    assert signals == [(4242, signal.SIGTERM)]
