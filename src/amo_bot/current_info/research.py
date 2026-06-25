from __future__ import annotations

import asyncio
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

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
from amo_bot.current_info.vector import EmbeddingProvider, build_embedding_provider_from_settings


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
        if not self._config.enabled:
            return _provider_unavailable_answer(
                request=request,
                task=task,
                query_plan=query_plan,
                warning="gpt_researcher_disabled",
            )
        if not task.query.strip():
            return _provider_unavailable_answer(
                request=request,
                task=task,
                query_plan=query_plan,
                warning="empty_query",
                status="invalid_request",
            )
        try:
            result = asyncio.run(asyncio.wait_for(self._answer_async(request=request, task=task), timeout=self._config.timeout_seconds))
        except TimeoutError:
            return _provider_unavailable_answer(
                request=request,
                task=task,
                query_plan=query_plan,
                warning="gpt_researcher_timeout",
            )
        except Exception as exc:
            return _provider_unavailable_answer(
                request=request,
                task=task,
                query_plan=query_plan,
                warning="gpt_researcher_failed",
                metadata={"error_class": exc.__class__.__name__},
            )
        return _research_result_to_answer(
            result,
            request=request,
            task=task,
            query_plan=query_plan,
            max_sources=self._config.max_sources,
            max_context_chars=self._config.max_context_chars,
        )

    async def _answer_async(self, *, request: CurrentInfoRequest, task: TaskSpec) -> dict[str, Any]:
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
                    query=task.query,
                    report_type="research_report",
                    report_source="web",
                    config_path=config_path,
                    vector_store=vector_store,
                )
                await researcher.conduct_research()
                report = await researcher.write_report()
                sources = _call_optional(researcher, "get_source_urls") or ()
                context = _call_optional(researcher, "get_research_context") or ""
                costs = _call_optional(researcher, "get_costs") or {}
                source_docs = _call_optional(researcher, "get_research_sources") or ()
        finally:
            try:
                os.unlink(config_path)
            except OSError:
                pass
        return {
            "report": str(report or ""),
            "sources": tuple(str(item) for item in sources if str(item).strip()),
            "context": str(context or ""),
            "costs": costs if isinstance(costs, dict) else {},
            "source_docs": source_docs,
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
            "LLM_KWARGS": json.dumps(llm_kwargs) if llm_kwargs else "{}",
            "EMBEDDING": "custom",
            "PROMPT_FAMILY": "default",
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
        timeout_seconds=float(getattr(settings, "amo_research_timeout_seconds", 120.0)),
        max_sources=int(getattr(settings, "amo_research_max_sources", 8)),
        max_context_chars=int(getattr(settings, "amo_research_max_context_chars", 12000)),
        deep_breadth=int(getattr(settings, "amo_research_deep_breadth", 3)),
        deep_depth=int(getattr(settings, "amo_research_deep_depth", 2)),
        deep_concurrency=int(getattr(settings, "amo_research_deep_concurrency", 4)),
        report_words=int(getattr(settings, "amo_research_report_words", 900)),
        vector_collection=str(getattr(settings, "amo_research_vector_collection", "amo_gpt_researcher_chunks") or "amo_gpt_researcher_chunks"),
        ollama_base_url=str(getattr(settings, "ollama_base_url", "http://127.0.0.1:11434") or "http://127.0.0.1:11434"),
        ollama_num_ctx=int(getattr(settings, "ollama_thinking_budget_max_prompt_chars", 0) or 0) or None,
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


def _llm_id(*, provider: str, model: str) -> str:
    model = model.strip()
    if ":" in model:
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
        connection=database_url,
        use_jsonb=True,
        async_mode=True,
    )


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
    context = " ".join(str(result.get("context", "") or "").split())[:max_context_chars]
    sources = tuple(dict.fromkeys(str(item).strip() for item in result.get("sources", ()) if str(item).strip()))[:max_sources]
    chunks = (
        (
            EvidenceChunk(
                text=context or report[:max_context_chars],
                source_url=sources[0] if sources else "",
                source_title="GPT Researcher context",
                relevance=0.85,
                metadata={"retrieval": "gpt_researcher", "cache": "research_vector_store"},
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
            quality_label="gpt_researcher_source",
            fetched=True,
            fetched_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        )
        for url in sources
    )
    evidence = EvidencePackage(
        chunks=chunks,
        sources=evidence_sources,
        freshness="current",
        confidence=0.78 if sources and report else 0.0,
        warnings=() if sources and report else ("empty_research_result",),
    )
    search_results = tuple(
        SearchResult(title=_host_from_url(url) or url, url=url, provider="gpt_researcher", rank=index + 1)
        for index, url in enumerate(sources)
    )
    return CurrentInfoAnswer(
        status="answered" if report and sources else "empty_evidence",
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
            "source_count": len(sources),
            "costs": result.get("costs", {}) if isinstance(result.get("costs"), dict) else {},
        },
    )


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
