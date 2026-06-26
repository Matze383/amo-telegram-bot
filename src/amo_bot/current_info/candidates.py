from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from amo_bot.core.source_hosts import normalize_source_host
from amo_bot.current_info.models import SearchResult


SOURCE_TYPE_NEWS = "News"
SOURCE_TYPE_OFFICIAL = "Official"
SOURCE_TYPE_DOCS = "Docs"
SOURCE_TYPE_MARKET_DATA = "MarketData"
SOURCE_TYPE_SOCIAL = "Social"
SOURCE_TYPE_FORUM = "Forum"
SOURCE_TYPE_COMMERCE = "Commerce"
SOURCE_TYPE_UNKNOWN = "Unknown"

_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "oly_enc_id",
    "ref",
    "ref_src",
    "spm",
    "vero_id",
    "yclid",
}

_SOCIAL_HOSTS = {
    "bsky.app",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "mastodon.social",
    "threads.net",
    "tiktok.com",
    "twitter.com",
    "x.com",
}

_FORUM_HOSTS = {
    "discourse.org",
    "news.ycombinator.com",
    "reddit.com",
    "stackoverflow.com",
    "stackexchange.com",
}

_COMMERCE_HOSTS = {
    "aliexpress.com",
    "amazon.com",
    "amazon.de",
    "ebay.com",
    "ebay.de",
    "etsy.com",
    "shopify.com",
}

_MARKET_DATA_HOST_PARTS = (
    "boerse",
    "börse",
    "deutsche-boerse",
    "finanznachrichten",
    "finanzen.",
    "finance.yahoo",
    "investing.",
    "marketbeat",
    "markets.businessinsider",
    "marketscreener",
    "marketwatch",
    "nasdaq",
    "nyse",
    "tradingview",
)

_NEWS_HOST_PARTS = (
    "apnews",
    "bbc.",
    "bloomberg",
    "cnn.",
    "faz.",
    "guardian",
    "nytimes",
    "reuters",
    "spiegel",
    "tagesschau",
    "zeit.",
)


def canonicalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return (url or "").strip()

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        return (url or "").strip()

    netloc = hostname
    if parsed.port and not (
        (parsed.scheme == "http" and parsed.port == 80) or (parsed.scheme == "https" and parsed.port == 443)
    ):
        netloc = f"{hostname}:{parsed.port}"

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower.startswith("utm_") or key_lower in _TRACKING_PARAMS:
            continue
        query_items.append((key, value))

    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            path,
            "",
            urlencode(sorted(query_items), doseq=True),
            "",
        )
    )


def classify_source_type(result: SearchResult) -> str:
    host = (result.host or urlparse(result.url).hostname or "").lower().removeprefix("www.")
    path = urlparse(result.url).path.lower()
    text = f"{result.title} {result.snippet}".lower()

    if host.endswith(".gov") or host.endswith(".mil") or host.endswith(".int"):
        return SOURCE_TYPE_OFFICIAL
    if any(part in host for part in (".gov.", ".mil.", "who.int", "europa.eu")):
        return SOURCE_TYPE_OFFICIAL
    if host.startswith("docs.") or any(marker in path for marker in ("/docs", "/documentation", "/api/", "/reference")):
        return SOURCE_TYPE_DOCS
    if any(host == known or host.endswith(f".{known}") for known in _SOCIAL_HOSTS):
        return SOURCE_TYPE_SOCIAL
    if any(host == known or host.endswith(f".{known}") for known in _FORUM_HOSTS) or "forum" in host:
        return SOURCE_TYPE_FORUM
    if any(host == known or host.endswith(f".{known}") for known in _COMMERCE_HOSTS):
        return SOURCE_TYPE_COMMERCE
    if any(marker in path for marker in ("/shop", "/store", "/product", "/pricing", "/cart")):
        return SOURCE_TYPE_COMMERCE
    if any(part in host for part in _MARKET_DATA_HOST_PARTS):
        return SOURCE_TYPE_MARKET_DATA
    if any(part in host for part in _NEWS_HOST_PARTS) or any(word in text for word in ("breaking news", "latest news")):
        return SOURCE_TYPE_NEWS
    if "/news" in path or "/article" in path:
        return SOURCE_TYPE_NEWS
    return SOURCE_TYPE_UNKNOWN


def normalize_dedupe_and_rank_search_results(
    results: tuple[SearchResult, ...],
    *,
    max_results: int,
    source_preferences: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[SearchResult, ...]:
    normalized: list[SearchResult] = []
    seen_canonical_urls: set[str] = set()
    normalized_source_preferences = {
        normalized_host: preference
        for raw_host, preference in (source_preferences or {}).items()
        if (normalized_host := normalize_source_host(str(raw_host)))
    }
    for result in results:
        canonical_url = canonicalize_url(result.url)
        dedupe_key = canonical_url.casefold()
        if not canonical_url or dedupe_key in seen_canonical_urls:
            continue
        seen_canonical_urls.add(dedupe_key)

        parsed = urlparse(canonical_url)
        host = normalize_source_host(parsed.hostname or result.host)
        metadata = dict(result.metadata)
        source_type = str(metadata.get("source_type") or classify_source_type(replace(result, url=canonical_url, host=host)))
        metadata["canonical_url"] = canonical_url
        metadata["source_type"] = source_type
        if host in normalized_source_preferences:
            metadata.update(_source_preference_metadata(normalized_source_preferences[host]))
        normalized.append(replace(result, url=canonical_url, host=host, metadata=metadata))

    ranked = _rank_candidates(normalized)
    limit = max(int(max_results), 1)
    return tuple(ranked[:limit])


def _rank_candidates(results: list[SearchResult]) -> list[SearchResult]:
    host_counts: dict[str, int] = {}
    ranked: list[tuple[tuple[float, int, str], SearchResult]] = []
    for index, result in enumerate(results):
        host = result.host or ""
        host_occurrence = host_counts.get(host, 0)
        host_counts[host] = host_occurrence + 1
        source_type = str(result.metadata.get("source_type") or SOURCE_TYPE_UNKNOWN)
        freshness = _freshness_timestamp(result.date)
        score = (
            float(result.rank or index + 1),
            float(host_occurrence) * 0.75,
            -_source_type_score(source_type) * 0.1,
            _source_observation_penalty(result.metadata),
            _source_preference_penalty(result.metadata),
            -freshness * 0.0000000001,
        )
        ranked.append(((sum(score), index, result.url), result))
    return [result for _, result in sorted(ranked, key=lambda item: item[0])]


def _source_type_score(source_type: str) -> int:
    return {
        SOURCE_TYPE_OFFICIAL: 5,
        SOURCE_TYPE_DOCS: 4,
        SOURCE_TYPE_MARKET_DATA: 4,
        SOURCE_TYPE_NEWS: 3,
        SOURCE_TYPE_FORUM: 2,
        SOURCE_TYPE_SOCIAL: 1,
        SOURCE_TYPE_COMMERCE: 0,
        SOURCE_TYPE_UNKNOWN: 0,
    }.get(source_type, 0)


def _source_observation_penalty(metadata: dict[str, object]) -> float:
    """Apply only metadata-only source observations supplied by upstream ports."""
    raw_outcome = (
        metadata.get("source_observation_outcome")
        or metadata.get("observation_outcome")
        or metadata.get("outcome")
        or ""
    )
    outcome = str(raw_outcome).strip().lower()
    if outcome in {"confirmed", "allow", "search_completed", "scrape_completed", "browser_completed"}:
        penalty = -0.35
    elif outcome in {"unconfirmed", "low_quality", "fail_closed", "error", "denied", "blocked"}:
        penalty = 0.65
    else:
        penalty = 0.0

    warning_count = _metadata_int(metadata.get("source_observation_warning_count") or metadata.get("warning_count"))
    warning_codes = _metadata_tuple(metadata.get("source_observation_warning_codes") or metadata.get("warning_codes"))
    if warning_codes:
        warning_count = max(warning_count, len(warning_codes))
    penalty += min(warning_count, 5) * 0.08
    if any("conflict" in code or "mismatch" in code for code in warning_codes):
        penalty += 0.6

    confidence = _metadata_float(metadata.get("source_observation_confidence") or metadata.get("confidence"))
    if confidence is not None:
        penalty -= max(0.0, min(confidence, 1.0)) * 0.25

    explicit_penalty = _metadata_float(metadata.get("source_observation_penalty") or metadata.get("observation_penalty"))
    if explicit_penalty is not None:
        penalty += explicit_penalty
    return penalty


def _source_preference_metadata(preference: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "source_preference_signal",
        "source_preference_weight",
        "source_preference_scope",
        "source_preference_domain",
        "source_preference_source",
    }
    return {key: preference[key] for key in allowed if key in preference}


def _source_preference_penalty(metadata: dict[str, object]) -> float:
    raw_signal = metadata.get("source_preference_signal") or metadata.get("source_preference")
    signal = str(raw_signal or "").strip().lower()
    explicit_weight = _metadata_float(metadata.get("source_preference_weight"))
    if explicit_weight is not None:
        return max(-2.0, min(2.0, explicit_weight))
    if signal in {"preferred", "trusted"}:
        return -0.75 if signal == "preferred" else -0.9
    if signal in {"avoid", "rejected", "low_quality", "negative"}:
        return 1.35 if signal == "rejected" else 0.9
    return 0.0


def _metadata_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip().lower() for item in value.replace(",", " ").split() if item.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip().lower() for item in value if str(item).strip())
    return (str(value).strip().lower(),) if str(value).strip() else ()


def _metadata_int(value: object) -> int:
    try:
        return max(0, int(value)) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _metadata_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _freshness_timestamp(value: str) -> float:
    candidate = (value or "").strip()
    if not candidate:
        return 0.0
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(candidate.replace("Z", "+0000"), fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    return 0.0
