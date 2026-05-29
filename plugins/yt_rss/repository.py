from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


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
    @staticmethod
    def topic_key_for(chat_id: int, thread_id: int | None, channel_key: str) -> str:
        return f"{chat_id}:{thread_id if thread_id is not None else 'root'}:{channel_key}"

    @staticmethod
    def _derive_channel_key(source: dict[str, Any], topic_key: str) -> str | None:
        channel_key = source.get("channel_key")
        if isinstance(channel_key, str) and channel_key.strip():
            return channel_key.strip()

        source_url = source.get("source_url")
        if isinstance(source_url, str) and source_url.strip():
            parsed = urlparse(source_url.strip())
            path_parts = [part for part in (parsed.path or "").split("/") if part]
            if len(path_parts) >= 2 and path_parts[0] == "channel":
                candidate = path_parts[1].strip()
                if candidate:
                    return candidate
            if source_url.strip().upper().startswith("UC"):
                return source_url.strip()

        key_parts = topic_key.split(":", 2)
        if len(key_parts) == 3 and key_parts[2].strip():
            return key_parts[2].strip()
        return None

    @staticmethod
    def _derive_canonical_channel_url(channel_key: str, source_url: str) -> str:
        canonical = f"https://www.youtube.com/channel/{channel_key}"
        if source_url:
            parsed = urlparse(source_url)
            path_parts = [part for part in (parsed.path or "").split("/") if part]
            if len(path_parts) >= 2 and path_parts[0] == "channel":
                return source_url.strip()
        return canonical

    @staticmethod
    def _derive_rss_url(channel_key: str, source_url: str) -> str:
        parsed = urlparse(source_url)
        query = parse_qs(parsed.query or "")
        q_channel = query.get("channel_id", [])
        if q_channel:
            candidate = q_channel[0].strip()
            if candidate:
                return f"https://www.youtube.com/feeds/videos.xml?channel_id={candidate}"
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_key}"

    @staticmethod
    def _coerce_subscription(topic_key: str, value: Any) -> tuple[SubscriptionRecord | None, dict[str, str] | None]:
        if not isinstance(value, dict):
            return None, {"error": "invalid_subscription_record:not_dict"}

        try:
            chat_id = int(value.get("chat_id"))
        except (TypeError, ValueError):
            return None, {"error": "invalid_subscription_record:chat_id"}

        thread_raw = value.get("thread_id")
        if thread_raw is None:
            thread_id = None
        else:
            try:
                thread_id = int(thread_raw)
            except (TypeError, ValueError):
                return None, {"error": "invalid_subscription_record:thread_id"}

        source_url = value.get("source_url")
        source_url = source_url.strip() if isinstance(source_url, str) else ""

        channel_key = YtRssStateRepository._derive_channel_key(value, topic_key)
        if not channel_key:
            return None, {"error": "invalid_subscription_record:missing_channel_key"}

        canonical_channel_url = value.get("canonical_channel_url")
        if not isinstance(canonical_channel_url, str) or not canonical_channel_url.strip():
            canonical_channel_url = YtRssStateRepository._derive_canonical_channel_url(channel_key, source_url)
        else:
            canonical_channel_url = canonical_channel_url.strip()

        rss_url = value.get("rss_url")
        if not isinstance(rss_url, str) or not rss_url.strip():
            rss_url = YtRssStateRepository._derive_rss_url(channel_key, source_url)
        else:
            rss_url = rss_url.strip()

        added_by = value.get("added_by_user_id")
        if added_by is None:
            added_by_user_id = None
        else:
            try:
                added_by_user_id = int(added_by)
            except (TypeError, ValueError):
                added_by_user_id = None

        added_at = value.get("added_at")
        if not isinstance(added_at, str) or not added_at.strip():
            added_at = datetime.now(timezone.utc).isoformat()
        else:
            added_at = added_at.strip()

        return (
            SubscriptionRecord(
                chat_id=chat_id,
                thread_id=thread_id,
                channel_key=channel_key,
                source_url=source_url or canonical_channel_url,
                canonical_channel_url=canonical_channel_url,
                rss_url=rss_url,
                added_by_user_id=added_by_user_id,
                added_at=added_at,
            ),
            None,
        )
    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._base_dir / "state.json"

    def _load(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"subscriptions": {}, "cursors": {}, "errors": {}}
        raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"subscriptions": {}, "cursors": {}}
        raw.setdefault("subscriptions", {})
        raw.setdefault("cursors", {})
        raw.setdefault("errors", {})
        raw.setdefault("config", {"poll_interval_seconds": 300})
        if not isinstance(raw.get("config"), dict):
            raw["config"] = {"poll_interval_seconds": 300}
        raw["config"].setdefault("poll_interval_seconds", 300)
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        self._state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _topic_key(chat_id: int, thread_id: int | None, channel_key: str) -> str:
        return YtRssStateRepository.topic_key_for(chat_id, thread_id, channel_key)

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
        changed = False
        for key, value in list(state["subscriptions"].items()):
            record, error = self._coerce_subscription(key, value)
            if record is None:
                state["subscriptions"].pop(key, None)
                if error:
                    state.setdefault("errors", {})[key] = error
                changed = True
                continue
            if record.chat_id == chat_id and record.thread_id == thread_id:
                out.append(record)
            normalized = asdict(record)
            if value != normalized:
                state["subscriptions"][key] = normalized
                changed = True
        if changed:
            self._save(state)
        return sorted(out, key=lambda item: item.channel_key)

    def list_all_subscriptions(self) -> list[SubscriptionRecord]:
        state = self._load()
        out: list[SubscriptionRecord] = []
        changed = False
        for key, value in list(state["subscriptions"].items()):
            record, error = self._coerce_subscription(key, value)
            if record is None:
                state["subscriptions"].pop(key, None)
                if error:
                    state.setdefault("errors", {})[key] = error
                changed = True
                continue
            out.append(record)
            normalized = asdict(record)
            if value != normalized:
                state["subscriptions"][key] = normalized
                changed = True
        if changed:
            self._save(state)
        return sorted(out, key=lambda item: (item.chat_id, item.thread_id or -1, item.channel_key))

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
        state["errors"].pop(key, None)
        self._save(state)

    def set_last_error(self, *, chat_id: int, thread_id: int | None, channel_key: str, error: str) -> None:
        state = self._load()
        key = self._topic_key(chat_id, thread_id, channel_key)
        state["errors"][key] = {"error": error}
        self._save(state)

    def get_poll_interval_seconds(self) -> int:
        state = self._load()
        raw = state.get("config", {}).get("poll_interval_seconds", 300)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 300
        return value if value > 0 else 300

    def set_poll_interval_seconds(self, value: int) -> int:
        if not isinstance(value, int):
            raise ValueError("invalid_interval")
        if value < 30 or value > 86400:
            raise ValueError("invalid_interval")
        state = self._load()
        state.setdefault("config", {})["poll_interval_seconds"] = value
        self._save(state)
        return value
