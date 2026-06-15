from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class AIModelTaskType(StrEnum):
    """Coarse task classes used for model routing.

    Keep these labels metadata-only. They are safe to log and must not contain
    prompt text, URLs, or user-provided queries.
    """

    ANSWER_SYNTHESIS = "answer_synthesis"
    WEB_RESEARCH = "web_research"
    SPORTS = "sports"
    NEWS = "news"
    SIMPLE = "simple"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class AIModelPolicyConfig:
    enabled: bool = False
    thinking_model: str = ""
    non_thinking_model: str = ""
    prefer_thinking_task_types: tuple[str, ...] = (
        AIModelTaskType.WEB_RESEARCH.value,
        AIModelTaskType.SPORTS.value,
        AIModelTaskType.NEWS.value,
        AIModelTaskType.ANSWER_SYNTHESIS.value,
    )
    simple_prompt_max_chars: int = 240
    thinking_timeout_seconds: float | None = None
    non_thinking_timeout_seconds: float | None = None
    thinking_budget_max_prompt_chars: int | None = None
    non_thinking_budget_max_prompt_chars: int | None = None


@dataclass(frozen=True, slots=True)
class AIModelRoute:
    task_type: AIModelTaskType
    model: str
    think: bool
    timeout_seconds: float
    max_prompt_chars: int
    decision: str
    reason: str
    fallback_model: str
    fallback_think: bool
    fallback_timeout_seconds: float
    fallback_max_prompt_chars: int


_SPORTS_RE = re.compile(
    r"\b(?:sports?|sport|fußball|fussball|football|soccer|basketball|tennis|"
    r"bundesliga|champions\s+league|world\s+cup|wm|standings?|fixture|fixtures?|"
    r"spielplan|score|ergebnis(?:se)?|tabelle)\b",
    re.IGNORECASE,
)
_NEWS_RE = re.compile(
    r"\b(?:news|nachrichten|breaking|latest|neueste(?:n)?|meldung(?:en)?|"
    r"headline|headlines|bericht(?:e)?|presse)\b",
    re.IGNORECASE,
)
_WEB_RESEARCH_RE = re.compile(
    r"\b(?:websearch|webscrape|browser|source|sources|quelle(?:n)?|research|"
    r"recherch|aktuell|current|today|heute|latest|live|stand|status|version|"
    r"release|price|preis|kurs|weather|wetter)\b",
    re.IGNORECASE,
)
_SIMPLE_RE = re.compile(
    r"^\s*(?:hi|hallo|hey|danke|thanks|ok|okay|ja|yes|no|nein|ping|test|"
    r"gute[nr]? morgen|gute[nr]? abend)[.!?\s]*$",
    re.IGNORECASE,
)
_SIMPLE_FACT_RE = re.compile(
    r"^\s*(?:what(?:'s| is)|was ist|wer ist|who is|wie viel|how much|"
    r"rechne|calculate)\b.{0,80}$",
    re.IGNORECASE,
)


def parse_task_type(value: str | AIModelTaskType | None) -> AIModelTaskType | None:
    if isinstance(value, AIModelTaskType):
        return value
    normalized = (value or "").strip().casefold().replace("-", "_")
    if not normalized:
        return None
    for task_type in AIModelTaskType:
        if normalized == task_type.value:
            return task_type
    return None


def infer_task_type(prompt: str) -> AIModelTaskType:
    text = (prompt or "").strip()
    if not text:
        return AIModelTaskType.GENERAL
    if is_simple_prompt(text):
        return AIModelTaskType.SIMPLE
    if _SPORTS_RE.search(text):
        return AIModelTaskType.SPORTS
    if _NEWS_RE.search(text):
        return AIModelTaskType.NEWS
    if _WEB_RESEARCH_RE.search(text):
        return AIModelTaskType.WEB_RESEARCH
    return AIModelTaskType.GENERAL


def is_simple_prompt(prompt: str) -> bool:
    text = (prompt or "").strip()
    return bool(_SIMPLE_RE.match(text) or _SIMPLE_FACT_RE.match(text))


def route_model(
    *,
    prompt: str,
    default_model: str,
    default_timeout_seconds: float,
    default_max_prompt_chars: int,
    config: AIModelPolicyConfig,
    task_type: str | AIModelTaskType | None = None,
) -> AIModelRoute:
    selected_task_type = parse_task_type(task_type) or infer_task_type(prompt)
    clean_default_model = default_model.strip()
    thinking_model = config.thinking_model.strip()
    non_thinking_model = config.non_thinking_model.strip()
    prefer_thinking = selected_task_type.value in set(config.prefer_thinking_task_types)
    low_budget_prompt = len((prompt or "").strip()) <= config.simple_prompt_max_chars
    simple_prompt = is_simple_prompt(prompt)

    if not config.enabled:
        return AIModelRoute(
            task_type=selected_task_type,
            model=clean_default_model,
            think=False,
            timeout_seconds=default_timeout_seconds,
            max_prompt_chars=default_max_prompt_chars,
            decision="default",
            reason="policy_disabled",
            fallback_model=non_thinking_model,
            fallback_think=False,
            fallback_timeout_seconds=config.non_thinking_timeout_seconds or default_timeout_seconds,
            fallback_max_prompt_chars=config.non_thinking_budget_max_prompt_chars or default_max_prompt_chars,
        )

    if non_thinking_model and (
        selected_task_type is AIModelTaskType.SIMPLE
        or simple_prompt
        or (low_budget_prompt and not prefer_thinking)
    ):
        return AIModelRoute(
            task_type=selected_task_type,
            model=non_thinking_model,
            think=False,
            timeout_seconds=config.non_thinking_timeout_seconds or default_timeout_seconds,
            max_prompt_chars=config.non_thinking_budget_max_prompt_chars or default_max_prompt_chars,
            decision="non_thinking",
            reason="simple_or_low_budget",
            fallback_model=non_thinking_model,
            fallback_think=False,
            fallback_timeout_seconds=config.non_thinking_timeout_seconds or default_timeout_seconds,
            fallback_max_prompt_chars=config.non_thinking_budget_max_prompt_chars or default_max_prompt_chars,
        )

    if prefer_thinking and thinking_model:
        return AIModelRoute(
            task_type=selected_task_type,
            model=thinking_model,
            think=True,
            timeout_seconds=config.thinking_timeout_seconds or default_timeout_seconds,
            max_prompt_chars=config.thinking_budget_max_prompt_chars or default_max_prompt_chars,
            decision="thinking",
            reason="preferred_task_type",
            fallback_model=non_thinking_model or clean_default_model,
            fallback_think=False,
            fallback_timeout_seconds=config.non_thinking_timeout_seconds or default_timeout_seconds,
            fallback_max_prompt_chars=config.non_thinking_budget_max_prompt_chars or default_max_prompt_chars,
        )

    return AIModelRoute(
        task_type=selected_task_type,
        model=clean_default_model,
        think=False,
        timeout_seconds=default_timeout_seconds,
        max_prompt_chars=default_max_prompt_chars,
        decision="default",
        reason="no_policy_match",
        fallback_model=non_thinking_model,
        fallback_think=False,
        fallback_timeout_seconds=config.non_thinking_timeout_seconds or default_timeout_seconds,
        fallback_max_prompt_chars=config.non_thinking_budget_max_prompt_chars or default_max_prompt_chars,
    )


__all__ = [
    "AIModelPolicyConfig",
    "AIModelRoute",
    "AIModelTaskType",
    "infer_task_type",
    "is_simple_prompt",
    "parse_task_type",
    "route_model",
]
