from __future__ import annotations

import importlib.util
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


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


def _context(*, command: str, argument: str, chat_id: int = 100, thread_id: int | None = 5, role: str = "admin"):
    return SimpleNamespace(
        command_name=command,
        argument=argument,
        chat_id=chat_id,
        message_id=99,
        message_thread_id=thread_id,
        user_id=123,
        role=role,
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


def test_popgun_on_off_is_topic_scoped(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    popgun = _load_popgun_module()
    host = _HostAPI()

    asyncio.run(popgun.handle_command(_context(command="popgun", argument="on", thread_id=10), host))
    asyncio.run(popgun.handle_command(_context(command="popgun", argument="off", thread_id=11), host))

    repo = popgun.PopgunStateRepository()
    topic_a = repo.get_topic(chat_id=100, thread_id=10)
    topic_b = repo.get_topic(chat_id=100, thread_id=11)

    assert topic_a is not None and topic_a.enabled is True
    assert topic_b is not None and topic_b.enabled is False
    assert "aktiviert" in host.replies[0][2]
    assert "deaktiviert" in host.replies[1][2]


def test_popgunadd_adds_one_symbol_for_current_topic_and_dedupes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    popgun = _load_popgun_module()

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve_symbol(self, symbol: str) -> str:
            return f"{symbol[:-4]}/USDT:USDT"

    monkeypatch.setattr(popgun, "CcxtCandleClient", _FakeClient)
    host = _HostAPI()

    asyncio.run(popgun.handle_command(_context(command="popgunadd", argument="adausdt", thread_id=10), host))
    asyncio.run(popgun.handle_command(_context(command="popgunadd", argument="ADAUSDT", thread_id=10), host))

    repo = popgun.PopgunStateRepository()
    topic = repo.get_topic(chat_id=100, thread_id=10)
    sibling = repo.get_topic(chat_id=100, thread_id=11)

    assert topic is not None
    assert "ADAUSDT" in topic.symbols
    assert topic.symbols.count("ADAUSDT") == 1
    assert sibling is None
    assert "hinzugefügt" in host.replies[0][2]
    assert "bereits vorhanden" in host.replies[1][2]


def test_popgun_rejects_non_manager(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    popgun = _load_popgun_module()
    host = _HostAPI()

    asyncio.run(popgun.handle_command(_context(command="popgun", argument="on", role="normal"), host))

    assert host.replies == [(100, 99, "Nur Admins/Owner dürfen Popgun verwalten.")]


def test_popgun_signal_dedupe_is_topic_symbol_timeframe_scoped(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    popgun = _load_popgun_module()
    repo = popgun.PopgunStateRepository()
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
