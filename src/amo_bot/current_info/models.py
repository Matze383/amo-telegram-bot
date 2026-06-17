from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


JsonDict = dict[str, Any]


@dataclass(frozen=True, slots=True)
class CurrentInfoRequest:
    query: str
    locale: str = "en"
    domain_hint: str = ""
    max_results: int = 5
    max_documents: int = 3
    user_id: int | None = None
    chat_id: int | None = None
    topic_id: int | None = None
    role: Any = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "query": self.query,
            "locale": self.locale,
            "domain_hint": self.domain_hint,
            "max_results": self.max_results,
            "max_documents": self.max_documents,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "topic_id": self.topic_id,
            "role": getattr(self.role, "value", self.role),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> CurrentInfoRequest:
        return cls(
            query=str(payload.get("query", "")),
            locale=str(payload.get("locale", "en") or "en"),
            domain_hint=str(payload.get("domain_hint", "") or ""),
            max_results=_coerce_positive_int(payload.get("max_results"), default=5),
            max_documents=_coerce_positive_int(payload.get("max_documents"), default=3),
            user_id=_coerce_optional_int(payload.get("user_id")),
            chat_id=_coerce_optional_int(payload.get("chat_id")),
            topic_id=_coerce_optional_int(payload.get("topic_id")),
            role=payload.get("role"),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_type: str
    query: str
    locale: str = "en"
    domain: str = ""
    constraints: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "task_type": self.task_type,
            "query": self.query,
            "locale": self.locale,
            "domain": self.domain,
            "constraints": dict(self.constraints),
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> TaskSpec:
        return cls(
            task_type=str(payload.get("task_type", "")),
            query=str(payload.get("query", "")),
            locale=str(payload.get("locale", "en") or "en"),
            domain=str(payload.get("domain", "") or ""),
            constraints=dict(payload.get("constraints") or {}),
        )


@dataclass(frozen=True, slots=True)
class QueryPlan:
    task: TaskSpec
    queries: tuple[str, ...]
    max_results: int = 5
    strategy: str = "search_first"

    def to_dict(self) -> JsonDict:
        return {
            "task": self.task.to_dict(),
            "queries": list(self.queries),
            "max_results": self.max_results,
            "strategy": self.strategy,
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> QueryPlan:
        return cls(
            task=TaskSpec.from_dict(dict(payload.get("task") or {})),
            queries=_coerce_str_tuple(payload.get("queries")),
            max_results=_coerce_positive_int(payload.get("max_results"), default=5),
            strategy=str(payload.get("strategy", "search_first") or "search_first"),
        )


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    provider: str = ""
    rank: int = 0
    host: str = ""
    date: str = ""
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "provider": self.provider,
            "rank": self.rank,
            "host": self.host,
            "date": self.date,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> SearchResult:
        url = str(payload.get("url", ""))
        host_value = payload.get("host")
        return cls(
            title=str(payload.get("title", "")),
            url=url,
            snippet=str(payload.get("snippet", "") or ""),
            provider=str(payload.get("provider", "") or ""),
            rank=_coerce_non_negative_int(payload.get("rank"), default=0),
            host=str(host_value) if host_value is not None else _host_from_url(url),
            date=str(payload.get("date", "") or ""),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class SearchProviderMetric:
    provider: str
    latency_ms: float = 0.0
    hit_count: int = 0
    error_class: str = ""
    fallback_reason: str = ""
    host_diversity: int = 0

    def to_dict(self) -> JsonDict:
        return {
            "provider": self.provider,
            "latency_ms": round(self.latency_ms, 3),
            "hit_count": self.hit_count,
            "error_class": self.error_class,
            "fallback_reason": self.fallback_reason,
            "host_diversity": self.host_diversity,
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> SearchProviderMetric:
        return cls(
            provider=str(payload.get("provider", "") or ""),
            latency_ms=_coerce_float(payload.get("latency_ms"), default=0.0),
            hit_count=_coerce_non_negative_int(payload.get("hit_count"), default=0),
            error_class=str(payload.get("error_class", "") or ""),
            fallback_reason=str(payload.get("fallback_reason", "") or ""),
            host_diversity=_coerce_non_negative_int(payload.get("host_diversity"), default=0),
        )


@dataclass(frozen=True, slots=True)
class SearchProviderResponse:
    results: tuple[SearchResult, ...] = ()
    metrics: tuple[SearchProviderMetric, ...] = ()

    def to_dict(self) -> JsonDict:
        return {
            "results": [item.to_dict() for item in self.results],
            "metrics": [item.to_dict() for item in self.metrics],
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> SearchProviderResponse:
        return cls(
            results=tuple(SearchResult.from_dict(dict(item)) for item in payload.get("results") or ()),
            metrics=tuple(SearchProviderMetric.from_dict(dict(item)) for item in payload.get("metrics") or ()),
        )


@dataclass(frozen=True, slots=True)
class SearchBundle:
    query_plan: QueryPlan
    results: tuple[SearchResult, ...] = ()
    warnings: tuple[str, ...] = ()
    metrics: tuple[SearchProviderMetric, ...] = ()

    def to_dict(self) -> JsonDict:
        return {
            "query_plan": self.query_plan.to_dict(),
            "results": [item.to_dict() for item in self.results],
            "warnings": list(self.warnings),
            "metrics": [item.to_dict() for item in self.metrics],
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> SearchBundle:
        return cls(
            query_plan=QueryPlan.from_dict(dict(payload.get("query_plan") or {})),
            results=tuple(SearchResult.from_dict(dict(item)) for item in payload.get("results") or ()),
            warnings=_coerce_str_tuple(payload.get("warnings")),
            metrics=tuple(SearchProviderMetric.from_dict(dict(item)) for item in payload.get("metrics") or ()),
        )


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    url: str
    text: str
    title: str = ""
    fetched_at: str = ""
    status_code: int | None = None
    provider: str = ""
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "url": self.url,
            "text": self.text,
            "title": self.title,
            "fetched_at": self.fetched_at,
            "status_code": self.status_code,
            "provider": self.provider,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> FetchedDocument:
        return cls(
            url=str(payload.get("url", "")),
            text=str(payload.get("text", "") or ""),
            title=str(payload.get("title", "") or ""),
            fetched_at=str(payload.get("fetched_at", "") or ""),
            status_code=_coerce_optional_int(payload.get("status_code")),
            provider=str(payload.get("provider", "") or ""),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class EvidenceChunk:
    text: str
    source_url: str
    source_title: str = ""
    relevance: float = 0.0
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "text": self.text,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "relevance": self.relevance,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> EvidenceChunk:
        return cls(
            text=str(payload.get("text", "") or ""),
            source_url=str(payload.get("source_url", "") or ""),
            source_title=str(payload.get("source_title", "") or ""),
            relevance=_coerce_float(payload.get("relevance"), default=0.0),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class EvidencePackageSource:
    url: str
    title: str = ""
    host: str = ""
    source_type: str = "Unknown"
    fetched: bool = False
    fetched_at: str = ""
    stale: bool = False

    def to_dict(self) -> JsonDict:
        return {
            "url": self.url,
            "title": self.title,
            "host": self.host,
            "source_type": self.source_type,
            "fetched": self.fetched,
            "fetched_at": self.fetched_at,
            "stale": self.stale,
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> EvidencePackageSource:
        return cls(
            url=str(payload.get("url", "") or ""),
            title=str(payload.get("title", "") or ""),
            host=str(payload.get("host", "") or ""),
            source_type=str(payload.get("source_type", "Unknown") or "Unknown"),
            fetched=bool(payload.get("fetched", False)),
            fetched_at=str(payload.get("fetched_at", "") or ""),
            stale=bool(payload.get("stale", False)),
        )


@dataclass(frozen=True, slots=True)
class EvidencePackage:
    chunks: tuple[EvidenceChunk, ...] = ()
    documents: tuple[FetchedDocument, ...] = ()
    sources: tuple[EvidencePackageSource, ...] = ()
    freshness: str = "unknown"
    confidence: float = 0.0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> JsonDict:
        return {
            "chunks": [item.to_dict() for item in self.chunks],
            "documents": [item.to_dict() for item in self.documents],
            "sources": [item.to_dict() for item in self.sources],
            "freshness": self.freshness,
            "confidence": self.confidence,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> EvidencePackage:
        return cls(
            chunks=tuple(EvidenceChunk.from_dict(dict(item)) for item in payload.get("chunks") or ()),
            documents=tuple(FetchedDocument.from_dict(dict(item)) for item in payload.get("documents") or ()),
            sources=tuple(EvidencePackageSource.from_dict(dict(item)) for item in payload.get("sources") or ()),
            freshness=str(payload.get("freshness", "unknown") or "unknown"),
            confidence=_coerce_confidence(payload.get("confidence"), default=0.0),
            warnings=_coerce_str_tuple(payload.get("warnings")),
        )


@dataclass(frozen=True, slots=True)
class CurrentInfoAnswer:
    status: str
    answer_text: str = ""
    request: CurrentInfoRequest | None = None
    task: TaskSpec | None = None
    query_plan: QueryPlan | None = None
    search_bundle: SearchBundle | None = None
    evidence: EvidencePackage | None = None
    sources: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    confidence: float = 0.0
    metadata: JsonDict = field(default_factory=dict)

    @property
    def answered(self) -> bool:
        return self.status == "answered" and bool(self.answer_text.strip())

    def to_dict(self) -> JsonDict:
        return {
            "status": self.status,
            "answer_text": self.answer_text,
            "confidence": self.confidence,
            "request": self.request.to_dict() if self.request is not None else None,
            "task": self.task.to_dict() if self.task is not None else None,
            "query_plan": self.query_plan.to_dict() if self.query_plan is not None else None,
            "search_bundle": self.search_bundle.to_dict() if self.search_bundle is not None else None,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
            "sources": list(self.sources),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: JsonDict) -> CurrentInfoAnswer:
        request_payload = payload.get("request")
        task_payload = payload.get("task")
        plan_payload = payload.get("query_plan")
        bundle_payload = payload.get("search_bundle")
        evidence_payload = payload.get("evidence")
        return cls(
            status=str(payload.get("status", "")),
            answer_text=str(payload.get("answer_text", "") or ""),
            confidence=_coerce_confidence(payload.get("confidence"), default=0.0),
            request=CurrentInfoRequest.from_dict(dict(request_payload)) if request_payload else None,
            task=TaskSpec.from_dict(dict(task_payload)) if task_payload else None,
            query_plan=QueryPlan.from_dict(dict(plan_payload)) if plan_payload else None,
            search_bundle=SearchBundle.from_dict(dict(bundle_payload)) if bundle_payload else None,
            evidence=EvidencePackage.from_dict(dict(evidence_payload)) if evidence_payload else None,
            sources=_coerce_str_tuple(payload.get("sources")),
            warnings=_coerce_str_tuple(payload.get("warnings")),
            metadata=dict(payload.get("metadata") or {}),
        )


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value if item is not None)


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 1)


def _coerce_non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_confidence(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(parsed, 1.0))


def _host_from_url(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower().rstrip(".")
