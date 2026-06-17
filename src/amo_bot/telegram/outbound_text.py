from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import NamedTuple

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_SAFE_MESSAGE_LIMIT = 4000

_HTML_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>")
_HTML_ENTITY_RE = re.compile(r"&(?:#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);")
_HTML_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_MARKDOWN_FORMAT_MARKERS = ("```", "`", "**", "__", "*", "_", "~~", "~")


class _OpenHtmlTag(NamedTuple):
    name: str
    opener: str


def split_telegram_message_text(
    text: str,
    *,
    limit: int = TELEGRAM_SAFE_MESSAGE_LIMIT,
    parse_mode: str | None = None,
) -> list[str]:
    """Split outbound Telegram text without silently truncating it.

    Plain text chunks rejoin to the original text byte-for-byte. HTML chunks are
    individually valid enough for Telegram parse mode: tags and entities are not
    split, and open tags are closed/reopened across chunk boundaries.
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    effective_limit = min(limit, TELEGRAM_MESSAGE_LIMIT)
    if len(text) <= effective_limit:
        return [text]

    normalized_parse_mode = (parse_mode or "").strip().casefold()
    if "html" in normalized_parse_mode:
        return _split_telegram_html_text(text, limit=effective_limit)
    return _split_plain_or_markdown_text(text, limit=effective_limit, parse_mode=normalized_parse_mode)


def _split_plain_or_markdown_text(text: str, *, limit: int, parse_mode: str) -> list[str]:
    if "markdown" in parse_mode and "```" in text:
        return _split_markdown_fenced_text(text, limit=limit, parse_mode=parse_mode)

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = _find_telegram_split_index(
            remaining,
            limit=limit,
            parse_mode=parse_mode,
        )
        chunk = remaining[:split_at]
        if not chunk:
            chunk = remaining[:limit]
            split_at = limit
        chunks.append(chunk)
        remaining = remaining[split_at:]

    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


def _find_telegram_split_index(text: str, *, limit: int, parse_mode: str) -> int:
    preferred_boundaries = ("\n\n", "\n", " ")
    for boundary in preferred_boundaries:
        start = min(limit, len(text))
        idx = text.rfind(boundary, 0, start + 1)
        while idx > 0:
            candidate = idx + len(boundary)
            if _parse_mode_boundary_is_safe(text[:candidate], parse_mode=parse_mode):
                return candidate
            idx = text.rfind(boundary, 0, idx)

    split_at = min(limit, len(text))
    while split_at > 0 and not _parse_mode_boundary_is_safe(text[:split_at], parse_mode=parse_mode):
        split_at -= 1
    return split_at or min(limit, len(text))


def _parse_mode_boundary_is_safe(prefix: str, *, parse_mode: str) -> bool:
    if not parse_mode:
        return True
    if "markdown" in parse_mode:
        return _markdown_prefix_boundary_is_safe(prefix)
    return True


def _markdown_prefix_boundary_is_safe(prefix: str) -> bool:
    stripped = prefix.rstrip()
    if _ends_with_partial_markdown_fence(stripped):
        return False
    if stripped.count("```") % 2:
        return False
    outside_fences = _markdown_text_outside_fenced_code(stripped)
    for marker in _MARKDOWN_FORMAT_MARKERS[1:]:
        if outside_fences.count(marker) % 2:
            return False
    return True


def _split_markdown_fenced_text(text: str, *, limit: int, parse_mode: str) -> list[str]:
    chunks: list[str] = []
    index = 0
    inside_fence = False

    while index < len(text):
        prefix = "```\n" if inside_fence else ""
        source_limit = limit - len(prefix)
        if source_limit <= 0:
            chunks.append(text[index : index + limit])
            index += limit
            continue

        if index + source_limit >= len(text):
            chunk_source = text[index:]
            emitted = prefix + chunk_source
            if inside_fence and _markdown_source_fence_is_open(chunk_source, initially_open=True):
                emitted += "\n```"
            chunks.append(emitted)
            break

        split_at = _find_markdown_fenced_split_index(
            text,
            start=index,
            limit=limit,
            initially_open=inside_fence,
            parse_mode=parse_mode,
        )
        chunk_source = text[index:split_at]
        inside_fence = _markdown_source_fence_is_open(chunk_source, initially_open=inside_fence)
        emitted = prefix + chunk_source + ("\n```" if inside_fence else "")
        chunks.append(emitted)
        index = split_at

    return chunks or [""]


def _find_markdown_fenced_split_index(
    text: str,
    *,
    start: int,
    limit: int,
    initially_open: bool,
    parse_mode: str,
) -> int:
    prefix = "```\n" if initially_open else ""
    max_source_end = min(len(text), start + limit - len(prefix))
    candidate_offsets = _markdown_candidate_offsets(text[start:max_source_end])
    for offset in candidate_offsets:
        if offset <= 0:
            continue
        candidate_end = start + offset
        if _crosses_markdown_fence_marker(text, candidate_end):
            continue
        source = text[start:candidate_end]
        inside_fence = _markdown_source_fence_is_open(source, initially_open=initially_open)
        emitted = prefix + source + ("\n```" if inside_fence else "")
        if len(emitted) <= limit and _parse_mode_boundary_is_safe(emitted, parse_mode=parse_mode):
            return candidate_end

    # A very small limit may leave no room for both content and synthetic fences.
    # Make forward progress without cutting through the literal ``` marker.
    fallback_end = max_source_end
    while fallback_end > start and _crosses_markdown_fence_marker(text, fallback_end):
        fallback_end -= 1
    return fallback_end if fallback_end > start else min(len(text), start + 1)


def _markdown_candidate_offsets(source: str) -> list[int]:
    offsets: list[int] = []
    for boundary in ("\n\n", "\n", " "):
        idx = source.rfind(boundary)
        while idx > 0:
            offsets.append(idx + len(boundary))
            idx = source.rfind(boundary, 0, idx)
    offsets.extend(range(len(source), 0, -1))
    return offsets


def _markdown_source_fence_is_open(source: str, *, initially_open: bool) -> bool:
    inside_fence = initially_open
    index = 0
    while True:
        marker_index = source.find("```", index)
        if marker_index == -1:
            return inside_fence
        inside_fence = not inside_fence
        index = marker_index + 3


def _markdown_text_outside_fenced_code(text: str) -> str:
    pieces: list[str] = []
    inside_fence = False
    index = 0
    while index < len(text):
        marker_index = text.find("```", index)
        if marker_index == -1:
            if not inside_fence:
                pieces.append(text[index:])
            break
        if not inside_fence:
            pieces.append(text[index:marker_index])
        inside_fence = not inside_fence
        index = marker_index + 3
    return "".join(pieces)


def _crosses_markdown_fence_marker(text: str, split_at: int) -> bool:
    for marker_start in range(max(0, split_at - 2), split_at):
        if text.startswith("```", marker_start) and marker_start < split_at < marker_start + 3:
            return True
    return False


def _ends_with_partial_markdown_fence(prefix: str) -> bool:
    trailing_backticks = len(prefix) - len(prefix.rstrip("`"))
    return trailing_backticks in {1, 2}


def _split_telegram_html_text(text: str, *, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    open_tags: list[_OpenHtmlTag] = []

    def close_suffix(tags: list[_OpenHtmlTag]) -> str:
        return "".join(f"</{tag.name}>" for tag in reversed(tags))

    def reopen_prefix(tags: list[_OpenHtmlTag]) -> str:
        return "".join(tag.opener for tag in tags)

    def flush() -> None:
        nonlocal current
        if not current:
            return
        chunks.append(current + close_suffix(open_tags))
        current = reopen_prefix(open_tags)

    for token in _iter_html_tokens(text):
        remaining_token = token
        while remaining_token:
            if current.endswith("\n") and _html_token_is_opening_tag(remaining_token):
                flush()
            next_stack = _next_html_stack(open_tags, remaining_token)
            reserve = len(close_suffix(next_stack))
            available = limit - len(current) - reserve
            if available <= 0:
                flush()
                if len(current) + reserve >= limit:
                    # Pathological nesting can consume the budget; make forward progress.
                    available = max(1, limit - len(current))
                else:
                    continue

            piece, remaining_token = _take_html_piece(remaining_token, available)
            if not piece:
                flush()
                piece, remaining_token = _take_html_piece(remaining_token, max(1, limit - len(current)))
            current += piece
            open_tags = _next_html_stack(open_tags, piece)

    if current:
        chunks.append(current + close_suffix(open_tags))
    return chunks or [""]


def _iter_html_tokens(text: str):
    index = 0
    while index < len(text):
        char = text[index]
        if char == "<":
            end = text.find(">", index + 1)
            if end != -1:
                yield text[index : end + 1]
                index = end + 1
                continue
        if char == "&":
            match = _HTML_ENTITY_RE.match(text, index)
            if match is not None:
                yield match.group(0)
                index = match.end()
                continue
        next_special = len(text)
        for marker in ("<", "&"):
            marker_index = text.find(marker, index + 1)
            if marker_index != -1:
                next_special = min(next_special, marker_index)
        yield text[index:next_special]
        index = next_special


def _take_html_piece(token: str, limit: int) -> tuple[str, str]:
    if len(token) <= limit:
        return token, ""
    if _HTML_TAG_RE.fullmatch(token) or _HTML_ENTITY_RE.fullmatch(token):
        return token, ""
    split_at = max(1, min(limit, len(token)))
    for boundary in ("\n\n", "\n", " "):
        idx = token.rfind(boundary, 0, split_at + 1)
        if idx > 0:
            split_at = idx + len(boundary)
            break
    return token[:split_at], token[split_at:]


def _next_html_stack(open_tags: list[_OpenHtmlTag], token: str) -> list[_OpenHtmlTag]:
    stack = list(open_tags)
    match = _HTML_TAG_RE.fullmatch(token)
    if match is None:
        return stack
    raw = match.group(0)
    name = match.group(1).casefold()
    if name in _HTML_VOID_TAGS or raw.endswith("/>"):
        return stack
    if raw.startswith("</"):
        for index in range(len(stack) - 1, -1, -1):
            if stack[index].name == name:
                del stack[index:]
                break
        return stack
    stack.append(_OpenHtmlTag(name=name, opener=raw))
    return stack


def _html_token_is_opening_tag(token: str) -> bool:
    match = _HTML_TAG_RE.fullmatch(token)
    if match is None:
        return False
    raw = match.group(0)
    name = match.group(1).casefold()
    return name not in _HTML_VOID_TAGS and not raw.startswith("</") and not raw.endswith("/>")


class _TelegramHtmlValidationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.errors: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs):  # noqa: ANN001
        if tag.casefold() not in _HTML_VOID_TAGS:
            self.stack.append(tag.casefold())

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if not self.stack or self.stack[-1] != name:
            self.errors.append(name)
            return
        self.stack.pop()


def html_chunk_is_balanced(text: str) -> bool:
    parser = _TelegramHtmlValidationParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return False
    return not parser.errors and not parser.stack
