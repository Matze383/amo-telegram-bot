from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Coroutine

from sqlalchemy.orm import sessionmaker

from amo_bot.db.models import AuditEvent
from amo_bot.db.repositories import PluginRepository
from amo_bot.plugins.command_runtime import ReplyFn, SendMessageFn
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.manifest import PluginManifest
from amo_bot.plugins.sandbox.runner import PluginSandboxRunner
from amo_bot.plugins.sandbox.types import SandboxErrorCode, SandboxRequest, SandboxResponse, SandboxRunnerError

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class WorkerPluginContext:
    plugin_id: str
    run_id: str
    trigger_type: str
    started_at: datetime


class WorkerPluginManager:
    def __init__(
        self,
        *,
        loader: PluginLoader,
        session_factory: sessionmaker,
        send_message: SendMessageFn,
        reply: ReplyFn,
    ) -> None:
        self._loader = loader
        self._session_factory = session_factory
        self._send_message = send_message
        self._reply = reply
        self._tasks: dict[str, asyncio.Task[None] | Future[None]] = {}
        self._thread_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()

    async def start(self, plugin_name: str, *, now: datetime | None = None) -> bool:
        task = self._tasks.get(plugin_name)
        if task is not None and not task.done():
            return False

        run_at = now or datetime.now(timezone.utc)
        manifest = self._require_worker_manifest(plugin_name)
        if manifest.worker is None:
            raise ValueError("plugin is not a worker")

        with self._session_factory() as session:
            repo = PluginRepository(session)
            repo.sync_discovered([manifest])
            status = repo.get_status(plugin_name)
            if status is None or not status.enabled:
                self._write_audit("plugin_worker_skipped", {"plugin_name": plugin_name, "reason": "plugin_disabled"})
                return False
            if status.worker_next_restart_at is not None and status.worker_next_restart_at > run_at.replace(tzinfo=None):
                self._write_audit("plugin_worker_skipped", {"plugin_name": plugin_name, "reason": "backoff"})
                return False
            repo.mark_worker_state(
                plugin_name=plugin_name,
                state="running",
                heartbeat_at=run_at,
                next_restart_at=None,
                last_error=None,
            )

        run_id = str(uuid.uuid4())
        self._write_audit("plugin_worker_start", {"plugin_name": plugin_name, "run_id": run_id})
        task = self._create_worker_task(self._run_worker(manifest=manifest, run_id=run_id, started_at=run_at))
        self._tasks[plugin_name] = task
        task.add_done_callback(lambda done, name=plugin_name, manifest=manifest: self._on_worker_done(name, manifest, done))
        return True

    async def stop(self, plugin_name: str, *, now: datetime | None = None) -> bool:
        task = self._tasks.get(plugin_name)
        if task is None or task.done():
            self._mark_state(plugin_name, state="stopped", heartbeat_at=now or datetime.now(timezone.utc))
            return False
        task.cancel()
        try:
            if isinstance(task, Future):
                await asyncio.wrap_future(task)
            elif task.get_loop() is asyncio.get_running_loop():
                await task
        except (asyncio.CancelledError, RuntimeError):
            pass
        self._mark_state(plugin_name, state="stopped", heartbeat_at=now or datetime.now(timezone.utc))
        self._write_audit("plugin_worker_stop", {"plugin_name": plugin_name})
        return True

    async def restart(self, plugin_name: str, *, now: datetime | None = None) -> bool:
        await self.stop(plugin_name, now=now)
        return await self.start(plugin_name, now=now)

    def start_sync(self, plugin_name: str, *, now: datetime | None = None) -> bool:
        return self._run_on_thread_loop(self.start(plugin_name, now=now)).result()

    def stop_sync(self, plugin_name: str, *, now: datetime | None = None) -> bool:
        return self._run_on_thread_loop(self.stop(plugin_name, now=now)).result()

    def restart_sync(self, plugin_name: str, *, now: datetime | None = None) -> bool:
        return self._run_on_thread_loop(self.restart(plugin_name, now=now)).result()

    def state(self, plugin_name: str) -> str | None:
        with self._session_factory() as session:
            status = PluginRepository(session).get_status(plugin_name)
        return status.worker_state if status else None

    async def _run_worker(self, *, manifest: PluginManifest, run_id: str, started_at: datetime) -> None:
        plugin_entry = (Path(self._loader.plugins_dir) / manifest.name / "main.py").as_posix()
        worker_timeout_ms = int(manifest.worker.get("timeout_ms", 60_000)) if manifest.worker else 60_000
        worker_timeout_ms = max(100, min(worker_timeout_ms, 600_000))

        while True:
            request = SandboxRequest.from_dict(
                {
                    "request_id": run_id,
                    "action": "run",
                    "plugin_id": manifest.name,
                    "payload": {
                        "plugin_entry": plugin_entry,
                        "trigger": "worker",
                        "run_id": run_id,
                        "started_at": started_at.isoformat(),
                        "capability": "plugin.runtime.worker.execute",
                        "permissions": list(manifest.required_permissions),
                    },
                    "timeout_ms": worker_timeout_ms,
                }
            )
            response = await asyncio.to_thread(
                self._run_sandbox_request,
                request,
                worker_timeout_ms,
                asyncio.get_running_loop(),
            )
            if response is None:
                continue
            if response.ok:
                return

            error_code = response.error_code or ""
            raise RuntimeError(response.error_message or error_code or "sandbox_worker_failed")

    def _run_sandbox_request(
        self,
        request: SandboxRequest,
        worker_timeout_ms: int,
        loop: asyncio.AbstractEventLoop,
    ) -> SandboxResponse | None:
        def _handle_stream_event(event: dict[str, Any]) -> None:
            if event.get("type") != "op":
                return
            op = event.get("op")
            if not isinstance(op, dict):
                return
            future = asyncio.run_coroutine_threadsafe(self._apply_worker_op(op), loop)
            future.result(timeout=max(1.0, worker_timeout_ms / 1000))

        try:
            response = PluginSandboxRunner(base_timeout_ms=worker_timeout_ms, plugins_dir=self._loader.plugins_dir).run(
                request,
                stream_event_handler=_handle_stream_event,
            )
        except SandboxRunnerError as exc:
            if exc.code == SandboxErrorCode.WORKER_TIMEOUT:
                return None
            raise RuntimeError(exc.message or exc.code.value or "sandbox_worker_failed") from exc

        if isinstance(response, dict):
            response = SandboxResponse.from_dict(response)
        return response

    async def _apply_worker_op(self, op: dict[str, Any]) -> None:
        op_name = op.get("op")
        chat_id = op.get("chat_id")
        text = op.get("text")
        if op_name == "send_message":
            if not isinstance(chat_id, int) or not isinstance(text, str):
                raise RuntimeError("invalid worker send_message op")
            message_thread_id = op.get("message_thread_id")
            if message_thread_id is not None:
                if not isinstance(message_thread_id, int) or message_thread_id < 1:
                    raise RuntimeError("invalid worker send_message op")
                await self._send_message(chat_id, text, message_thread_id)  # type: ignore[misc]
            else:
                await self._send_message(chat_id, text)
            return
        if op_name == "reply":
            message_id = op.get("message_id")
            if not isinstance(chat_id, int) or not isinstance(message_id, int) or not isinstance(text, str):
                raise RuntimeError("invalid worker reply op")
            message_thread_id = op.get("message_thread_id")
            if message_thread_id is not None and (not isinstance(message_thread_id, int) or message_thread_id < 1):
                raise RuntimeError("invalid worker reply op")
            await self._reply(chat_id, message_id, text, message_thread_id)
            return
        raise RuntimeError("invalid worker op")

    async def _finalize_worker_done(
        self,
        plugin_name: str,
        manifest: PluginManifest,
        task: asyncio.Task[None] | Future[None],
    ) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            self._mark_state(plugin_name, state="stopped", heartbeat_at=datetime.now(timezone.utc))
            self._write_audit("plugin_worker_exit", {"plugin_name": plugin_name})
            return

        logger.exception("worker plugin crashed plugin=%s", plugin_name, exc_info=exc)
        backoff = manifest.worker["restart_backoff_seconds"] if manifest.worker else 60
        now = datetime.now(timezone.utc)
        self._mark_state(
            plugin_name,
            state="crashed",
            heartbeat_at=now,
            next_restart_at=now + timedelta(seconds=backoff),
            last_error=str(exc),
            increment_restart_count=True,
        )
        self._write_audit("plugin_worker_crash", {"plugin_name": plugin_name, "error": str(exc), "backoff_seconds": backoff})

    def _on_worker_done(self, plugin_name: str, manifest: PluginManifest, task: asyncio.Task[None] | Future[None]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._finalize_worker_done(plugin_name, manifest, task))
            return
        loop.create_task(self._finalize_worker_done(plugin_name, manifest, task))

    def _require_worker_manifest(self, plugin_name: str) -> PluginManifest:
        discovery = self._loader.discover()
        for manifest in discovery.valid:
            if manifest.name == plugin_name:
                return manifest
        raise ValueError("plugin not found or manifest invalid")


    def _create_worker_task(self, coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None] | Future[None]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return self._run_on_thread_loop(coroutine)
        return loop.create_task(coroutine)

    def _run_on_thread_loop(self, coroutine: Coroutine[Any, Any, None] | Coroutine[Any, Any, bool]) -> Future[Any]:
        with self._thread_lock:
            if self._thread_loop is None or self._thread_loop.is_closed():
                self._thread_loop = asyncio.new_event_loop()
                self._thread = threading.Thread(target=self._thread_loop.run_forever, name="amo-plugin-workers", daemon=True)
                self._thread.start()
            return asyncio.run_coroutine_threadsafe(coroutine, self._thread_loop)

    def _mark_state(
        self,
        plugin_name: str,
        *,
        state: str,
        heartbeat_at: datetime,
        next_restart_at: datetime | None = None,
        last_error: str | None = None,
        increment_restart_count: bool = False,
    ) -> None:
        with self._session_factory() as session:
            PluginRepository(session).mark_worker_state(
                plugin_name=plugin_name,
                state=state,
                heartbeat_at=heartbeat_at,
                next_restart_at=next_restart_at,
                last_error=last_error,
                increment_restart_count=increment_restart_count,
            )

    def _write_audit(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._session_factory() as session:
            session.add(AuditEvent(actor_telegram_user_id=None, event_type=event_type, payload_json=json.dumps(payload)))
            session.commit()
