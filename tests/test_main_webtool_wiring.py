from __future__ import annotations

from amo_bot.ai.webtool_dispatcher import WebtoolCapabilityRequest
from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.repositories import WebToolRoleQuotaRepository


def test_runtime_like_webtool_dispatcher_wrapper_uses_fresh_session(tmp_path):
    db_path = tmp_path / "runtime_wiring.sqlite3"
    database_url = f"sqlite:///{db_path}"
    init_db(database_url)
    session_factory = create_session_factory(database_url)

    class _SessionBoundWebtoolCapabilityDispatcher:
        def __init__(self, *, session_factory):
            self._session_factory = session_factory

        def execute(self, request):
            from amo_bot.ai.webtool_dispatcher import WebtoolCapabilityDispatcher

            with self._session_factory() as session:
                repo = WebToolRoleQuotaRepository(session)
                dispatcher = WebtoolCapabilityDispatcher(quota_repo=repo)
                return dispatcher.execute(request)

    wrapper = _SessionBoundWebtoolCapabilityDispatcher(session_factory=session_factory)

    req = WebtoolCapabilityRequest(
        capability="websearch",
        user_id=123,
        role=Role.OWNER,
        chat_id=-1001,
        query="python",
    )

    result = wrapper.execute(req)

    assert result.allowed is False
    assert result.decision == "provider_unavailable"
    assert result.reason == "search_provider_not_configured"
