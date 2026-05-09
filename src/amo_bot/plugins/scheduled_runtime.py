from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import sessionmaker

from amo_bot.db.models import AuditEvent
from amo_bot.db.repositories import PluginRepository
from amo_bot.plugins.command_runtime import PluginHostAPI, ReplyFn, SendMessageFn
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ScheduledPluginContext:
    plugin_id: str
    run_id: str
    trigger_type: str
    scheduled_at: datetime


class ScheduledPluginExecutor:
    def __init__(
        self,
        *,
        loader: PluginLoader,
        session_factory: sessionmaker,
        send_message: SendMessageFn,
        reply: ReplyFn,
        timeout_seconds: float = 2.0,
        backoff_seconds: int = 60,
    ) -> None:
        self._loader = loader
        self._session_factory = session_factory
        self._send_message = send_message
        self._reply = reply
        self._timeout_seconds = timeout_seconds
        self._backoff_seconds = backoff_seconds

    async def run_due_once(self, *, now: datetime | None = None) -> int:
        run_at = now or datetime.now(timezone.utc)
        discovery = self._loader.discover()
        manifests = {manifest.name: manifest for manifest in discovery.valid if manifest.schedule is not None}

        with self._session_factory() as session:
            repo = PluginRepository(session)
            repo.sync_discovered(list(manifests.values()))
            due = repo.list_due_scheduled_plugins(now=run_at)

        executed = 0
        for row in due:
            manifest = manifests.get(row.name)
            if manifest is None or manifest.schedule is None:
                continue
            await self._execute_one(manifest=manifest, now=run_at)
            executed += 1
        return executed

    async def _execute_one(self, *, manifest: PluginManifest, now: datetime) -> None:
        run_id = str(uuid.uuid4())
        interval_seconds = manifest.schedule["interval_seconds"] if manifest.schedule else self._backoff_seconds
        success_next_run_at = now + timedelta(seconds=interval_seconds)
        failure_next_run_at = now + timedelta(seconds=self._backoff_seconds)

        context = ScheduledPluginContext(
            plugin_id=manifest.name,
            run_id=run_id,
            trigger_type="schedule",
            scheduled_at=now,
        )

        self._write_audit(
            event_type="plugin_schedule_start",
            payload={"plugin_name": manifest.name, "run_id": run_id},
        )

        start = time.monotonic()
        try:
            host_api = PluginHostAPI(
                send_message=self._send_message,
                reply=self._reply,
                required_permissions=set(manifest.required_permissions),
            )
            handler = self._load_handler(manifest)
            await asyncio.wait_for(handler(context, host_api), timeout=self._timeout_seconds)
        except asyncio.TimeoutError:
            self._record_result(
                manifest.name,
                status="timeout",
                ran_at=now,
                next_run_at=failure_next_run_at,
                event_type="plugin_schedule_timeout",
                payload={"plugin_name": manifest.name, "run_id": run_id, "timeout_seconds": self._timeout_seconds},
            )
            return
        except Exception as exc:
            logger.exception("scheduled plugin failed plugin=%s", manifest.name)
            self._record_result(
                manifest.name,
                status="error",
                ran_at=now,
                next_run_at=failure_next_run_at,
                event_type="plugin_schedule_error",
                payload={"plugin_name": manifest.name, "run_id": run_id, "error": str(exc)},
            )
            return

        duration_ms = int((time.monotonic() - start) * 1000)
        self._record_result(
            manifest.name,
            status="success",
            ran_at=now,
            next_run_at=success_next_run_at,
            event_type="plugin_schedule_success",
            payload={"plugin_name": manifest.name, "run_id": run_id, "duration_ms": duration_ms},
        )

    def _load_handler(self, manifest: PluginManifest) -> Callable[[ScheduledPluginContext, PluginHostAPI], Awaitable[Any]]:
        plugin_dir = Path(self._loader.plugins_dir) / manifest.name
        module_path = plugin_dir / "main.py"
        if not module_path.exists():
            raise RuntimeError("plugin entrypoint main.py not found")

        module_name = f"amo_scheduled_plugin_{manifest.name}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("unable to load plugin module")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        handler = getattr(module, "handle_schedule", None)
        if handler is None or not callable(handler):
            raise RuntimeError("plugin handle_schedule(context, host_api) missing")
        if not inspect.iscoroutinefunction(handler):
            raise RuntimeError("plugin handle_schedule must be async")
        return handler

    def _record_result(
        self,
        plugin_name: str,
        *,
        status: str,
        ran_at: datetime,
        next_run_at: datetime,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        with self._session_factory() as session:
            repo = PluginRepository(session)
            repo.mark_scheduled_result(
                plugin_name=plugin_name,
                status=status,
                ran_at=ran_at,
                next_run_at=next_run_at,
            )
            session.add(AuditEvent(actor_telegram_user_id=None, event_type=event_type, payload_json=json.dumps(payload)))
            session.commit()

    def _write_audit(self, *, event_type: str, payload: dict[str, Any]) -> None:
        with self._session_factory() as session:
            session.add(AuditEvent(actor_telegram_user_id=None, event_type=event_type, payload_json=json.dumps(payload)))
            session.commit()
