from amo_bot.ai.webtool_subagent import WebtoolSubagentRequest
from amo_bot.ai.webtool_provider_adapter import RealWebsearchProviderAdapter
from amo_bot.ai.webtool_subagent import create_webtool_subagent_service
from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import WebToolRoleQuotaRepository


class _DummySearchProvider:
    def search(self, *, query: str, locale: str, safesearch: str, max_results: int):
        _ = (query, locale, safesearch, max_results)
        return (
            type("R", (), {"title": "t", "url": "https://example.com", "snippet": "s"})(),
        )


def test_real_websearch_adapter_accepts_webtool_quota_repo_without_attributeerror(monkeypatch, tmp_path):
    import amo_bot.ai.webtool_provider_adapter as m

    monkeypatch.setattr(m, "_CorepluginSearchProviderAdapter", lambda: _DummySearchProvider())

    db_path = tmp_path / "runtime_quota_compat.sqlite3"
    database_url = f"sqlite:///{db_path}"
    init_db(database_url)
    session_factory = create_session_factory(database_url)

    with session_factory() as session:
        repo = WebToolRoleQuotaRepository(session)
        service = create_webtool_subagent_service(
            quota_repo=repo,
            search_provider=RealWebsearchProviderAdapter(quota_limiter=repo),
            browser_provider=None,
        )
        req = WebtoolSubagentRequest(
            operation_type="websearch",
            user_id=1,
            role=Role.OWNER,
            chat_id=-1001,
            topic_id=None,
            day="2026-05-30",
            query="btc price",
            locale="en",
            max_results=3,
        )
        result = service.execute(req)

    assert result.reason in {"search_completed", "empty_result", "provider_error", "provider_timeout"}
    assert result.reason != "search_failed"
    assert result.metadata.get("error_class") != "AttributeError"
