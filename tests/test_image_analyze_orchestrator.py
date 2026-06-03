from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from amo_bot.ai.image_analyze_orchestrator import (
    FakeImageAnalyzeRuntimeProvider,
    ImageAnalyzeOrchestrator,
    ImageAnalyzeOrchestratorRequest,
    ImageAnalyzeProviderError,
    ImageAnalyzeProviderRequest,
    ImageAnalyzeProviderResult,
)
from amo_bot.auth.roles import Role
from amo_bot.db.base import create_session_factory
from amo_bot.db.init_db import init_db
from amo_bot.db.models import ImageAnalyzeAuditEvent, ImageAnalyzeQuotaCounter


def _image() -> dict[str, object]:
    return {
        "ok": True,
        "type_hint": "image",
        "file_unique_id": "uniq-1",
        "media_ref": {"reason_code": "ok", "mime_type": "image/png", "bytes_stored": 99},
    }


def _req(*, reply_to_image: dict[str, object] | None = None) -> ImageAnalyzeOrchestratorRequest:
    return ImageAnalyzeOrchestratorRequest(
        user_id=7,
        role=Role.NORMAL,
        chat_id=1001,
        message_thread_id=11,
        command="analyze_image",
        provider="fake",
        reply_to_image=_image() if reply_to_image is None else reply_to_image,
        prompt="what is visible?",
    )


class _RecordingProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[ImageAnalyzeProviderRequest] = []

    def analyze(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult:
        self.calls.append(request)
        return ImageAnalyzeProviderResult(provider=self.name, summary="recorded")


class _TimeoutProvider:
    name = "vision"

    def analyze(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult:
        raise TimeoutError("provider timed out")


class _ErrorProvider:
    name = "vision"

    def analyze(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult:
        raise ImageAnalyzeProviderError("provider failed")


class _EmptyProvider:
    name = "vision"

    def analyze(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult:
        return ImageAnalyzeProviderResult(provider=self.name, summary="   ")


def test_successful_runtime_seam_invokes_provider_once() -> None:
    provider = _RecordingProvider()
    orchestrator = ImageAnalyzeOrchestrator(provider=provider)

    result = orchestrator.evaluate_and_maybe_invoke_provider(
        request=_req(),
        now=datetime(2026, 5, 21, 10, 0, tzinfo=UTC),
    )

    assert result.allowed is True
    assert result.outcome == "allowed"
    assert result.provider_called is True
    assert result.provider_result == ImageAnalyzeProviderResult(provider="fake", summary="recorded")
    assert result.day == "2026-05-21"
    assert len(provider.calls) == 1
    assert provider.calls[0].image_ref == "telegram-file:uniq-1"
    assert "Antworte standardmäßig auf Deutsch" in provider.calls[0].prompt
    assert "Nutzeranfrage:\nwhat is visible?" in provider.calls[0].prompt
    assert "system-provided" not in provider.calls[0].prompt
    assert "higher priority" not in provider.calls[0].prompt


def test_fake_provider_is_deterministic() -> None:
    provider = FakeImageAnalyzeRuntimeProvider()

    result = provider.analyze(
        ImageAnalyzeProviderRequest(
            image_ref="telegram-image",
            prompt="describe",
            user_id=1,
            chat_id=2,
            message_thread_id=None,
        )
    )

    assert result.provider == "fake"
    assert result.summary == "fake image analysis for telegram-image: describe"


def test_missing_image_denies_before_provider_call() -> None:
    provider = _RecordingProvider()
    orchestrator = ImageAnalyzeOrchestrator(provider=provider)

    result = orchestrator.evaluate_and_maybe_invoke_provider(
        request=_req(reply_to_image={"ok": False, "reason_code": "missing_image"}),
    )

    assert result.allowed is False
    assert result.outcome == "missing_image"
    assert provider.calls == []


def test_invalid_image_denies_before_provider_call() -> None:
    provider = _RecordingProvider()
    orchestrator = ImageAnalyzeOrchestrator(provider=provider)

    result = orchestrator.evaluate_and_maybe_invoke_provider(
        request=_req(reply_to_image={"ok": False, "reason_code": "invalid_type"}),
    )

    assert result.allowed is False
    assert result.outcome == "invalid_type"
    assert provider.calls == []


def test_timeout_error_maps_to_provider_timeout() -> None:
    orchestrator = ImageAnalyzeOrchestrator(provider=_TimeoutProvider())

    result = orchestrator.evaluate_and_maybe_invoke_provider(request=_req())

    assert result.allowed is False
    assert result.outcome == "provider_timeout"


def test_provider_error_maps_to_provider_error() -> None:
    orchestrator = ImageAnalyzeOrchestrator(provider=_ErrorProvider())

    result = orchestrator.evaluate_and_maybe_invoke_provider(request=_req())

    assert result.allowed is False
    assert result.outcome == "provider_error"


def test_empty_provider_result_maps_to_provider_empty() -> None:
    orchestrator = ImageAnalyzeOrchestrator(provider=_EmptyProvider())

    result = orchestrator.evaluate_and_maybe_invoke_provider(request=_req())

    assert result.allowed is False
    assert result.outcome == "provider_empty"


def test_image_mime_and_size_guards_deny_before_provider() -> None:
    provider = _RecordingProvider()
    orchestrator = ImageAnalyzeOrchestrator(
        provider=provider,
        allowed_mime_types={"image/png"},
        max_image_bytes=100,
    )

    bad_mime = orchestrator.evaluate_and_maybe_invoke_provider(
        request=_req(reply_to_image={"ok": True, "type_hint": "image", "media_ref": {"mime_type": "image/jpeg", "bytes_stored": 80}})
    )
    assert bad_mime.allowed is False
    assert bad_mime.outcome == "invalid_type"

    too_large = orchestrator.evaluate_and_maybe_invoke_provider(
        request=_req(reply_to_image={"ok": True, "type_hint": "image", "media_ref": {"mime_type": "image/png", "bytes_stored": 101}})
    )
    assert too_large.allowed is False
    assert too_large.outcome == "oversize"

    assert provider.calls == []


def test_deny_hook_prevents_provider_call() -> None:
    provider = _RecordingProvider()
    orchestrator = ImageAnalyzeOrchestrator(provider=provider, deny_hook=lambda _request: "policy_denied")

    result = orchestrator.evaluate_and_maybe_invoke_provider(request=_req())

    assert result.allowed is False
    assert result.outcome == "policy_denied"
    assert result.provider_called is False
    assert provider.calls == []


def test_topic_gate_quota_and_audit(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'img_orch_quota.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)

    with sf() as session:
        from amo_bot.db.repositories import TopicAgentMemoryRepository

        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=1001, topic_id=11, image_analysis_mode="disabled")
        session.commit()

    provider = _RecordingProvider()
    orchestrator = ImageAnalyzeOrchestrator(
        provider=provider,
        session_factory=sf,
        role_daily_quota={Role.NORMAL: 2, Role.VIP: None, Role.IGNORE: 0},
    )

    denied_topic = orchestrator.evaluate_and_maybe_invoke_provider(
        request=_req(),
        now=datetime(2026, 5, 21, 10, 0, tzinfo=UTC),
    )
    assert denied_topic.allowed is False
    assert denied_topic.outcome == "topic_disabled"
    assert provider.calls == []

    with sf() as session:
        from amo_bot.db.repositories import TopicAgentMemoryRepository

        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=1001, topic_id=11, image_analysis_mode="enabled")
        session.commit()

    allowed_1 = orchestrator.evaluate_and_maybe_invoke_provider(
        request=_req(),
        now=datetime(2026, 5, 21, 10, 1, tzinfo=UTC),
    )
    assert allowed_1.allowed is True
    assert allowed_1.count == 1

    allowed_2 = orchestrator.evaluate_and_maybe_invoke_provider(
        request=_req(),
        now=datetime(2026, 5, 21, 10, 2, tzinfo=UTC),
    )
    assert allowed_2.allowed is True
    assert allowed_2.count == 2

    denied_quota = orchestrator.evaluate_and_maybe_invoke_provider(
        request=_req(),
        now=datetime(2026, 5, 21, 10, 3, tzinfo=UTC),
    )
    assert denied_quota.allowed is False
    assert denied_quota.outcome == "quota_exceeded"
    assert len(provider.calls) == 2

    allowed_next_day = orchestrator.evaluate_and_maybe_invoke_provider(
        request=_req(),
        now=datetime(2026, 5, 22, 9, 0, tzinfo=UTC),
    )
    assert allowed_next_day.allowed is True
    assert allowed_next_day.count == 1

    with sf() as session:
        counters = session.scalars(select(ImageAnalyzeQuotaCounter).order_by(ImageAnalyzeQuotaCounter.day)).all()
        assert [(c.day, c.count) for c in counters] == [("2026-05-21", 2), ("2026-05-22", 1)]

        audits = session.scalars(select(ImageAnalyzeAuditEvent).order_by(ImageAnalyzeAuditEvent.id)).all()
        assert len(audits) >= 5
        assert audits[-1].outcome == "allowed"


def test_disabled_and_unlimited_roles(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'img_orch_roles.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)
    with sf() as session:
        from amo_bot.db.repositories import TopicAgentMemoryRepository

        repo = TopicAgentMemoryRepository(session)
        repo.upsert_config(scope_type="topic", chat_id=1001, topic_id=11, image_analysis_mode="enabled")
        session.commit()

    provider = _RecordingProvider()
    orchestrator = ImageAnalyzeOrchestrator(
        provider=provider,
        session_factory=sf,
        role_daily_quota={Role.IGNORE: 0, Role.VIP: None},
    )

    disabled = orchestrator.evaluate_and_maybe_invoke_provider(
        request=ImageAnalyzeOrchestratorRequest(
            user_id=9,
            role=Role.IGNORE,
            chat_id=1001,
            message_thread_id=11,
            command="analyze_image",
            reply_to_image=_image(),
            prompt="x",
        ),
    )
    assert disabled.allowed is False
    assert disabled.outcome == "role_disabled"

    for _ in range(4):
        res = orchestrator.evaluate_and_maybe_invoke_provider(
            request=ImageAnalyzeOrchestratorRequest(
                user_id=10,
                role=Role.VIP,
                chat_id=1001,
                message_thread_id=11,
                command="analyze_image",
                reply_to_image=_image(),
                prompt="x",
            ),
        )
        assert res.allowed is True

    assert len(provider.calls) == 4


def test_invalid_auto_image_is_audited_before_provider_call(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'img_orch_invalid_audit.db'}"
    init_db(db_url)
    sf = create_session_factory(db_url)
    provider = _RecordingProvider()
    orchestrator = ImageAnalyzeOrchestrator(provider=provider, session_factory=sf)

    result = orchestrator.evaluate_and_maybe_invoke_provider(
        request=_req(reply_to_image={"ok": False, "reason_code": "download_failed"}),
        now=datetime(2026, 5, 22, 8, 0, tzinfo=UTC),
    )

    assert result.allowed is False
    assert result.outcome == "download_failed"
    assert provider.calls == []
    with sf() as session:
        audits = session.scalars(select(ImageAnalyzeAuditEvent)).all()
    assert len(audits) == 1
    assert audits[0].command == "analyze_image"
    assert audits[0].outcome == "download_failed"


class _AsyncRecordingProvider:
    name = "async-vision"

    def __init__(self) -> None:
        self.calls: list[ImageAnalyzeProviderRequest] = []

    async def analyze_async(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult:
        self.calls.append(request)
        return ImageAnalyzeProviderResult(provider=self.name, summary="async recorded")

    def analyze(self, request: ImageAnalyzeProviderRequest) -> ImageAnalyzeProviderResult:
        raise AssertionError("sync analyze must not be called from async orchestration path")


async def _run_async_provider(orchestrator: ImageAnalyzeOrchestrator, provider: _AsyncRecordingProvider):
    return await orchestrator.evaluate_and_maybe_invoke_provider_async(request=_req(), provider_call=provider)


def test_async_provider_path_awaits_provider_without_sync_analyze() -> None:
    provider = _AsyncRecordingProvider()
    orchestrator = ImageAnalyzeOrchestrator(provider=provider)

    import asyncio

    result = asyncio.run(_run_async_provider(orchestrator, provider))

    assert result.allowed is True
    assert result.provider_called is True
    assert result.provider_result == ImageAnalyzeProviderResult(provider="async-vision", summary="async recorded")
    assert len(provider.calls) == 1
