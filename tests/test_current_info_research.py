from __future__ import annotations

import json
import os
from dataclasses import dataclass

from amo_bot.current_info import CurrentInfoRequest, CurrentInfoService
from amo_bot.current_info.research import (
    AmoLangChainEmbeddings,
    GptResearcherProvider,
    GptResearcherProviderConfig,
    ResearchModelConfig,
    build_gpt_researcher_provider_config_from_settings,
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


def test_research_model_config_uses_ollama_roles_and_keeps_explicit_provider_ids() -> None:
    settings = _Settings()

    resolved = resolve_research_model_config(settings)

    assert resolved.fast_llm == "ollama:fast-model"
    assert resolved.smart_llm == "ollama:smart-model"
    assert resolved.strategic_llm == "ollama:strategic-model"

    explicit = resolve_research_model_config(
        _Settings(
            amo_research_fast_model="ollama:small",
            amo_research_smart_model="litellm:remote-smart",
            amo_research_strategic_model="ollama:planner",
        )
    )

    assert explicit.fast_llm == "ollama:small"
    assert explicit.smart_llm == "litellm:remote-smart"
    assert explicit.strategic_llm == "ollama:planner"


def test_build_gpt_researcher_config_from_settings_keeps_config_switchable() -> None:
    config = build_gpt_researcher_provider_config_from_settings(_Settings())

    assert config.model_config.fast_llm == "ollama:fast-model"
    assert config.model_config.smart_llm == "ollama:smart-model"
    assert config.model_config.strategic_llm == "ollama:strategic-model"
    assert config.searxng_url == "https://searx.example"
    assert config.vector_collection == "research_chunks"


def test_amo_langchain_embeddings_delegates_to_amo_embedding_provider() -> None:
    provider = _FakeEmbeddingProvider()
    embeddings = AmoLangChainEmbeddings(provider)

    docs = embeddings.embed_documents(["alpha", "beta"])
    query = embeddings.embed_query("gamma")

    assert docs == [[1.0, 0.5], [2.0, 0.5]]
    assert query == [1.0, 0.5]
    assert provider.calls == [("alpha", "beta"), ("gamma",)]


def test_gpt_researcher_provider_maps_report_sources_and_ollama_config(monkeypatch) -> None:
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
        ),
        embedding_provider=_FakeEmbeddingProvider(),
        database_url="sqlite:///:memory:",
        researcher_cls=_FakeResearcher,
    )

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
    assert json.loads(instance.config["LLM_KWARGS"]) == {"num_ctx": 8192}


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
