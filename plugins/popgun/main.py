from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger("amo.plugins.popgun")
BERLIN_TZ = ZoneInfo("Europe/Berlin")
PLUGIN_STATE_DIR = Path("data") / "plugin_state" / "popgun"
DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "XLMUSDT",
    "SOLUSDT",
    "PAXGUSDT",
    "XAGUSDT",
]
DEFAULT_TIMEFRAMES = ["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w", "1M"]
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")


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
    def __init__(self, *, exchange_id: str = "bybit", rate_limit_ms: int | None = 1500) -> None:
        import ccxt

        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Unbekannte Exchange: {exchange_id}")
        self._ccxt = ccxt
        self.exchange_id = exchange_id
        exchange_config: dict[str, object] = {"enableRateLimit": True, "timeout": 1500}
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

    @staticmethod
    def normalize_exchange_symbol(symbol: str) -> str:
        if "/" in symbol:
            return symbol
        if symbol.endswith("USDT"):
            return f"{symbol[:-4]}/USDT"
        return symbol

    def resolve_symbol(self, symbol: str) -> str:
        if symbol in self.exchange.markets:
            return symbol

        normalized = self.normalize_exchange_symbol(symbol)
        if normalized in self.exchange.markets:
            return normalized

        if self.exchange_id == "bybit" and normalized.endswith("/USDT"):
            perpetual = f"{normalized}:USDT"
            if perpetual in self.exchange.markets:
                return perpetual

        raise self._ccxt.BadSymbol(f"Symbol nicht gefunden: {symbol}")


@dataclass(slots=True)
class TopicConfig:
    chat_id: int
    thread_id: int | None
    enabled: bool
    symbols: list[str]
    timeframes: list[str]
    updated_at: str


class PopgunStateRepository:
    def __init__(self, base_dir: str | Path = PLUGIN_STATE_DIR) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._base_dir / "state.json"

    @staticmethod
    def topic_key(chat_id: int, thread_id: int | None) -> str:
        return f"{chat_id}:{thread_id if thread_id is not None else 'root'}"

    def _default_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "default_symbols": list(DEFAULT_SYMBOLS),
            "default_timeframes": list(DEFAULT_TIMEFRAMES),
            "topics": {},
            "alerts": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return self._default_state()
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOGGER.warning("popgun state unreadable; using empty state")
            return self._default_state()
        if not isinstance(raw, dict):
            return self._default_state()
        raw.setdefault("version", 1)
        raw.setdefault("default_symbols", list(DEFAULT_SYMBOLS))
        raw.setdefault("default_timeframes", list(DEFAULT_TIMEFRAMES))
        raw.setdefault("topics", {})
        raw.setdefault("alerts", {})
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

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
        timeframes_raw = value.get("timeframes")
        timeframes = [str(item).strip() for item in timeframes_raw] if isinstance(timeframes_raw, list) else []
        timeframes = [item for item in timeframes if item]
        if not timeframes:
            timeframes = list(DEFAULT_TIMEFRAMES)
        return TopicConfig(
            chat_id=chat_id,
            thread_id=thread_id,
            enabled=bool(value.get("enabled", False)),
            symbols=symbols,
            timeframes=timeframes,
            updated_at=str(value.get("updated_at") or datetime.now(UTC).isoformat()),
        )

    def get_topic(self, *, chat_id: int, thread_id: int | None, create: bool = False) -> TopicConfig | None:
        state = self._load()
        key = self.topic_key(chat_id, thread_id)
        raw = state["topics"].get(key)
        topic = self._coerce_topic(key, raw)
        if topic is not None:
            normalized = asdict(topic)
            if raw != normalized:
                state["topics"][key] = normalized
                self._save(state)
            return topic
        if not create:
            return None
        topic = TopicConfig(
            chat_id=chat_id,
            thread_id=thread_id,
            enabled=False,
            symbols=normalize_symbol_list(state.get("default_symbols") or DEFAULT_SYMBOLS),
            timeframes=[str(item) for item in (state.get("default_timeframes") or DEFAULT_TIMEFRAMES)],
            updated_at=datetime.now(UTC).isoformat(),
        )
        state["topics"][key] = asdict(topic)
        self._save(state)
        return topic

    def set_enabled(self, *, chat_id: int, thread_id: int | None, enabled: bool) -> TopicConfig:
        state = self._load()
        key = self.topic_key(chat_id, thread_id)
        topic = self.get_topic(chat_id=chat_id, thread_id=thread_id, create=True)
        assert topic is not None
        topic.enabled = enabled
        topic.updated_at = datetime.now(UTC).isoformat()
        state["topics"][key] = asdict(topic)
        self._save(state)
        return topic

    def add_symbol(self, *, chat_id: int, thread_id: int | None, symbol: str) -> tuple[TopicConfig, bool]:
        state = self._load()
        key = self.topic_key(chat_id, thread_id)
        topic = self.get_topic(chat_id=chat_id, thread_id=thread_id, create=True)
        assert topic is not None
        normalized = normalize_symbol(symbol)
        created = normalized not in topic.symbols
        if created:
            topic.symbols.append(normalized)
            topic.symbols = sorted(topic.symbols)
            topic.updated_at = datetime.now(UTC).isoformat()
            state["topics"][key] = asdict(topic)
            self._save(state)
        return topic, created

    def list_enabled_topics(self) -> list[TopicConfig]:
        state = self._load()
        topics: list[TopicConfig] = []
        changed = False
        for key, value in list(state.get("topics", {}).items()):
            topic = self._coerce_topic(key, value)
            if topic is None:
                state["topics"].pop(key, None)
                changed = True
                continue
            if topic.enabled and topic.symbols:
                topics.append(topic)
            normalized = asdict(topic)
            if value != normalized:
                state["topics"][key] = normalized
                changed = True
        if changed:
            self._save(state)
        return sorted(topics, key=lambda item: (item.chat_id, item.thread_id or -1))

    def is_new_signal(self, *, topic: TopicConfig, signal: PopgunSignal) -> bool:
        state = self._load()
        topic_key = self.topic_key(topic.chat_id, topic.thread_id)
        key = f"{topic_key}:{signal.symbol}:{signal.timeframe}"
        last_timestamp = state.setdefault("alerts", {}).get(key)
        if last_timestamp == signal.timestamp:
            return False
        state["alerts"][key] = signal.timestamp
        self._save(state)
        return True


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
    readable_time = datetime.fromtimestamp(signal.timestamp / 1000, tz=UTC).astimezone(BERLIN_TZ).strftime("%d.%m.%Y %H:%M")
    return (
        f"[POPGUN] {signal.symbol} {signal.timeframe}\n"
        f"{readable_time}\n"
        f"InsideBar  ({signal.inside_low}-{signal.inside_high})\n"
        f"OutsideBar ({signal.outside_low}-{signal.outside_high})"
    )


async def handle_command(context: Any, host_api: Any) -> None:
    repo = PopgunStateRepository()
    command = str(_get(context, "command_name", "")).strip().lower()
    argument = str(_get(context, "argument", "") or "").strip()
    chat_id = int(_get(context, "chat_id"))
    message_id = int(_get(context, "message_id"))
    thread_id = _get(context, "message_thread_id", None)
    thread_id = int(thread_id) if thread_id is not None else None

    if not _is_manager(context):
        await host_api.reply(chat_id, message_id, "Nur Admins/Owner dürfen Popgun verwalten.")
        return

    if command == "popgun":
        action = argument.lower()
        if action not in {"on", "off"}:
            await host_api.reply(chat_id, message_id, "Verwendung: /popgun on oder /popgun off")
            return
        topic = repo.set_enabled(chat_id=chat_id, thread_id=thread_id, enabled=action == "on")
        status = "aktiviert" if topic.enabled else "deaktiviert"
        await host_api.reply(
            chat_id,
            message_id,
            f"Popgun ist für dieses Topic {status}. Coins: {', '.join(topic.symbols) if topic.symbols else 'keine'}",
        )
        return

    if command == "popgunadd":
        parts = argument.split()
        if len(parts) != 1:
            await host_api.reply(chat_id, message_id, "Verwendung: /popgunadd BTCUSDT")
            return
        try:
            symbol = normalize_symbol(parts[0])
        except ValueError:
            await host_api.reply(chat_id, message_id, "Ungültiges Symbol. Beispiel: /popgunadd BTCUSDT")
            return
        try:
            CcxtCandleClient(exchange_id="bybit").resolve_symbol(symbol)
        except Exception as exc:
            LOGGER.warning("popgun symbol validation failed", extra={"symbol": symbol, "error": type(exc).__name__})
            await host_api.reply(chat_id, message_id, f"Symbol nicht auf Bybit gefunden: {symbol}")
            return
        topic, created = repo.add_symbol(chat_id=chat_id, thread_id=thread_id, symbol=symbol)
        if created:
            await host_api.reply(chat_id, message_id, f"{symbol} wurde für dieses Topic hinzugefügt.")
        else:
            await host_api.reply(chat_id, message_id, f"{symbol} ist in diesem Topic bereits vorhanden.")
        return

    await host_api.reply(chat_id, message_id, "Unbekannter Popgun-Befehl.")


async def handle_worker(context: Any, host_api: Any) -> dict[str, Any]:
    repo = PopgunStateRepository()
    detector = PopgunDetector()
    poll_interval_seconds = 300
    candle_limit = 5
    request_pause_seconds = 0.5
    batch_pause_seconds = 1.5

    try:
        candle_client = CcxtCandleClient(exchange_id="bybit", rate_limit_ms=1500)
    except Exception as exc:
        LOGGER.exception("popgun worker failed to initialize exchange")
        raise RuntimeError(f"exchange_init_failed:{type(exc).__name__}") from exc

    while True:
        topics = repo.list_enabled_topics()
        for topic in topics:
            for symbol in topic.symbols:
                for timeframe in topic.timeframes:
                    try:
                        candles = candle_client.fetch_candles(symbol=symbol, timeframe=timeframe, limit=candle_limit)
                        signal = detector.detect_latest(symbol=symbol, timeframe=timeframe, candles=candles)
                    except Exception as exc:
                        LOGGER.warning(
                            "popgun scan failed",
                            extra={"symbol": symbol, "timeframe": timeframe, "error": type(exc).__name__},
                        )
                        continue
                    if signal is not None and repo.is_new_signal(topic=topic, signal=signal):
                        await host_api.send_message(
                            topic.chat_id,
                            _format_signal(signal),
                            message_thread_id=topic.thread_id,
                        )
                    await asyncio.sleep(request_pause_seconds)
            await asyncio.sleep(batch_pause_seconds)
        await asyncio.sleep(poll_interval_seconds)
