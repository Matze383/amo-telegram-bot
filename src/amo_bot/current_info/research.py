from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

from amo_bot.ai.current_time_context import DEFAULT_AI_PROMPT_TIMEZONE, build_current_time_context
from amo_bot.current_info.models import (
    CurrentInfoAnswer,
    CurrentInfoRequest,
    EvidenceChunk,
    EvidencePackage,
    EvidencePackageSource,
    QueryPlan,
    SearchBundle,
    SearchResult,
    TaskSpec,
)
from amo_bot.current_info.observability import log_current_info_event, safe_error_message
from amo_bot.current_info.vector import EmbeddingProvider, build_embedding_provider_from_settings
from amo_bot.evidence_intents import is_stock_listing_status_query


logger = logging.getLogger(__name__)


class ResearchProviderError(RuntimeError):
    pass


class ResearchProviderUnavailable(ResearchProviderError):
    pass


class CurrentInfoResearchProvider(Protocol):
    def answer(self, *, request: CurrentInfoRequest, task: TaskSpec, query_plan: QueryPlan) -> CurrentInfoAnswer:
        ...


@dataclass(frozen=True, slots=True)
class ResearchModelConfig:
    provider: str
    fast_llm: str
    smart_llm: str
    strategic_llm: str


@dataclass(frozen=True, slots=True)
class GptResearcherProviderConfig:
    enabled: bool
    model_config: ResearchModelConfig
    searxng_url: str
    timeout_seconds: float
    max_sources: int
    max_context_chars: int
    deep_breadth: int
    deep_depth: int
    deep_concurrency: int
    report_words: int
    vector_collection: str
    ollama_base_url: str
    ollama_num_ctx: int | None = None
    embedding: str = "openai:text-embedding-3-small"


class AmoLangChainEmbeddings:
    """LangChain-compatible embedding adapter backed by AMO's EmbeddingProvider."""

    def __init__(self, embedding_provider: EmbeddingProvider) -> None:
        self._embedding_provider = embedding_provider

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self._embedding_provider.embed_texts(tuple(texts))
        return [list(vector) for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        vectors = self._embedding_provider.embed_texts((text,))
        if not vectors:
            raise ResearchProviderError("embedding provider returned no query vector")
        return list(vectors[0])

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_documents, texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)


class GptResearcherProvider:
    def __init__(
        self,
        *,
        config: GptResearcherProviderConfig,
        embedding_provider: EmbeddingProvider,
        database_url: str = "",
        researcher_cls: Any | None = None,
        vector_store: Any | None = None,
    ) -> None:
        self._config = config
        self._embedding_provider = embedding_provider
        self._database_url = database_url
        self._researcher_cls = researcher_cls
        self._vector_store = vector_store

    def answer(self, *, request: CurrentInfoRequest, task: TaskSpec, query_plan: QueryPlan) -> CurrentInfoAnswer:
        report_type = _research_report_type(request)
        report_metadata = _research_report_metadata(report_type=report_type, config=self._config)
        if not self._config.enabled:
            return _provider_unavailable_answer(
                request=request,
                task=task,
                query_plan=query_plan,
                warning="gpt_researcher_disabled",
                metadata=report_metadata,
            )
        if not task.query.strip():
            return _provider_unavailable_answer(
                request=request,
                task=task,
                query_plan=query_plan,
                warning="empty_query",
                status="invalid_request",
                metadata=report_metadata,
            )
        self._log_lifecycle(
            event="current_info.GptResearcherConfigured",
            stage="configured",
            request=request,
            task=task,
            outcome="configured",
            extra={
                **report_metadata,
                "timeout_seconds": self._config.timeout_seconds,
                "max_sources": self._config.max_sources,
                "report_words": self._config.report_words,
            },
        )
        try:
            result = asyncio.run(
                asyncio.wait_for(
                    self._answer_async(request=request, task=task, report_type=report_type),
                    timeout=self._config.timeout_seconds,
                )
            )
        except TimeoutError:
            self._log_lifecycle(
                event="current_info.GptResearcherTimeout",
                stage="answer",
                request=request,
                task=task,
                outcome="timeout",
                reason_code="gpt_researcher_timeout",
                level=logging.WARNING,
                extra={**report_metadata, "timeout_seconds": self._config.timeout_seconds},
            )
            return _provider_unavailable_answer(
                request=request,
                task=task,
                query_plan=query_plan,
                warning="gpt_researcher_timeout",
                metadata=report_metadata,
            )
        except Exception as exc:
            error_message = safe_error_message(exc)
            remediation = _pgvector_embedding_id_notnull_remediation(exc)
            logger.warning(
                "gpt_researcher_failed: %s: %s",
                exc.__class__.__name__,
                error_message,
            )
            self._log_lifecycle(
                event="current_info.GptResearcherFailed",
                stage="answer",
                request=request,
                task=task,
                outcome="error",
                reason_code="gpt_researcher_failed",
                level=logging.WARNING,
                extra={
                    **report_metadata,
                    "error_class": exc.__class__.__name__,
                    "error_message": error_message,
                    **({"remediation": remediation} if remediation else {}),
                },
            )
            return _provider_unavailable_answer(
                request=request,
                task=task,
                query_plan=query_plan,
                warning="gpt_researcher_failed",
                metadata={
                    **report_metadata,
                    "error_class": exc.__class__.__name__,
                    "error_message": error_message,
                    **({"remediation": remediation} if remediation else {}),
                },
            )
        answer = _research_result_to_answer(
            result,
            request=request,
            task=task,
            query_plan=query_plan,
            max_sources=self._config.max_sources,
            max_context_chars=self._config.max_context_chars,
        )
        self._log_lifecycle(
            event="current_info.GptResearcherEvidenceQuality",
            stage="evidence_quality",
            request=request,
            task=task,
            outcome=answer.status,
            reason_code=",".join(answer.warnings) if answer.warnings else None,
            level=logging.WARNING if answer.status == "unverified_evidence" else logging.INFO,
            extra={
                "source_count": answer.metadata.get("source_count", 0),
                "source_doc_count": answer.metadata.get("source_doc_count", 0),
                "fetched_source_count": answer.metadata.get("fetched_source_count", 0),
                "snippet_only_source_count": answer.metadata.get("snippet_only_source_count", 0),
                "evidence_quality": answer.metadata.get("evidence_quality", "unknown"),
                "confidence": answer.confidence,
                "warnings": answer.warnings,
                "listing_verdict": (answer.metadata.get("listing_verdict") or {}).get("classification")
                if isinstance(answer.metadata.get("listing_verdict"), dict)
                else "",
                "listing_conflict": bool((answer.metadata.get("listing_verdict") or {}).get("conflict"))
                if isinstance(answer.metadata.get("listing_verdict"), dict)
                else False,
            },
        )
        return answer

    async def _answer_async(self, *, request: CurrentInfoRequest, task: TaskSpec, report_type: str) -> dict[str, Any]:
        researcher_cls = self._researcher_cls or _load_gpt_researcher_class()
        vector_store = self._vector_store or _build_pgvector_store(
            database_url=self._database_url,
            collection_name=self._config.vector_collection,
            embedding_provider=self._embedding_provider,
        )
        config_path = _write_temp_config(self._gpt_researcher_config(language=_language_for_locale(task.locale)))
        try:
            with _temporary_env(
                {
                    "SEARX_URL": self._config.searxng_url,
                    "OLLAMA_BASE_URL": self._config.ollama_base_url,
                }
            ):
                researcher = researcher_cls(
                    query=_gpt_researcher_query(request=request, task=task),
                    report_type=report_type,
                    report_source="web",
                    config_path=config_path,
                    vector_store=vector_store,
                )
                self._log_lifecycle(
                    event="current_info.GptResearcherLifecycle",
                    stage="conduct_research",
                    request=request,
                    task=task,
                    outcome="started",
                    extra=_research_report_metadata(report_type=report_type, config=self._config),
                )
                await researcher.conduct_research()
                self._log_lifecycle(
                    event="current_info.GptResearcherLifecycle",
                    stage="conduct_research",
                    request=request,
                    task=task,
                    outcome="completed",
                    extra=_research_report_metadata(report_type=report_type, config=self._config),
                )
                self._log_lifecycle(
                    event="current_info.GptResearcherLifecycle",
                    stage="write_report",
                    request=request,
                    task=task,
                    outcome="started",
                    extra=_research_report_metadata(report_type=report_type, config=self._config),
                )
                report = await researcher.write_report()
                self._log_lifecycle(
                    event="current_info.GptResearcherLifecycle",
                    stage="write_report",
                    request=request,
                    task=task,
                    outcome="completed",
                    extra=_research_report_metadata(report_type=report_type, config=self._config),
                )
                sources = _call_optional(researcher, "get_source_urls") or ()
                source_docs = _call_optional(researcher, "get_research_sources") or ()
                context = _call_optional(researcher, "get_research_context") or ""
                costs = _call_optional(researcher, "get_costs") or {}
        finally:
            try:
                os.unlink(config_path)
            except OSError:
                pass
        return {
            "report": str(report or ""),
            "sources": sources,
            "source_docs": source_docs,
            "context": context,
            "costs": costs if isinstance(costs, dict) else {},
            "report_type": report_type,
            "deep_breadth": self._config.deep_breadth,
            "deep_depth": self._config.deep_depth,
            "deep_concurrency": self._config.deep_concurrency,
        }

    def _gpt_researcher_config(self, *, language: str) -> dict[str, Any]:
        llm_kwargs: dict[str, Any] = {}
        if self._config.ollama_num_ctx:
            llm_kwargs["num_ctx"] = self._config.ollama_num_ctx
        return {
            "RETRIEVER": "searx",
            "REPORT_SOURCE": "web",
            "FAST_LLM": self._config.model_config.fast_llm,
            "SMART_LLM": self._config.model_config.smart_llm,
            "STRATEGIC_LLM": self._config.model_config.strategic_llm,
            "LANGUAGE": language,
            "CURATE_SOURCES": False,
            "MAX_SEARCH_RESULTS_PER_QUERY": self._config.max_sources,
            "TOTAL_WORDS": self._config.report_words,
            "MAX_ITERATIONS": max(self._config.deep_depth, 1),
            "MAX_SUBTOPICS": max(self._config.deep_breadth, 1),
            "DEEP_RESEARCH_BREADTH": self._config.deep_breadth,
            "DEEP_RESEARCH_DEPTH": self._config.deep_depth,
            "DEEP_RESEARCH_CONCURRENCY": self._config.deep_concurrency,
            "LLM_KWARGS": llm_kwargs,
            "EMBEDDING": self._config.embedding,
            "PROMPT_FAMILY": "default",
        }

    def _log_lifecycle(
        self,
        *,
        event: str,
        stage: str,
        request: CurrentInfoRequest,
        task: TaskSpec,
        outcome: str,
        reason_code: str | None = None,
        extra: dict[str, Any] | None = None,
        level: int = logging.INFO,
    ) -> None:
        log_current_info_event(
            logger,
            event=event,
            stage=stage,
            query=task.query,
            chat_id=request.chat_id,
            user_id=request.user_id,
            topic_id=request.topic_id,
            outcome=outcome,
            reason_code=reason_code,
            extra={**self._model_log_fields(), **(extra or {})},
            level=level,
        )

    def _model_log_fields(self) -> dict[str, Any]:
        return {
            "model_provider": self._config.model_config.provider,
            "fast_llm": self._config.model_config.fast_llm,
            "smart_llm": self._config.model_config.smart_llm,
            "strategic_llm": self._config.model_config.strategic_llm,
            "embedding": self._config.embedding,
        }


def build_gpt_researcher_provider_from_settings(
    settings: Any,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    researcher_cls: Any | None = None,
    vector_store: Any | None = None,
) -> GptResearcherProvider | None:
    if not bool(getattr(settings, "amo_gpt_researcher_enabled", False)):
        return None
    config = build_gpt_researcher_provider_config_from_settings(settings)
    embedding_provider = embedding_provider or build_embedding_provider_from_settings(settings)
    return GptResearcherProvider(
        config=config,
        embedding_provider=embedding_provider,
        database_url=str(getattr(settings, "database_url", "") or ""),
        researcher_cls=researcher_cls,
        vector_store=vector_store,
    )


def build_gpt_researcher_provider_config_from_settings(settings: Any) -> GptResearcherProviderConfig:
    return GptResearcherProviderConfig(
        enabled=bool(getattr(settings, "amo_gpt_researcher_enabled", False)),
        model_config=resolve_research_model_config(settings),
        searxng_url=str(getattr(settings, "amo_searxng_url", "") or "").strip().rstrip("/"),
        timeout_seconds=float(getattr(settings, "amo_research_timeout_seconds", 300.0)),
        max_sources=int(getattr(settings, "amo_research_max_sources", 8)),
        max_context_chars=int(getattr(settings, "amo_research_max_context_chars", 12000)),
        deep_breadth=int(getattr(settings, "amo_research_deep_breadth", 3)),
        deep_depth=int(getattr(settings, "amo_research_deep_depth", 2)),
        deep_concurrency=int(getattr(settings, "amo_research_deep_concurrency", 4)),
        report_words=int(getattr(settings, "amo_research_report_words", 900)),
        vector_collection=str(getattr(settings, "amo_research_vector_collection", "amo_gpt_researcher_chunks") or "amo_gpt_researcher_chunks"),
        ollama_base_url=str(getattr(settings, "ollama_base_url", "http://127.0.0.1:11434") or "http://127.0.0.1:11434"),
        ollama_num_ctx=int(getattr(settings, "ollama_thinking_budget_max_prompt_chars", 0) or 0) or None,
        embedding=resolve_research_embedding_config(settings),
    )


def resolve_research_model_config(settings: Any) -> ResearchModelConfig:
    provider = str(getattr(settings, "amo_research_model_provider", "ollama") or "ollama").strip().casefold()
    default_model = str(getattr(settings, "ollama_model", "") or "").strip()
    non_thinking = str(getattr(settings, "ollama_non_thinking_model", "") or "").strip()
    thinking = str(getattr(settings, "ollama_thinking_model", "") or "").strip()
    fast_model = str(getattr(settings, "amo_research_fast_model", "") or "").strip() or non_thinking or default_model
    smart_model = str(getattr(settings, "amo_research_smart_model", "") or "").strip() or default_model
    strategic_model = str(getattr(settings, "amo_research_strategic_model", "") or "").strip() or thinking or default_model
    if not fast_model or not smart_model or not strategic_model:
        raise ValueError("Research models require OLLAMA_MODEL or explicit AMO_RESEARCH_*_MODEL settings")
    return ResearchModelConfig(
        provider=provider,
        fast_llm=_llm_id(provider=provider, model=fast_model),
        smart_llm=_llm_id(provider=provider, model=smart_model),
        strategic_llm=_llm_id(provider=provider, model=strategic_model),
    )


def resolve_research_embedding_config(settings: Any) -> str:
    provider = str(getattr(settings, "amo_vector_embedding_provider", "ollama") or "ollama").strip().casefold()
    model = str(getattr(settings, "amo_vector_embedding_model", "") or "").strip()
    if not provider or not model:
        raise ValueError("Research embeddings require AMO_VECTOR_EMBEDDING_PROVIDER and AMO_VECTOR_EMBEDDING_MODEL")
    return _embedding_id(provider=provider, model=model)


def _embedding_id(*, provider: str, model: str) -> str:
    model = model.strip()
    explicit_provider = model.split(":", 1)[0].casefold() if ":" in model else ""
    if explicit_provider in {
        "openai",
        "azure_openai",
        "cohere",
        "gigachat",
        "google_vertexai",
        "google_genai",
        "fireworks",
        "ollama",
        "together",
        "mistralai",
        "huggingface",
        "nomic",
        "voyageai",
        "dashscope",
        "custom",
        "bedrock",
        "aimlapi",
        "netmind",
        "openrouter",
        "minimax",
    }:
        return model
    return f"{provider}:{model}"


def _llm_id(*, provider: str, model: str) -> str:
    model = model.strip()
    explicit_provider = model.split(":", 1)[0].casefold() if ":" in model else ""
    if explicit_provider in {
        "openai",
        "anthropic",
        "azure_openai",
        "cohere",
        "google_vertexai",
        "google_genai",
        "fireworks",
        "ollama",
        "together",
        "mistralai",
        "huggingface",
        "groq",
        "bedrock",
        "dashscope",
        "xai",
        "deepseek",
        "litellm",
        "gigachat",
        "openrouter",
        "vllm_openai",
        "aimlapi",
        "netmind",
        "forge",
        "avian",
        "minimax",
    }:
        return model
    return f"{provider}:{model}"


def _load_gpt_researcher_class() -> Any:
    try:
        from gpt_researcher import GPTResearcher  # type: ignore
    except Exception as exc:
        raise ResearchProviderUnavailable("gpt-researcher is not installed") from exc
    return GPTResearcher


def _build_pgvector_store(*, database_url: str, collection_name: str, embedding_provider: EmbeddingProvider) -> Any | None:
    if not database_url.strip().startswith(("postgresql://", "postgresql+")):
        return None
    try:
        from langchain_postgres.vectorstores import PGVector  # type: ignore
    except Exception as exc:
        raise ResearchProviderUnavailable("langchain-postgres is not installed") from exc
    return PGVector(
        embeddings=AmoLangChainEmbeddings(embedding_provider),
        collection_name=collection_name,
        connection=_sync_pgvector_connection_url(database_url),
        use_jsonb=True,
        async_mode=False,
    )


def _sync_pgvector_connection_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername == "postgresql+asyncpg":
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def _gpt_researcher_query(*, request: CurrentInfoRequest, task: TaskSpec) -> str:
    return "\n\n".join(
        (
            "Current date/time context for this research run:",
            _current_time_context_for_request(request),
            "Original user research task:",
            task.query,
            (
                "Date handling: Treat all dated claims relative to the current date above. "
                "If evidence says an event was planned, scheduled, expected, or upcoming for a date "
                "before the current date, do not describe it as future or still pending unless current "
                "evidence explicitly confirms that status. Say that the source reports a date that has "
                "already passed and distinguish that from what is established now."
            ),
        )
    )


def _current_time_context_for_request(request: CurrentInfoRequest) -> str:
    metadata = request.metadata or {}
    for key in ("current_time_context_text", "current_time_context"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    now = _parse_request_datetime(metadata.get("now"))
    timezone_name = str(metadata.get("timezone") or DEFAULT_AI_PROMPT_TIMEZONE)
    return build_current_time_context(now=now, timezone_name=timezone_name)


def _parse_request_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _research_report_type(request: CurrentInfoRequest) -> str:
    metadata = dict(request.metadata or {})
    if metadata.get("deep_research") is True:
        return "deep_research"

    candidates = (
        metadata.get("gpt_researcher_report_type"),
        metadata.get("research_report_type"),
        metadata.get("report_type"),
        metadata.get("webresearch_mode"),
        metadata.get("research_mode"),
    )
    for candidate in candidates:
        normalized = str(candidate or "").strip().casefold().replace("-", "_")
        if normalized in {"deep", "deep_research", "deepresearch"}:
            return "deep_research"
        if normalized in {"standard", "research", "research_report", "webresearch"}:
            return "research_report"
    return "research_report"


def _research_report_metadata(*, report_type: str, config: GptResearcherProviderConfig) -> dict[str, Any]:
    return {
        "report_type": report_type,
        "research_report_type": report_type,
        "deep_research": report_type == "deep_research",
        "deep_breadth": config.deep_breadth,
        "deep_depth": config.deep_depth,
        "deep_concurrency": config.deep_concurrency,
    }


def _write_temp_config(config: dict[str, Any]) -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", prefix="amo-gpt-researcher-", delete=False, encoding="utf-8")
    with handle:
        json.dump(config, handle)
    return handle.name


@contextmanager
def _temporary_env(values: dict[str, str]):
    previous: dict[str, str | None] = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value.strip():
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _call_optional(obj: Any, name: str) -> Any:
    method = getattr(obj, name, None)
    if method is None:
        return None
    return method()


def _research_result_to_answer(
    result: dict[str, Any],
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
    query_plan: QueryPlan,
    max_sources: int,
    max_context_chars: int,
) -> CurrentInfoAnswer:
    report = " ".join(str(result.get("report", "") or "").split())
    context = " ".join(_research_context_text(result.get("context", "")).split())[:max_context_chars]
    source_docs = result.get("source_docs", ())
    sources = _extract_source_urls(
        (
            result.get("sources", ()),
            source_docs,
            result.get("context", ()),
        ),
        max_sources=max_sources,
    )
    fetched_source_urls = _fetched_source_urls(source_docs)
    fetched_count = sum(1 for url in sources if url in fetched_source_urls)
    snippet_only_count = max(len(sources) - fetched_count, 0)
    evidence_quality_warnings = _research_evidence_quality_warnings(
        report=report,
        sources=sources,
        fetched_count=fetched_count,
        snippet_only_count=snippet_only_count,
    )
    verdict = _listing_evidence_verdict(
        query=" ".join((request.query if request else "", task.query if task else "")),
        evidence_text="\n".join(part for part in (context, report) if part),
    )
    verdict_warning_codes = _listing_verdict_warning_codes(verdict)
    warning_codes = tuple(dict.fromkeys((*evidence_quality_warnings, *verdict_warning_codes)))
    confidence = _research_confidence(
        has_report=bool(report),
        source_count=len(sources),
        fetched_count=fetched_count,
        snippet_only_count=snippet_only_count,
        verdict=verdict,
    )
    chunks = (
        (
            EvidenceChunk(
                text=context or report[:max_context_chars],
                source_url=sources[0] if sources else "",
                source_title="GPT Researcher context",
                relevance=0.85,
                metadata={
                    "retrieval": "gpt_researcher",
                    "cache": "research_vector_store",
                    "evidence_state": "fetched" if fetched_count else "snippet_only",
                    "confidence": confidence,
                    "warning_codes": warning_codes,
                    "listing_verdict": verdict["classification"],
                    "supports_listed_count": verdict["supports_listed_count"],
                    "supports_private_count": verdict["supports_private_count"],
                    "listing_conflict": verdict["conflict"],
                },
            ),
        )
        if (context or report)
        else ()
    )
    evidence_sources = tuple(
        EvidencePackageSource(
            url=url,
            title=_host_from_url(url) or url,
            host=_host_from_url(url),
            source_type="Unknown",
            source_role="research_source",
            quality_label="gpt_researcher_fetched_source" if url in fetched_source_urls else "snippet_only",
            fetched=url in fetched_source_urls,
            fetched_at=datetime.now(UTC).replace(microsecond=0).isoformat() if url in fetched_source_urls else "",
        )
        for url in sources
    )
    evidence = EvidencePackage(
        chunks=chunks,
        sources=evidence_sources,
        freshness="current" if fetched_count else "snippet_only",
        confidence=confidence,
        warnings=warning_codes if sources and report else ("empty_research_result",),
    )
    search_results = tuple(
        SearchResult(title=_host_from_url(url) or url, url=url, provider="gpt_researcher", rank=index + 1)
        for index, url in enumerate(sources)
    )
    return CurrentInfoAnswer(
        status=_research_answer_status(report=report, sources=sources, warnings=evidence.warnings),
        answer_text=report,
        confidence=evidence.confidence,
        request=request,
        task=task,
        query_plan=query_plan,
        search_bundle=SearchBundle(query_plan=query_plan, results=search_results),
        evidence=evidence,
        sources=sources,
        warnings=evidence.warnings,
        metadata={
            "provider_mode": "gpt_researcher",
            "report_type": str(result.get("report_type") or "research_report"),
            "research_report_type": str(result.get("report_type") or "research_report"),
            "deep_research": str(result.get("report_type") or "").casefold() == "deep_research",
            "deep_breadth": int(result.get("deep_breadth") or 0),
            "deep_depth": int(result.get("deep_depth") or 0),
            "deep_concurrency": int(result.get("deep_concurrency") or 0),
            "source_count": len(sources),
            "source_doc_count": _container_len(source_docs),
            "fetched_source_count": fetched_count,
            "snippet_only_source_count": snippet_only_count,
            "context_chars": len(context),
            "evidence_quality": "fetched" if fetched_count else "snippet_only",
            "listing_verdict": verdict,
            "costs": result.get("costs", {}) if isinstance(result.get("costs"), dict) else {},
        },
    )


def _container_len(value: Any) -> int:
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value)
    if value:
        return 1
    return 0


def _research_answer_status(*, report: str, sources: tuple[str, ...], warnings: tuple[str, ...]) -> str:
    if not report or not sources:
        return "empty_evidence"
    if any(warning in warnings for warning in {"snippet_only_evidence", "source_conflict"}):
        return "unverified_evidence"
    return "answered"


def _research_evidence_quality_warnings(
    *,
    report: str,
    sources: tuple[str, ...],
    fetched_count: int,
    snippet_only_count: int,
) -> tuple[str, ...]:
    if not report or not sources:
        return ("empty_research_result",)
    warnings: list[str] = []
    if fetched_count <= 0 and snippet_only_count > 0:
        warnings.append("snippet_only_evidence")
    elif snippet_only_count > 0:
        warnings.append("unfetched_source_urls")
    return tuple(warnings)


def _research_confidence(
    *,
    has_report: bool,
    source_count: int,
    fetched_count: int,
    snippet_only_count: int,
    verdict: dict[str, Any],
) -> float:
    if not has_report or source_count <= 0:
        return 0.0
    confidence = 0.78
    if fetched_count <= 0 and snippet_only_count > 0:
        confidence = min(confidence, 0.42)
    elif snippet_only_count > 0:
        confidence = min(confidence, 0.62)
    if bool(verdict.get("conflict")):
        confidence = min(confidence, 0.35)
    return confidence


def _fetched_source_urls(source_docs: Any) -> frozenset[str]:
    urls: list[str] = []
    seen_containers: set[int] = set()

    def walk(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            return
        if isinstance(item, Mapping):
            object_id = id(item)
            if object_id in seen_containers:
                return
            seen_containers.add(object_id)
            if _looks_like_fetched_document(item):
                urls.extend(_extract_fetched_document_source_urls(item))
            for nested in item.values():
                walk(nested)
            return
        if isinstance(item, (list, tuple, set, frozenset)):
            object_id = id(item)
            if object_id in seen_containers:
                return
            seen_containers.add(object_id)
            for nested in item:
                walk(nested)
            return
        if _looks_like_fetched_document(item):
            urls.extend(_extract_fetched_document_source_urls(item))

    walk(source_docs)
    return frozenset(urls)


def _looks_like_fetched_document(item: Any) -> bool:
    if isinstance(item, Mapping):
        if any(str(item.get(key) or "").strip() for key in ("raw_content", "page_content", "document", "html")):
            return True
        metadata = item.get("metadata")
        if isinstance(metadata, Mapping) and any(
            str(metadata.get(key) or "").strip().casefold() in {"scraped", "fetched", "browser", "webpage", "web_page"}
            for key in ("source_state", "retrieval_state", "content_state", "source_kind")
        ):
            return True
        return bool(item.get("fetched") is True or item.get("scraped") is True)
    return any(
        str(getattr(item, attr, "") or "").strip()
        for attr in ("raw_content", "page_content", "document", "html")
    )


_LISTING_PUBLIC_RE = re.compile(
    r"\b(?:"
    r"publicly\s+(?:listed|traded)|listed\s+(?:on|at)|trading\s+(?:on|under)|"
    r"shares?\s+(?:trade|trading)|stock\s+(?:trades|trading)|ipo\s+(?:completed|closed|priced)|"
    r"(?:ticker|symbol)\s+(?:is|:|under|[A-Z0-9][A-Z0-9.-]{1,9}\b)|isin|wkn|nasdaq|nyse|stock\s+exchange|"
    r"börsennotiert|boersennotiert|aktie\s+(?:ist\s+)?handelbar|an\s+der\s+börse|an\s+der\s+boerse"
    r")\b",
    re.IGNORECASE,
)
_LISTING_PRIVATE_RE = re.compile(
    r"\b(?:"
    r"private\s+(?:company|firm)|privately\s+held|not\s+(?:publicly\s+)?(?:listed|traded)|"
    r"no\s+(?:public\s+)?(?:stock|ticker|shares?)|has\s+not\s+(?:gone\s+public|listed)|"
    r"nicht\s+(?:börsennotiert|boersennotiert|öffentlich\s+gelistet|oeffentlich\s+gelistet)|"
    r"kein(?:e|en|er)?\s+(?:aktie|ticker|börsengang|boersengang)|privat\s+(?:gehalten|finanziert)"
    r")\b",
    re.IGNORECASE,
)


def _listing_evidence_verdict(*, query: str, evidence_text: str) -> dict[str, Any]:
    if not is_stock_listing_status_query(query):
        return {
            "classification": "not_applicable",
            "conflict": False,
            "supports_listed_count": 0,
            "supports_private_count": 0,
        }
    public_count = len(_LISTING_PUBLIC_RE.findall(evidence_text or ""))
    private_count = len(_LISTING_PRIVATE_RE.findall(evidence_text or ""))
    conflict = public_count > 0 and private_count > 0
    if conflict:
        classification = "conflicting"
    elif public_count > 0:
        classification = "supports_listed"
    elif private_count > 0:
        classification = "supports_private"
    else:
        classification = "unclear"
    return {
        "classification": classification,
        "conflict": conflict,
        "supports_listed_count": public_count,
        "supports_private_count": private_count,
        "summary": _listing_verdict_summary(
            classification=classification,
            public_count=public_count,
            private_count=private_count,
        ),
    }


def _listing_verdict_warning_codes(verdict: dict[str, Any]) -> tuple[str, ...]:
    if verdict.get("classification") == "not_applicable":
        return ()
    if verdict.get("conflict"):
        return ("source_conflict", "listing_evidence_conflict")
    if verdict.get("classification") == "unclear":
        return ("listing_evidence_unclear",)
    return ()


def _listing_verdict_summary(*, classification: str, public_count: int, private_count: int) -> str:
    if classification == "conflicting":
        return (
            "Listing evidence is conflicting: checked text contains strong public/listed/trading indicators "
            f"({public_count}) and strong private/not-traded indicators ({private_count})."
        )
    if classification == "supports_listed":
        return f"Listing evidence contains public/listed/trading indicators ({public_count})."
    if classification == "supports_private":
        return f"Listing evidence contains private/not-publicly-traded indicators ({private_count})."
    if classification == "unclear":
        return "Listing evidence does not contain a strong deterministic listing verdict."
    return "No listing verdict required."


def _pgvector_embedding_id_notnull_remediation(exc: BaseException) -> str:
    text = " ".join(
        str(part)
        for part in (
            exc.__class__.__name__,
            exc,
            getattr(exc, "__cause__", ""),
            getattr(exc, "__context__", ""),
        )
    )
    lowered = text.casefold()
    if "langchain_pg_embedding" not in lowered or "null value in column" not in lowered or '"id"' not in lowered:
        return ""
    return (
        "Existing langchain-postgres table langchain_pg_embedding rejects inserts without an id. "
        "Run an ops migration on the GPT-Researcher database to make langchain_pg_embedding.id compatible "
        "with the installed langchain-postgres schema, e.g. recreate the langchain_pg_* tables for the research "
        "collection or add a database-side default id generator matching the column type after taking a backup."
    )


_SOURCE_URL_KEYS = frozenset({"source", "url", "link", "href", "source_url"})
_FETCHED_DOCUMENT_SOURCE_URL_KEYS = frozenset({"source", "url", "source_url"})


def _extract_fetched_document_source_urls(value: Any) -> tuple[str, ...]:
    urls: list[str] = []
    seen_urls: set[str] = set()

    def add(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return
        url = candidate.strip()
        if not _is_http_url(url) or url in seen_urls:
            return
        seen_urls.add(url)
        urls.append(url)

    if isinstance(value, Mapping):
        for key in _FETCHED_DOCUMENT_SOURCE_URL_KEYS:
            add(value.get(key))
        metadata = value.get("metadata")
        if isinstance(metadata, Mapping):
            for key in _FETCHED_DOCUMENT_SOURCE_URL_KEYS:
                add(metadata.get(key))
        return tuple(urls)

    for attr in _FETCHED_DOCUMENT_SOURCE_URL_KEYS:
        if hasattr(value, attr):
            add(getattr(value, attr))
    metadata = getattr(value, "metadata", None)
    if isinstance(metadata, Mapping):
        for key in _FETCHED_DOCUMENT_SOURCE_URL_KEYS:
            add(metadata.get(key))
    return tuple(urls)


def _extract_source_urls(value: Any, *, max_sources: int | None = None) -> tuple[str, ...]:
    urls: list[str] = []
    seen_urls: set[str] = set()
    seen_containers: set[int] = set()

    def add(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return
        url = candidate.strip()
        if not _is_http_url(url) or url in seen_urls:
            return
        seen_urls.add(url)
        urls.append(url)

    def walk_source_fields(item: Mapping[Any, Any]) -> None:
        for key, nested in item.items():
            if str(key).casefold() in _SOURCE_URL_KEYS:
                if isinstance(nested, str):
                    add(nested)
                else:
                    walk(nested)

    def walk(item: Any) -> None:
        if max_sources is not None and len(urls) >= max_sources:
            return
        if item is None:
            return
        if isinstance(item, str):
            add(item)
            return
        if isinstance(item, Mapping):
            object_id = id(item)
            if object_id in seen_containers:
                return
            seen_containers.add(object_id)
            walk_source_fields(item)
            for key, nested in item.items():
                if str(key).casefold() == "metadata" and isinstance(nested, Mapping):
                    walk_source_fields(nested)
                else:
                    walk(nested)
            return
        if isinstance(item, (list, tuple, set, frozenset)):
            object_id = id(item)
            if object_id in seen_containers:
                return
            seen_containers.add(object_id)
            for nested in item:
                walk(nested)
            return
        for attr in _SOURCE_URL_KEYS:
            if hasattr(item, attr):
                add(getattr(item, attr))
        metadata = getattr(item, "metadata", None)
        if isinstance(metadata, Mapping):
            walk_source_fields(metadata)

    walk(value)
    return tuple(urls)


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _research_context_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        parts = [_research_context_text(nested) for nested in value.values()]
        return " ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set, frozenset)):
        parts = [_research_context_text(nested) for nested in value]
        return " ".join(part for part in parts if part)
    return str(value)


def _provider_unavailable_answer(
    *,
    request: CurrentInfoRequest,
    task: TaskSpec,
    query_plan: QueryPlan,
    warning: str,
    status: str = "provider_unavailable",
    metadata: dict[str, Any] | None = None,
) -> CurrentInfoAnswer:
    return CurrentInfoAnswer(
        status=status,
        request=request,
        task=task,
        query_plan=query_plan,
        warnings=(warning,),
        metadata={"provider_mode": "gpt_researcher", **(metadata or {})},
    )


def _host_from_url(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").removeprefix("www.")
    except Exception:
        return ""


def _language_for_locale(locale: str) -> str:
    return "german" if (locale or "").casefold().startswith("de") else "english"
