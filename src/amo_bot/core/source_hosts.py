from __future__ import annotations

from urllib.parse import urlparse


def normalize_source_host(value: str | None) -> str:
    """Normalize source hosts for metadata-only source quality matching."""

    raw = (value or "").strip()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        host = (urlparse(candidate).hostname or "").casefold().rstrip(".")
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    if not host or "/" in host or len(host) > 253:
        return ""
    return host
