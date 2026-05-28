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

from amo_bot.core.logging import duration_timer, log_event, set_run_id, get_run_id
from amo_bot.db.models import AuditEvent
from amo_bot.db.repositories import PluginRepository
from amo_bot.plugins.command_runtime import PluginHostAPI, ReplyFn, SendMessageFn
from amo_bot.plugins.loader import PluginLoader
from amo_bot.plugins.manifest import PluginManifest
from amo_bot.plugins.sandbox.runner import PluginSandboxRunner
from amo_bot.plugins.sandbox.types import SandboxRequest

logger = logging.getLogger(__name__)
_COMPONENT = "plugin.scheduled"

_ALLOWED_SCHEDULE_DIAGNOSTIC_FIELDS = {
    "event",
    "subscriptions_count",
    "checked_count",
    "chat_id",
    "thread_id",
    "channel_key",
    "success",
    "reason_code",
    "error_category",
    "item_count",
    "new_item_count",
    "posted_count",
    "cursor_advanced",
}


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
        self._sandbox_timeout_seconds = max(timeout_seconds, 1.0)
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
            run_id = str(uuid.uuid4())
            token = set_run_id(run_id)
            try:
                await self._execute_one(manifest=manifest, now=run_at)
                executed += 1
            finally:
                set_run_id(None)  # type: ignore[arg-type]
        return executed

    async def _execute_one(self, *, manifest: PluginManifest, now: datetime) -> None:
        run_id = get_run_id() or str(uuid.uuid4())
        schedule = manifest.schedule or {}
        interval_seconds = schedule.get("interval_seconds")
        if isinstance(interval_seconds, int):
            success_next_run_at = now + timedelta(seconds=interval_seconds)
        else:
            success_next_run_at = now + timedelta(seconds=self._backoff_seconds)
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
        log_event(
            logger, logging.INFO,
            event="plugin.schedule.start",
            component=_COMPONENT,
            extra={"plugin_id": manifest.name, "run_id": run_id},
        )

        timing: dict[str, Any] = {}
        with duration_timer(timing):
            try:
                request = SandboxRequest.from_dict(
                    {
                        "request_id": run_id,
                        "action": "run",
                        "plugin_id": manifest.name,
                        "payload": {
                            "plugin_entry": f"{manifest.name}/main.py",
                            "trigger": "schedule",
                            "run_id": run_id,
                            "scheduled_at": now.isoformat(),
                            "capability": "plugin.runtime.schedule.execute",
                            "permissions": list(manifest.required_permissions),
                        },
                        "timeout_ms": int(self._sandbox_timeout_seconds * 1000),
                    }
                )
                response = PluginSandboxRunner(
                    plugins_dir=self._loader.plugins_dir,
                    max_timeout_ms=int(self._sandbox_timeout_seconds * 1000),
                ).run(request)
                if not response.ok:
                    raise RuntimeError(response.error_message or response.error_code or "sandbox_schedule_failed")
                result = response.result or {}
                await self._apply_sandbox_ops(result)
                self._log_schedule_diagnostics(manifest.name, run_id, result)
            except asyncio.TimeoutError:
                duration_ms = timing.get("duration_ms", 0)
                self._record_result(
                    manifest.name,
                    status="timeout",
                    ran_at=now,
                    next_run_at=failure_next_run_at,
                    event_type="plugin_schedule_timeout",
                    payload={"plugin_name": manifest.name, "run_id": run_id, "timeout_seconds": self._timeout_seconds},
                )
                log_event(
                    logger, logging.WARNING,
                    event="plugin.schedule.timeout",
                    component=_COMPONENT,
                    extra={"plugin_id": manifest.name, "run_id": run_id, "duration_ms": duration_ms},
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
                log_event(
                    logger, logging.ERROR,
                    event="plugin.schedule.error",
                    component=_COMPONENT,
                    extra={"plugin_id": manifest.name, "run_id": run_id, "error": str(exc)},
                )
                return

        duration_ms = timing.get("duration_ms", 0)
        self._record_result(
            manifest.name,
            status="success",
            ran_at=now,
            next_run_at=success_next_run_at,
            event_type="plugin_schedule_success",
            payload={"plugin_name": manifest.name, "run_id": run_id, "duration_ms": duration_ms},
        )
        log_event(
            logger, logging.INFO,
            event="plugin.schedule.success",
            component=_COMPONENT,
            duration_ms=duration_ms,
            extra={"plugin_id": manifest.name, "run_id": run_id},
        )

    async def _apply_sandbox_ops(self, result: dict[str, Any]) -> None:
        ops = result.get("ops", [])
        if not isinstance(ops, list):
            raise RuntimeError("invalid sandbox ops")
        for op in ops:
            if not isinstance(op, dict):
                raise RuntimeError("invalid sandbox op")
            op_name = op.get("op")
            chat_id = op.get("chat_id")
            text = op.get("text")
            if op_name == "send_message":
                if not isinstance(chat_id, int) or not isinstance(text, str):
                    raise RuntimeError("invalid sandbox send_message op")
                message_thread_id_raw = op.get("message_thread_id")
                message_thread_id: int | None = None
                if message_thread_id_raw is not None:
                    if not isinstance(message_thread_id_raw, int) or message_thread_id_raw < 1:
                        raise RuntimeError("invalid sandbox send_message op")
                    message_thread_id = message_thread_id_raw
                await self._send_message(chat_id, text, message_thread_id)
            elif op_name == "reply":
                message_id = op.get("message_id")
                if not isinstance(chat_id, int) or not isinstance(message_id, int) or not isinstance(text, str):
                    raise RuntimeError("invalid sandbox reply op")
                await self._reply(chat_id, message_id, text, None)
            else:
                raise RuntimeError("invalid sandbox op")

    def _log_schedule_diagnostics(self, plugin_name: str, run_id: str, result: dict[str, Any]) -> None:
        diagnostics = result.get("schedule_diagnostics")
        if not isinstance(diagnostics, list):
            return
        for entry in diagnostics:
            if not isinstance(entry, dict):
                continue
            sanitized = {k: entry[k] for k in _ALLOWED_SCHEDULE_DIAGNOSTIC_FIELDS if k in entry}
            if not sanitized:
                continue
            logger.info(
                "plugin_schedule_diagnostic",
                extra={"plugin_name": plugin_name, "run_id": run_id, **sanitized},
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
