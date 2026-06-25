from __future__ import annotations

import json
import logging
import os
import sys
import types
from dataclasses import dataclass

from amo_bot.current_info import CurrentInfoRequest, CurrentInfoService
from amo_bot.current_info.research import (
    AmoLangChainEmbeddings,
    GptResearcherProvider,
    GptResearcherProviderConfig,
    ResearchModelConfig,
    build_gpt_researcher_provider_config_from_settings,
    _sync_pgvector_connection_url,
    resolve_research_embedding_config,
    resolve_research_model_config,
)


class _FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls.append(texts)
        return tuple((float(index), 0.5) for index, _text in enumerate(texts, start=1))


class _FakeResearcher:
    instances: list["_FakeResearcher"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.config = json.loads(open(kwargs["config_path"], encoding="utf-8").read())
        self.config["LLM_KWARGS"]["verbose"] = False
        self.searx_url = os.environ.get("SEARX_URL", "")
        self.ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "")
        _FakeResearcher.instances.append(self)

    async def conduct_research(self) -> None:
        self.conducted = True

    async def write_report(self) -> str:
        return "Research answer with sourced details."

    def get_source_urls(self) -> tuple[str, ...]:
        return ("https://source.example/report", "https://source.example/report", "https://other.example/context")

    def get_research_context(self) -> str:
        return "Detailed context from several sources."

    def get_costs(self) -> dict[str, float]:
        return {"total": 0.0}

    def get_research_sources(self) -> tuple[dict[str, str], ...]:
        return ({"url": "https://source.example/report"},)


class _FailingResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def conduct_research(self) -> None:
        raise RuntimeError(
            "provider failed Authorization: Bearer live-token-123 "
            "url=https://api.telegram.org/bot123456:ABCDEF/sendMessage?api_key=sk-testsecret123456"
        )


class _SyncVectorResearcher:
    instances: list["_SyncVectorResearcher"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        _SyncVectorResearcher.instances.append(self)

    async def conduct_research(self) -> None:
        self.kwargs["vector_store"].add_documents(["document"])

    async def write_report(self) -> str:
        return "Research answer."

    def get_source_urls(self) -> tuple[str, ...]:
        return ("https://source.example/research",)


@dataclass
class _Settings:
    amo_gpt_researcher_enabled: bool = True
    amo_research_model_provider: str = "ollama"
    amo_research_fast_model: str = ""
    amo_research_smart_model: str = ""
    amo_research_strategic_model: str = ""
    amo_research_timeout_seconds: float = 30.0
    amo_research_max_sources: int = 5
    amo_research_max_context_chars: int = 5000
    amo_research_deep_breadth: int = 2
    amo_research_deep_depth: int = 2
    amo_research_deep_concurrency: int = 2
    amo_research_report_words: int = 700
    amo_research_vector_collection: str = "research_chunks"
    amo_searxng_url: str = "https://searx.example"
    ollama_base_url: str = "http://ollama.test:11434"
    ollama_model: str = "smart-model"
    ollama_non_thinking_model: str = "fast-model"
    ollama_thinking_model: str = "strategic-model"
    ollama_thinking_budget_max_prompt_chars: int | None = 8192
    amo_vector_embedding_provider: str = "ollama"
    amo_vector_embedding_model: str = "nomic-embed-text-v2-moe:latest"


def test_research_model_config_uses_ollama_roles_and_keeps_explicit_provider_ids() -> None:
    settings = _Settings()

    resolved = resolve_research_model_config(settings)

    assert resolved.fast_llm == "ollama:fast-model"
    assert resolved.smart_llm == "ollama:smart-model"
    assert resolved.strategic_llm == "ollama:strategic-model"

    explicit = resolve_research_model_config(
        _Settings(
            amo_research_fast_model="gemma4:e2b",
            amo_research_smart_model="litellm:remote-smart",
            amo_research_strategic_model="ollama:planner",
        )
    )

    assert explicit.fast_llm == "ollama:gemma4:e2b"
    assert explicit.smart_llm == "litellm:remote-smart"
    assert explicit.strategic_llm == "ollama:planner"


def test_build_gpt_researcher_config_from_settings_keeps_config_switchable() -> None:
    config = build_gpt_researcher_provider_config_from_settings(_Settings())

    assert config.model_config.fast_llm == "ollama:fast-model"
    assert config.model_config.smart_llm == "ollama:smart-model"
    assert config.model_config.strategic_llm == "ollama:strategic-model"
    assert config.embedding == "ollama:nomic-embed-text-v2-moe:latest"
    assert config.searxng_url == "https://searx.example"
    assert config.vector_collection == "research_chunks"


def test_research_embedding_config_uses_provider_model_format_and_allows_explicit_ids() -> None:
    assert resolve_research_embedding_config(_Settings()) == "ollama:nomic-embed-text-v2-moe:latest"
    assert (
        resolve_research_embedding_config(
            _Settings(
                amo_vector_embedding_provider="ollama",
                amo_vector_embedding_model="openai:text-embedding-3-small",
            )
        )
        == "openai:text-embedding-3-small"
    )


def test_amo_langchain_embeddings_delegates_to_amo_embedding_provider() -> None:
    provider = _FakeEmbeddingProvider()
    embeddings = AmoLangChainEmbeddings(provider)

    docs = embeddings.embed_documents(["alpha", "beta"])
    query = embeddings.embed_query("gamma")

    assert docs == [[1.0, 0.5], [2.0, 0.5]]
    assert query == [1.0, 0.5]
    assert provider.calls == [("alpha", "beta"), ("gamma",)]


def test_gpt_researcher_provider_maps_report_sources_and_ollama_config(monkeypatch, caplog) -> None:
    _FakeResearcher.instances.clear()
    monkeypatch.setenv("SEARX_URL", "https://previous.example")
    provider = GptResearcherProvider(
        config=GptResearcherProviderConfig(
            enabled=True,
            model_config=ResearchModelConfig(
                provider="ollama",
                fast_llm="ollama:fast-model",
                smart_llm="ollama:smart-model",
                strategic_llm="ollama:strategic-model",
            ),
            searxng_url="https://searx.example",
            timeout_seconds=30,
            max_sources=2,
            max_context_chars=500,
            deep_breadth=2,
            deep_depth=2,
            deep_concurrency=2,
            report_words=700,
            vector_collection="research_chunks",
            ollama_base_url="http://ollama.test:11434",
            ollama_num_ctx=8192,
            embedding="ollama:nomic-embed-text-v2-moe:latest",
        ),
        embedding_provider=_FakeEmbeddingProvider(),
        database_url="sqlite:///:memory:",
        researcher_cls=_FakeResearcher,
    )

    with caplog.at_level(logging.INFO, logger="amo_bot.current_info.research"):
        answer = provider.answer(
            request=CurrentInfoRequest(query="Recherchiere AMO", locale="de"),
            task=CurrentInfoService()._task_planner.plan_task(CurrentInfoRequest(query="Recherchiere AMO", locale="de")),
            query_plan=CurrentInfoService()._query_planner.plan_queries(
                request=CurrentInfoRequest(query="Recherchiere AMO", locale="de"),
                task=CurrentInfoService()._task_planner.plan_task(CurrentInfoRequest(query="Recherchiere AMO", locale="de")),
            ),
        )

    assert answer.status == "answered"
    assert answer.answer_text == "Research answer with sourced details."
    assert answer.sources == ("https://source.example/report", "https://other.example/context")
    assert answer.metadata["provider_mode"] == "gpt_researcher"
    assert os.environ["SEARX_URL"] == "https://previous.example"
    instance = _FakeResearcher.instances[0]
    assert instance.searx_url == "https://searx.example"
    assert instance.ollama_base_url == "http://ollama.test:11434"
    assert instance.config["FAST_LLM"] == "ollama:fast-model"
    assert instance.config["SMART_LLM"] == "ollama:smart-model"
    assert instance.config["STRATEGIC_LLM"] == "ollama:strategic-model"
    assert instance.config["EMBEDDING"] == "ollama:nomic-embed-text-v2-moe:latest"
    assert instance.config["LLM_KWARGS"] == {"num_ctx": 8192, "verbose": False}
    assert "current_info.GptResearcherConfigured" in caplog.text
    assert "current_info.GptResearcherLifecycle" in caplog.text
    assert "'stage': 'configured'" in caplog.text
    assert "'stage': 'conduct_research'" in caplog.text
    assert "'stage': 'write_report'" in caplog.text
    assert "'fast_llm': 'ollama:fast-model'" in caplog.text
    assert "'smart_llm': 'ollama:smart-model'" in caplog.text
    assert "'strategic_llm': 'ollama:strategic-model'" in caplog.text
    assert "'embedding': 'ollama:nomic-embed-text-v2-moe:latest'" in caplog.text


def test_gpt_researcher_builds_sync_pgvector_for_sync_document_load(monkeypatch) -> None:
    created_pgvector_kwargs: list[dict[str, object]] = []

    class _FakePGVector:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            created_pgvector_kwargs.append(kwargs)

        def add_documents(self, documents) -> None:
            assert self.kwargs["async_mode"] is False
            assert list(documents) == ["document"]

    package = types.ModuleType("langchain_postgres")
    vectorstores = types.ModuleType("langchain_postgres.vectorstores")
    vectorstores.PGVector = _FakePGVector
    monkeypatch.setitem(sys.modules, "langchain_postgres", package)
    monkeypatch.setitem(sys.modules, "langchain_postgres.vectorstores", vectorstores)
    _SyncVectorResearcher.instances.clear()
    request = CurrentInfoRequest(query="Research AMO", locale="en")
    service = CurrentInfoService()
    task = service._task_planner.plan_task(request)
    query_plan = service._query_planner.plan_queries(request=request, task=task)
    provider = GptResearcherProvider(
        config=GptResearcherProviderConfig(
            enabled=True,
            model_config=ResearchModelConfig(
                provider="ollama",
                fast_llm="ollama:fast-model",
                smart_llm="ollama:smart-model",
                strategic_llm="ollama:strategic-model",
            ),
            searxng_url="https://searx.example",
            timeout_seconds=30,
            max_sources=2,
            max_context_chars=500,
            deep_breadth=1,
            deep_depth=1,
            deep_concurrency=1,
            report_words=200,
            vector_collection="research_chunks",
            ollama_base_url="http://ollama.test:11434",
        ),
        embedding_provider=_FakeEmbeddingProvider(),
        database_url="postgresql+asyncpg://amo:secret@db.example:5432/amo",
        researcher_cls=_SyncVectorResearcher,
    )

    answer = provider.answer(request=request, task=task, query_plan=query_plan)

    assert answer.status == "answered"
    assert created_pgvector_kwargs
    assert created_pgvector_kwargs[0]["async_mode"] is False
    assert created_pgvector_kwargs[0]["connection"] == "postgresql+psycopg://amo:secret@db.example:5432/amo"
    assert _SyncVectorResearcher.instances[0].kwargs["vector_store"].kwargs["collection_name"] == "research_chunks"


def test_sync_pgvector_connection_url_keeps_sync_postgres_urls() -> None:
    assert _sync_pgvector_connection_url("postgresql+psycopg://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert _sync_pgvector_connection_url("postgresql://u:p@host/db") == "postgresql://u:p@host/db"


def test_gpt_researcher_provider_returns_safe_error_metadata_and_logs(caplog) -> None:
    request = CurrentInfoRequest(query="Recherchiere AMO", locale="de")
    service = CurrentInfoService()
    task = service._task_planner.plan_task(request)
    query_plan = service._query_planner.plan_queries(request=request, task=task)
    provider = GptResearcherProvider(
        config=GptResearcherProviderConfig(
            enabled=True,
            model_config=ResearchModelConfig(
                provider="ollama",
                fast_llm="ollama:fast-model",
                smart_llm="ollama:smart-model",
                strategic_llm="ollama:strategic-model",
            ),
            searxng_url="https://searx.example",
            timeout_seconds=30,
            max_sources=2,
            max_context_chars=500,
            deep_breadth=1,
            deep_depth=1,
            deep_concurrency=1,
            report_words=200,
            vector_collection="research_chunks",
            ollama_base_url="http://ollama.test:11434",
        ),
        embedding_provider=_FakeEmbeddingProvider(),
        database_url="sqlite:///:memory:",
        researcher_cls=_FailingResearcher,
    )

    with caplog.at_level(logging.WARNING, logger="amo_bot.current_info.research"):
        answer = provider.answer(request=request, task=task, query_plan=query_plan)

    assert answer.status == "provider_unavailable"
    assert answer.warnings == ("gpt_researcher_failed",)
    assert answer.metadata["error_class"] == "RuntimeError"
    assert "live-token-123" not in answer.metadata["error_message"]
    assert "123456:ABCDEF" not in answer.metadata["error_message"]
    assert "sk-testsecret123456" not in answer.metadata["error_message"]
    assert "Authorization: Bearer ***REDACTED***" in answer.metadata["error_message"]
    assert "/bot***REDACTED***/sendMessage?api_key=***REDACTED***" in answer.metadata["error_message"]
    assert "live-token-123" not in caplog.text
    assert "123456:ABCDEF" not in caplog.text
    assert "sk-testsecret123456" not in caplog.text
    assert "gpt_researcher_failed: RuntimeError: provider failed Authorization: Bearer ***REDACTED***" in caplog.text


def test_current_info_service_uses_research_provider_for_webresearch_requests() -> None:
    class _ResearchProvider:
        def __init__(self) -> None:
            self.calls = 0

        def answer(self, *, request, task, query_plan):
            self.calls += 1
            return GptResearcherProvider(
                config=GptResearcherProviderConfig(
                    enabled=True,
                    model_config=ResearchModelConfig("ollama", "ollama:fast", "ollama:smart", "ollama:strategic"),
                    searxng_url="",
                    timeout_seconds=1,
                    max_sources=1,
                    max_context_chars=100,
                    deep_breadth=1,
                    deep_depth=1,
                    deep_concurrency=1,
                    report_words=200,
                    vector_collection="research",
                    ollama_base_url="",
                ),
                embedding_provider=_FakeEmbeddingProvider(),
                researcher_cls=_FakeResearcher,
            ).answer(request=request, task=task, query_plan=query_plan)

    research_provider = _ResearchProvider()
    service = CurrentInfoService(research_provider=research_provider)

    answer = service.answer(
        CurrentInfoRequest(
            query="Recherchiere AMO",
            locale="de",
            metadata={"capability": "webresearch"},
        )
    )

    assert research_provider.calls == 1
    assert answer.status == "answered"
    assert answer.metadata["provider_mode"] == "gpt_researcher"
