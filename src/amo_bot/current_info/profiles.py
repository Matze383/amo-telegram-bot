from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class SearchProfileConfigError(ValueError):
    def __init__(self, reason_code: str, message: str, *, provider: str = "", field: str = "") -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.provider = provider
        self.field = field


class SearchIntent(StrEnum):
    DEFAULT = "default"
    NEWS_CURRENT = "news/current"
    DOCS_OFFICIAL = "docs/official"
    LOCAL_REGION = "local/region"
    BROAD_WEB = "broad web"


@dataclass(frozen=True, slots=True)
class SearchProfile:
    intent: SearchIntent
    locale: str
    language: str
    region: str
    content_types: tuple[str, ...] = ()
    freshness: str = ""
    safesearch: str = "moderate"


@dataclass(frozen=True, slots=True)
class SearchProfileRule:
    content_types: tuple[str, ...]
    freshness: str = ""


@dataclass(frozen=True, slots=True)
class SearchProfileConfig:
    profiles: dict[SearchIntent, SearchProfileRule]


@dataclass(frozen=True, slots=True)
class ProviderProfileCapabilities:
    provider: str
    content_types: frozenset[str]
    freshness_values: frozenset[str]
    safesearch_values: frozenset[str]
    content_type_mapping: dict[str, str]
    freshness_mapping: dict[str, str]
    safesearch_mapping: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProviderProfileParams:
    language: str
    locale: str
    region: str
    content_types: tuple[str, ...]
    freshness: str
    safesearch: Any


SEARXNG_CAPABILITIES = ProviderProfileCapabilities(
    provider="searxng",
    content_types=frozenset({"general", "news"}),
    freshness_values=frozenset({"", "day", "month", "year"}),
    safesearch_values=frozenset({"off", "moderate", "strict"}),
    content_type_mapping={"web": "general", "news": "news"},
    freshness_mapping={"": "", "day": "day", "week": "month", "month": "month", "year": "year"},
    safesearch_mapping={"off": 0, "moderate": 1, "strict": 2},
)


BRAVE_CAPABILITIES = ProviderProfileCapabilities(
    provider="brave",
    content_types=frozenset(
        {"discussions", "faq", "infobox", "locations", "news", "query", "summarizer", "videos", "web"}
    ),
    freshness_values=frozenset({"", "pd", "pw", "pm", "py"}),
    safesearch_values=frozenset({"off", "moderate", "strict"}),
    content_type_mapping={
        "web": "web",
        "discussions": "discussions",
        "faq": "faq",
        "locations": "locations",
        "news": "news",
    },
    freshness_mapping={"": "", "day": "pd", "week": "pw", "month": "pm", "year": "py"},
    safesearch_mapping={"off": "off", "moderate": "moderate", "strict": "strict"},
)


_DEFAULT_PROFILE_RULES = {
    SearchIntent.DEFAULT: SearchProfileRule(content_types=("web",), freshness=""),
    SearchIntent.NEWS_CURRENT: SearchProfileRule(content_types=("news", "web"), freshness="day"),
    SearchIntent.DOCS_OFFICIAL: SearchProfileRule(content_types=("web", "faq"), freshness=""),
    SearchIntent.LOCAL_REGION: SearchProfileRule(content_types=("web", "news", "locations"), freshness="week"),
    SearchIntent.BROAD_WEB: SearchProfileRule(content_types=("web", "discussions", "faq", "news"), freshness=""),
}

_SUPPORTED_PROFILE_FRESHNESS = frozenset({"", "day", "week", "month", "year"})


def default_search_profile_config() -> SearchProfileConfig:
    return SearchProfileConfig(profiles=dict(_DEFAULT_PROFILE_RULES))


def load_search_profile_config(data: dict[str, Any] | None) -> SearchProfileConfig:
    if data is None:
        return default_search_profile_config()
    if not isinstance(data, dict):
        raise SearchProfileConfigError(
            "invalid_search_profile_config",
            "search profile config must be an object",
            provider="profile",
        )

    raw_profiles = data.get("profiles", data)
    if not isinstance(raw_profiles, dict):
        raise SearchProfileConfigError(
            "invalid_search_profile_profiles",
            "search profile config profiles must be an object",
            provider="profile",
        )

    profiles = dict(_DEFAULT_PROFILE_RULES)
    for raw_intent, raw_rule in raw_profiles.items():
        intent = _intent_from_config_key(raw_intent)
        if not isinstance(raw_rule, dict):
            raise SearchProfileConfigError(
                "invalid_search_profile_rule",
                f"search profile rule for {intent.value} must be an object",
                provider="profile",
                field=intent.value,
            )
        profiles[intent] = _profile_rule_from_config(intent=intent, data=raw_rule)
    return SearchProfileConfig(profiles=profiles)


def load_search_profile_config_file(path: str | Path) -> SearchProfileConfig:
    profile_path = Path(path)
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SearchProfileConfigError(
            "invalid_search_profile_file",
            f"could not read search profile config file: {profile_path}",
            provider="profile",
        ) from exc
    except yaml.YAMLError as exc:
        raise SearchProfileConfigError(
            "invalid_search_profile_file",
            f"could not parse search profile config file: {profile_path}",
            provider="profile",
        ) from exc
    return load_search_profile_config(raw or {})


def select_search_profile(
    *,
    query: str,
    locale: str,
    safesearch: str = "moderate",
    region: str | None = None,
    config: SearchProfileConfig | None = None,
) -> SearchProfile:
    normalized_locale = _normalize_locale(locale)
    normalized_query = " ".join((query or "").casefold().split())
    intent = _detect_search_intent(normalized_query)
    country = _normalize_country(region) or _country_from_locale(normalized_locale)
    language = normalized_locale.split("-", 1)[0]
    profile_config = config or default_search_profile_config()
    rule = profile_config.profiles.get(intent) or profile_config.profiles[SearchIntent.DEFAULT]

    return SearchProfile(
        intent=intent,
        locale=normalized_locale,
        language=language,
        region=country,
        content_types=rule.content_types,
        freshness=rule.freshness,
        safesearch=_validate_safesearch(safesearch, provider="profile", field="safesearch"),
    )


def map_search_profile(profile: SearchProfile, capabilities: ProviderProfileCapabilities) -> ProviderProfileParams:
    content_types = tuple(
        mapped
        for content_type in profile.content_types
        if (mapped := _map_content_type(content_type, capabilities)) in capabilities.content_types
    )
    freshness = _map_freshness(profile.freshness, capabilities)
    safesearch = _map_safesearch(profile.safesearch, capabilities)
    return ProviderProfileParams(
        language=profile.language,
        locale=profile.locale,
        region=profile.region,
        content_types=content_types,
        freshness=freshness,
        safesearch=safesearch,
    )


def searxng_profile_params(
    profile: SearchProfile,
    *,
    language: str | None = None,
    categories: str | None = None,
    time_range: str | None = None,
    safesearch: str | None = None,
) -> dict[str, str | int]:
    mapped = map_search_profile(profile, SEARXNG_CAPABILITIES)
    params: dict[str, str | int] = {
        "language": language or profile.locale,
        "safesearch": _map_safesearch(safesearch or profile.safesearch, SEARXNG_CAPABILITIES),
    }
    resolved_categories = _validate_csv_values(
        categories,
        allowed=SEARXNG_CAPABILITIES.content_types,
        provider="searxng",
        field="categories",
    ) or _dedupe(mapped.content_types)
    if resolved_categories:
        params["categories"] = ",".join(_order_searxng_categories(resolved_categories))
    resolved_time_range = _validate_choice(
        time_range,
        allowed=SEARXNG_CAPABILITIES.freshness_values,
        provider="searxng",
        field="time_range",
    ) if time_range is not None else mapped.freshness
    if resolved_time_range:
        params["time_range"] = resolved_time_range
    return params


def brave_profile_params(
    profile: SearchProfile,
    *,
    country: str | None = None,
    search_lang: str | None = None,
    ui_lang: str | None = None,
    freshness: str | None = None,
    safesearch: str | None = None,
    result_filter: str | None = None,
) -> dict[str, str]:
    mapped = map_search_profile(profile, BRAVE_CAPABILITIES)
    params: dict[str, str] = {}
    resolved_search_lang = search_lang or mapped.language
    if resolved_search_lang:
        params["search_lang"] = resolved_search_lang
    resolved_ui_lang = ui_lang or brave_ui_lang(mapped.locale)
    if resolved_ui_lang:
        params["ui_lang"] = resolved_ui_lang
    resolved_country = country or mapped.region
    if resolved_country:
        country_code = _normalize_country(resolved_country)
        if not country_code:
            raise SearchProfileConfigError(
                "invalid_provider_profile_country",
                "brave country must be a 2-letter country code",
                provider="brave",
                field="country",
            )
        params["country"] = country_code
    params["safesearch"] = _map_safesearch(safesearch or profile.safesearch, BRAVE_CAPABILITIES)
    resolved_freshness = _validate_choice(
        freshness,
        allowed=BRAVE_CAPABILITIES.freshness_values,
        provider="brave",
        field="freshness",
    ) if freshness is not None else mapped.freshness
    if resolved_freshness:
        params["freshness"] = resolved_freshness
    resolved_filter = _validate_csv_values(
        result_filter,
        allowed=BRAVE_CAPABILITIES.content_types,
        provider="brave",
        field="result_filter",
    ) or _dedupe(mapped.content_types)
    if resolved_filter:
        params["result_filter"] = ",".join(resolved_filter)
    return params


def brave_ui_lang(locale: str) -> str:
    normalized = _normalize_locale(locale)
    parts = normalized.split("-", 1)
    if len(parts) == 2:
        return f"{parts[0]}-{parts[1].upper()}"
    return normalized


def _detect_search_intent(query: str) -> SearchIntent:
    if _DOCS_OFFICIAL_RE.search(query):
        return SearchIntent.DOCS_OFFICIAL
    if _LOCAL_REGION_RE.search(query):
        return SearchIntent.LOCAL_REGION
    if _NEWS_CURRENT_RE.search(query):
        return SearchIntent.NEWS_CURRENT
    if _BROAD_WEB_RE.search(query):
        return SearchIntent.BROAD_WEB
    return SearchIntent.DEFAULT


def _intent_from_config_key(value: Any) -> SearchIntent:
    candidate = str(value or "").strip().casefold().replace("_", "/")
    aliases = {
        "default": SearchIntent.DEFAULT,
        "news/current": SearchIntent.NEWS_CURRENT,
        "news": SearchIntent.NEWS_CURRENT,
        "current": SearchIntent.NEWS_CURRENT,
        "docs/official": SearchIntent.DOCS_OFFICIAL,
        "docs": SearchIntent.DOCS_OFFICIAL,
        "official": SearchIntent.DOCS_OFFICIAL,
        "local/region": SearchIntent.LOCAL_REGION,
        "local": SearchIntent.LOCAL_REGION,
        "region": SearchIntent.LOCAL_REGION,
        "broad web": SearchIntent.BROAD_WEB,
        "broad/web": SearchIntent.BROAD_WEB,
        "broad": SearchIntent.BROAD_WEB,
    }
    if candidate in aliases:
        return aliases[candidate]
    raise SearchProfileConfigError(
        "invalid_search_profile_intent",
        f"unsupported search profile intent: {value}",
        provider="profile",
        field="profiles",
    )


def _profile_rule_from_config(*, intent: SearchIntent, data: dict[str, Any]) -> SearchProfileRule:
    raw_content_types = data.get("content_types")
    if not isinstance(raw_content_types, list | tuple) or not raw_content_types:
        raise SearchProfileConfigError(
            "invalid_search_profile_content_types",
            f"search profile {intent.value} content_types must be a non-empty list",
            provider="profile",
            field="content_types",
        )
    content_types = _dedupe(tuple(_validate_content_type(value, intent=intent) for value in raw_content_types))
    freshness = _validate_profile_freshness(data.get("freshness", ""), intent=intent)
    return SearchProfileRule(content_types=content_types, freshness=freshness)


def _validate_content_type(value: Any, *, intent: SearchIntent) -> str:
    candidate = str(value or "").strip().casefold()
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,40}", candidate):
        return candidate
    raise SearchProfileConfigError(
        "invalid_search_profile_content_type",
        f"search profile {intent.value} content_types contains an invalid value",
        provider="profile",
        field="content_types",
    )


def _validate_profile_freshness(value: Any, *, intent: SearchIntent) -> str:
    candidate = str(value or "").strip().casefold()
    if candidate in _SUPPORTED_PROFILE_FRESHNESS:
        return candidate
    raise SearchProfileConfigError(
        "invalid_search_profile_freshness",
        f"search profile {intent.value} freshness must be one of: day, week, month, year, or empty",
        provider="profile",
        field="freshness",
    )


def _normalize_locale(locale: str) -> str:
    candidate = (locale or "").strip().lower().replace("_", "-")
    if not candidate:
        return "en-us"
    safe_map = {"en": "en-us", "de": "de-de"}
    if candidate in safe_map:
        return safe_map[candidate]
    if re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", candidate):
        return candidate
    return "en-us"


def _country_from_locale(locale: str) -> str:
    normalized = _normalize_locale(locale)
    parts = normalized.split("-", 1)
    if len(parts) == 2 and re.fullmatch(r"[a-z]{2}", parts[1]):
        return parts[1].upper()
    return {"de": "DE", "en": "US"}.get(parts[0], "")


def _normalize_country(value: str | None) -> str:
    candidate = (value or "").strip().upper()
    if re.fullmatch(r"[A-Z]{2}", candidate):
        return candidate
    return ""


def _validate_safesearch(value: str, *, provider: str, field: str) -> str:
    normalized = (value or "moderate").strip().casefold()
    if normalized in {"off", "moderate", "strict"}:
        return normalized
    raise SearchProfileConfigError(
        "invalid_provider_profile_safesearch",
        f"{provider} {field} must be one of: off, moderate, strict",
        provider=provider,
        field=field,
    )


def _map_safesearch(value: str, capabilities: ProviderProfileCapabilities) -> Any:
    normalized = _validate_safesearch(value, provider=capabilities.provider, field="safesearch")
    if normalized not in capabilities.safesearch_values:
        raise SearchProfileConfigError(
            "unsupported_provider_profile_safesearch",
            f"{capabilities.provider} does not support safesearch={normalized}",
            provider=capabilities.provider,
            field="safesearch",
        )
    return capabilities.safesearch_mapping[normalized]


def _map_freshness(value: str, capabilities: ProviderProfileCapabilities) -> str:
    mapped = capabilities.freshness_mapping.get(value)
    if mapped is None:
        raise SearchProfileConfigError(
            "unsupported_provider_profile_freshness",
            f"{capabilities.provider} does not support freshness={value}",
            provider=capabilities.provider,
            field="freshness",
        )
    if mapped not in capabilities.freshness_values:
        raise SearchProfileConfigError(
            "invalid_provider_profile_freshness",
            f"{capabilities.provider} mapped freshness={mapped} is unsupported",
            provider=capabilities.provider,
            field="freshness",
        )
    return mapped


def _map_content_type(content_type: str, capabilities: ProviderProfileCapabilities) -> str | None:
    mapped = capabilities.content_type_mapping.get(content_type)
    if mapped is not None:
        return mapped
    if content_type == "faq":
        return capabilities.content_type_mapping.get("web")
    return None


def _order_searxng_categories(values: tuple[str, ...]) -> tuple[str, ...]:
    priority = {"news": 0, "general": 1}
    return tuple(sorted(values, key=lambda value: priority.get(value, len(priority))))


def _validate_choice(
    value: str | None,
    *,
    allowed: frozenset[str],
    provider: str,
    field: str,
) -> str:
    candidate = (value or "").strip().casefold()
    if candidate in allowed:
        return candidate
    raise SearchProfileConfigError(
        f"invalid_provider_profile_{field}",
        f"{provider} {field} has unsupported value",
        provider=provider,
        field=field,
    )


def _validate_csv_values(
    value: str | None,
    *,
    allowed: frozenset[str],
    provider: str,
    field: str,
) -> tuple[str, ...]:
    if value is None:
        return ()
    values = _dedupe(tuple(part.strip().casefold() for part in value.split(",") if part.strip()))
    invalid = tuple(part for part in values if part not in allowed)
    if invalid:
        raise SearchProfileConfigError(
            f"invalid_provider_profile_{field}",
            f"{provider} {field} contains unsupported values",
            provider=provider,
            field=field,
        )
    return values


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


_NEWS_CURRENT_RE = re.compile(
    r"\b("
    r"aktuell(?:e|er|en|es)?|heute|jetzt|gerade|neu(?:e|er|en|es)?|nachrichten|meldung(?:en)?|"
    r"latest|current|currently|today|now|breaking|news|recent|update|updates|release|status"
    r")\b"
)
_DOCS_OFFICIAL_RE = re.compile(
    r"\b("
    r"official|offiziell(?:e|er|en|es)?|docs?|documentation|dokumentation|manual|handbuch|"
    r"api\s+reference|reference|spec|specification|changelog|release\s+notes"
    r")\b"
)
_LOCAL_REGION_RE = re.compile(
    r"\b("
    r"near\s+me|nearby|local|regional|region|city|stadt|umgebung|in\s+der\s+naehe|in\s+der\s+nähe|"
    r"berlin|hamburg|munich|muenchen|münchen|cologne|koeln|köln|germany|deutschland|"
    r"traffic|verkehr|weather|wetter|opening\s+hours|oeffnungszeiten|öffnungszeiten"
    r")\b"
)
_BROAD_WEB_RE = re.compile(
    r"\b("
    r"web|internet|sources?|quellen|overview|ueberblick|überblick|compare|vergleich|"
    r"reviews?|erfahrungen|forums?|forum|reddit|discussion|diskussion"
    r")\b"
)
