from __future__ import annotations

import re
from dataclasses import dataclass

from amo_bot.ai.webtool_dispatcher import WebtoolCapabilityRequest
from amo_bot.auth.roles import Role


@dataclass(frozen=True, slots=True)
class WebtoolChatTrigger:
    capability: str
    query: str
    url: str


_WEBSEARCH_PREFIX_RE = re.compile(r"^\s*websearch\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)
_WEBSCRAPE_PREFIX_RE = re.compile(r"^\s*webscrape\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)


def parse_webtool_chat_trigger(text: str) -> WebtoolChatTrigger | None:
    raw = (text or "").strip()
    if not raw:
        return None

    m_search = _WEBSEARCH_PREFIX_RE.match(raw)
    if m_search:
        query = m_search.group(1).strip()
        if query:
            return WebtoolChatTrigger(capability="websearch", query=query, url="")
        return None

    m_scrape = _WEBSCRAPE_PREFIX_RE.match(raw)
    if m_scrape:
        payload = m_scrape.group(1).strip()
        if payload and (payload.startswith("http://") or payload.startswith("https://")):
            return WebtoolChatTrigger(capability="webscraping", query="", url=payload)
        return None

    return None


def build_webtool_request(
    *,
    trigger: WebtoolChatTrigger,
    user_id: int,
    role: Role,
    chat_id: int,
    topic_id: int | None,
    locale: str,
) -> WebtoolCapabilityRequest:
    return WebtoolCapabilityRequest(
        capability=trigger.capability,
        user_id=user_id,
        role=role,
        chat_id=chat_id,
        topic_id=topic_id,
        query=trigger.query,
        url=trigger.url,
        locale=locale,
        max_results=3,
    )


def format_webtool_fail_text(locale: str) -> str:
    if locale == "en":
        return "I can’t run this webtool request right now."
    return "Ich kann diese Webtool-Anfrage gerade nicht ausführen."


def format_webtool_quota_text(locale: str, role: Role) -> str:
    if locale == "en":
        return f"Webtool rate limit reached for role {role.value}."
    return f"Webtool-Limit für Rolle {role.value} erreicht."


def format_webtool_success_text(*, locale: str, capability: str, text: str) -> str:
    label = "Websearch" if capability == "websearch" else "Webscrape"
    prefix = f"{label}: " if locale == "en" else f"{label}: "
    compact = " ".join((text or "").split())
    if len(compact) > 700:
        compact = compact[:700].rstrip() + " …"
    return prefix + compact
