from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import os
import re
import tempfile
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

from amo_bot.ai.current_time_context import DEFAULT_AI_PROMPT_TIMEZONE, build_current_time_context
from amo_bot.core.logging import get_request_id
from amo_bot.current_info.models import (
    CurrentInfoAnswer,
    CurrentInfoRequest,
    EvidenceChunk,
    EvidencePackage,
    EvidencePackageSource,
    FetchedDocument,
    QueryPlan,
    SearchBundle,
    SearchResult,
    TaskSpec,
)
from amo_bot.current_info.observability import log_current_info_event, query_hash, safe_error_message
from amo_bot.current_info.ports import CurrentInfoFetchProvider
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


class _NonEmptyVectorStore:
    """Protect langchain-postgres PGVector from empty batch inserts."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def add_documents(self, documents: Any, **kwargs: Any) -> list[str]:
        documents_ = list(documents) if documents is not None else []
        if not documents_:
            logger.info("gpt_researcher_vectorstore_empty_add_documents_skipped")
            return []
        return self._wrapped.add_documents(documents_, **kwargs)

    def add_texts(self, texts: Any, metadatas: Any = None, ids: Any = None, **kwargs: Any) -> list[str]:
        texts_ = list(texts) if texts is not None else []
        if not texts_:
            logger.info("gpt_researcher_vectorstore_empty_add_texts_skipped")
            return []
        metadatas_ = list(metadatas) if metadatas is not None else None
        ids_ = list(ids) if ids is not None else None
        return self._wrapped.add_texts(texts_, metadatas=metadatas_, ids=ids_, **kwargs)

    def add_embeddings(
        self,
        texts: Any,
        embeddings: Any,
        metadatas: Any = None,
        ids: Any = None,
        **kwargs: Any,
    ) -> list[str]:
        texts_ = list(texts) if texts is not None else []
        embeddings_ = list(embeddings) if embeddings is not None else []
        if not texts_ and not embeddings_:
            logger.info("gpt_researcher_vectorstore_empty_add_embeddings_skipped")
            return []
        if len(texts_) != len(embeddings_):
            raise ResearchProviderError("vectorstore embedding batch size does not match text batch size")
        metadatas_ = list(metadatas) if metadatas is not None else None
        ids_ = list(ids) if ids is not None else None
        return self._wrapped.add_embeddings(
            texts=texts_,
            embeddings=embeddings_,
            metadatas=metadatas_,
            ids=ids_,
            **kwargs,
        )


class GptResearcherProvider:
    def __init__(
        self,
        *,
        config: GptResearcherProviderConfig,
        embedding_provider: EmbeddingProvider,
        database_url: str = "",
        researcher_cls: Any | None = None,
        vector_store: Any | None = None,
        source_fetcher: CurrentInfoFetchProvider | None = None,
    ) -> None:
        self._config = config
        self._embedding_provider = embedding_provider
        self._database_url = database_url
        self._researcher_cls = researcher_cls
        self._vector_store = vector_store
        self._source_fetcher = source_fetcher

    def answer(self, *, request: CurrentInfoRequest, task: TaskSpec, query_plan: QueryPlan) -> CurrentInfoAnswer:
        report_type = _research_report_type(request)
        report_metadata = _research_report_metadata(report_type=report_type, config=self._config)
        research_run_id = f"gptr-{uuid.uuid4().hex[:12]}"
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
                    self._answer_async(
                        request=request,
                        task=task,
                        report_type=report_type,
                        research_run_id=research_run_id,
                    ),
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
                "research_run_id": research_run_id,
                "source_count": answer.metadata.get("source_count", 0),
                "source_doc_count": answer.metadata.get("source_doc_count", 0),
                "non_empty_source_doc_count": answer.metadata.get("non_empty_source_doc_count", 0),
                "fetched_source_count": answer.metadata.get("fetched_source_count", 0),
                "snippet_only_source_count": answer.metadata.get("snippet_only_source_count", 0),
                "source_urls_present_but_no_nonempty_docs": answer.metadata.get(
                    "source_urls_present_but_no_nonempty_docs", False
                ),
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
        self._log_lifecycle(
            event="current_info.GptResearcherAnswerTransition",
            stage="answer_transition",
            request=request,
            task=task,
            outcome=answer.status,
            reason_code=",".join(answer.warnings) if answer.warnings else None,
            level=logging.WARNING if answer.status != "answered" else logging.INFO,
            extra=_answer_transition_diagnostics(answer=answer, research_run_id=research_run_id),
        )
        return answer

    async def _answer_async(
        self,
        *,
        request: CurrentInfoRequest,
        task: TaskSpec,
        report_type: str,
        research_run_id: str,
    ) -> dict[str, Any]:
        researcher_cls = self._researcher_cls or _load_gpt_researcher_class()
        vector_store = self._vector_store or _build_pgvector_store(
            database_url=self._database_url,
            collection_name=self._config.vector_collection,
            embedding_provider=self._embedding_provider,
        )
        gpt_researcher_config = self._gpt_researcher_config(language=_language_for_locale(task.locale))
        config_path = _write_temp_config(gpt_researcher_config)
        try:
            emit_searx_debug = _research_debug_emitter(
                provider=self,
                event="current_info.GptResearcherSearxAdapter",
                request=request,
                task=task,
                research_run_id=research_run_id,
            )
            emit_browser_debug = _research_debug_emitter(
                provider=self,
                event="current_info.GptResearcherBrowserActivity",
                request=request,
                task=task,
                research_run_id=research_run_id,
            )
            with _temporary_env(
                {
                    "SEARX_URL": self._config.searxng_url,
                    "OLLAMA_BASE_URL": self._config.ollama_base_url,
                }
            ), _temporary_gpt_researcher_searx_snippet_adapter(
                emit=emit_searx_debug
            ), _temporary_gpt_researcher_browser_activity_probe(emit=emit_browser_debug):
                researcher_query = _gpt_researcher_query(request=request, task=task)
                self._log_lifecycle(
                    event="current_info.GptResearcherInput",
                    stage="input",
                    request=request,
                    task=task,
                    outcome="prepared",
                    extra={
                        "research_run_id": research_run_id,
                        **_research_report_metadata(report_type=report_type, config=self._config),
                        **_gpt_researcher_config_diagnostics(gpt_researcher_config),
                        **_research_query_diagnostics(
                            user_task=task.query,
                            researcher_query=researcher_query,
                            current_time_context=_current_time_context_for_request(request),
                        ),
                    },
                )
                researcher = researcher_cls(
                    query=researcher_query,
                    report_type=report_type,
                    report_source="web",
                    config_path=config_path,
                    vector_store=vector_store,
                )
                restore_researcher_browser_probe = _install_researcher_browser_activity_probe(
                    researcher,
                    emit=emit_browser_debug,
                )
                self._log_lifecycle(
                    event="current_info.GptResearcherDiagnostics",
                    stage="runtime_config",
                    request=request,
                    task=task,
                    outcome="collected",
                    extra={
                        "research_run_id": research_run_id,
                        **_gpt_researcher_runtime_config_diagnostics(
                            researcher=researcher,
                            config=gpt_researcher_config,
                        ),
                    },
                )
                self._log_lifecycle(
                    event="current_info.GptResearcherDiagnostics",
                    stage="researcher_state",
                    request=request,
                    task=task,
                    outcome="before_conduct_research",
                    extra={
                        "research_run_id": research_run_id,
                        **_researcher_state_diagnostics(researcher),
                    },
                )
                self._log_lifecycle(
                    event="current_info.GptResearcherLifecycle",
                    stage="conduct_research",
                    request=request,
                    task=task,
                    outcome="started",
                    extra={
                        "research_run_id": research_run_id,
                        **_research_report_metadata(report_type=report_type, config=self._config),
                    },
                )
                try:
                    await researcher.conduct_research()
                finally:
                    restore_researcher_browser_probe()
                conduct_sources = _call_optional(researcher, "get_source_urls") or ()
                conduct_source_docs = _call_optional(researcher, "get_research_sources") or ()
                self._log_lifecycle(
                    event="current_info.GptResearcherLifecycle",
                    stage="conduct_research",
                    request=request,
                    task=task,
                    outcome="completed",
                    extra={
                        "research_run_id": research_run_id,
                        **_research_report_metadata(report_type=report_type, config=self._config),
                        **_source_url_diagnostics(conduct_sources, prefix="post_conduct"),
                        **_source_doc_diagnostics(conduct_source_docs, prefix="post_conduct_source_doc"),
                        **_researcher_state_diagnostics(researcher),
                    },
                )
                self._log_lifecycle(
                    event="current_info.GptResearcherLifecycle",
                    stage="write_report",
                    request=request,
                    task=task,
                    outcome="started",
                    extra={
                        "research_run_id": research_run_id,
                        **_research_report_metadata(report_type=report_type, config=self._config),
                    },
                )
                report = await researcher.write_report()
                write_source_docs = _call_optional(researcher, "get_research_sources") or conduct_source_docs
                self._log_lifecycle(
                    event="current_info.GptResearcherLifecycle",
                    stage="write_report",
                    request=request,
                    task=task,
                    outcome="completed",
                    extra={
                        "research_run_id": research_run_id,
                        **_research_report_metadata(report_type=report_type, config=self._config),
                        **_source_doc_diagnostics(write_source_docs, prefix="post_write_source_doc"),
                        **_researcher_state_diagnostics(researcher),
                    },
                )
                sources = _call_optional(researcher, "get_source_urls") or conduct_sources
                self._log_lifecycle(
                    event="current_info.GptResearcherDiagnostics",
                    stage="source_urls",
                    request=request,
                    task=task,
                    outcome="collected",
                    extra={"research_run_id": research_run_id, **_source_url_diagnostics(sources)},
                )
                source_docs = write_source_docs
                self._log_lifecycle(
                    event="current_info.GptResearcherDiagnostics",
                    stage="source_docs",
                    request=request,
                    task=task,
                    outcome="collected",
                    extra={"research_run_id": research_run_id, **_source_doc_diagnostics(source_docs)},
                )
                source_docs, source_validation_diagnostics = await self._validate_source_urls(
                    sources=sources,
                    source_docs=source_docs,
                    request=request,
                    task=task,
                )
                self._log_lifecycle(
                    event="current_info.GptResearcherDiagnostics",
                    stage="source_validation",
                    request=request,
                    task=task,
                    outcome="checked",
                    reason_code=str(source_validation_diagnostics.get("reason_code") or "") or None,
                    level=logging.WARNING if source_validation_diagnostics.get("reason_code") else logging.INFO,
                    extra={"research_run_id": research_run_id, **source_validation_diagnostics},
                )
                context = _call_optional(researcher, "get_research_context") or ""
                self._log_lifecycle(
                    event="current_info.GptResearcherDiagnostics",
                    stage="research_context",
                    request=request,
                    task=task,
                    outcome="collected",
                    extra={"research_run_id": research_run_id, **_research_context_diagnostics(context)},
                )
                self._log_lifecycle(
                    event="current_info.GptResearcherDiagnostics",
                    stage="source_mapping",
                    request=request,
                    task=task,
                    outcome="checked",
                    reason_code=",".join(_source_mapping_warning_codes(sources=sources, source_docs=source_docs))
                    or None,
                    extra={
                        "research_run_id": research_run_id,
                        **_source_mapping_diagnostics(sources=sources, source_docs=source_docs),
                    },
                    level=logging.WARNING
                    if _source_mapping_warning_codes(sources=sources, source_docs=source_docs)
                    else logging.INFO,
                )
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
            "max_sources": self._config.max_sources,
            "max_context_chars": self._config.max_context_chars,
            "research_run_id": research_run_id,
            "report_words": self._config.report_words,
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

    async def _validate_source_urls(
        self,
        *,
        sources: Any,
        source_docs: Any,
        request: CurrentInfoRequest,
        task: TaskSpec,
    ) -> tuple[Any, dict[str, Any]]:
        del request
        source_urls = _extract_source_urls(sources, max_sources=self._config.max_sources)
        if not source_urls:
            return source_docs, {"source_validation": "skipped", "reason_code": "no_source_urls"}
        existing_fetched_urls = _fetched_source_urls(source_docs)
        if existing_fetched_urls:
            return source_docs, {
                "source_validation": "skipped",
                "reason_code": "",
                "source_validation_source_url_count": len(source_urls),
                "source_validation_existing_fetched_count": len(existing_fetched_urls),
            }
        if self._source_fetcher is None:
            return source_docs, {
                "source_validation": "skipped",
                "reason_code": "source_fetcher_unavailable",
                "source_validation_source_url_count": len(source_urls),
            }

        fetched_docs: list[dict[str, Any]] = []
        failed_urls: list[str] = []
        for url in source_urls:
            try:
                document = await asyncio.to_thread(self._source_fetcher.fetch, url=url, locale=task.locale)
            except Exception as exc:
                failed_urls.append(url)
                logger.warning(
                    "gpt_researcher_source_validation_fetch_failed: %s: %s url=%s",
                    exc.__class__.__name__,
                    safe_error_message(exc),
                    _safe_log_url(url),
                )
                continue
            if document is None or not document.text.strip():
                failed_urls.append(url)
                continue
            fetched_docs.append(_fetched_document_to_source_doc(document))

        merged_source_docs = _merge_source_docs(source_docs, tuple(fetched_docs))
        return merged_source_docs, {
            "source_validation": "attempted",
            "reason_code": "" if fetched_docs else "source_validation_empty",
            "source_validation_source_url_count": len(source_urls),
            "source_validation_attempted_count": len(source_urls),
            "source_validation_fetched_count": len(fetched_docs),
            "source_validation_failed_count": len(failed_urls),
            "source_validation_fetched_urls": tuple(
                _safe_log_url(str(item.get("url") or "")) for item in fetched_docs[:10]
            ),
            "source_validation_failed_urls": tuple(_safe_log_url(url) for url in failed_urls[:10]),
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
            "llm_call_visibility": "config_only_gpt_researcher_internal_calls",
            "request_correlation_id": get_request_id() or "",
        }


def _research_debug_emitter(
    *,
    provider: GptResearcherProvider,
    event: str,
    request: CurrentInfoRequest,
    task: TaskSpec,
    research_run_id: str,
) -> Callable[..., None]:
    def emit(
        *,
        stage: str,
        outcome: str,
        extra: dict[str, Any] | None = None,
        reason_code: str | None = None,
        level: int = logging.INFO,
    ) -> None:
        provider._log_lifecycle(
            event=event,
            stage=stage,
            request=request,
            task=task,
            outcome=outcome,
            reason_code=reason_code,
            level=level,
            extra={"research_run_id": research_run_id, **(extra or {})},
        )

    return emit


def build_gpt_researcher_provider_from_settings(
    settings: Any,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    researcher_cls: Any | None = None,
    vector_store: Any | None = None,
    source_fetcher: CurrentInfoFetchProvider | None = None,
) -> GptResearcherProvider | None:
    if not bool(getattr(settings, "amo_gpt_researcher_enabled", False)):
        return None
    config = build_gpt_researcher_provider_config_from_settings(settings)
    embedding_provider = embedding_provider or build_embedding_provider_from_settings(settings)
    if source_fetcher is None:
        from amo_bot.current_info.fetch import build_document_fetcher_from_settings

        source_fetcher = build_document_fetcher_from_settings(settings)
    return GptResearcherProvider(
        config=config,
        embedding_provider=embedding_provider,
        database_url=str(getattr(settings, "database_url", "") or ""),
        researcher_cls=researcher_cls,
        vector_store=vector_store,
        source_fetcher=source_fetcher,
    )


def build_gpt_researcher_provider_config_from_settings(settings: Any) -> GptResearcherProviderConfig:
    return GptResearcherProviderConfig(
        enabled=bool(getattr(settings, "amo_gpt_researcher_enabled", False)),
        model_config=resolve_research_model_config(settings),
        searxng_url=str(getattr(settings, "amo_searxng_url", "") or "").strip().rstrip("/"),
        timeout_seconds=float(getattr(settings, "amo_research_timeout_seconds", 360.0)),
        max_sources=int(getattr(settings, "amo_research_max_sources", 10)),
        max_context_chars=int(getattr(settings, "amo_research_max_context_chars", 16000)),
        deep_breadth=int(getattr(settings, "amo_research_deep_breadth", 3)),
        deep_depth=int(getattr(settings, "amo_research_deep_depth", 2)),
        deep_concurrency=int(getattr(settings, "amo_research_deep_concurrency", 4)),
        report_words=int(getattr(settings, "amo_research_report_words", 1200)),
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


@contextmanager
def _temporary_gpt_researcher_searx_snippet_adapter(*, emit: Callable[..., None] | None = None):
    """Keep SearX snippets out of GPT-Researcher's prefetched-content path."""

    try:
        searx_module = importlib.import_module("gpt_researcher.retrievers.searx.searx")
        searx_search_cls = getattr(searx_module, "SearxSearch", None)
        original_search = getattr(searx_search_cls, "search", None)
    except Exception:
        yield
        return

    if searx_search_cls is None or original_search is None or getattr(original_search, "_amo_snippet_adapter", False):
        yield
        return

    def search_without_prefetched_snippets(self, *args: Any, **kwargs: Any) -> Any:
        if emit:
            emit(stage="searx_search", outcome="called", extra={"searx_search_called": True})
        results = original_search(self, *args, **kwargs)
        normalized = _strip_searx_prefetched_content(results)
        if emit:
            emit(
                stage="searx_search",
                outcome="completed",
                extra=_searx_search_result_diagnostics(results=results, normalized=normalized),
            )
        return normalized

    search_without_prefetched_snippets._amo_snippet_adapter = True  # type: ignore[attr-defined]
    try:
        searx_search_cls.search = search_without_prefetched_snippets
        if emit:
            emit(
                stage="searx_adapter",
                outcome="installed",
                extra={"searx_snippet_adapter_installed": True},
            )
        yield
    finally:
        searx_search_cls.search = original_search


@contextmanager
def _temporary_gpt_researcher_browser_activity_probe(*, emit: Callable[..., None] | None = None):
    restores: list[Callable[[], None]] = []
    for module_name, class_name in (
        ("gpt_researcher.skills.browser", "BrowserManager"),
        ("gpt_researcher.scraper.browser.browser_manager", "BrowserManager"),
        ("gpt_researcher.scraper.browser_manager", "BrowserManager"),
        ("gpt_researcher.scraper.scraper", "BrowserManager"),
    ):
        try:
            module = importlib.import_module(module_name)
            browser_manager_cls = getattr(module, class_name, None)
            original = getattr(browser_manager_cls, "browse_urls", None)
        except Exception:
            continue
        if browser_manager_cls is None or original is None or _has_activity_probe_marker(original):
            continue
        wrapped = _browser_activity_wrapper(original=original, emit=emit, scope="class")
        try:
            browser_manager_cls.browse_urls = wrapped
        except Exception:
            continue
        restores.append(_restore_attr(browser_manager_cls, "browse_urls", original))
        if emit:
            emit(
                stage="browser_probe",
                outcome="installed",
                extra={"browser_probe_scope": "class", "browser_probe_target": f"{module_name}.{class_name}"},
            )
    try:
        yield
    finally:
        for restore in reversed(restores):
            restore()


def _install_researcher_browser_activity_probe(researcher: Any, *, emit: Callable[..., None] | None = None) -> Callable[[], None]:
    manager = getattr(researcher, "scraper_manager", None) or getattr(researcher, "browser_manager", None)
    if manager is None:
        return lambda: None
    original = getattr(manager, "browse_urls", None)
    if original is None or _has_activity_probe_marker(original):
        return lambda: None
    wrapped = _browser_activity_wrapper(original=original, emit=emit, scope="instance")
    try:
        setattr(manager, "browse_urls", wrapped)
    except Exception:
        return lambda: None
    if emit:
        emit(
            stage="browser_probe",
            outcome="installed",
            extra={"browser_probe_scope": "instance", "browser_probe_target": type(manager).__name__},
        )
    return _restore_attr(manager, "browse_urls", original)


def _has_activity_probe_marker(value: Any) -> bool:
    if getattr(value, "_amo_browser_activity_probe", False):
        return True
    wrapped_function = getattr(value, "__func__", None)
    return bool(getattr(wrapped_function, "_amo_browser_activity_probe", False))


def _restore_attr(target: Any, name: str, original: Any) -> Callable[[], None]:
    def restore() -> None:
        try:
            setattr(target, name, original)
        except Exception:
            pass

    return restore


def _browser_activity_wrapper(*, original: Any, emit: Callable[..., None] | None, scope: str) -> Any:
    call_count = 0

    async def browse_urls_with_activity(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        urls = _browse_urls_input(args=args, kwargs=kwargs)
        if emit:
            emit(
                stage="browse_urls",
                outcome="called",
                extra={
                    "browser_probe_scope": scope,
                    "browse_urls_call_count": call_count,
                    **_browse_url_input_diagnostics(urls),
                },
            )
        try:
            result = original(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            if emit:
                emit(
                    stage="browse_urls",
                    outcome="error",
                    reason_code="browse_urls_error",
                    level=logging.WARNING,
                    extra={
                        "browser_probe_scope": scope,
                        "browse_urls_call_count": call_count,
                        "error_class": exc.__class__.__name__,
                        "timeout": isinstance(exc, TimeoutError),
                        **_browse_url_input_diagnostics(urls),
                    },
                )
            raise
        if emit:
            emit(
                stage="browse_urls",
                outcome="completed",
                extra={
                    "browser_probe_scope": scope,
                    "browse_urls_call_count": call_count,
                    **_browse_url_input_diagnostics(urls),
                    **_browse_urls_output_diagnostics(result),
                },
            )
        return result

    browse_urls_with_activity._amo_browser_activity_probe = True  # type: ignore[attr-defined]
    return browse_urls_with_activity


def _browse_urls_input(*, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if "urls" in kwargs:
        return kwargs.get("urls")
    if len(args) >= 2:
        return args[1]
    if args:
        return args[0]
    return ()


def _browse_url_input_diagnostics(urls: Any) -> dict[str, Any]:
    extracted = _extract_source_urls(urls)
    if not extracted and isinstance(urls, (list, tuple, set, frozenset)):
        extracted = tuple(str(item).strip() for item in urls if str(item).strip().startswith(("http://", "https://")))
    return {
        "browse_urls_input_count": _container_len(urls),
        "browse_urls_input_url_count": len(extracted),
        "browse_urls_input_domains": tuple(dict.fromkeys(_host_from_url(url) for url in extracted if _host_from_url(url)))[:10],
        "browse_urls_input_urls": tuple(_safe_log_url(url) for url in extracted[:10]),
        "browse_urls_input_truncated": len(extracted) > 10,
    }


def _browse_urls_output_diagnostics(result: Any) -> dict[str, Any]:
    return {
        "browse_urls_output_doc_count": _container_len(result),
        "browse_urls_output_non_empty_count": _non_empty_source_doc_count(result),
        **_source_doc_diagnostics(result, prefix="browse_urls_output_doc"),
    }


def _searx_search_result_diagnostics(*, results: Any, normalized: Any) -> dict[str, Any]:
    result_items = results if isinstance(results, list) else []
    normalized_items = normalized if isinstance(normalized, list) else []
    urls = _extract_source_urls(result_items)
    raw_or_body_count = sum(
        1
        for item in result_items
        if isinstance(item, Mapping)
        and (
            bool(str(item.get("raw_content") or "").strip())
            or bool(str(item.get("body") or "").strip())
        )
    )
    snippet_count = sum(
        1 for item in normalized_items if isinstance(item, Mapping) and bool(str(item.get("snippet") or "").strip())
    )
    return {
        "searx_raw_result_count": len(result_items),
        "searx_url_result_count": len(urls),
        "searx_raw_content_or_body_present_count": raw_or_body_count,
        "searx_snippet_present_after_strip_count": snippet_count,
        "searx_result_domains": tuple(dict.fromkeys(_host_from_url(url) for url in urls if _host_from_url(url)))[:10],
        "searx_result_urls": tuple(_safe_log_url(url) for url in urls[:10]),
        "searx_result_truncated": len(urls) > 10,
    }


def _strip_searx_prefetched_content(results: Any) -> Any:
    if not isinstance(results, list):
        return results

    normalized: list[Any] = []
    for result in results:
        if not isinstance(result, Mapping):
            normalized.append(result)
            continue

        url = str(result.get("href") or result.get("url") or "").strip()
        if not url:
            normalized.append(dict(result))
            continue

        item = dict(result)
        snippet = str(item.get("raw_content") or item.get("body") or item.get("content") or "").strip()
        item.pop("raw_content", None)
        item.pop("body", None)
        if snippet and "snippet" not in item:
            item["snippet"] = snippet
        normalized.append(item)

    return normalized


def _build_pgvector_store(*, database_url: str, collection_name: str, embedding_provider: EmbeddingProvider) -> Any | None:
    if not database_url.strip().startswith(("postgresql://", "postgresql+")):
        return None
    try:
        from langchain_postgres.vectorstores import PGVector  # type: ignore
    except Exception as exc:
        raise ResearchProviderUnavailable("langchain-postgres is not installed") from exc
    store = PGVector(
        embeddings=AmoLangChainEmbeddings(embedding_provider),
        collection_name=collection_name,
        connection=_sync_pgvector_connection_url(database_url),
        use_jsonb=True,
        async_mode=False,
    )
    return _NonEmptyVectorStore(store)


def _sync_pgvector_connection_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername == "postgresql+asyncpg":
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def _research_query_diagnostics(
    *,
    user_task: str,
    researcher_query: str,
    current_time_context: str,
) -> dict[str, Any]:
    user_task = " ".join((user_task or "").split()).strip()
    researcher_query = str(researcher_query or "")
    current_time_context = str(current_time_context or "")
    return {
        "user_task_hash": query_hash(user_task),
        "user_task_length": len(user_task),
        "researcher_task_hash": query_hash(researcher_query),
        "researcher_task_length": len(researcher_query),
        "task_augmented": researcher_query != user_task,
        "current_time_context_length": len(current_time_context),
        "date_context_present": bool(current_time_context.strip()),
        "task_policy": "user_task_embedded_with_date_context",
    }


def _gpt_researcher_config_diagnostics(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "gpt_researcher_retriever": str(config.get("RETRIEVER") or ""),
        "gpt_researcher_scraper": str(config.get("SCRAPER") or ""),
        "gpt_researcher_report_source": str(config.get("REPORT_SOURCE") or ""),
        "gpt_researcher_max_search_results_per_query": int(config.get("MAX_SEARCH_RESULTS_PER_QUERY") or 0),
    }


def _gpt_researcher_runtime_config_diagnostics(*, researcher: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    cfg = getattr(researcher, "cfg", None)
    report_source = getattr(researcher, "report_source", None) or getattr(cfg, "report_source", None)
    scraper = getattr(cfg, "scraper", None) or getattr(cfg, "scraper_name", None) or config.get("SCRAPER") or ""
    retriever = getattr(cfg, "retriever", None) or getattr(cfg, "retrievers", None) or config.get("RETRIEVER") or ""
    max_results = (
        getattr(cfg, "max_search_results_per_query", None)
        or getattr(cfg, "max_search_results", None)
        or config.get("MAX_SEARCH_RESULTS_PER_QUERY")
        or 0
    )
    return {
        "gpt_researcher_active_retriever": _safe_config_value(retriever),
        "gpt_researcher_active_scraper": _safe_config_value(scraper),
        "gpt_researcher_active_report_source": _safe_config_value(report_source),
        "gpt_researcher_active_max_search_results_per_query": int(max_results or 0),
    }


def _safe_config_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set, frozenset)):
        return ",".join(str(item) for item in value)
    return str(value or "")


def _researcher_state_diagnostics(researcher: Any) -> dict[str, Any]:
    search_results = _first_existing_attr(researcher, ("search_results",), nested_attrs=("research_conductor",))
    new_search_urls = _first_existing_attr(researcher, ("new_search_urls",), nested_attrs=("research_conductor",))
    visited_urls = _first_existing_attr(researcher, ("visited_urls",), nested_attrs=("research_conductor",))
    source_urls = _first_existing_attr(researcher, ("source_urls",), nested_attrs=("research_conductor",))
    return {
        "search_results_count": _container_len(search_results),
        "search_results_url_count": len(_extract_source_urls(search_results)),
        "new_search_urls_count": _container_len(new_search_urls),
        "new_search_urls_url_count": len(_extract_source_urls(new_search_urls)),
        "visited_urls_count": _container_len(visited_urls),
        "visited_urls_url_count": len(_extract_source_urls(visited_urls)),
        "source_urls_attr_count": _container_len(source_urls),
        "source_urls_attr_url_count": len(_extract_source_urls(source_urls)),
    }


def _first_existing_attr(value: Any, attr_names: tuple[str, ...], *, nested_attrs: tuple[str, ...]) -> Any:
    for attr_name in attr_names:
        if hasattr(value, attr_name):
            return getattr(value, attr_name)
    for nested_attr in nested_attrs:
        nested = getattr(value, nested_attr, None)
        if nested is None:
            continue
        for attr_name in attr_names:
            if hasattr(nested, attr_name):
                return getattr(nested, attr_name)
    return ()


def _source_url_diagnostics(value: Any, *, prefix: str = "source") -> dict[str, Any]:
    urls = _extract_source_urls(value)
    safe_urls = tuple(_safe_log_url(url) for url in urls[:10])
    return {
        f"{prefix}_url_count": len(urls),
        f"{prefix}_domains": tuple(dict.fromkeys(_host_from_url(url) for url in urls if _host_from_url(url)))[:10],
        f"{prefix}_urls": safe_urls,
        f"{prefix}_truncated": len(urls) > len(safe_urls),
    }


def _source_doc_diagnostics(source_docs: Any, *, prefix: str = "source_doc") -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    seen_containers: set[int] = set()

    def walk(item: Any) -> None:
        if item is None or isinstance(item, str) or len(summaries) >= 20:
            return
        if isinstance(item, Mapping):
            object_id = id(item)
            if object_id in seen_containers:
                return
            seen_containers.add(object_id)
            if _looks_like_source_doc(item):
                summaries.append(_source_doc_item_summary(item))
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
        if _looks_like_source_doc(item):
            summaries.append(_source_doc_item_summary(item))

    walk(source_docs)
    fetched_urls = sorted(_fetched_source_urls(source_docs))
    all_doc_urls = _extract_source_urls(source_docs)
    content_buckets: dict[str, int] = {}
    shape_counts: dict[str, int] = {}
    fetched_like_count = 0
    non_empty_count = 0
    for summary in summaries:
        bucket = str(summary.get("content_length_bucket") or "unknown")
        content_buckets[bucket] = content_buckets.get(bucket, 0) + 1
        shape = str(summary.get("shape") or "unknown")
        shape_counts[shape] = shape_counts.get(shape, 0) + 1
        if summary.get("fetched_like"):
            fetched_like_count += 1
        if summary.get("content_length_bucket") != "empty":
            non_empty_count += 1
    return {
        f"{prefix}_container_count": _container_len(source_docs),
        f"{prefix}_summary_count": len(summaries),
        f"{prefix}_summary_truncated": len(summaries) >= 20,
        f"{prefix}_shape_counts": shape_counts,
        f"{prefix}_content_length_buckets": content_buckets,
        f"{prefix}_non_empty_count": non_empty_count,
        f"{prefix}_fetched_like_count": fetched_like_count,
        f"{prefix}_url_count": len(all_doc_urls),
        f"{prefix}_domains": tuple(dict.fromkeys(_host_from_url(url) for url in all_doc_urls if _host_from_url(url)))[:10],
        f"{prefix}_urls": tuple(_safe_log_url(url) for url in all_doc_urls[:10]),
        "fetched_source_url_count": len(fetched_urls),
        "fetched_source_domains": tuple(dict.fromkeys(_host_from_url(url) for url in fetched_urls if _host_from_url(url)))[:10],
        "fetched_source_urls": tuple(_safe_log_url(url) for url in fetched_urls[:10]),
    }


def _looks_like_source_doc(item: Any) -> bool:
    if _looks_like_fetched_document(item):
        return True
    return bool(_extract_source_urls(item))


def _source_doc_item_summary(item: Any) -> dict[str, Any]:
    urls = _extract_source_urls(item)
    content_length = _source_doc_content_length(item)
    if isinstance(item, Mapping):
        keys = tuple(sorted(str(key) for key in item.keys()))[:12]
        shape = "mapping"
    else:
        keys = tuple(
            attr
            for attr in ("raw_content", "page_content", "document", "html", "content", "text", "body", "metadata")
            if hasattr(item, attr)
        )
        shape = type(item).__name__
    return {
        "shape": shape,
        "keys": keys,
        "url_count": len(urls),
        "domains": tuple(dict.fromkeys(_host_from_url(url) for url in urls if _host_from_url(url)))[:5],
        "content_length_bucket": _length_bucket(content_length),
        "fetched_like": _looks_like_fetched_document(item),
    }


def _source_doc_content_length(item: Any) -> int:
    fields = ("raw_content", "page_content", "document", "html", "content", "text", "body")
    values: list[str] = []
    if isinstance(item, Mapping):
        for key in fields:
            value = item.get(key)
            if isinstance(value, str):
                values.append(value)
        return max((len(value) for value in values), default=0)
    for attr in fields:
        value = getattr(item, attr, None)
        if isinstance(value, str):
            values.append(value)
    return max((len(value) for value in values), default=0)


def _research_context_diagnostics(context: Any) -> dict[str, Any]:
    context_text = _research_context_text(context)
    urls = _extract_source_urls(context)
    return {
        "context_shape": _value_shape(context),
        "context_chars": len(context_text),
        "context_length_bucket": _length_bucket(len(context_text)),
        "context_empty": not bool(context_text.strip()),
        "context_url_count": len(urls),
        "context_domains": tuple(dict.fromkeys(_host_from_url(url) for url in urls if _host_from_url(url)))[:10],
        "context_urls": tuple(_safe_log_url(url) for url in urls[:10]),
    }


def _source_mapping_warning_codes(*, sources: Any, source_docs: Any) -> tuple[str, ...]:
    source_urls = set(_extract_source_urls(sources))
    fetched_urls = set(_fetched_source_urls(source_docs))
    warnings: list[str] = []
    if source_urls and not fetched_urls:
        warnings.append("no_fetched_source_docs")
    if source_urls - fetched_urls:
        warnings.append("source_urls_without_fetched_docs")
    if fetched_urls - source_urls:
        warnings.append("fetched_docs_not_in_source_urls")
    return tuple(warnings)


def _source_mapping_diagnostics(*, sources: Any, source_docs: Any) -> dict[str, Any]:
    source_urls = set(_extract_source_urls(sources))
    fetched_urls = set(_fetched_source_urls(source_docs))
    unfetched = sorted(source_urls - fetched_urls)
    fetched_without_source = sorted(fetched_urls - source_urls)
    return {
        "source_url_count": len(source_urls),
        "fetched_source_url_count": len(fetched_urls),
        "unfetched_source_url_count": len(unfetched),
        "fetched_without_source_url_count": len(fetched_without_source),
        "unfetched_source_domains": tuple(dict.fromkeys(_host_from_url(url) for url in unfetched if _host_from_url(url)))[:10],
        "fetched_without_source_domains": tuple(
            dict.fromkeys(_host_from_url(url) for url in fetched_without_source if _host_from_url(url))
        )[:10],
        "unfetched_source_urls": tuple(_safe_log_url(url) for url in unfetched[:10]),
        "fetched_without_source_urls": tuple(_safe_log_url(url) for url in fetched_without_source[:10]),
    }


def _safe_log_url(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path or ""
    if len(path) > 160:
        path = f"{path[:157]}..."
    return parsed._replace(params="", query="", fragment="", path=path).geturl()


def _length_bucket(length: int) -> str:
    if length <= 0:
        return "empty"
    if length < 500:
        return "lt_500"
    if length < 2_000:
        return "500_1999"
    if length < 10_000:
        return "2000_9999"
    if length < 50_000:
        return "10000_49999"
    return "gte_50000"


def _value_shape(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, Mapping):
        return "mapping"
    if isinstance(value, (list, tuple, set, frozenset)):
        return type(value).__name__
    return type(value).__name__


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
    non_empty_source_doc_count = _non_empty_source_doc_count(source_docs)
    snippet_only_count = max(len(sources) - fetched_count, 0)
    evidence_quality_warnings = _research_evidence_quality_warnings(
        report=report,
        sources=sources,
        source_docs=source_docs,
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
            "max_sources": int(result.get("max_sources") or max_sources),
            "max_context_chars": int(result.get("max_context_chars") or max_context_chars),
            "report_words": int(result.get("report_words") or 0),
            "source_count": len(sources),
            "source_doc_count": _container_len(source_docs),
            "non_empty_source_doc_count": non_empty_source_doc_count,
            "fetched_source_count": fetched_count,
            "snippet_only_source_count": snippet_only_count,
            "source_urls_present_but_no_nonempty_docs": bool(sources) and non_empty_source_doc_count <= 0,
            "context_chars": len(context),
            "evidence_quality": "fetched" if fetched_count else "snippet_only",
            "listing_verdict": verdict,
            "costs": result.get("costs", {}) if isinstance(result.get("costs"), dict) else {},
        },
    )


def _answer_transition_diagnostics(*, answer: CurrentInfoAnswer, research_run_id: str) -> dict[str, Any]:
    listing_verdict = answer.metadata.get("listing_verdict") if isinstance(answer.metadata, dict) else None
    evidence_warnings: tuple[str, ...] = ()
    if answer.evidence is not None:
        evidence_warnings = tuple(answer.evidence.warnings)
    return {
        "research_run_id": research_run_id,
        "answer_status": answer.status,
        "answer_warning_count": len(answer.warnings),
        "answer_warnings": answer.warnings,
        "evidence_warning_count": len(evidence_warnings),
        "evidence_warnings": evidence_warnings,
        "evidence_quality": answer.metadata.get("evidence_quality", "unknown"),
        "source_count": answer.metadata.get("source_count", 0),
        "source_doc_count": answer.metadata.get("source_doc_count", 0),
        "non_empty_source_doc_count": answer.metadata.get("non_empty_source_doc_count", 0),
        "fetched_source_count": answer.metadata.get("fetched_source_count", 0),
        "snippet_only_source_count": answer.metadata.get("snippet_only_source_count", 0),
        "source_urls_present_but_no_nonempty_docs": answer.metadata.get(
            "source_urls_present_but_no_nonempty_docs", False
        ),
        "confidence": answer.confidence,
        "rejection_reason": ",".join(answer.warnings) if answer.status != "answered" and answer.warnings else "",
        "listing_verdict": str(listing_verdict.get("classification") or "")
        if isinstance(listing_verdict, dict)
        else "",
        "listing_conflict": bool(listing_verdict.get("conflict")) if isinstance(listing_verdict, dict) else False,
        "listing_supports_listed_count": int(listing_verdict.get("supports_listed_count") or 0)
        if isinstance(listing_verdict, dict)
        else 0,
        "listing_supports_private_count": int(listing_verdict.get("supports_private_count") or 0)
        if isinstance(listing_verdict, dict)
        else 0,
    }


def _container_len(value: Any) -> int:
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value)
    if value:
        return 1
    return 0


def _merge_source_docs(source_docs: Any, fetched_docs: tuple[dict[str, Any], ...]) -> Any:
    if not fetched_docs:
        return source_docs
    if source_docs is None:
        return fetched_docs
    if isinstance(source_docs, tuple):
        return (*source_docs, *fetched_docs)
    if isinstance(source_docs, list):
        return (*source_docs, *fetched_docs)
    return (source_docs, *fetched_docs)


def _fetched_document_to_source_doc(document: FetchedDocument) -> dict[str, Any]:
    metadata = {
        **dict(document.metadata or {}),
        "source": document.url,
        "url": document.url,
        "source_state": "fetched",
        "fetch_provider": document.provider,
        "status_code": document.status_code,
        "fetched_at": document.fetched_at,
    }
    return {
        "url": document.url,
        "title": document.title,
        "raw_content": document.text,
        "metadata": metadata,
        "fetched": True,
    }


def _all_source_doc_containers_empty(source_docs: Any) -> bool:
    if _container_len(source_docs) <= 0:
        return False

    has_container = False
    seen_containers: set[int] = set()

    def walk(item: Any) -> bool:
        nonlocal has_container
        if item is None or isinstance(item, str):
            return False
        if isinstance(item, Mapping):
            object_id = id(item)
            if object_id in seen_containers:
                return False
            seen_containers.add(object_id)
            has_container = True
            if _source_doc_content_length(item) > 0 or _looks_like_fetched_document(item):
                return True
            return any(walk(nested) for nested in item.values())
        if isinstance(item, (list, tuple, set, frozenset)):
            object_id = id(item)
            if object_id in seen_containers:
                return False
            seen_containers.add(object_id)
            has_container = True
            return any(walk(nested) for nested in item)
        if _source_doc_content_length(item) > 0 or _looks_like_fetched_document(item):
            has_container = True
            return True
        return False

    has_content = walk(source_docs)
    return has_container and not has_content


def _non_empty_source_doc_count(source_docs: Any) -> int:
    count = 0
    seen_containers: set[int] = set()

    def walk(item: Any) -> None:
        nonlocal count
        if item is None or isinstance(item, str):
            return
        if isinstance(item, Mapping):
            object_id = id(item)
            if object_id in seen_containers:
                return
            seen_containers.add(object_id)
            if _looks_like_source_doc(item) and _source_doc_content_length(item) > 0:
                count += 1
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
        if _looks_like_source_doc(item) and _source_doc_content_length(item) > 0:
            count += 1

    walk(source_docs)
    return count


def _research_answer_status(*, report: str, sources: tuple[str, ...], warnings: tuple[str, ...]) -> str:
    if not report or not sources:
        return "empty_evidence"
    if "empty_scraped_source_docs" in warnings:
        return "empty_evidence"
    if any(warning in warnings for warning in {"snippet_only_evidence", "source_conflict"}):
        return "unverified_evidence"
    return "answered"


def _research_evidence_quality_warnings(
    *,
    report: str,
    sources: tuple[str, ...],
    source_docs: Any = (),
    fetched_count: int,
    snippet_only_count: int,
) -> tuple[str, ...]:
    if not report or not sources:
        return ("empty_research_result",)
    if fetched_count <= 0 and _all_source_doc_containers_empty(source_docs):
        return ("empty_scraped_source_docs",)
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
        if (
            any(str(item.get(key) or "").strip() for key in ("content", "text", "body"))
            and _extract_fetched_document_source_urls(item)
        ):
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
        for attr in ("raw_content", "page_content", "document", "html", "content", "text", "body")
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
_FETCHED_DOCUMENT_SOURCE_URL_KEYS = frozenset({"source", "url", "source_url", "link", "href"})


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
