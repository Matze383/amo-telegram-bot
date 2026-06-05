from __future__ import annotations

import importlib.util
import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import PopgunRepository


def _load_popgun_module():
    module_path = Path(__file__).resolve().parents[1] / "plugins" / "popgun" / "main.py"
    spec = importlib.util.spec_from_file_location("popgun_plugin_under_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _HostAPI:
    def __init__(self) -> None:
        self.replies: list[tuple[int, int, str]] = []
        self.sent: list[tuple[int, str, int | None]] = []

    async def reply(self, chat_id: int, message_id: int, text: str) -> None:
        self.replies.append((chat_id, message_id, text))

    async def send_message(self, chat_id: int, text: str, message_thread_id: int | None = None) -> None:
        self.sent.append((chat_id, text, message_thread_id))


def _db_url(tmp_path: Path, name: str) -> str:
    db_url = f"sqlite:///{tmp_path / name}"
    init_db(db_url)
    return db_url


def _seed_topic(
    db_url: str,
    *,
    chat_id: int = 100,
    thread_id: int | None,
    enabled: bool,
    symbols: list[str],
    timeframes: list[str],
) -> None:
    with create_session_factory(db_url)() as session:
        PopgunRepository(session).ensure_defaults(symbols=[], timeframes=timeframes)
        PopgunRepository(session).upsert_topic(
            chat_id=chat_id,
            thread_id=thread_id,
            enabled=enabled,
            symbols=symbols,
            timeframes=timeframes,
        )


def _context(
    *,
    command: str,
    argument: str,
    chat_id: int = 100,
    thread_id: int | None = 5,
    role: str = "admin",
    database_url: str | None = None,
):
    return SimpleNamespace(
        command_name=command,
        argument=argument,
        chat_id=chat_id,
        message_id=99,
        message_thread_id=thread_id,
        user_id=123,
        role=role,
        database_url=database_url,
    )


def test_popgun_detector_detects_inside_then_outside_bar() -> None:
    popgun = _load_popgun_module()
    detector = popgun.PopgunDetector()
    candles = [
        popgun.Candle(timestamp=1, open=10, high=20, low=5, close=12, volume=1),
        popgun.Candle(timestamp=2, open=12, high=18, low=7, close=13, volume=1),
        popgun.Candle(timestamp=3, open=13, high=19, low=6, close=14, volume=1),
    ]

    signal = detector.detect_latest(symbol="BTCUSDT", timeframe="15m", candles=candles)

    assert signal is not None
    assert signal.symbol == "BTCUSDT"
    assert signal.timeframe == "15m"
    assert signal.timestamp == 3


def test_popgun_detector_ignores_negative_cases() -> None:
    popgun = _load_popgun_module()
    detector = popgun.PopgunDetector()

    assert detector.detect_latest(symbol="BTCUSDT", timeframe="15m", candles=[]) is None
    assert detector.detect_latest(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=[
            popgun.Candle(timestamp=1, open=10, high=20, low=5, close=12, volume=1),
            popgun.Candle(timestamp=2, open=12, high=21, low=7, close=13, volume=1),
            popgun.Candle(timestamp=3, open=13, high=22, low=6, close=14, volume=1),
        ],
    ) is None


def test_popgun_fixed_timeframes_are_bybit_usdt_futures_supported_and_skip_5m() -> None:
    popgun = _load_popgun_module()

    assert popgun.POPGUN_EXCHANGE_ID == "bybit"
    assert popgun.POPGUN_EXCHANGE_NAME == "Bybit USDT Futures/Perps"
    assert popgun.DEFAULT_TIMEFRAMES == ["15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w", "1M"]
    assert "5m" not in popgun.DEFAULT_TIMEFRAMES


def test_ccxt_candle_client_defaults_to_bybit_usdt_perp_symbol_resolution(monkeypatch) -> None:
    popgun = _load_popgun_module()
    created_configs: list[dict[str, object]] = []

    class _BadSymbol(Exception):
        pass

    class _FakeBybit:
        def __init__(self, config: dict[str, object]) -> None:
            created_configs.append(config)
            self.markets: dict[str, object] = {}

        def load_markets(self) -> None:
            self.markets = {
                "BTC/USDT:USDT": {"swap": True, "linear": True, "quote": "USDT", "settle": "USDT"},
                "ETH/USDT": {"spot": True, "quote": "USDT"},
                "PAXG/USDT:USDT": {"swap": True, "linear": True, "quote": "USDT", "settle": "USDT"},
                "BTC/USD:BTC": {"swap": True, "linear": False, "quote": "USD", "settle": "BTC"},
            }

    fake_ccxt = SimpleNamespace(bybit=_FakeBybit, BadSymbol=_BadSymbol)
    monkeypatch.setitem(sys.modules, "ccxt", fake_ccxt)

    client = popgun.CcxtCandleClient()

    assert client.exchange_id == "bybit"
    assert created_configs == [
        {
            "enableRateLimit": True,
            "timeout": 1500,
            "options": {"defaultType": "swap", "defaultSubType": "linear", "settle": "USDT"},
            "rateLimit": 1500,
        }
    ]
    assert client.resolve_symbol("BTCUSDT") == "BTC/USDT:USDT"
    assert client.resolve_symbol("BTC/USDT") == "BTC/USDT:USDT"
    assert client.resolve_symbol("PAXGUSDT") == "PAXG/USDT:USDT"

    try:
        client.resolve_symbol("XAGUSDT")
    except _BadSymbol as exc:
        assert "XAGUSDT" in str(exc)
    else:
        raise AssertionError("XAGUSDT should not resolve when Bybit USDT perp markets do not include it")

    try:
        client.resolve_symbol("ETH/USDT")
    except _BadSymbol as exc:
        assert "ETH/USDT" in str(exc)
    else:
        raise AssertionError("ETH/USDT spot should not resolve for Bybit USDT perps")


def test_popgun_on_off_is_topic_scoped(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_on_off.sqlite")
    popgun = _load_popgun_module()
    host = _HostAPI()

    asyncio.run(
        popgun.handle_command(
            _context(command="popgun", argument="on", thread_id=10, database_url=db_url),
            host,
        )
    )
    asyncio.run(
        popgun.handle_command(
            _context(command="popgun", argument="off", thread_id=11, database_url=db_url),
            host,
        )
    )

    repo = popgun.PopgunStateRepository(database_url=db_url)
    topic_a = repo.get_topic(chat_id=100, thread_id=10)
    topic_b = repo.get_topic(chat_id=100, thread_id=11)

    assert topic_a is not None and topic_a.enabled is True
    assert topic_b is not None and topic_b.enabled is False
    assert "aktiviert" in host.replies[0][2]
    assert "deaktiviert" in host.replies[1][2]


def test_popgunadd_adds_one_symbol_for_current_topic_and_dedupes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_add.sqlite")
    popgun = _load_popgun_module()

    class _FakeClient:
        calls: list[dict[str, object]] = []

        def __init__(self, *args, **kwargs) -> None:
            self.calls.append(kwargs)

        def resolve_symbol(self, symbol: str) -> str:
            return f"{symbol[:-4]}/USDT:USDT"

    monkeypatch.setattr(popgun, "CcxtCandleClient", _FakeClient)
    host = _HostAPI()

    asyncio.run(
        popgun.handle_command(
            _context(command="popgunadd", argument="adausdt", thread_id=10, database_url=db_url),
            host,
        )
    )
    asyncio.run(
        popgun.handle_command(
            _context(command="popgunadd", argument="ADAUSDT", thread_id=10, database_url=db_url),
            host,
        )
    )

    repo = popgun.PopgunStateRepository(database_url=db_url)
    topic = repo.get_topic(chat_id=100, thread_id=10)
    sibling = repo.get_topic(chat_id=100, thread_id=11)

    assert topic is not None
    assert "ADAUSDT" in topic.symbols
    assert topic.symbols.count("ADAUSDT") == 1
    assert sibling is None
    assert "hinzugefügt" in host.replies[0][2]
    assert "bereits vorhanden" in host.replies[1][2]
    assert _FakeClient.calls == [{"exchange_id": "bybit"}, {"exchange_id": "bybit"}]


def test_popgunadd_rejects_symbol_missing_on_bybit_usdt_futures(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_add_missing.sqlite")
    popgun = _load_popgun_module()

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve_symbol(self, symbol: str) -> str:
            raise RuntimeError(f"missing {symbol}")

    monkeypatch.setattr(popgun, "CcxtCandleClient", _FakeClient)
    caplog.set_level(logging.WARNING, logger="amo.plugins.popgun")
    host = _HostAPI()

    asyncio.run(
        popgun.handle_command(
            _context(command="popgunadd", argument="FAKEUSDT", thread_id=10, database_url=db_url),
            host,
        )
    )

    assert host.replies == [
        (100, 99, "Symbol nicht auf Bybit USDT Futures/Perps gefunden: FAKEUSDT"),
    ]
    record = next(record for record in caplog.records if record.msg == "popgun symbol validation failed")
    assert record.exchange_id == "bybit"
    assert record.exchange_name == "Bybit USDT Futures/Perps"
    assert record.symbol == "FAKEUSDT"


def test_popgun_command_logging_includes_context_and_outcomes(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_logging.sqlite")
    popgun = _load_popgun_module()

    class _FakeClient:
        calls: list[dict[str, object]] = []

        def __init__(self, *args, **kwargs) -> None:
            self.calls.append(kwargs)

        def resolve_symbol(self, symbol: str) -> str:
            return f"{symbol[:-4]}/USDT:USDT"

    monkeypatch.setattr(popgun, "CcxtCandleClient", _FakeClient)
    caplog.set_level(logging.INFO, logger="amo.plugins.popgun")
    host = _HostAPI()

    asyncio.run(
        popgun.handle_command(
            _context(command="popgun", argument="on", thread_id=10, database_url=db_url),
            host,
        )
    )
    asyncio.run(
        popgun.handle_command(
            _context(command="popgunadd", argument="ADAUSDT", thread_id=10, database_url=db_url),
            host,
        )
    )
    asyncio.run(
        popgun.handle_command(
            _context(command="popgunadd", argument="ADAUSDT", thread_id=10, database_url=db_url),
            host,
        )
    )
    asyncio.run(
        popgun.handle_command(
            _context(command="popgunadd", argument="bad", thread_id=10, database_url=db_url),
            host,
        )
    )
    asyncio.run(
        popgun.handle_command(
            _context(command="popgun", argument="on", role="normal", thread_id=10, database_url=db_url),
            host,
        )
    )

    received = [record for record in caplog.records if record.msg == "popgun command received"]
    handled = [record for record in caplog.records if record.msg == "popgun command handled"]

    assert len(received) == 5
    assert {record.outcome for record in handled} == {
        "state_changed",
        "symbol_added",
        "symbol_already_present",
        "invalid_symbol",
        "unauthorized",
    }
    state_changed = next(record for record in handled if record.outcome == "state_changed")
    assert state_changed.chat_id == 100
    assert state_changed.thread_id == 10
    assert state_changed.role == "admin"
    assert state_changed.action == "on"
    assert state_changed.enabled is True
    added = next(record for record in handled if record.outcome == "symbol_added")
    assert added.symbol == "ADAUSDT"
    assert _FakeClient.calls == [{"exchange_id": "bybit"}, {"exchange_id": "bybit"}]


def test_popgun_rejects_non_manager(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_reject.sqlite")
    popgun = _load_popgun_module()
    host = _HostAPI()

    asyncio.run(
        popgun.handle_command(
            _context(command="popgun", argument="on", role="normal", database_url=db_url),
            host,
        )
    )

    assert host.replies == [(100, 99, "Nur Admins/Owner dürfen Popgun verwalten.")]


def test_popgun_state_logging_for_unreadable_and_malformed_topics(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_legacy.sqlite")
    popgun = _load_popgun_module()
    state_dir = tmp_path / "data" / "plugin_state" / "popgun"
    state_dir.mkdir(parents=True)
    state_path = state_dir / "state.json"

    caplog.set_level(logging.DEBUG, logger="amo.plugins.popgun")
    state_path.write_text("{not-json", encoding="utf-8")
    assert popgun.PopgunStateRepository(database_url=db_url).list_enabled_topics() == []

    unreadable = next(record for record in caplog.records if record.msg == "popgun state unreadable; using empty state")
    assert unreadable.error_class == "JSONDecodeError"
    assert unreadable.state_path.endswith("data/plugin_state/popgun/state.json")

    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_symbols": [],
                "default_timeframes": [],
                "topics": {
                    "bad": "not-a-topic",
                    "100:10": {
                        "chat_id": "100",
                        "thread_id": "10",
                        "enabled": True,
                        "symbols": ["btc/usdt", 123],
                        "timeframes": [],
                    },
                },
                "alerts": {},
            }
        ),
        encoding="utf-8",
    )

    topics = popgun.PopgunStateRepository(database_url=db_url).list_enabled_topics()

    assert len(topics) == 1
    assert topics[0].symbols == ["BTCUSDT"]
    assert topics[0].timeframes == popgun.DEFAULT_TIMEFRAMES
    assert any(
        record.msg == "popgun topic state dropped" and record.topic_key == "bad"
        for record in caplog.records
    )
    assert any(
        record.msg == "popgun topic state normalized" and record.topic_key == "100:10"
        for record in caplog.records
    )


def test_popgun_legacy_state_imports_defaults_and_alerts_idempotently(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_legacy_import.sqlite")
    popgun = _load_popgun_module()
    state_dir = tmp_path / "data" / "plugin_state" / "popgun"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "default_symbols": ["eth/usdt", "bad"],
                "default_timeframes": ["15m", "1h", "5m"],
                "topics": {
                    "100:root": {
                        "chat_id": 100,
                        "thread_id": None,
                        "enabled": True,
                        "symbols": [],
                        "timeframes": ["legacy"],
                        "updated_at": "2030-01-01T00:00:00+00:00",
                    },
                },
                "alerts": {"100:root:ETHUSDT:15m": 123},
            }
        ),
        encoding="utf-8",
    )

    repo = popgun.PopgunStateRepository(database_url=db_url)
    topic = repo.get_topic(chat_id=100, thread_id=None)
    assert topic is not None
    assert topic.symbols == ["ETHUSDT"]
    assert topic.timeframes == popgun.DEFAULT_TIMEFRAMES

    signal = popgun.PopgunSignal(
        symbol="ETHUSDT",
        timeframe="15m",
        timestamp=123,
        inside_high=10,
        inside_low=5,
        outside_high=11,
        outside_low=4,
    )
    assert repo.is_new_signal(topic=topic, signal=signal) is False

    popgun.PopgunStateRepository(database_url=db_url)
    with create_session_factory(db_url)() as session:
        assert PopgunRepository(session).record_alert_if_new(
            chat_id=100,
            thread_id=None,
            symbol="ETHUSDT",
            timeframe="15m",
            signal_timestamp=123,
            inside_high=None,
            inside_low=None,
            outside_high=None,
            outside_low=None,
        ) is False


def test_popgun_legacy_import_does_not_overwrite_existing_sql_topic(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_legacy_preserve_sql.sqlite")
    popgun = _load_popgun_module()
    _seed_topic(
        db_url,
        thread_id=10,
        enabled=True,
        symbols=["BTCUSDT"],
        timeframes=list(popgun.DEFAULT_TIMEFRAMES),
    )
    state_dir = tmp_path / "data" / "plugin_state" / "popgun"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "default_symbols": ["ETHUSDT"],
                "topics": {
                    "100:10": {
                        "chat_id": 100,
                        "thread_id": 10,
                        "enabled": False,
                        "symbols": ["ETHUSDT"],
                        "updated_at": "2030-01-01T00:00:00+00:00",
                    }
                },
                "alerts": {},
            }
        ),
        encoding="utf-8",
    )

    repo = popgun.PopgunStateRepository(database_url=db_url)
    topic = repo.get_topic(chat_id=100, thread_id=10)

    assert topic is not None
    assert topic.enabled is True
    assert topic.symbols == ["BTCUSDT"]


def test_popgun_build_fetch_plan_dedupes_shared_topic_symbols() -> None:
    popgun = _load_popgun_module()
    topics = [
        popgun.TopicConfig(
            chat_id=100,
            thread_id=10,
            enabled=True,
            symbols=["BTCUSDT", "ETHUSDT"],
            timeframes=["legacy"],
            updated_at="2030-01-01T00:00:00+00:00",
        ),
        popgun.TopicConfig(
            chat_id=100,
            thread_id=11,
            enabled=True,
            symbols=["BTCUSDT"],
            timeframes=["legacy"],
            updated_at="2030-01-01T00:00:00+00:00",
        ),
        popgun.TopicConfig(
            chat_id=100,
            thread_id=12,
            enabled=False,
            symbols=["XRPUSDT"],
            timeframes=["legacy"],
            updated_at="2030-01-01T00:00:00+00:00",
        ),
    ]

    jobs = popgun.build_fetch_plan(topics)

    assert len(jobs) == 2 * len(popgun.DEFAULT_TIMEFRAMES)
    assert jobs.count(popgun.FetchJob(symbol="BTCUSDT", timeframe="15m")) == 1
    assert popgun.FetchJob(symbol="XRPUSDT", timeframe="15m") not in jobs


def test_popgun_signal_dedupe_is_topic_symbol_timeframe_scoped(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_dedupe.sqlite")
    popgun = _load_popgun_module()
    repo = popgun.PopgunStateRepository(database_url=db_url)
    topic = repo.set_enabled(chat_id=100, thread_id=10, enabled=True)
    signal = popgun.PopgunSignal(
        symbol="BTCUSDT",
        timeframe="15m",
        timestamp=123,
        inside_high=10,
        inside_low=5,
        outside_high=11,
        outside_low=4,
    )

    assert repo.is_new_signal(topic=topic, signal=signal) is True
    assert repo.is_new_signal(topic=topic, signal=signal) is False
    other_topic = repo.set_enabled(chat_id=100, thread_id=11, enabled=True)
    assert repo.is_new_signal(topic=other_topic, signal=signal) is True


def test_popgun_worker_fetches_globally_and_fans_out_to_subscribed_topics(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_worker_fanout.sqlite")
    popgun = _load_popgun_module()
    _seed_topic(
        db_url,
        thread_id=10,
        enabled=True,
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframes=list(popgun.DEFAULT_TIMEFRAMES),
    )
    _seed_topic(
        db_url,
        thread_id=11,
        enabled=True,
        symbols=["BTCUSDT"],
        timeframes=list(popgun.DEFAULT_TIMEFRAMES),
    )
    _seed_topic(
        db_url,
        thread_id=12,
        enabled=True,
        symbols=["XRPUSDT"],
        timeframes=list(popgun.DEFAULT_TIMEFRAMES),
    )
    calls: list[tuple[str, str]] = []

    class _FakeClient:
        exchange_id = "bybit"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def supports_symbol(self, symbol: str) -> bool:
            return True

        def fetch_candles(self, *, symbol: str, timeframe: str, limit: int) -> list:
            calls.append((symbol, timeframe))
            if symbol == "BTCUSDT" and timeframe == "15m":
                return [
                    popgun.Candle(timestamp=1, open=10, high=20, low=5, close=12, volume=1),
                    popgun.Candle(timestamp=2, open=12, high=18, low=7, close=13, volume=1),
                    popgun.Candle(timestamp=3, open=13, high=19, low=6, close=14, volume=1),
                ]
            return [
                popgun.Candle(timestamp=1, open=10, high=20, low=5, close=12, volume=1),
                popgun.Candle(timestamp=2, open=12, high=21, low=7, close=13, volume=1),
                popgun.Candle(timestamp=3, open=13, high=22, low=6, close=14, volume=1),
            ]

    async def _fake_sleep(seconds: float) -> None:
        if seconds == 60:
            raise asyncio.CancelledError

    monkeypatch.setattr(popgun, "CcxtCandleClient", _FakeClient)
    monkeypatch.setattr(popgun.asyncio, "sleep", _fake_sleep)
    host = _HostAPI()

    try:
        asyncio.run(popgun.handle_worker(SimpleNamespace(database_url=db_url), host))
    except asyncio.CancelledError:
        pass

    assert calls.count(("BTCUSDT", "15m")) == 1
    assert len(calls) == 3 * len(popgun.DEFAULT_TIMEFRAMES)
    assert [message[2] for message in host.sent] == [10, 11]
    assert all("BTCUSDT 15m" in message[1] for message in host.sent)


def test_popgun_worker_skips_symbols_unsupported_by_bybit_usdt_futures(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_worker_unsupported.sqlite")
    popgun = _load_popgun_module()
    _seed_topic(
        db_url,
        thread_id=10,
        enabled=True,
        symbols=["BTCUSDT", "XAGUSDT"],
        timeframes=list(popgun.DEFAULT_TIMEFRAMES),
    )
    calls: list[tuple[str, str]] = []

    class _FakeClient:
        exchange_id = "bybit"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def supports_symbol(self, symbol: str) -> bool:
            return symbol != "XAGUSDT"

        def fetch_candles(self, *, symbol: str, timeframe: str, limit: int) -> list:
            calls.append((symbol, timeframe))
            return []

    async def _fake_sleep(seconds: float) -> None:
        if seconds == 60:
            raise asyncio.CancelledError

    monkeypatch.setattr(popgun, "CcxtCandleClient", _FakeClient)
    monkeypatch.setattr(popgun.asyncio, "sleep", _fake_sleep)
    caplog.set_level(logging.DEBUG, logger="amo.plugins.popgun")
    host = _HostAPI()

    try:
        asyncio.run(popgun.handle_worker(SimpleNamespace(database_url=db_url), host))
    except asyncio.CancelledError:
        pass

    assert calls == [("BTCUSDT", timeframe) for timeframe in popgun.DEFAULT_TIMEFRAMES]
    unsupported = next(record for record in caplog.records if record.msg == "popgun symbol unsupported on exchange")
    assert unsupported.symbol == "XAGUSDT"
    assert unsupported.exchange_id == "bybit"
    assert unsupported.exchange_name == "Bybit USDT Futures/Perps"
    summary = next(record for record in caplog.records if record.msg == "popgun worker loop summary")
    assert summary.fetch_jobs_count == 2 * len(popgun.DEFAULT_TIMEFRAMES)
    assert summary.scans_attempted == len(popgun.DEFAULT_TIMEFRAMES)
    assert summary.unsupported_symbols_count == 1


def test_popgun_worker_dedupes_alerts_per_topic_independently(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_worker_dedupe.sqlite")
    popgun = _load_popgun_module()
    repo = popgun.PopgunStateRepository(database_url=db_url)
    topic_with_seen_signal = popgun.TopicConfig(
        chat_id=100,
        thread_id=10,
        enabled=True,
        symbols=["BTCUSDT"],
        timeframes=list(popgun.DEFAULT_TIMEFRAMES),
        updated_at="2030-01-01T00:00:00+00:00",
    )
    seen_signal = popgun.PopgunSignal(
        symbol="BTCUSDT",
        timeframe="15m",
        timestamp=3,
        inside_high=18,
        inside_low=7,
        outside_high=19,
        outside_low=6,
    )
    assert repo.is_new_signal(topic=topic_with_seen_signal, signal=seen_signal) is True
    _seed_topic(db_url, thread_id=10, enabled=True, symbols=["BTCUSDT"], timeframes=list(popgun.DEFAULT_TIMEFRAMES))
    _seed_topic(db_url, thread_id=11, enabled=True, symbols=["BTCUSDT"], timeframes=list(popgun.DEFAULT_TIMEFRAMES))

    class _FakeClient:
        exchange_id = "bybit"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def supports_symbol(self, symbol: str) -> bool:
            return True

        def fetch_candles(self, *, symbol: str, timeframe: str, limit: int) -> list:
            if timeframe == "15m":
                return [
                    popgun.Candle(timestamp=1, open=10, high=20, low=5, close=12, volume=1),
                    popgun.Candle(timestamp=2, open=12, high=18, low=7, close=13, volume=1),
                    popgun.Candle(timestamp=3, open=13, high=19, low=6, close=14, volume=1),
                ]
            return []

    async def _fake_sleep(seconds: float) -> None:
        if seconds == 60:
            raise asyncio.CancelledError

    monkeypatch.setattr(popgun, "CcxtCandleClient", _FakeClient)
    monkeypatch.setattr(popgun.asyncio, "sleep", _fake_sleep)
    host = _HostAPI()

    try:
        asyncio.run(popgun.handle_worker(SimpleNamespace(database_url=db_url), host))
    except asyncio.CancelledError:
        pass

    assert [message[2] for message in host.sent] == [11]


def test_popgun_worker_logs_signal_alert_failure_and_summary(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.chdir(tmp_path)
    db_url = _db_url(tmp_path, "popgun_worker_logs.sqlite")
    popgun = _load_popgun_module()
    _seed_topic(
        db_url,
        thread_id=10,
        enabled=True,
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframes=list(popgun.DEFAULT_TIMEFRAMES),
    )

    class _FakeClient:
        exchange_id = "bybit"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def supports_symbol(self, symbol: str) -> bool:
            return True

        def fetch_candles(self, *, symbol: str, timeframe: str, limit: int) -> list:
            if symbol == "ETHUSDT" and timeframe == "15m":
                raise RuntimeError("exchange unavailable")
            if timeframe != "15m":
                return []
            return [
                popgun.Candle(timestamp=1, open=10, high=20, low=5, close=12, volume=1),
                popgun.Candle(timestamp=2, open=12, high=18, low=7, close=13, volume=1),
                popgun.Candle(timestamp=3, open=13, high=19, low=6, close=14, volume=1),
            ]

    async def _fake_sleep(seconds: float) -> None:
        if seconds == 60:
            raise asyncio.CancelledError

    monkeypatch.setattr(popgun, "CcxtCandleClient", _FakeClient)
    monkeypatch.setattr(popgun.asyncio, "sleep", _fake_sleep)
    caplog.set_level(logging.DEBUG, logger="amo.plugins.popgun")
    host = _HostAPI()

    try:
        asyncio.run(popgun.handle_worker(SimpleNamespace(database_url=db_url), host))
    except asyncio.CancelledError:
        pass

    assert len(host.sent) == 1
    initialized = next(record for record in caplog.records if record.msg == "popgun worker initialized")
    assert initialized.poll_interval_seconds == 60
    assert initialized.candle_limit == 5
    assert initialized.exchange_id == "bybit"
    assert initialized.exchange_name == "Bybit USDT Futures/Perps"
    detected = next(record for record in caplog.records if record.msg == "popgun signal detected")
    assert detected.symbol == "BTCUSDT"
    assert detected.timeframe == "15m"
    alert = next(record for record in caplog.records if record.msg == "popgun alert sent")
    assert alert.chat_id == 100
    assert alert.thread_id == 10
    failure = next(record for record in caplog.records if record.msg == "popgun scan failed")
    assert failure.symbol == "ETHUSDT"
    assert failure.timeframe == "15m"
    assert failure.error_class == "RuntimeError"
    summary = next(record for record in caplog.records if record.msg == "popgun worker loop summary")
    assert summary.enabled_topics_count == 1
    assert summary.fetch_jobs_count == 2 * len(popgun.DEFAULT_TIMEFRAMES)
    assert summary.scans_attempted == 2 * len(popgun.DEFAULT_TIMEFRAMES)
    assert summary.signals_found == 1
    assert summary.fanout_topics_count == 1
    assert summary.alerts_sent == 1
    assert summary.errors_count == 1
    assert summary.duration_ms >= 0


def test_popgun_manifest_and_ccxt_dependency_are_declared() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = (root / "plugins" / "popgun" / "plugin.yaml").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert "name: popgun" in manifest
    assert "  - popgun\n" in manifest
    assert "  - popgunadd\n" in manifest
    assert "worker:" in manifest
    assert "timeout_ms: 360000" in manifest
    assert "  - admin\n" in manifest
    assert "  - owner\n" in manifest
    assert "  - send_message\n" in manifest
    assert "ccxt>=4.5,<5" in requirements
    assert '"ccxt>=4.5,<5"' in pyproject
