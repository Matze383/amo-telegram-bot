from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import urlparse

from amo_bot.core.logging import log_event
from amo_bot.current_info.models import JsonDict


@dataclass(frozen=True, slots=True)
class CurrentInfoSafetyConfig:
    max_search_provider_runs_per_response: int = 2
    max_fetch_runs_per_response: int = 3
    max_total_provider_runs_per_response: int = 8
    provider_rate_limit_per_minute: int = 60
    brave_quota_per_minute: int = 30
    crawlee_max_concurrent_per_host: int = 2
    debug_enabled: bool = False


class CurrentInfoBudgetExceeded(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(slots=True)
class CurrentInfoRunBudget:
    config: CurrentInfoSafetyConfig
    search_provider_runs: int = 0
    fetch_runs: int = 0
    total_provider_runs: int = 0
    warnings: list[str] = field(default_factory=list)

    def consume_search_provider_run(self) -> None:
        self._consume_total()
        if self.search_provider_runs >= max(self.config.max_search_provider_runs_per_response, 0):
            self._deny("search_provider_budget_exceeded")
        self.search_provider_runs += 1

    def consume_fetch_run(self) -> None:
        self._consume_total()
        if self.fetch_runs >= max(self.config.max_fetch_runs_per_response, 0):
            self._deny("fetch_budget_exceeded")
        self.fetch_runs += 1

    def to_debug_dict(self) -> JsonDict:
        return {
            "search_provider_runs": self.search_provider_runs,
            "fetch_runs": self.fetch_runs,
            "total_provider_runs": self.total_provider_runs,
            "warnings": list(dict.fromkeys(self.warnings)),
        }

    def _consume_total(self) -> None:
        if self.total_provider_runs >= max(self.config.max_total_provider_runs_per_response, 0):
            self._deny("global_provider_budget_exceeded")
        self.total_provider_runs += 1

    def _deny(self, reason_code: str) -> None:
        self.warnings.append(reason_code)
        raise CurrentInfoBudgetExceeded(reason_code)


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, limit: int, window_seconds: float = 60.0) -> bool:
        if limit <= 0:
            return False
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


class HostConcurrencyLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_by_host: dict[str, int] = defaultdict(int)

    @contextmanager
    def acquire(self, url: str, *, limit: int) -> Iterator[None]:
        host = _host_from_url(url)
        if limit <= 0 or not host:
            yield
            return
        with self._lock:
            if self._active_by_host[host] >= limit:
                raise CurrentInfoBudgetExceeded("host_concurrency_limit")
            self._active_by_host[host] += 1
        try:
            yield
        finally:
            with self._lock:
                self._active_by_host[host] = max(self._active_by_host[host] - 1, 0)
                if self._active_by_host[host] == 0:
                    self._active_by_host.pop(host, None)


GLOBAL_PROVIDER_RATE_LIMITER = InMemoryRateLimiter()
GLOBAL_HOST_CONCURRENCY_LIMITER = HostConcurrencyLimiter()


def build_current_info_safety_config_from_settings(settings: Any) -> CurrentInfoSafetyConfig:
    return CurrentInfoSafetyConfig(
        max_search_provider_runs_per_response=int(
            getattr(settings, "amo_current_info_max_search_provider_runs_per_response", 2)
        ),
        max_fetch_runs_per_response=int(getattr(settings, "amo_current_info_max_fetch_runs_per_response", 3)),
        max_total_provider_runs_per_response=int(
            getattr(settings, "amo_current_info_max_total_provider_runs_per_response", 8)
        ),
        provider_rate_limit_per_minute=int(getattr(settings, "amo_current_info_provider_rate_limit_per_minute", 60)),
        brave_quota_per_minute=int(getattr(settings, "amo_brave_search_quota_per_minute", 30)),
        crawlee_max_concurrent_per_host=int(getattr(settings, "amo_crawlee_max_concurrent_per_host", 2)),
        debug_enabled=bool(getattr(settings, "amo_current_info_debug_output", False)),
    )


def query_hash(query: str) -> str:
    normalized = " ".join((query or "").split()).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def safe_query_fields(query: str) -> JsonDict:
    normalized = " ".join((query or "").split()).strip()
    return {
        "query_hash": query_hash(normalized),
        "query_length": len(normalized),
    }


def log_current_info_event(
    logger: logging.Logger,
    *,
    event: str,
    stage: str,
    query: str = "",
    chat_id: int | None = None,
    user_id: int | None = None,
    topic_id: int | None = None,
    duration_ms: int | None = None,
    outcome: str | None = None,
    reason_code: str | None = None,
    extra: JsonDict | None = None,
    level: int = logging.INFO,
) -> None:
    payload: JsonDict = {"stage": stage}
    if query:
        payload.update(safe_query_fields(query))
    if extra:
        payload.update(_redact_log_payload(extra))
    log_event(
        logger,
        level,
        event=event,
        component="current_info",
        chat_id=chat_id,
        user_id=user_id,
        message_thread_id=topic_id,
        duration_ms=duration_ms,
        outcome=outcome,
        reason_code=reason_code,
        extra=payload,
    )


def _redact_log_payload(payload: JsonDict) -> JsonDict:
    redacted: JsonDict = {}
    for key, value in payload.items():
        if "query" in key.casefold() and key not in {"query_hash", "query_length"}:
            continue
        redacted[key] = value
    return redacted


def _host_from_url(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower().rstrip(".")
