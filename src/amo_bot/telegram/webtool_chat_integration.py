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


_FOLLOWUP_QUERY_MAX_CHARS = 220

_WEBSEARCH_PREFIX_RE = re.compile(r"^\s*websearch\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)
_WEBSCRAPE_PREFIX_RE = re.compile(r"^\s*webscrape\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)
_WEBBROWSER_PREFIX_RE = re.compile(r"^\s*(?:webbrowser|browser)\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL)
_WEB_RESEARCH_FOLLOWUP_RE = re.compile(
    r"(?:"
    r"such\s+(?:bitte\s+)?weiter|weiter\s+suchen|noch(?:mal|mals?)\s+suchen|mehr\s+suchen|"
    r"andere\s+quellen|öffne\s+(?:die\s+)?quellen|oeffne\s+(?:die\s+)?quellen|"
    r"(?:prüf|prüfe|pruef|pruefe)\s+(?:die\s+)?quellen|"
    r"(?:prüf|prüfe|pruef|pruefe)\s+andere\s+quellen|"
    r"das\s+reicht\s+nicht|zu\s+wenig|nicht\s+gefunden|keine\s+aktuellen\s+daten|"
    r"try\s+again|search\s+more|more\s+sources|open\s+sources|check\s+sources|"
    r"not\s+enough|couldn['’]?t\s+find|could\s+not\s+find"
    r")",
    re.IGNORECASE,
)


def _compact_followup_query(value: str, *, max_len: int = _FOLLOWUP_QUERY_MAX_CHARS) -> str:
    compact = " ".join((value or "").split())
    if len(compact) > max_len:
        compact = compact[:max_len].rstrip() + " …"
    return compact


def is_web_research_followup_feedback(text: str) -> bool:
    """Detect bounded user feedback asking the bot to continue web research.

    This is intentionally only a phrase detector. Callers must still require an
    already-selected bot interaction path (for example reply-to-bot or mention)
    before it can trigger tools.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_WEB_RESEARCH_FOLLOWUP_RE.search(raw))


def build_web_research_followup_query(*, feedback_text: str, context_text: str = "") -> str:
    """Build a conservative bounded search query for web-research follow-up.

    Prefer available reply/original context, but keep user feedback in the query
    so the provider sees that the request is for more/different sources. No
    logging layer should record this raw value.
    """
    feedback = _compact_followup_query(feedback_text, max_len=120)
    context = _compact_followup_query(context_text, max_len=160)
    if context:
        return _compact_followup_query(f"{context} {feedback}")
    return _compact_followup_query(feedback)


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

    m_browser = _WEBBROWSER_PREFIX_RE.match(raw)
    if m_browser:
        payload = m_browser.group(1).strip()
        if payload and payload.startswith("https://"):
            return WebtoolChatTrigger(capability="browser", query="", url=payload)
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
    if capability == "websearch":
        label = "Websearch"
    elif capability == "webscraping":
        label = "Webscrape"
    else:
        label = "Browser"
    prefix = f"{label}: " if locale == "en" else f"{label}: "
    compact = " ".join((text or "").split())
    if len(compact) > 700:
        compact = compact[:700].rstrip() + " …"
    return prefix + compact
