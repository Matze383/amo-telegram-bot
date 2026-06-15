from __future__ import annotations

import re
from dataclasses import dataclass

from amo_bot.ai.webtool_dispatcher import WebtoolCapabilityRequest
from amo_bot.auth.roles import Role
from amo_bot.telegram import sports_query


@dataclass(frozen=True, slots=True)
class WebtoolChatTrigger:
    capability: str
    query: str
    url: str


_FOLLOWUP_QUERY_MAX_CHARS = 220
_EMPTY_RESULT_RETRY_QUERY_MAX_CHARS = 150
_EMPTY_RESULT_RETRY_MAX_QUERIES = 3
_WEBTOOL_CHAT_RESULT_MAX_CHARS = 700
_WEBTOOL_CONTEXT_RESULT_MAX_CHARS = 900

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
_BOT_MENTION_RE = re.compile(r"@\w{3,32}\b")
_MARKDOWN_PUNCT_RE = re.compile(r"[`*_~>#\[\](){}]")
_FOLLOWUP_FILLER_RE = re.compile(
    r"\b(?:"
    r"such\s+(?:bitte\s+)?weiter|weiter\s+suchen|noch(?:mal|mals?)\s+suchen|mehr\s+suchen|"
    r"andere\s+quellen|das\s+reicht\s+nicht|zu\s+wenig|nicht\s+gefunden|keine\s+aktuellen\s+daten|"
    r"try\s+again|search\s+more|more\s+sources|not\s+enough|couldn['’]?t\s+find|could\s+not\s+find"
    r")\b",
    re.IGNORECASE,
)
_OLD_ANSWER_MARKER_RE = re.compile(
    r"\b(?:bot\s+answer|assistant\s+answer|previous\s+answer|prior\s+answer|alte\s+antwort|vorherige\s+antwort|antwort)\s*:",
    re.IGNORECASE,
)
_STALE_VALUE_RE = re.compile(
    r"(?:[$€£]\s*)?\b\d+(?:[.,]\d+)?\s*(?:usd|eur|gbp|chf|jpy|cad|aud|dollar|euro|€|\$|%)?\b",
    re.IGNORECASE,
)
_CONTEXT_TOPIC_RE = re.compile(
    r"\b(?:zum\s+thema|thema|about|zu|for)\s+(.+?)(?:\s+(?:finden|gefunden|confirmed|bestätigt|bestaetigt)\b|[.!?]|$)",
    re.IGNORECASE,
)
_BTC_RE = re.compile(r"\b(?:btc|bitcoin)\b", re.IGNORECASE)
_PRICE_INTENT_RE = re.compile(
    r"\b(?:price|preis|kurs|rate|current|aktuell(?:e[nrms]?)?|jetzt|heute|live|usd|dollar)\b",
    re.IGNORECASE,
)
_SPORTS_DATE_FRAGMENT_RE = re.compile(
    r"\b\d{1,2}\s*(?:jan(?:uar)?|feb(?:ruar)?|märz|maerz|mar(?:ch)?|apr(?:il)?|"
    r"mai|may|jun(?:i|e)?|jul(?:i|y)?|aug(?:ust)?|sep(?:tember)?|okt(?:ober)?|"
    r"oct(?:ober)?|nov(?:ember)?|dez(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
_TOOL_TRACE_TAG_NAMES = (
    "tool",
    "query",
    "args",
    "arguments",
    "input",
    "output",
    "function",
    "tool_call",
    "tool_calls",
    "search_query",
)
_TOOL_TRACE_BLOCK_RE = re.compile(
    r"<(?P<tag>" + "|".join(_TOOL_TRACE_TAG_NAMES) + r")\b[^>]*>.*?</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_TOOL_TRACE_LINE_RE = re.compile(
    r"^\s*</?(?:" + "|".join(_TOOL_TRACE_TAG_NAMES) + r")\b[^>]*>\s*$",
    re.IGNORECASE,
)
_MARKDOWN_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_RETRY_KEEP_WORD_RE = re.compile(r"[0-9A-Za-zÄÖÜäöüß][0-9A-Za-zÄÖÜäöüß.+#-]{1,}")
_RETRY_STOPWORDS = {
    "amo", "bot", "bitte", "such", "suche", "weiter", "reicht", "nicht", "wenig",
    "aktuell", "aktuelle", "aktueller", "aktuellen", "heute", "jetzt", "live", "current",
    "right", "now", "please", "search", "more", "again", "sources", "quellen", "answer",
    "antwort", "vorherige", "alte", "was", "ist", "sind", "der", "die", "das", "den",
    "dem", "ein", "eine", "einer", "einen", "und", "oder", "for", "the", "what", "with",
    "stand", "war", "gibt", "zum", "es",
}


def _compact_followup_query(value: str, *, max_len: int = _FOLLOWUP_QUERY_MAX_CHARS) -> str:
    compact = " ".join((value or "").split())
    if len(compact) > max_len:
        compact = compact[:max_len].rstrip() + " …"
    return compact


def _clean_empty_result_retry_text(text: str) -> str:
    cleaned = _BOT_MENTION_RE.sub(" ", text or "")
    cleaned = _OLD_ANSWER_MARKER_RE.sub(" ", cleaned)
    cleaned = _MARKDOWN_PUNCT_RE.sub(" ", cleaned)
    cleaned = _FOLLOWUP_FILLER_RE.sub(" ", cleaned)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"\bwebsearch\s*:\s*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:;,.!?\t\n\r")
    return cleaned


def _clean_followup_query_part(text: str, *, remove_stale_values: bool) -> str:
    cleaned = _BOT_MENTION_RE.sub(" ", text or "")
    old_answer_match = _OLD_ANSWER_MARKER_RE.search(cleaned)
    if old_answer_match and not remove_stale_values and old_answer_match.start() > 0:
        cleaned = cleaned[:old_answer_match.start()]
    else:
        cleaned = _OLD_ANSWER_MARKER_RE.sub(" ", cleaned)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = _MARKDOWN_PUNCT_RE.sub(" ", cleaned)
    if remove_stale_values:
        cleaned = _STALE_VALUE_RE.sub(" ", cleaned)
    topic_match = _CONTEXT_TOPIC_RE.search(cleaned)
    if topic_match:
        cleaned = topic_match.group(1)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:;,.!?\t\n\r")
    return cleaned


def _simplify_empty_result_retry_query(text: str, *, max_len: int) -> str:
    """Return a shorter keyword query for empty-result retries.

    Metadata-only logging rules mean the caller may log only lengths/classes, not
    this returned value. Keep high-signal tokens, drop request/follow-up filler,
    and preserve order without inventing context.
    """
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _RETRY_KEEP_WORD_RE.finditer(text or ""):
        token = match.group(0).strip("-_.")
        key = token.casefold()
        if not token or key in _RETRY_STOPWORDS or key in seen:
            continue
        # Drop plain old-answer numerals such as stale prices; keep year-like or
        # version-like tokens only when they are attached to letters/dots.
        if token.isdigit() and len(token) < 4:
            continue
        tokens.append(token)
        seen.add(key)
        if len(tokens) >= 8:
            break
    simplified = " ".join(tokens).strip()
    if len(simplified) > max_len:
        simplified = simplified[:max_len].rstrip(" -–—:;,.!")
    return simplified


def build_empty_result_retry_query(text: str, *, max_len: int = _EMPTY_RESULT_RETRY_QUERY_MAX_CHARS) -> str:
    """Build one safer, simplified retry query from the current user message.

    This intentionally avoids prior bot-answer/reply context so an empty first
    auto-search cannot be retried with stale, over-specific context. Callers must
    not log the returned raw query.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    if _BTC_RE.search(raw) and _PRICE_INTENT_RE.search(raw):
        if re.search(r"\b(?:kurs|preis|aktuell(?:e[nrms]?)?|heute|jetzt)\b", raw, re.IGNORECASE):
            return "bitcoin kurs USD BTC"
        return "bitcoin price USD BTC"

    cleaned = _clean_empty_result_retry_text(raw)
    if not cleaned:
        return ""
    simplified = _simplify_empty_result_retry_query(cleaned, max_len=max_len)
    if simplified:
        return simplified
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" -–—:;,.!")
    return cleaned


def build_empty_result_retry_queries(
    text: str,
    *,
    max_len: int = _EMPTY_RESULT_RETRY_QUERY_MAX_CHARS,
    max_queries: int = _EMPTY_RESULT_RETRY_MAX_QUERIES,
) -> tuple[str, ...]:
    """Build bounded empty-result retry queries, with richer generic sports variants."""
    raw = (text or "").strip()
    if not raw or max_queries < 1:
        return ()
    primary = build_empty_result_retry_query(raw, max_len=max_len)
    if not sports_query.has_sports_signal(raw):
        return (primary,) if primary else ()

    candidates = _build_sports_empty_result_retry_queries(raw, primary=primary, max_len=max_len)
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        compact = _compact_followup_query(candidate, max_len=max_len).strip()
        key = compact.casefold()
        if not compact or key in seen:
            continue
        deduped.append(compact)
        seen.add(key)
        if len(deduped) >= max_queries:
            break
    return tuple(deduped)


def _build_sports_empty_result_retry_queries(raw: str, *, primary: str, max_len: int) -> tuple[str, ...]:
    cleaned = _clean_empty_result_retry_text(raw)
    compact_cleaned = _compact_followup_query(cleaned, max_len=max_len)
    english = _sports_query_to_english(compact_cleaned or primary)
    date_fragment = _extract_sports_date_fragment(raw)
    sports_terms = "result fixture match schedule"

    candidates = [
        primary,
        f"{english} {sports_terms}",
    ]
    if date_fragment and date_fragment.casefold() not in english.casefold():
        candidates.append(f"{english} {date_fragment} {sports_terms}")
    candidates.append(f"{english} standings group result fixture")
    return tuple(candidates)


def _sports_query_to_english(text: str) -> str:
    return sports_query.normalize_search_terms(text)


def _extract_sports_date_fragment(text: str) -> str:
    match = _SPORTS_DATE_FRAGMENT_RE.search(text or "")
    return match.group(0).strip() if match else ""


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
    feedback = _compact_followup_query(
        _clean_followup_query_part(feedback_text, remove_stale_values=False),
        max_len=120,
    )
    context = _compact_followup_query(
        _clean_followup_query_part(context_text, remove_stale_values=True),
        max_len=160,
    )
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
    evidence_domain: str = "",
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
        max_results=5,
        evidence_domain=evidence_domain,
    )


def format_webtool_fail_text(locale: str) -> str:
    if locale == "en":
        return "I can’t run this webtool request right now."
    return "Ich kann diese Webtool-Anfrage gerade nicht ausführen."


def format_webtool_quota_text(locale: str, role: Role) -> str:
    if locale == "en":
        return f"Webtool rate limit reached for role {role.value}."
    return f"Webtool-Limit für Rolle {role.value} erreicht."


def sanitize_webtool_user_facing_text(text: str) -> str:
    """Remove internal tool traces and Telegram-hostile tables from LLM/webtool text."""
    without_trace_blocks = _TOOL_TRACE_BLOCK_RE.sub("", text or "")
    lines = [
        line
        for line in without_trace_blocks.splitlines()
        if not _TOOL_TRACE_LINE_RE.match(line)
    ]
    return _markdown_tables_to_bullets(lines).strip()


def _markdown_tables_to_bullets(lines: list[str]) -> str:
    output: list[str] = []
    index = 0
    while index < len(lines):
        if _line_starts_markdown_table(lines, index):
            table_lines: list[str] = []
            while index < len(lines) and _is_markdown_table_line(lines[index]):
                table_lines.append(lines[index])
                index += 1
            output.extend(_format_markdown_table_as_bullets(table_lines))
            continue
        output.append(lines[index])
        index += 1
    return "\n".join(output)


def _line_starts_markdown_table(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and _is_markdown_table_line(lines[index])
        and _MARKDOWN_TABLE_SEPARATOR_RE.match(lines[index + 1]) is not None
    )


def _is_markdown_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.count("|") >= 2


def _split_markdown_table_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _format_markdown_table_as_bullets(table_lines: list[str]) -> list[str]:
    if len(table_lines) < 2:
        return table_lines
    headers = _split_markdown_table_row(table_lines[0])
    rows = [
        _split_markdown_table_row(line)
        for line in table_lines[2:]
        if not _MARKDOWN_TABLE_SEPARATOR_RE.match(line)
    ]
    bullets: list[str] = []
    for row in rows:
        values = []
        for index, cell in enumerate(row):
            if not cell:
                continue
            header = headers[index] if index < len(headers) and headers[index] else ""
            values.append(f"{header}: {cell}" if header else cell)
        if values:
            bullets.append("- " + "; ".join(values))
    return bullets


def compact_webtool_result_text(
    text: str,
    *,
    max_chars: int = _WEBTOOL_CONTEXT_RESULT_MAX_CHARS,
) -> str:
    """Return a bounded one-line webtool result for prompts or chat output."""
    compact = " ".join(sanitize_webtool_user_facing_text(text).split())
    if max_chars < 1:
        return ""
    if len(compact) <= max_chars:
        return compact
    suffix_template = " ... [truncated; {omitted} chars omitted from active context]"
    suffix = suffix_template.format(omitted=max(0, len(compact) - max_chars))
    keep = max(0, max_chars - len(suffix))
    suffix = suffix_template.format(omitted=len(compact) - keep)
    return compact[:keep].rstrip() + suffix


def format_webtool_success_text(*, locale: str, capability: str, text: str) -> str:
    if capability == "websearch":
        label = "Websearch"
    elif capability == "webscraping":
        label = "Webscrape"
    else:
        label = "Browser"
    prefix = f"{label}: " if locale == "en" else f"{label}: "
    compact = compact_webtool_result_text(text, max_chars=_WEBTOOL_CHAT_RESULT_MAX_CHARS)
    return prefix + compact
