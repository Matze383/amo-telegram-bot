from __future__ import annotations

from dataclasses import dataclass, field
import logging
import multiprocessing as mp
import os
import signal
import time
from typing import Callable

from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.telegram_queue import TelegramProcessHealthRepository


logger = logging.getLogger(__name__)


ProcessTarget = Callable[..., None]


def get_spawn_context() -> mp.context.BaseContext:
    return mp.get_context("spawn")


@dataclass(slots=True)
class ManagedProcess:
    name: str
    kind: str
    target: ProcessTarget
    args: tuple = ()
    process: mp.Process | None = None


@dataclass(slots=True)
class TelegramProcessSupervisor:
    database_url: str
    processes: dict[str, ManagedProcess] = field(default_factory=dict)
    shutdown_timeout_seconds: float = 10.0
    _stopping: bool = False

    def validate_and_prepare(self) -> None:
        init_db(self.database_url)
        with create_session_factory(self.database_url)() as session:
            TelegramProcessHealthRepository(session).heartbeat(
                process_name="supervisor",
                process_kind="supervisor",
                status="running",
                pid=os.getpid(),
            )

    def register(self, *, name: str, kind: str, target: ProcessTarget, args: tuple = ()) -> None:
        if name in self.processes:
            raise ValueError(f"process already registered: {name}")
        self.processes[name] = ManagedProcess(name=name, kind=kind, target=target, args=args)

    def start_registered(self, names: list[str] | None = None) -> None:
        self.validate_and_prepare()
        ctx = get_spawn_context()
        selected = names if names is not None else list(self.processes)
        for name in selected:
            managed = self.processes[name]
            if managed.process is not None and managed.process.is_alive():
                continue
            process = ctx.Process(target=managed.target, args=managed.args, name=managed.name)
            process.start()
            managed.process = process
            with create_session_factory(self.database_url)() as session:
                TelegramProcessHealthRepository(session).heartbeat(
                    process_name=managed.name,
                    process_kind=managed.kind,
                    status="started",
                    pid=process.pid,
                )

    def terminate_all(self) -> None:
        for managed in self.processes.values():
            process = managed.process
            if process is not None and process.is_alive():
                process.terminate()
        for managed in self.processes.values():
            process = managed.process
            if process is None:
                continue
            process.join(timeout=self.shutdown_timeout_seconds)
            if process.is_alive():
                try:
                    os.kill(process.pid or 0, signal.SIGKILL)
                except OSError:
                    pass
            with create_session_factory(self.database_url)() as session:
                TelegramProcessHealthRepository(session).heartbeat(
                    process_name=managed.name,
                    process_kind=managed.kind,
                    status="stopped",
                    pid=process.pid,
                )

    def start_runtime(
        self,
        *,
        sender: ManagedProcess,
        workers: list[ManagedProcess],
        poller: ManagedProcess,
        background: list[ManagedProcess] | None = None,
    ) -> None:
        self.register(name=sender.name, kind=sender.kind, target=sender.target, args=sender.args)
        for worker in workers:
            self.register(name=worker.name, kind=worker.kind, target=worker.target, args=worker.args)
        self.register(name=poller.name, kind=poller.kind, target=poller.target, args=poller.args)
        for proc in background or []:
            self.register(name=proc.name, kind=proc.kind, target=proc.target, args=proc.args)

        order = [sender.name, *(worker.name for worker in workers), poller.name, *((proc.name for proc in background or []))]
        self.start_registered(order)

    def run_runtime(
        self,
        *,
        sender: ManagedProcess,
        workers: list[ManagedProcess],
        poller: ManagedProcess,
        background: list[ManagedProcess] | None = None,
        monitor_interval_seconds: float = 2.0,
    ) -> None:
        """Start queue runtime and keep the fixed process set alive."""

        self.start_runtime(sender=sender, workers=workers, poller=poller, background=background)
        self._install_signal_handlers()
        try:
            while not self._stopping:
                self._restart_dead_processes()
                time.sleep(max(0.2, monitor_interval_seconds))
        finally:
            self.terminate_all()

    def _install_signal_handlers(self) -> None:
        def _request_stop(_signum, _frame) -> None:  # noqa: ANN001
            self._stopping = True

        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)

    def _restart_dead_processes(self) -> None:
        for name, managed in list(self.processes.items()):
            process = managed.process
            if process is None or process.is_alive():
                continue
            logger.warning("restarting stopped telegram process name=%s exitcode=%s", name, process.exitcode)
            managed.process = None
            self.start_registered([name])
