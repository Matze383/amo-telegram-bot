from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import types
from dataclasses import dataclass

from amo_bot.current_info import CurrentInfoRequest, CurrentInfoService, FetchedDocument
from amo_bot.current_info.research import (
    AmoLangChainEmbeddings,
    GptResearcherProvider,
    GptResearcherProviderConfig,
    ResearchModelConfig,
    RoleSkillConfig,
    build_gpt_researcher_provider_config_from_settings,
    load_gpt_researcher_role_skills,
    _build_pgvector_store,
    _extract_source_urls,
    _inject_role_skill_messages,
    _role_for_llm_call,
    _temporary_gpt_researcher_role_skill_prompt_adapter,
    _NonEmptyVectorStore,
    _strip_searx_prefetched_content,
    _sync_pgvector_connection_url,
    resolve_gpt_researcher_role_skill_config,
    resolve_research_embedding_config,
    resolve_research_model_config,
    _pgvector_embedding_id_notnull_remediation,
)
from amo_bot.evidence_intents import is_finance_listing_query, is_stock_listing_status_query


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
        return ({"url": "https://source.example/report", "raw_content": "Fetched page body " * 40},)


class _FailingResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def conduct_research(self) -> None:
        raise RuntimeError(
            "provider failed Authorization: Bearer live-token-123 "
            "url=https://api.telegram.org/bot123456:ABCDEF/sendMessage?api_key=sk-testsecret123456"
        )


class _SlowResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def conduct_research(self) -> None:
        import asyncio

        await asyncio.sleep(0.05)


class _FallbackSourcesResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def conduct_research(self) -> None:
        self.conducted = True

    async def write_report(self) -> str:
        return "Research answer recovered from structured sources."

    def get_source_urls(self) -> tuple[str, ...]:
        return ()

    def get_research_context(self) -> list[dict[str, object]]:
        return [{"content": "Detailed context.", "metadata": {"href": "https://context.example/item"}}]

    def get_costs(self) -> dict[str, float]:
        return {"total": 0.0}

    def get_research_sources(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "title": "Primary source",
                "raw_content": "Fetched source content " * 40,
                "metadata": {"url": "https://source.example/recovered"},
            },
            {"link": "https://source.example/recovered"},
        )


class _NoRecoverableSourcesResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def conduct_research(self) -> None:
        self.conducted = True

    async def write_report(self) -> str:
        return "Research answer without reliable source URLs."

    def get_source_urls(self) -> tuple[str, ...]:
        return ()

    def get_research_context(self) -> list[dict[str, object]]:
        return [{"content": "Detailed context.", "metadata": {"href": "not-a-url"}}]

    def get_research_sources(self) -> tuple[dict[str, object], ...]:
        return ({"url": "ftp://source.example/file"}, {"link": ""})


class _EmptySourceDocsResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def conduct_research(self) -> None:
        self.conducted = True

    async def write_report(self) -> str:
        return "Research answer with citations but empty source docs."

    def get_source_urls(self) -> tuple[str, ...]:
        return ("https://source.example/empty", "https://other.example/empty")

    def get_research_sources(self) -> tuple[dict[str, object], ...]:
        return ({"url": "https://source.example/empty"}, {"url": "https://other.example/empty", "raw_content": ""})


class _InstrumentedEmptySourceDocsResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.cfg = types.SimpleNamespace(
            retriever="searx",
            scraper="bs",
            report_source="web",
            max_search_results_per_query=2,
        )
        self.search_results = ()
        self.source_urls = ()
        self.visited_urls = set()
        self.research_conductor = types.SimpleNamespace(
            new_search_urls=(),
            visited_urls=set(),
            search_results=(),
        )

    async def conduct_research(self) -> None:
        self.search_results = (
            {"url": "https://source.example/empty", "title": "Empty source"},
            {"href": "https://other.example/empty", "title": "Other empty source"},
        )
        self.source_urls = ("https://source.example/empty", "https://other.example/empty")
        self.visited_urls = {"https://source.example/empty", "https://other.example/empty"}
        self.research_conductor.new_search_urls = self.source_urls
        self.research_conductor.visited_urls = self.visited_urls
        self.research_conductor.search_results = self.search_results

    async def write_report(self) -> str:
        return "Research answer with URLs but empty scraped documents."

    def get_source_urls(self) -> tuple[str, ...]:
        return self.source_urls

    def get_research_sources(self) -> tuple[dict[str, object], ...]:
        return ({"url": "https://source.example/empty"}, {"url": "https://other.example/empty", "raw_content": ""})


class _RecordingSourceFetcher:
    def __init__(self, documents: dict[str, FetchedDocument | None]) -> None:
        self.documents = documents
        self.calls: list[tuple[str, str]] = []

    def fetch(self, *, url: str, locale: str) -> FetchedDocument | None:
        self.calls.append((url, locale))
        return self.documents.get(url)


class _SearxSnippetBrowseResearcher:
    instances: list["_SearxSnippetBrowseResearcher"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.source_urls: tuple[str, ...] = ()
        self.source_docs: tuple[dict[str, object], ...] = ()
        self.scraper_manager = _RecordingScraperManager()
        _SearxSnippetBrowseResearcher.instances.append(self)

    async def conduct_research(self) -> None:
        from gpt_researcher.retrievers.searx.searx import SearxSearch

        search_results = SearxSearch("amo").search(max_results=2)
        self.search_results = tuple(search_results)

        new_search_urls: list[str] = []
        prefetched_content: list[dict[str, str]] = []
        for result in search_results:
            url = result.get("href") or result.get("url")
            raw_content = result.get("raw_content") or result.get("body")
            if url and raw_content and len(raw_content) > 100:
                prefetched_content.append({"url": url, "raw_content": raw_content})
            elif url:
                new_search_urls.append(url)

        scraped_content = await self.scraper_manager.browse_urls(new_search_urls)
        self.source_docs = (*scraped_content, *prefetched_content)
        self.source_urls = tuple(new_search_urls) + tuple(item["url"] for item in prefetched_content)
        self.research_conductor = types.SimpleNamespace(
            new_search_urls=tuple(new_search_urls),
            visited_urls=set(new_search_urls),
            search_results=self.search_results,
        )

    async def write_report(self) -> str:
        return "Research answer backed by scraped SearX source."

    def get_source_urls(self) -> tuple[str, ...]:
        return self.source_urls

    def get_research_sources(self) -> tuple[dict[str, object], ...]:
        return self.source_docs


class _RecordingScraperManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def browse_urls(self, urls: list[str]) -> list[dict[str, object]]:
        self.calls.append(tuple(urls))
        return [
            {
                "url": url,
                "raw_content": "Fetched page content from scraper. " * 20,
                "title": "Fetched source",
            }
            for url in urls
        ]


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

    def get_research_sources(self) -> tuple[dict[str, object], ...]:
        return ({"url": "https://source.example/research", "raw_content": "Fetched research page " * 40},)


class _EmptyVectorBatchResearcher:
    instances: list["_EmptyVectorBatchResearcher"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        _EmptyVectorBatchResearcher.instances.append(self)

    async def conduct_research(self) -> None:
        self.kwargs["vector_store"].add_documents([])

    async def write_report(self) -> str:
        return "Research answer without reliable source URLs."

    def get_source_urls(self) -> tuple[str, ...]:
        return ()

    def get_research_sources(self) -> tuple[dict[str, object], ...]:
        return ()


class _SnippetOnlyListingConflictResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def conduct_research(self) -> None:
        self.conducted = True

    async def write_report(self) -> str:
        return (
            "One source says the company is a private company and not publicly traded. "
            "Another says the IPO completed and shares are trading on Nasdaq under ticker SPCX."
        )

    def get_source_urls(self) -> tuple[str, ...]:
        return ("https://finance.example/private", "https://market.example/spcx")

    def get_research_context(self) -> list[dict[str, object]]:
        return [
            {
                "content": "The company remains privately held and not publicly traded.",
                "metadata": {"url": "https://finance.example/private"},
            },
            {
                "content": "IPO completed; ticker SPCX is listed on Nasdaq.",
                "metadata": {"url": "https://market.example/spcx"},
            },
        ]


class _ContentSourceResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def conduct_research(self) -> None:
        self.conducted = True

    async def write_report(self) -> str:
        return "Opel is part of Stellantis; Stellantis shares trade under ticker STLA."

    def get_source_urls(self) -> tuple[str, ...]:
        return ("https://research.example/stellantis-opel",)

    def get_research_sources(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "link": "https://research.example/stellantis-opel",
                "content": "Opel is part of Stellantis; Stellantis shares trade under ticker STLA. " * 20,
                "metadata": {"source_state": "fetched"},
            },
        )


class _ObjectDoc:
    def __init__(self, page_content: str, metadata: dict[str, object]) -> None:
        self.page_content = page_content
        self.metadata = metadata


class _ObjectMetadataFetchedResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def conduct_research(self) -> None:
        self.conducted = True

    async def write_report(self) -> str:
        return "Research answer backed by fetched object document."

    def get_source_urls(self) -> tuple[str, ...]:
        return ()

    def get_research_sources(self) -> tuple[_ObjectDoc, ...]:
        return (
            _ObjectDoc("Fetched object page body " * 40, {"source": "https://object.example/source"}),
            _ObjectDoc("Fetched object page body " * 40, {"url": "https://object.example/url"}),
        )


class _PrivateTickerNegativeResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def conduct_research(self) -> None:
        self.conducted = True

    async def write_report(self) -> str:
        return "Anthropic is a privately held company and does not have a public stock ticker."

    def get_source_urls(self) -> tuple[str, ...]:
        return ("https://source.example/anthropic",)

    def get_research_context(self) -> str:
        return "Anthropic is a privately held company and does not have a public stock ticker."

    def get_research_sources(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "url": "https://source.example/anthropic",
                "raw_content": "Anthropic is a privately held company and does not have a public stock ticker. " * 10,
            },
        )


class _ObjectMetadataUnrelatedReferenceResearcher:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def conduct_research(self) -> None:
        self.conducted = True

    async def write_report(self) -> str:
        return "Research answer backed by the source document."

    def get_source_urls(self) -> tuple[str, ...]:
        return ()

    def get_research_sources(self) -> tuple[_ObjectDoc, ...]:
        return (
            _ObjectDoc(
                "Fetched object page body " * 40,
                {
                    "unrelated_reference": "https://irrelevant.example/ad",
                    "source": "https://source.example/doc",
                },
            ),
        )


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
    amo_research_role_skills_dir: str = "skills/gpt_researcher"
    amo_research_fast_skill_path: str = ""
    amo_research_smart_skill_path: str = ""
    amo_research_strategic_skill_path: str = ""
    amo_research_role_skill_max_chars: int = 12000
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


def _provider_for_researcher(researcher_cls, *, source_fetcher=None) -> GptResearcherProvider:
    return GptResearcherProvider(
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
        researcher_cls=researcher_cls,
        source_fetcher=source_fetcher,
    )


def _research_request_parts(query: str = "Recherchiere AMO"):
    request = CurrentInfoRequest(query=query, locale="de")
    service = CurrentInfoService()
    task = service._task_planner.plan_task(request)
    query_plan = service._query_planner.plan_queries(request=request, task=task)
    return request, task, query_plan


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
    assert config.role_skills is not None
    assert config.role_skills.skills_dir == "skills/gpt_researcher"


def test_gpt_researcher_role_skill_loader_maps_roles_and_ignores_missing_files(tmp_path) -> None:
    base = tmp_path / "skills" / "gpt_researcher"
    (base / "fast").mkdir(parents=True)
    (base / "strategic").mkdir(parents=True)
    (base / "fast" / "SKILL.md").write_text("Fast role instruction", encoding="utf-8")
    (base / "strategic" / "SKILL.md").write_text("Strategic role instruction", encoding="utf-8")

    loaded = load_gpt_researcher_role_skills(
        RoleSkillConfig(
            skills_dir=str(base),
            role_paths={},
            max_chars=100,
        )
    )

    assert loaded == {
        "fast": "Fast role instruction",
        "strategic": "Strategic role instruction",
    }


def test_gpt_researcher_role_skill_loader_supports_relative_configured_paths(tmp_path) -> None:
    base = tmp_path / "role-skills"
    (base / "custom").mkdir(parents=True)
    (base / "custom" / "smart.md").write_text("Smart role instruction", encoding="utf-8")

    loaded = load_gpt_researcher_role_skills(
        RoleSkillConfig(
            skills_dir=str(base),
            role_paths={"smart": "custom/smart.md"},
            max_chars=100,
        )
    )

    assert loaded == {"smart": "Smart role instruction"}


def test_gpt_researcher_role_skill_loader_rejects_external_paths(tmp_path) -> None:
    base = tmp_path / "role-skills"
    outside = tmp_path / "outside.md"
    base.mkdir()
    outside.write_text("secret-ish instruction", encoding="utf-8")

    try:
        load_gpt_researcher_role_skills(
            RoleSkillConfig(
                skills_dir=str(base),
                role_paths={"fast": str(outside)},
                max_chars=100,
            )
        )
    except ValueError as exc:
        assert "must stay under" in str(exc)
    else:
        raise AssertionError("external skill path was accepted")


def test_gpt_researcher_role_skill_loader_enforces_size_limit(tmp_path) -> None:
    base = tmp_path / "role-skills"
    (base / "fast").mkdir(parents=True)
    (base / "fast" / "SKILL.md").write_text("too long", encoding="utf-8")

    try:
        load_gpt_researcher_role_skills(RoleSkillConfig(skills_dir=str(base), role_paths={}, max_chars=3))
    except ValueError as exc:
        assert "exceeds 3 characters" in str(exc)
    else:
        raise AssertionError("oversized skill was accepted")


def test_gpt_researcher_role_skills_are_loaded_into_config_without_query_fallback(tmp_path, caplog) -> None:
    _FakeResearcher.instances.clear()
    base = tmp_path / "skills" / "gpt_researcher"
    for role in ("fast", "smart", "strategic"):
        (base / role).mkdir(parents=True)
        (base / role / "SKILL.md").write_text(f"{role} role instruction", encoding="utf-8")

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
            role_skills=RoleSkillConfig(skills_dir=str(base), role_paths={}, max_chars=100),
        ),
        embedding_provider=_FakeEmbeddingProvider(),
        database_url="sqlite:///:memory:",
        researcher_cls=_FakeResearcher,
    )
    request, task, query_plan = _research_request_parts("Recherchiere Rollen")

    with caplog.at_level(logging.INFO, logger="amo_bot.current_info.research"):
        answer = provider.answer(request=request, task=task, query_plan=query_plan)

    assert answer.status == "answered"
    instance = _FakeResearcher.instances[0]
    assert instance.config["ROLE_SKILLS"] == {
        "fast": "fast role instruction",
        "smart": "smart role instruction",
        "strategic": "strategic role instruction",
    }
    assert instance.config["FAST_LLM_SKILL"] == "fast role instruction"
    assert instance.config["SMART_LLM_SKILL"] == "smart role instruction"
    assert instance.config["STRATEGIC_LLM_SKILL"] == "strategic role instruction"
    assert "GPT-Researcher role skill instructions:" not in instance.kwargs["query"]
    assert "fast role instruction" not in instance.kwargs["query"]
    assert "smart role instruction" not in instance.kwargs["query"]
    assert "strategic role instruction" not in instance.kwargs["query"]
    assert "'gpt_researcher_role_skill_roles': ('fast', 'smart', 'strategic')" in caplog.text
    assert "'gpt_researcher_role_skill_count': 3" in caplog.text
    assert "fast role instruction" not in caplog.text
    assert "smart role instruction" not in caplog.text
    assert "strategic role instruction" not in caplog.text


def test_gpt_researcher_role_skill_prompt_adapter_maps_llm_roles_to_system_messages() -> None:
    model_config = ResearchModelConfig(
        provider="ollama",
        fast_llm="ollama:fast-model",
        smart_llm="ollama:smart-model",
        strategic_llm="ollama:strategic-model",
    )
    role_skills = {
        "fast": "fast role instruction",
        "smart": "smart role instruction",
        "strategic": "strategic role instruction",
    }

    assert _role_for_llm_call(model="fast-model", llm_provider="ollama", model_config=model_config) == "fast"
    assert _role_for_llm_call(model="smart-model", llm_provider="ollama", model_config=model_config) == "smart"
    assert (
        _role_for_llm_call(model="strategic-model", llm_provider="ollama", model_config=model_config)
        == "strategic"
    )

    smart_messages = _inject_role_skill_messages(
        messages=[
            {"role": "system", "content": "You write concise reports."},
            {"role": "user", "content": "Write the report."},
        ],
        role="smart",
        role_skills=role_skills,
    )
    assert smart_messages[0]["role"] == "system"
    assert "You write concise reports." in smart_messages[0]["content"]
    assert "AMO GPT-Researcher SMART role skill instructions from SKILL.md:" in smart_messages[0]["content"]
    assert "smart role instruction" in smart_messages[0]["content"]
    assert "fast role instruction" not in smart_messages[0]["content"]
    assert "strategic role instruction" not in smart_messages[0]["content"]
    assert smart_messages[1]["content"] == "Write the report."

    strategic_messages = _inject_role_skill_messages(
        messages=[{"role": "user", "content": "Generate search queries."}],
        role="strategic",
        role_skills=role_skills,
    )
    assert strategic_messages[0] == {
        "role": "system",
        "content": (
            "AMO GPT-Researcher STRATEGIC role skill instructions from SKILL.md:\n"
            "strategic role instruction"
        ),
    }
    assert strategic_messages[1]["content"] == "Generate search queries."

    fast_messages = _inject_role_skill_messages(
        messages=[{"role": "user", "content": "Triage this source."}],
        role="fast",
        role_skills=role_skills,
    )
    assert "fast role instruction" in fast_messages[0]["content"]
    assert "smart role instruction" not in fast_messages[0]["content"]
    assert "strategic role instruction" not in fast_messages[0]["content"]


def test_gpt_researcher_role_skill_prompt_adapter_patches_gpt_researcher_llm_calls(monkeypatch) -> None:
    model_config = ResearchModelConfig(
        provider="ollama",
        fast_llm="ollama:fast-model",
        smart_llm="ollama:smart-model",
        strategic_llm="ollama:strategic-model",
    )
    captured: list[dict[str, object]] = []

    async def fake_create_chat_completion(**kwargs):
        captured.append(kwargs)
        return '["example query"]'

    query_processing = types.ModuleType("gpt_researcher.actions.query_processing")
    query_processing.create_chat_completion = fake_create_chat_completion
    monkeypatch.setitem(sys.modules, "gpt_researcher.actions.query_processing", query_processing)

    with _temporary_gpt_researcher_role_skill_prompt_adapter(
        model_config=model_config,
        role_skills={
            "fast": "fast role instruction",
            "smart": "smart role instruction",
            "strategic": "strategic role instruction",
        },
    ):
        asyncio.run(
            query_processing.create_chat_completion(
                model="strategic-model",
                llm_provider="ollama",
                messages=[{"role": "user", "content": "Generate search queries."}],
            )
        )

    assert len(captured) == 1
    messages = captured[0]["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "system"
    assert "AMO GPT-Researcher STRATEGIC role skill instructions from SKILL.md:" in messages[0]["content"]
    assert "strategic role instruction" in messages[0]["content"]
    assert "fast role instruction" not in messages[0]["content"]
    assert "smart role instruction" not in messages[0]["content"]


def test_resolve_gpt_researcher_role_skill_config_uses_settings_paths() -> None:
    resolved = resolve_gpt_researcher_role_skill_config(
        _Settings(
            amo_research_role_skills_dir="/tmp/research-skills",
            amo_research_fast_skill_path="roles/fast.md",
            amo_research_role_skill_max_chars=345,
        )
    )

    assert resolved is not None
    assert resolved.skills_dir == "/tmp/research-skills"
    assert resolved.role_paths["fast"] == "roles/fast.md"
    assert resolved.role_paths["smart"] == ""
    assert resolved.max_chars == 345


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
    assert "Recherchiere AMO" in instance.kwargs["query"]
    assert "Current date/time context for this research run:" in instance.kwargs["query"]
    assert "Date handling:" in instance.kwargs["query"]
    assert instance.kwargs["report_type"] == "research_report"
    assert instance.config["FAST_LLM"] == "ollama:fast-model"
    assert instance.config["SMART_LLM"] == "ollama:smart-model"
    assert instance.config["STRATEGIC_LLM"] == "ollama:strategic-model"
    assert instance.config["EMBEDDING"] == "ollama:nomic-embed-text-v2-moe:latest"
    assert instance.config["LLM_KWARGS"] == {"num_ctx": 8192, "verbose": False}
    assert "current_info.GptResearcherConfigured" in caplog.text
    assert "current_info.GptResearcherInput" in caplog.text
    assert "current_info.GptResearcherLifecycle" in caplog.text
    assert "current_info.GptResearcherDiagnostics" in caplog.text
    assert "'stage': 'configured'" in caplog.text
    assert "'stage': 'input'" in caplog.text
    assert "'stage': 'conduct_research'" in caplog.text
    assert "'stage': 'write_report'" in caplog.text
    assert "'stage': 'source_urls'" in caplog.text
    assert "'stage': 'source_docs'" in caplog.text
    assert "'stage': 'research_context'" in caplog.text
    assert "'stage': 'source_mapping'" in caplog.text
    assert "'user_task_length': 16" in caplog.text
    assert "'task_augmented': True" in caplog.text
    assert "'source_url_count': 2" in caplog.text
    assert "'source_doc_fetched_like_count': 1" in caplog.text
    assert "'context_chars': 38" in caplog.text
    assert "'unfetched_source_url_count': 1" in caplog.text
    assert "'fast_llm': 'ollama:fast-model'" in caplog.text
    assert "'smart_llm': 'ollama:smart-model'" in caplog.text
    assert "'strategic_llm': 'ollama:strategic-model'" in caplog.text
    assert "'embedding': 'ollama:nomic-embed-text-v2-moe:latest'" in caplog.text
    assert "'llm_call_visibility': 'config_only_gpt_researcher_internal_calls'" in caplog.text
    assert "'report_type': 'research_report'" in caplog.text
    assert "Current date/time context for this research run" not in caplog.text
    assert "Date handling:" not in caplog.text
    assert answer.metadata["research_report_type"] == "research_report"
    assert answer.metadata["deep_research"] is False
    assert answer.metadata["deep_breadth"] == 2
    assert answer.metadata["deep_depth"] == 2
    assert answer.metadata["deep_concurrency"] == 2
    assert answer.metadata["max_sources"] == 2
    assert answer.metadata["max_context_chars"] == 500
    assert answer.metadata["report_words"] == 700


def test_gpt_researcher_provider_uses_deep_research_report_type_from_metadata(caplog) -> None:
    _FakeResearcher.instances.clear()
    request, task, query_plan = _research_request_parts(
        "erstelle mir einen ausführlichen aktuellen Bericht zu ExampleTech Partner Pläne Finanzen"
    )
    request = CurrentInfoRequest(
        query=request.query,
        locale=request.locale,
        metadata={"research_report_type": "deep_research"},
    )

    with caplog.at_level(logging.INFO, logger="amo_bot.current_info.research"):
        answer = _provider_for_researcher(_FakeResearcher).answer(
            request=request,
            task=task,
            query_plan=query_plan,
        )

    assert answer.status == "answered"
    instance = _FakeResearcher.instances[0]
    assert instance.kwargs["report_type"] == "deep_research"
    query = instance.kwargs["query"]
    assert "Deep research decomposition:" in query
    assert "Split the report into multiple focused subquestions before synthesis:" in query
    assert "Partner strategy, partner ecosystem, partner programs, and announced plans." not in query
    assert "financial performance, guidance, investor-relations materials" not in query
    assert answer.metadata["research_report_type"] == "deep_research"
    assert answer.metadata["deep_research"] is True
    assert answer.metadata["deep_breadth"] == 1
    assert answer.metadata["deep_depth"] == 1
    assert answer.metadata["max_sources"] == 2
    assert answer.metadata["deep_concurrency"] == 1
    assert "'report_type': 'deep_research'" in caplog.text
    assert "'deep_breadth': 1" in caplog.text


def test_gpt_researcher_query_uses_request_date_context_for_expired_planned_dates() -> None:
    _FakeResearcher.instances.clear()
    request = CurrentInfoRequest(
        query="Ist SpaceX schon boersennotiert?",
        locale="de",
        metadata={
            "current_time_context_text": "\n".join(
                (
                    "Context:",
                    "Current date: 2026-06-26",
                    "Timezone: Europe/Berlin",
                    "Local timestamp: 2026-06-26T12:00:00+02:00",
                    "UTC timestamp: 2026-06-26T10:00:00Z",
                )
            ),
            "timezone": "Europe/Berlin",
        },
    )
    service = CurrentInfoService()
    task = service._task_planner.plan_task(request)
    query_plan = service._query_planner.plan_queries(request=request, task=task)

    answer = _provider_for_researcher(_FakeResearcher).answer(
        request=request,
        task=task,
        query_plan=query_plan,
    )

    assert answer.status == "answered"
    query = _FakeResearcher.instances[0].kwargs["query"]
    assert "Ist SpaceX schon boersennotiert?" in query
    assert "Current date: 2026-06-26" in query
    assert "Timezone: Europe/Berlin" in query
    assert "do not describe it as future" in query
    assert "date that has already passed" in query


def test_gpt_researcher_provider_recovers_sources_from_research_sources_when_source_urls_empty() -> None:
    request, task, query_plan = _research_request_parts()
    answer = _provider_for_researcher(_FallbackSourcesResearcher).answer(
        request=request,
        task=task,
        query_plan=query_plan,
    )

    assert answer.status == "answered"
    assert answer.sources == ("https://source.example/recovered", "https://context.example/item")
    assert answer.metadata["source_count"] == 2
    assert answer.metadata["fetched_source_count"] == 1
    assert answer.metadata["snippet_only_source_count"] == 1
    assert answer.evidence is not None
    assert answer.evidence.sources[0].fetched is True
    assert answer.evidence.sources[1].fetched is False
    assert answer.confidence > 0


def test_gpt_researcher_snippet_only_sources_are_not_marked_fetched(caplog) -> None:
    request, task, query_plan = _research_request_parts("Ist SpaceX schon boersennotiert?")

    with caplog.at_level(logging.WARNING, logger="amo_bot.current_info.research"):
        answer = _provider_for_researcher(_SnippetOnlyListingConflictResearcher).answer(
            request=request,
            task=task,
            query_plan=query_plan,
        )

    assert answer.status == "unverified_evidence"
    assert answer.confidence == 0.35
    assert answer.warnings == ("snippet_only_evidence", "source_conflict", "listing_evidence_conflict")
    assert answer.metadata["source_count"] == 2
    assert answer.metadata["fetched_source_count"] == 0
    assert answer.metadata["snippet_only_source_count"] == 2
    assert answer.metadata["evidence_quality"] == "snippet_only"
    assert answer.evidence is not None
    assert answer.evidence.freshness == "snippet_only"
    assert all(source.fetched is False for source in answer.evidence.sources)
    assert {source.quality_label for source in answer.evidence.sources} == {"snippet_only"}
    assert "current_info.GptResearcherEvidenceQuality" in caplog.text
    assert "snippet_only_source_count" in caplog.text
    assert "no_fetched_source_docs" in caplog.text
    assert "source_urls_without_fetched_docs" in caplog.text


def test_searx_snippet_body_is_not_prefetched_content() -> None:
    long_snippet = "SearX snippet only. " * 12

    normalized = _strip_searx_prefetched_content(
        [
            {
                "href": "https://source.example/searx-snippet",
                "body": long_snippet,
                "title": "Snippet source",
            }
        ]
    )

    assert normalized == [
        {
            "href": "https://source.example/searx-snippet",
            "title": "Snippet source",
        }
    ]
    assert "body" not in normalized[0]
    assert "raw_content" not in normalized[0]
    assert "content" not in normalized[0]
    assert "snippet" not in normalized[0]


def test_gpt_researcher_searx_snippet_url_reaches_browse_urls(monkeypatch, caplog) -> None:
    long_snippet = "SearX snippet only. " * 12
    source_url = "https://source.example/searx-snippet?api_key=secret-token"

    class _FakeSearxSearch:
        def __init__(self, query: str, query_domains=None) -> None:
            self.query = query
            self.query_domains = query_domains

        def search(self, max_results: int = 10):
            return [
                {
                    "href": source_url,
                    "body": long_snippet,
                    "title": "Snippet source",
                }
            ][:max_results]

    original_search = _FakeSearxSearch.search
    gpt_package = types.ModuleType("gpt_researcher")
    retrievers_package = types.ModuleType("gpt_researcher.retrievers")
    searx_package = types.ModuleType("gpt_researcher.retrievers.searx")
    searx_module = types.ModuleType("gpt_researcher.retrievers.searx.searx")
    searx_module.SearxSearch = _FakeSearxSearch
    monkeypatch.setitem(sys.modules, "gpt_researcher", gpt_package)
    monkeypatch.setitem(sys.modules, "gpt_researcher.retrievers", retrievers_package)
    monkeypatch.setitem(sys.modules, "gpt_researcher.retrievers.searx", searx_package)
    monkeypatch.setitem(sys.modules, "gpt_researcher.retrievers.searx.searx", searx_module)

    _SearxSnippetBrowseResearcher.instances.clear()
    request, task, query_plan = _research_request_parts("Research SearX snippet")

    with caplog.at_level(logging.INFO, logger="amo_bot.current_info.research"):
        answer = _provider_for_researcher(_SearxSnippetBrowseResearcher).answer(
            request=request,
            task=task,
            query_plan=query_plan,
        )

    instance = _SearxSnippetBrowseResearcher.instances[0]
    assert instance.scraper_manager.calls == [(source_url,)]
    assert instance.research_conductor.new_search_urls == (source_url,)
    assert instance.search_results == (
        {
            "href": source_url,
            "title": "Snippet source",
        },
    )
    assert _FakeSearxSearch.search is original_search
    assert answer.status == "answered"
    assert answer.sources == (source_url,)
    assert answer.metadata["fetched_source_count"] == 1
    assert answer.metadata["snippet_only_source_count"] == 0
    assert answer.metadata["evidence_quality"] == "fetched"
    assert "snippet_only_evidence" not in answer.warnings
    assert "current_info.GptResearcherSearxAdapter" in caplog.text
    assert "current_info.GptResearcherBrowserActivity" in caplog.text
    assert "current_info.GptResearcherAnswerTransition" in caplog.text
    assert "'stage': 'searx_adapter'" in caplog.text
    assert "'searx_snippet_adapter_installed': True" in caplog.text
    assert "'stage': 'searx_search'" in caplog.text
    assert "'searx_search_called': True" in caplog.text
    assert "'searx_raw_result_count': 1" in caplog.text
    assert "'searx_url_result_count': 1" in caplog.text
    assert "'searx_raw_content_or_body_present_count': 1" in caplog.text
    assert "'searx_snippet_present_after_strip_count': 0" in caplog.text
    assert "'stage': 'browse_urls'" in caplog.text
    assert "'browse_urls_call_count': 1" in caplog.text
    assert "'browse_urls_input_url_count': 1" in caplog.text
    assert "'browse_urls_output_doc_count': 1" in caplog.text
    assert "'browse_urls_output_non_empty_count': 1" in caplog.text
    assert "'post_conduct_source_doc_non_empty_count': 1" in caplog.text
    assert "'post_write_source_doc_non_empty_count': 1" in caplog.text
    assert "'answer_status': 'answered'" in caplog.text
    assert "secret-token" not in caplog.text
    assert "?api_key=" not in caplog.text
    assert long_snippet.strip() not in caplog.text
    assert "Fetched page content from scraper" not in caplog.text


def test_gpt_researcher_content_source_docs_count_as_fetched() -> None:
    request, task, query_plan = _research_request_parts("Ist Opel börsennotiert?")

    answer = _provider_for_researcher(_ContentSourceResearcher).answer(
        request=request,
        task=task,
        query_plan=query_plan,
    )

    assert answer.status == "answered"
    assert answer.metadata["fetched_source_count"] == 1
    assert answer.metadata["snippet_only_source_count"] == 0
    assert answer.metadata["evidence_quality"] == "fetched"
    assert "snippet_only_evidence" not in answer.warnings
    assert answer.evidence is not None
    assert answer.evidence.sources[0].fetched is True


def test_gpt_researcher_validates_empty_source_docs_from_discovered_urls(caplog) -> None:
    request, task, query_plan = _research_request_parts("Research empty GPT-Researcher docs")
    fetcher = _RecordingSourceFetcher(
        {
            "https://source.example/empty": FetchedDocument(
                url="https://source.example/empty",
                title="Recovered source",
                text="Recovered fetched page content. " * 20,
                fetched_at="2026-06-27T10:00:00+00:00",
                status_code=200,
                provider="unit_fetcher",
            ),
            "https://other.example/empty": None,
        }
    )

    with caplog.at_level(logging.INFO, logger="amo_bot.current_info.research"):
        answer = _provider_for_researcher(_EmptySourceDocsResearcher, source_fetcher=fetcher).answer(
            request=request,
            task=task,
            query_plan=query_plan,
        )

    assert fetcher.calls == [("https://source.example/empty", "de"), ("https://other.example/empty", "de")]
    assert answer.status == "answered"
    assert answer.metadata["fetched_source_count"] == 1
    assert answer.metadata["snippet_only_source_count"] == 1
    assert answer.metadata["evidence_quality"] == "fetched"
    assert answer.evidence is not None
    assert answer.evidence.sources[0].fetched is True
    assert answer.evidence.sources[0].quality_label == "gpt_researcher_fetched_source"
    assert "'stage': 'source_validation'" in caplog.text
    assert "'source_validation_fetched_count': 1" in caplog.text


def test_gpt_researcher_empty_source_docs_fail_closed_when_validation_fetch_empty(caplog) -> None:
    request, task, query_plan = _research_request_parts("Research empty GPT-Researcher docs")
    fetcher = _RecordingSourceFetcher({})

    with caplog.at_level(logging.WARNING, logger="amo_bot.current_info.research"):
        answer = _provider_for_researcher(_EmptySourceDocsResearcher, source_fetcher=fetcher).answer(
            request=request,
            task=task,
            query_plan=query_plan,
        )

    assert fetcher.calls == [("https://source.example/empty", "de"), ("https://other.example/empty", "de")]
    assert answer.status == "empty_evidence"
    assert answer.warnings == ("empty_scraped_source_docs",)
    assert answer.confidence == 0.42
    assert answer.metadata["fetched_source_count"] == 0
    assert answer.metadata["snippet_only_source_count"] == 2
    assert answer.evidence is not None
    assert all(source.fetched is False for source in answer.evidence.sources)
    assert "source_validation_empty" in caplog.text


def test_gpt_researcher_diagnostics_log_urls_present_with_empty_docs(caplog) -> None:
    request, task, query_plan = _research_request_parts("Research GPT-Researcher scrape diagnostics")

    with caplog.at_level(logging.INFO, logger="amo_bot.current_info.research"):
        answer = _provider_for_researcher(_InstrumentedEmptySourceDocsResearcher).answer(
            request=request,
            task=task,
            query_plan=query_plan,
        )

    assert answer.status == "empty_evidence"
    assert answer.metadata["source_count"] == 2
    assert answer.metadata["source_doc_count"] == 2
    assert answer.metadata["non_empty_source_doc_count"] == 0
    assert answer.metadata["fetched_source_count"] == 0
    assert answer.metadata["source_urls_present_but_no_nonempty_docs"] is True
    assert "current_info.GptResearcherDiagnostics" in caplog.text
    assert "'stage': 'runtime_config'" in caplog.text
    assert "'stage': 'researcher_state'" in caplog.text
    assert "'outcome': 'before_conduct_research'" in caplog.text
    assert "'gpt_researcher_retriever': 'searx'" in caplog.text
    assert "'gpt_researcher_report_source': 'web'" in caplog.text
    assert "'gpt_researcher_max_search_results_per_query': 2" in caplog.text
    assert "'gpt_researcher_active_retriever': 'searx'" in caplog.text
    assert "'gpt_researcher_active_scraper': 'bs'" in caplog.text
    assert "'gpt_researcher_active_report_source': 'web'" in caplog.text
    assert "'search_results_count': 2" in caplog.text
    assert "'new_search_urls_count': 2" in caplog.text
    assert "'visited_urls_count': 2" in caplog.text
    assert "'post_conduct_url_count': 2" in caplog.text
    assert "'post_conduct_source_doc_container_count': 2" in caplog.text
    assert "'post_conduct_source_doc_non_empty_count': 0" in caplog.text
    assert "'source_doc_non_empty_count': 0" in caplog.text
    assert "'source_urls_present_but_no_nonempty_docs': True" in caplog.text
    assert "'research_run_id': 'gptr-" in caplog.text


def test_gpt_researcher_listing_conflict_yields_uncertain_verdict() -> None:
    request, task, query_plan = _research_request_parts("Ist SpaceX an der Boerse gelistet?")
    answer = _provider_for_researcher(_SnippetOnlyListingConflictResearcher).answer(
        request=request,
        task=task,
        query_plan=query_plan,
    )

    verdict = answer.metadata["listing_verdict"]
    assert verdict["classification"] == "conflicting"
    assert verdict["conflict"] is True
    assert verdict["supports_listed_count"] >= 1
    assert verdict["supports_private_count"] >= 1
    assert answer.status == "unverified_evidence"
    assert "source_conflict" in answer.warnings


def test_gpt_researcher_negative_public_ticker_statement_supports_private_listing_verdict() -> None:
    request, task, query_plan = _research_request_parts("Ist Anthropic an der Boerse?")

    answer = _provider_for_researcher(_PrivateTickerNegativeResearcher).answer(
        request=request,
        task=task,
        query_plan=query_plan,
    )

    verdict = answer.metadata["listing_verdict"]
    assert verdict["classification"] == "supports_private"
    assert verdict["conflict"] is False
    assert verdict["supports_private_count"] >= 1
    assert verdict["supports_listed_count"] == 0
    assert "source_conflict" not in answer.warnings
    assert "listing_evidence_conflict" not in answer.warnings


def test_gpt_researcher_derivative_exchange_query_does_not_get_stock_listing_verdict() -> None:
    query = "Ist BTCUSDT auf Bybit handelbar?"
    assert is_finance_listing_query(query) is True
    assert is_stock_listing_status_query(query) is False
    request, task, query_plan = _research_request_parts(query)

    answer = _provider_for_researcher(_SnippetOnlyListingConflictResearcher).answer(
        request=request,
        task=task,
        query_plan=query_plan,
    )

    assert answer.metadata["listing_verdict"]["classification"] == "not_applicable"
    assert answer.metadata["listing_verdict"]["conflict"] is False
    assert "source_conflict" not in answer.warnings
    assert "listing_evidence_conflict" not in answer.warnings


def test_gpt_researcher_generic_private_company_query_does_not_get_stock_listing_verdict() -> None:
    query = "Was bedeutet private company?"
    assert is_finance_listing_query(query) is True
    assert is_stock_listing_status_query(query) is False
    request, task, query_plan = _research_request_parts(query)

    answer = _provider_for_researcher(_SnippetOnlyListingConflictResearcher).answer(
        request=request,
        task=task,
        query_plan=query_plan,
    )

    assert answer.metadata["listing_verdict"]["classification"] == "not_applicable"
    assert answer.metadata["listing_verdict"]["conflict"] is False
    assert "source_conflict" not in answer.warnings
    assert "listing_evidence_conflict" not in answer.warnings


def test_stock_listing_status_helper_keeps_actual_listing_questions_applicable() -> None:
    assert is_stock_listing_status_query("Ist SpaceX schon boersennotiert?") is True
    assert is_stock_listing_status_query("Hat SpaceX einen IPO oder Ticker?") is True
    assert is_stock_listing_status_query("Is Siemens publicly listed on a stock exchange?") is True
    assert is_stock_listing_status_query("Kann man SpaceX Aktien kaufen?") is True


def test_gpt_researcher_object_doc_metadata_source_counts_as_fetched() -> None:
    request, task, query_plan = _research_request_parts("Research object docs")

    answer = _provider_for_researcher(_ObjectMetadataFetchedResearcher).answer(
        request=request,
        task=task,
        query_plan=query_plan,
    )

    assert answer.status == "answered"
    assert answer.sources == ("https://object.example/source", "https://object.example/url")
    assert answer.metadata["fetched_source_count"] == 2
    assert answer.metadata["snippet_only_source_count"] == 0
    assert answer.evidence is not None
    assert all(source.fetched is True for source in answer.evidence.sources)


def test_gpt_researcher_fetched_object_doc_ignores_unrelated_metadata_url_for_fetched_count() -> None:
    request, task, query_plan = _research_request_parts("Research object docs")

    answer = _provider_for_researcher(_ObjectMetadataUnrelatedReferenceResearcher).answer(
        request=request,
        task=task,
        query_plan=query_plan,
    )

    assert answer.sources == ("https://source.example/doc",)
    assert answer.metadata["fetched_source_count"] == 1
    assert answer.metadata["snippet_only_source_count"] == 0
    assert answer.evidence is not None
    source_states = {source.url: source.fetched for source in answer.evidence.sources}
    assert source_states == {"https://source.example/doc": True}


def test_gpt_researcher_provider_fails_closed_when_no_reliable_urls_are_recovered() -> None:
    request, task, query_plan = _research_request_parts()
    answer = _provider_for_researcher(_NoRecoverableSourcesResearcher).answer(
        request=request,
        task=task,
        query_plan=query_plan,
    )

    assert answer.status == "empty_evidence"
    assert answer.answer_text == "Research answer without reliable source URLs."
    assert answer.sources == ()
    assert answer.metadata["source_count"] == 0
    assert answer.warnings == ("empty_research_result",)


def test_pgvector_embedding_id_notnull_error_reports_ops_remediation() -> None:
    error = RuntimeError(
        "psycopg.errors.NotNullViolation: null value in column "
        '"id" of relation "langchain_pg_embedding" violates not-null constraint'
    )

    remediation = _pgvector_embedding_id_notnull_remediation(error)

    assert "langchain_pg_embedding.id" in remediation
    assert "ops migration" in remediation
    assert "backup" in remediation


def test_extract_source_urls_handles_nested_object_shapes_and_filters_invalid_values() -> None:
    class _UrlObject:
        source_url = "https://object.example/source"
        link = "javascript:alert(1)"

    payload = [
        "https://plain.example/source",
        "not-a-url",
        {"url": "https://dict.example/source", "nested": [{"href": "http://nested.example/item"}]},
        {"link": "ftp://invalid.example/file", "source": {"source_url": "https://deep.example/source"}},
        _UrlObject(),
        {"url": "https://dict.example/source"},
    ]

    assert _extract_source_urls(payload) == (
        "https://plain.example/source",
        "https://dict.example/source",
        "http://nested.example/item",
        "https://deep.example/source",
        "https://object.example/source",
    )


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


def test_gpt_researcher_pgvector_skips_empty_document_batches(monkeypatch) -> None:
    empty_add_calls: list[list[object]] = []

    class _FakePGVector:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def add_documents(self, documents) -> list[str]:
            documents_ = list(documents)
            empty_add_calls.append(documents_)
            if not documents_:
                raise AssertionError("empty document batch reached PGVector")
            return ["stored"]

    package = types.ModuleType("langchain_postgres")
    vectorstores = types.ModuleType("langchain_postgres.vectorstores")
    vectorstores.PGVector = _FakePGVector
    monkeypatch.setitem(sys.modules, "langchain_postgres", package)
    monkeypatch.setitem(sys.modules, "langchain_postgres.vectorstores", vectorstores)
    _EmptyVectorBatchResearcher.instances.clear()
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
        researcher_cls=_EmptyVectorBatchResearcher,
    )

    answer = provider.answer(request=request, task=task, query_plan=query_plan)

    assert answer.status == "empty_evidence"
    assert answer.warnings == ("empty_research_result",)
    assert empty_add_calls == []
    assert isinstance(_EmptyVectorBatchResearcher.instances[0].kwargs["vector_store"], _NonEmptyVectorStore)


def test_pgvector_store_wrapper_skips_empty_text_and_embedding_batches(monkeypatch) -> None:
    class _FakePGVector:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.text_calls: list[object] = []
            self.embedding_calls: list[object] = []

        def add_texts(self, texts, metadatas=None, ids=None, **kwargs) -> list[str]:
            self.text_calls.append((list(texts), metadatas, ids, kwargs))
            return ["text-id"]

        def add_embeddings(self, texts, embeddings, metadatas=None, ids=None, **kwargs) -> list[str]:
            self.embedding_calls.append((list(texts), list(embeddings), metadatas, ids, kwargs))
            return ["embedding-id"]

    package = types.ModuleType("langchain_postgres")
    vectorstores = types.ModuleType("langchain_postgres.vectorstores")
    vectorstores.PGVector = _FakePGVector
    monkeypatch.setitem(sys.modules, "langchain_postgres", package)
    monkeypatch.setitem(sys.modules, "langchain_postgres.vectorstores", vectorstores)

    store = _build_pgvector_store(
        database_url="postgresql+asyncpg://amo:secret@db.example:5432/amo",
        collection_name="research_chunks",
        embedding_provider=_FakeEmbeddingProvider(),
    )

    assert store is not None
    assert store.add_texts([]) == []
    assert store.add_embeddings(texts=[], embeddings=[]) == []
    assert store.add_texts(["kept"], metadatas=[{"source": "unit"}], ids=["id-1"]) == ["text-id"]
    assert store.add_embeddings(texts=["kept"], embeddings=[[1.0, 2.0]]) == ["embedding-id"]
    assert store._wrapped.text_calls == [(["kept"], [{"source": "unit"}], ["id-1"], {})]
    assert store._wrapped.embedding_calls == [(["kept"], [[1.0, 2.0]], None, None, {})]


def test_sync_pgvector_connection_url_keeps_sync_postgres_urls() -> None:
    assert _sync_pgvector_connection_url("postgresql+psycopg://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert _sync_pgvector_connection_url("postgresql://u:p@host/db") == "postgresql://u:p@host/db"


def test_gpt_researcher_provider_returns_safe_error_metadata_and_logs(caplog) -> None:
    request = CurrentInfoRequest(query="Recherchiere AMO", locale="de", metadata={"deep_research": True})
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
    assert answer.metadata["research_report_type"] == "deep_research"
    assert answer.metadata["deep_research"] is True
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
    assert "'report_type': 'deep_research'" in caplog.text


def test_gpt_researcher_provider_timeout_fails_closed_with_deep_metadata(caplog) -> None:
    request = CurrentInfoRequest(query="Recherchiere AMO", locale="de", metadata={"research_mode": "deep"})
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
            timeout_seconds=0.001,
            max_sources=2,
            max_context_chars=500,
            deep_breadth=3,
            deep_depth=2,
            deep_concurrency=4,
            report_words=200,
            vector_collection="research_chunks",
            ollama_base_url="http://ollama.test:11434",
        ),
        embedding_provider=_FakeEmbeddingProvider(),
        database_url="sqlite:///:memory:",
        researcher_cls=_SlowResearcher,
    )

    with caplog.at_level(logging.WARNING, logger="amo_bot.current_info.research"):
        answer = provider.answer(request=request, task=task, query_plan=query_plan)

    assert answer.status == "provider_unavailable"
    assert answer.warnings == ("gpt_researcher_timeout",)
    assert answer.metadata["research_report_type"] == "deep_research"
    assert answer.metadata["deep_breadth"] == 3
    assert answer.metadata["deep_depth"] == 2
    assert answer.metadata["deep_concurrency"] == 4
    assert "current_info.GptResearcherTimeout" in caplog.text


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
