from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from amo_bot.config.settings import get_settings
from amo_bot.db.base import create_session_factory
from amo_bot.db.repositories import PopgunRepository

LOGGER = logging.getLogger("amo.plugins.popgun")
BERLIN_TZ = ZoneInfo("Europe/Berlin")
PLUGIN_STATE_DIR = Path("data") / "plugin_state" / "popgun"
POPGUN_EXCHANGE_ID = "bybit"
POPGUN_EXCHANGE_NAME = "Bybit USDT Futures/Perps"
DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "XLMUSDT",
    "SOLUSDT",
    "PAXGUSDT",
    "XAGUSDT",
]
FIXED_TIMEFRAMES = ["15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w", "1M"]
DEFAULT_TIMEFRAMES = FIXED_TIMEFRAMES
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")


def _topic_log_extra(*, chat_id: int, thread_id: int | None, **values: Any) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "thread_id": thread_id,
        **values,
    }


@dataclass(slots=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class PopgunSignal:
    symbol: str
    timeframe: str
    timestamp: int
    inside_high: float
    inside_low: float
    outside_high: float
    outside_low: float


class PopgunDetector:
    def detect_latest(self, *, symbol: str, timeframe: str, candles: list[Candle]) -> PopgunSignal | None:
        if len(candles) < 3:
            return None

        prev_candle = candles[-3]
        inside_candle = candles[-2]
        outside_candle = candles[-1]

        is_inside_bar = inside_candle.high <= prev_candle.high and inside_candle.low >= prev_candle.low
        is_outside_bar = outside_candle.high > inside_candle.high and outside_candle.low < inside_candle.low
        if not (is_inside_bar and is_outside_bar):
            return None

        return PopgunSignal(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=outside_candle.timestamp,
            inside_high=inside_candle.high,
            inside_low=inside_candle.low,
            outside_high=outside_candle.high,
            outside_low=outside_candle.low,
        )


class CcxtCandleClient:
    def __init__(self, *, exchange_id: str = POPGUN_EXCHANGE_ID, rate_limit_ms: int | None = 1500) -> None:
        import ccxt

        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Unbekannte Exchange: {exchange_id}")
        self._ccxt = ccxt
        self.exchange_id = exchange_id
        exchange_config: dict[str, object] = {
            "enableRateLimit": True,
            "timeout": 1500,
            "options": {
                "defaultType": "swap",
                "defaultSubType": "linear",
                "settle": "USDT",
            },
        }
        if rate_limit_ms is not None:
            exchange_config["rateLimit"] = rate_limit_ms
        self.exchange = exchange_class(exchange_config)
        self.exchange.load_markets()

    def fetch_candles(self, *, symbol: str, timeframe: str, limit: int = 5) -> list[Candle]:
        resolved_symbol = self.resolve_symbol(symbol)
        ohlcv = self.exchange.fetch_ohlcv(symbol=resolved_symbol, timeframe=timeframe, limit=limit)
        return [
            Candle(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in ohlcv
        ]

    def supports_symbol(self, symbol: str) -> bool:
        try:
            self.resolve_symbol(symbol)
        except Exception:
            return False
        return True

    @staticmethod
    def normalize_exchange_symbol(symbol: str) -> str:
        if "/" in symbol:
            if ":" not in symbol and symbol.endswith("/USDT"):
                return f"{symbol}:USDT"
            return symbol
        if symbol.endswith("USDT"):
            return f"{symbol[:-4]}/USDT:USDT"
        return symbol

    def resolve_symbol(self, symbol: str) -> str:
        if symbol in self.exchange.markets and self._is_supported_usdt_swap(symbol):
            return symbol

        normalized = self.normalize_exchange_symbol(symbol)
        if normalized in self.exchange.markets and self._is_supported_usdt_swap(normalized):
            return normalized

        raise self._ccxt.BadSymbol(f"Symbol nicht gefunden: {symbol}")

    def _is_supported_usdt_swap(self, symbol: str) -> bool:
        market = self.exchange.markets.get(symbol, {})
        if not isinstance(market, dict) or not market:
            return True
        quote = str(market.get("quote") or "").upper()
        settle = str(market.get("settle") or market.get("settleId") or "").upper()
        return bool(market.get("swap")) and bool(market.get("linear")) and quote == "USDT" and settle == "USDT"


@dataclass(slots=True)
class TopicConfig:
    chat_id: int
    thread_id: int | None
    enabled: bool
    symbols: list[str]
    timeframes: list[str]
    updated_at: str


@dataclass(frozen=True, slots=True)
class FetchJob:
    symbol: str
    timeframe: str


class PopgunStateRepository:
    def __init__(
        self,
        base_dir: str | Path = PLUGIN_STATE_DIR,
        database_url: str | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._state_path = self._base_dir / "state.json"
        self._database_url = database_url or get_settings().database_url
        self._session_factory = create_session_factory(self._database_url)
        self._ensure_sql_defaults_and_migrate_legacy()

    @staticmethod
    def topic_key(chat_id: int, thread_id: int | None) -> str:
        return PopgunRepository.topic_key(chat_id, thread_id)

    def _default_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "default_symbols": list(DEFAULT_SYMBOLS),
            "default_timeframes": list(DEFAULT_TIMEFRAMES),
            "topics": {},
            "alerts": {},
        }

    def _load_from_file(self) -> dict[str, Any] | None:
        if not self._state_path.exists():
            return None
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning(
                "popgun state unreadable; using empty state",
                extra={"state_path": str(self._state_path), "error_class": type(exc).__name__},
            )
            return None
        if not isinstance(raw, dict):
            LOGGER.debug(
                "popgun state malformed; using empty state",
                extra={"state_path": str(self._state_path), "value_type": type(raw).__name__},
            )
            return None
        raw.setdefault("version", 1)
        raw.setdefault("default_symbols", list(DEFAULT_SYMBOLS))
        raw.setdefault("default_timeframes", list(DEFAULT_TIMEFRAMES))
        raw.setdefault("topics", {})
        raw.setdefault("alerts", {})
        return raw

    def _load(self) -> dict[str, Any]:
        return self._load_from_file() or self._default_state()

    def _coerce_topic(self, key: str, value: Any) -> TopicConfig | None:
        if not isinstance(value, dict):
            return None
        try:
            chat_id = int(value.get("chat_id"))
        except (TypeError, ValueError):
            return None
        thread_raw = value.get("thread_id")
        if thread_raw is None:
            thread_id = None
        else:
            try:
                thread_id = int(thread_raw)
            except (TypeError, ValueError):
                return None
        symbols_raw = value.get("symbols")
        symbols = normalize_symbol_list(symbols_raw if isinstance(symbols_raw, list) else [])
        return TopicConfig(
            chat_id=chat_id,
            thread_id=thread_id,
            enabled=bool(value.get("enabled", False)),
            symbols=symbols,
            timeframes=list(DEFAULT_TIMEFRAMES),
            updated_at=str(value.get("updated_at") or datetime.now(UTC).isoformat()),
        )

    def _ensure_sql_defaults_and_migrate_legacy(self) -> None:
        with self._session_factory() as session:
            repository = PopgunRepository(session)
            has_sql_state = repository.has_topics_or_alerts()
            repository.ensure_defaults(symbols=list(DEFAULT_SYMBOLS), timeframes=list(DEFAULT_TIMEFRAMES))
            if not self._state_path.exists() or repository.is_legacy_import_completed():
                return

        legacy_state = self._load_from_file()
        if legacy_state is None:
            return

        topics = legacy_state.get("topics")
        alerts = legacy_state.get("alerts")
        if not isinstance(topics, dict):
            topics = {}
        if not isinstance(alerts, dict):
            alerts = {}
        default_symbols_raw = legacy_state.get("default_symbols")
        normalized_defaults = normalize_symbol_list(
            default_symbols_raw if isinstance(default_symbols_raw, list) else []
        ) or list(DEFAULT_SYMBOLS)
        normalized_timeframes = list(DEFAULT_TIMEFRAMES)
        has_custom_defaults = normalized_defaults != list(DEFAULT_SYMBOLS)
        if not topics and not alerts and not has_custom_defaults:
            return

        imported_topics = 0
        imported_alerts = 0
        skipped_topics = 0
        with self._session_factory() as session:
            repository = PopgunRepository(session)
            if not has_sql_state:
                repository.set_defaults(symbols=normalized_defaults, timeframes=normalized_timeframes)
            for key, value in topics.items():
                topic = self._coerce_topic(str(key), value)
                if topic is None:
                    LOGGER.debug(
                        "popgun topic state dropped",
                        extra={"topic_key": str(key), "value_type": type(value).__name__},
                    )
                    continue
                if not topic.symbols:
                    topic.symbols = list(normalized_defaults)
                topic.timeframes = list(normalized_timeframes)
                normalized_topic = {
                    "chat_id": topic.chat_id,
                    "thread_id": topic.thread_id,
                    "enabled": topic.enabled,
                    "symbols": topic.symbols,
                    "timeframes": topic.timeframes,
                    "updated_at": topic.updated_at,
                }
                if value != normalized_topic:
                    LOGGER.debug("popgun topic state normalized", extra={"topic_key": str(key)})
                if repository.get_topic(chat_id=topic.chat_id, thread_id=topic.thread_id) is not None:
                    skipped_topics += 1
                    LOGGER.info(
                        "popgun legacy topic skipped; sql topic already exists",
                        extra={"topic_key": str(key)},
                    )
                    continue
                repository.upsert_topic(
                    chat_id=topic.chat_id,
                    thread_id=topic.thread_id,
                    enabled=topic.enabled,
                    symbols=topic.symbols,
                    timeframes=topic.timeframes,
                )
                imported_topics += 1

            for key, timestamp in alerts.items():
                imported_alerts += self._import_legacy_alert(repository, str(key), timestamp)

            repository.mark_legacy_import_completed(
                state_path=str(self._state_path),
                topics_count=imported_topics,
                alerts_count=imported_alerts,
            )

        LOGGER.info(
            "popgun legacy state imported",
            extra={
                "state_path": str(self._state_path),
                "topics_count": imported_topics,
                "skipped_topics_count": skipped_topics,
                "alerts_count": imported_alerts,
            },
        )

    def _import_legacy_alert(self, repository: PopgunRepository, key: str, timestamp: Any) -> int:
        try:
            topic_key, symbol_raw, timeframe_raw = key.rsplit(":", 2)
            chat_raw, thread_raw = topic_key.split(":", 1)
            chat_id = int(chat_raw)
            thread_id = None if thread_raw == "root" else int(thread_raw)
            symbol = normalize_symbol(symbol_raw)
            timeframe = normalize_timeframe(timeframe_raw)
            signal_timestamp = int(timestamp)
        except (TypeError, ValueError):
            LOGGER.debug("popgun legacy alert state dropped", extra={"alert_key": key})
            return 0

        return int(
            repository.record_alert_if_new(
                chat_id=chat_id,
                thread_id=thread_id,
                symbol=symbol,
                timeframe=timeframe,
                signal_timestamp=signal_timestamp,
                inside_high=None,
                inside_low=None,
                outside_high=None,
                outside_low=None,
            )
        )

    @staticmethod
    def _from_snapshot(snapshot: Any) -> TopicConfig:
        return TopicConfig(
            chat_id=snapshot.chat_id,
            thread_id=snapshot.thread_id,
            enabled=snapshot.enabled,
            symbols=normalize_symbol_list(snapshot.symbols),
            timeframes=normalize_timeframe_list(snapshot.timeframes),
            updated_at=snapshot.updated_at,
        )

    def get_topic(self, *, chat_id: int, thread_id: int | None, create: bool = False) -> TopicConfig | None:
        with self._session_factory() as session:
            repository = PopgunRepository(session)
            snapshot = repository.get_topic(chat_id=chat_id, thread_id=thread_id)
            if snapshot is not None:
                return self._from_snapshot(snapshot)
            if not create:
                return None
            default_symbols, default_timeframes = repository.get_defaults(
                fallback_symbols=list(DEFAULT_SYMBOLS),
                fallback_timeframes=list(DEFAULT_TIMEFRAMES),
            )
            snapshot = repository.upsert_topic(
                chat_id=chat_id,
                thread_id=thread_id,
                enabled=False,
                symbols=normalize_symbol_list(default_symbols),
                timeframes=normalize_timeframe_list(default_timeframes),
            )
            return self._from_snapshot(snapshot)

    def set_enabled(self, *, chat_id: int, thread_id: int | None, enabled: bool) -> TopicConfig:
        topic = self.get_topic(chat_id=chat_id, thread_id=thread_id, create=True)
        assert topic is not None
        with self._session_factory() as session:
            snapshot = PopgunRepository(session).upsert_topic(
                chat_id=chat_id,
                thread_id=thread_id,
                enabled=enabled,
                symbols=topic.symbols,
                timeframes=list(DEFAULT_TIMEFRAMES),
            )
            return self._from_snapshot(snapshot)

    def add_symbol(self, *, chat_id: int, thread_id: int | None, symbol: str) -> tuple[TopicConfig, bool]:
        topic = self.get_topic(chat_id=chat_id, thread_id=thread_id, create=True)
        assert topic is not None
        normalized = normalize_symbol(symbol)
        created = normalized not in topic.symbols
        if created:
            topic.symbols.append(normalized)
            topic.symbols = sorted(topic.symbols)
        with self._session_factory() as session:
            snapshot = PopgunRepository(session).upsert_topic(
                chat_id=chat_id,
                thread_id=thread_id,
                enabled=topic.enabled,
                symbols=topic.symbols,
                timeframes=list(DEFAULT_TIMEFRAMES),
            )
            return self._from_snapshot(snapshot), created

    def list_enabled_topics(self) -> list[TopicConfig]:
        with self._session_factory() as session:
            return [self._from_snapshot(snapshot) for snapshot in PopgunRepository(session).list_enabled_topics()]

    def is_new_signal(self, *, topic: TopicConfig, signal: PopgunSignal) -> bool:
        with self._session_factory() as session:
            return PopgunRepository(session).record_alert_if_new(
                chat_id=topic.chat_id,
                thread_id=topic.thread_id,
                symbol=signal.symbol,
                timeframe=signal.timeframe,
                signal_timestamp=signal.timestamp,
                inside_high=signal.inside_high,
                inside_low=signal.inside_low,
                outside_high=signal.outside_high,
                outside_low=signal.outside_low,
            )


def normalize_symbol(raw: str) -> str:
    normalized = (raw or "").strip().upper().replace("/", "").replace(":USDT", "")
    if not SYMBOL_RE.fullmatch(normalized):
        raise ValueError("invalid_symbol")
    return normalized


def normalize_symbol_list(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            normalized = normalize_symbol(value)
        except ValueError:
            continue
        if normalized not in out:
            out.append(normalized)
    return sorted(out)


def normalize_timeframe(raw: str) -> str:
    normalized = (raw or "").strip()
    if normalized not in FIXED_TIMEFRAMES:
        raise ValueError("invalid_timeframe")
    return normalized


def normalize_timeframe_list(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            normalized = normalize_timeframe(value)
        except ValueError:
            continue
        if normalized not in out:
            out.append(normalized)
    return out or list(DEFAULT_TIMEFRAMES)


def build_fetch_plan(topics: list[TopicConfig]) -> list[FetchJob]:
    jobs: list[FetchJob] = []
    seen: set[tuple[str, str]] = set()
    for topic in topics:
        if not topic.enabled:
            continue
        for symbol in topic.symbols:
            for timeframe in DEFAULT_TIMEFRAMES:
                key = (symbol, timeframe)
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(FetchJob(symbol=symbol, timeframe=timeframe))
    return jobs


def topics_subscribed_to_symbol(topics: list[TopicConfig], symbol: str) -> list[TopicConfig]:
    return [topic for topic in topics if topic.enabled and symbol in topic.symbols]


def _get(context: Any, key: str, default: Any = None) -> Any:
    if isinstance(context, dict):
        return context.get(key, default)
    return getattr(context, key, default)


def _role_name(context: Any) -> str:
    role = _get(context, "role", "")
    return str(getattr(role, "value", role)).strip().lower()


def _is_manager(context: Any) -> bool:
    return _role_name(context) in {"admin", "owner"}


def _format_signal(signal: PopgunSignal) -> str:
    readable_time = (
        datetime.fromtimestamp(signal.timestamp / 1000, tz=UTC)
        .astimezone(BERLIN_TZ)
        .strftime("%d.%m.%Y %H:%M")
    )
    return (
        f"[POPGUN] {signal.symbol} {signal.timeframe}\n"
        f"{readable_time}\n"
        f"InsideBar  ({signal.inside_low}-{signal.inside_high})\n"
        f"OutsideBar ({signal.outside_low}-{signal.outside_high})"
    )


async def handle_command(context: Any, host_api: Any) -> None:
    repo = PopgunStateRepository(database_url=_get(context, "database_url", None))
    command = str(_get(context, "command_name", "")).strip().lower()
    argument = str(_get(context, "argument", "") or "").strip()
    chat_id = int(_get(context, "chat_id"))
    message_id = int(_get(context, "message_id"))
    thread_id = _get(context, "message_thread_id", None)
    thread_id = int(thread_id) if thread_id is not None else None
    role = _role_name(context)
    base_extra = _topic_log_extra(chat_id=chat_id, thread_id=thread_id, command=command, role=role)

    LOGGER.info("popgun command received", extra=base_extra)

    if not _is_manager(context):
        await host_api.reply(chat_id, message_id, "Nur Admins/Owner dürfen Popgun verwalten.")
        LOGGER.info("popgun command handled", extra={**base_extra, "outcome": "unauthorized"})
        return

    if command == "popgun":
        action = argument.lower()
        if action not in {"on", "off"}:
            await host_api.reply(chat_id, message_id, "Verwendung: /popgun on oder /popgun off")
            LOGGER.info(
                "popgun command handled",
                extra={**base_extra, "outcome": "invalid_usage", "action": action},
            )
            return
        topic = repo.set_enabled(chat_id=chat_id, thread_id=thread_id, enabled=action == "on")
        status = "aktiviert" if topic.enabled else "deaktiviert"
        await host_api.reply(
            chat_id,
            message_id,
            f"Popgun ist für dieses Topic {status}. Coins: {', '.join(topic.symbols) if topic.symbols else 'keine'}",
        )
        LOGGER.info(
            "popgun command handled",
            extra={
                **base_extra,
                "outcome": "state_changed",
                "action": action,
                "enabled": topic.enabled,
                "symbol_count": len(topic.symbols),
            },
        )
        return

    if command == "popgunadd":
        parts = argument.split()
        if len(parts) != 1:
            await host_api.reply(chat_id, message_id, "Verwendung: /popgunadd BTCUSDT")
            LOGGER.info(
                "popgun command handled",
                extra={**base_extra, "outcome": "invalid_usage", "action": "add"},
            )
            return
        try:
            symbol = normalize_symbol(parts[0])
        except ValueError:
            await host_api.reply(chat_id, message_id, "Ungültiges Symbol. Beispiel: /popgunadd BTCUSDT")
            LOGGER.info(
                "popgun command handled",
                extra={**base_extra, "outcome": "invalid_symbol", "action": "add"},
            )
            return
        try:
            CcxtCandleClient(exchange_id=POPGUN_EXCHANGE_ID).resolve_symbol(symbol)
        except Exception as exc:
            LOGGER.warning(
                "popgun symbol validation failed",
                extra={
                    **base_extra,
                    "symbol": symbol,
                    "exchange_id": POPGUN_EXCHANGE_ID,
                    "exchange_name": POPGUN_EXCHANGE_NAME,
                    "error_class": type(exc).__name__,
                },
            )
            await host_api.reply(chat_id, message_id, f"Symbol nicht auf {POPGUN_EXCHANGE_NAME} gefunden: {symbol}")
            LOGGER.info(
                "popgun command handled",
                extra={**base_extra, "outcome": "symbol_not_found", "action": "add", "symbol": symbol},
            )
            return
        topic, created = repo.add_symbol(chat_id=chat_id, thread_id=thread_id, symbol=symbol)
        if created:
            await host_api.reply(chat_id, message_id, f"{symbol} wurde für dieses Topic hinzugefügt.")
            outcome = "symbol_added"
        else:
            await host_api.reply(chat_id, message_id, f"{symbol} ist in diesem Topic bereits vorhanden.")
            outcome = "symbol_already_present"
        LOGGER.info(
            "popgun command handled",
            extra={
                **base_extra,
                "outcome": outcome,
                "action": "add",
                "symbol": symbol,
                "symbol_count": len(topic.symbols),
            },
        )
        return

    await host_api.reply(chat_id, message_id, "Unbekannter Popgun-Befehl.")
    LOGGER.info("popgun command handled", extra={**base_extra, "outcome": "unknown_command"})


async def handle_worker(context: Any, host_api: Any) -> dict[str, Any]:
    repo = PopgunStateRepository(database_url=_get(context, "database_url", None))
    detector = PopgunDetector()
    poll_interval_seconds = 60
    candle_limit = 5
    request_pause_seconds = 0.5
    batch_pause_seconds = 1.5

    try:
        candle_client = CcxtCandleClient(exchange_id=POPGUN_EXCHANGE_ID, rate_limit_ms=1500)
    except Exception as exc:
        LOGGER.exception("popgun worker failed to initialize exchange")
        raise RuntimeError(f"exchange_init_failed:{type(exc).__name__}") from exc

    LOGGER.info(
        "popgun worker initialized",
        extra={
            "poll_interval_seconds": poll_interval_seconds,
            "candle_limit": candle_limit,
            "request_pause_seconds": request_pause_seconds,
            "batch_pause_seconds": batch_pause_seconds,
            "exchange_id": candle_client.exchange_id,
            "exchange_name": POPGUN_EXCHANGE_NAME,
        },
    )

    while True:
        loop_started = time.monotonic()
        topics = repo.list_enabled_topics()
        fetch_jobs = build_fetch_plan(topics)
        fetch_symbols = sorted({job.symbol for job in fetch_jobs})
        unsupported_symbols = [symbol for symbol in fetch_symbols if not candle_client.supports_symbol(symbol)]
        for symbol in unsupported_symbols:
            LOGGER.warning(
                "popgun symbol unsupported on exchange",
                extra={
                    "symbol": symbol,
                    "exchange_id": candle_client.exchange_id,
                    "exchange_name": POPGUN_EXCHANGE_NAME,
                },
            )
        if unsupported_symbols:
            unsupported_symbol_set = set(unsupported_symbols)
            fetch_jobs_to_scan = [job for job in fetch_jobs if job.symbol not in unsupported_symbol_set]
        else:
            fetch_jobs_to_scan = fetch_jobs
        scans_attempted = 0
        signals_found = 0
        fanout_topics_count = 0
        alerts_sent = 0
        errors_count = 0
        for job in fetch_jobs_to_scan:
            scans_attempted += 1
            try:
                candles = candle_client.fetch_candles(symbol=job.symbol, timeframe=job.timeframe, limit=candle_limit)
                signal = detector.detect_latest(symbol=job.symbol, timeframe=job.timeframe, candles=candles)
            except Exception as exc:
                errors_count += 1
                LOGGER.warning(
                    "popgun scan failed",
                    extra={
                        "symbol": job.symbol,
                        "timeframe": job.timeframe,
                        "error_class": type(exc).__name__,
                    },
                )
                await asyncio.sleep(request_pause_seconds)
                continue
            if signal is not None:
                signals_found += 1
                fanout_topics = topics_subscribed_to_symbol(topics, signal.symbol)
                fanout_topics_count += len(fanout_topics)
                LOGGER.debug(
                    "popgun signal detected",
                    extra={
                        "symbol": signal.symbol,
                        "timeframe": signal.timeframe,
                        "signal_timestamp": signal.timestamp,
                        "fanout_topics_count": len(fanout_topics),
                    },
                )
                for topic in fanout_topics:
                    signal_extra = {
                        **_topic_log_extra(chat_id=topic.chat_id, thread_id=topic.thread_id),
                        "symbol": signal.symbol,
                        "timeframe": signal.timeframe,
                        "signal_timestamp": signal.timestamp,
                    }
                    if not repo.is_new_signal(topic=topic, signal=signal):
                        LOGGER.debug("popgun duplicate signal skipped", extra=signal_extra)
                        continue
                    await host_api.send_message(
                        topic.chat_id,
                        _format_signal(signal),
                        message_thread_id=topic.thread_id,
                    )
                    alerts_sent += 1
                    LOGGER.info("popgun alert sent", extra=signal_extra)
            await asyncio.sleep(request_pause_seconds)
        if topics:
            await asyncio.sleep(batch_pause_seconds)
        duration_ms = round((time.monotonic() - loop_started) * 1000, 2)
        LOGGER.info(
            "popgun worker loop summary",
            extra={
                "enabled_topics_count": len(topics),
                "fetch_jobs_count": len(fetch_jobs),
                "scans_attempted": scans_attempted,
                "signals_found": signals_found,
                "fanout_topics_count": fanout_topics_count,
                "alerts_sent": alerts_sent,
                "errors_count": errors_count,
                "unsupported_symbols_count": len(unsupported_symbols),
                "duration_ms": duration_ms,
            },
        )
        await asyncio.sleep(poll_interval_seconds)
