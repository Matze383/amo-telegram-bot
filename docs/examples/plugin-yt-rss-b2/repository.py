from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SubscriptionRecord:
    chat_id: int
    thread_id: int | None
    channel_key: str
    source_url: str
    canonical_channel_url: str
    rss_url: str
    added_by_user_id: int | None
    added_at: str


@dataclass(slots=True)
class CursorRecord:
    cursor: str | None = None
    dedupe: list[str] | None = None


class YtRssStateRepository:
    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._base_dir / "state.json"

    def _load(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"subscriptions": {}, "cursors": {}}
        raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"subscriptions": {}, "cursors": {}}
        raw.setdefault("subscriptions", {})
        raw.setdefault("cursors", {})
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        self._state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _topic_key(chat_id: int, thread_id: int | None, channel_key: str) -> str:
        return f"{chat_id}:{thread_id if thread_id is not None else 'root'}:{channel_key}"

    def add_subscription(
        self,
        *,
        chat_id: int,
        thread_id: int | None,
        channel_key: str,
        source_url: str,
        canonical_channel_url: str,
        rss_url: str,
        added_by_user_id: int | None,
    ) -> bool:
        state = self._load()
        key = self._topic_key(chat_id, thread_id, channel_key)
        if key in state["subscriptions"]:
            return False
        state["subscriptions"][key] = asdict(
            SubscriptionRecord(
                chat_id=chat_id,
                thread_id=thread_id,
                channel_key=channel_key,
                source_url=source_url,
                canonical_channel_url=canonical_channel_url,
                rss_url=rss_url,
                added_by_user_id=added_by_user_id,
                added_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        self._save(state)
        return True

    def delete_subscription(self, *, chat_id: int, thread_id: int | None, channel_key: str) -> bool:
        state = self._load()
        key = self._topic_key(chat_id, thread_id, channel_key)
        existed = state["subscriptions"].pop(key, None) is not None
        state["cursors"].pop(key, None)
        if existed:
            self._save(state)
        return existed

    def list_subscriptions(self, *, chat_id: int, thread_id: int | None) -> list[SubscriptionRecord]:
        state = self._load()
        out: list[SubscriptionRecord] = []
        for value in state["subscriptions"].values():
            if value.get("chat_id") == chat_id and value.get("thread_id") == thread_id:
                out.append(SubscriptionRecord(**value))
        return sorted(out, key=lambda item: item.channel_key)

    def get_cursor(self, *, chat_id: int, thread_id: int | None, channel_key: str) -> CursorRecord:
        state = self._load()
        key = self._topic_key(chat_id, thread_id, channel_key)
        value = state["cursors"].get(key)
        if not isinstance(value, dict):
            return CursorRecord(cursor=None, dedupe=[])
        dedupe = value.get("dedupe")
        return CursorRecord(cursor=value.get("cursor"), dedupe=dedupe if isinstance(dedupe, list) else [])

    def set_cursor(self, *, chat_id: int, thread_id: int | None, channel_key: str, cursor: str | None, dedupe: list[str]) -> None:
        state = self._load()
        key = self._topic_key(chat_id, thread_id, channel_key)
        state["cursors"][key] = asdict(CursorRecord(cursor=cursor, dedupe=dedupe))
        self._save(state)
