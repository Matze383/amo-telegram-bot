from amo_bot.ai.router import AIRouter, AIRouterDecision, AIRouterReasonCode


def test_default_decision_is_passthrough_noop() -> None:
    decision = AIRouter().decide(prompt="hello")
    assert decision == AIRouterDecision(
        passthrough=True,
        reason_code=AIRouterReasonCode.DEFAULT_NOOP,
    )


def test_default_decision_is_deterministic() -> None:
    router = AIRouter()
    first = router.decide(prompt="one")
    second = router.decide(prompt="two")
    assert first == second


def test_every_decision_has_exactly_one_reason_code() -> None:
    decision = AIRouter().decide(prompt="hello")
    assert isinstance(decision.reason_code, AIRouterReasonCode)
    assert decision.reason_code.value == "default_noop"
