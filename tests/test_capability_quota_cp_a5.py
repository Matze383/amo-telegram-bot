from amo_bot.ai import (
    CapabilityActorType,
    CapabilityQuotaRequest,
    CapabilityQuotaRule,
    CapabilityScopeType,
    CoreCapabilityQuotaLimiter,
    QuotaDecisionResult,
)


def _request(*, actor_id: str = "ai-1", scope_id: str = "topic-101") -> CapabilityQuotaRequest:
    return CapabilityQuotaRequest(
        capability_name="ki.memory.read",
        actor_type=CapabilityActorType.AI,
        actor_id=actor_id,
        scope_type=CapabilityScopeType.TOPIC,
        scope_id=scope_id,
    )


def test_cp_a5_quota_allows_then_denies() -> None:
    limiter = CoreCapabilityQuotaLimiter(rules={"ki.memory.read": CapabilityQuotaRule(limit=1)})

    allowed = limiter.evaluate(_request())
    denied = limiter.evaluate(_request())

    assert allowed.result is QuotaDecisionResult.ALLOW
    assert allowed.reason_code == "quota_allow"
    assert denied.result is QuotaDecisionResult.DENY
    assert denied.reason_code == "quota_exceeded"


def test_cp_a5_quota_actor_and_scope_isolation() -> None:
    limiter = CoreCapabilityQuotaLimiter(rules={"ki.memory.read": CapabilityQuotaRule(limit=1)})

    first = limiter.evaluate(_request(actor_id="ai-1", scope_id="topic-1"))
    second = limiter.evaluate(_request(actor_id="ai-1", scope_id="topic-2"))
    third = limiter.evaluate(_request(actor_id="ai-2", scope_id="topic-1"))

    assert first.result is QuotaDecisionResult.ALLOW
    assert second.result is QuotaDecisionResult.ALLOW
    assert third.result is QuotaDecisionResult.ALLOW


def test_cp_a5_quota_no_counter_leakage_after_reset() -> None:
    limiter = CoreCapabilityQuotaLimiter(rules={"ki.memory.read": CapabilityQuotaRule(limit=1)})

    first = limiter.evaluate(_request())
    denied = limiter.evaluate(_request())
    limiter.reset_counters()
    after_reset = limiter.evaluate(_request())

    assert first.result is QuotaDecisionResult.ALLOW
    assert denied.result is QuotaDecisionResult.DENY
    assert after_reset.result is QuotaDecisionResult.ALLOW
