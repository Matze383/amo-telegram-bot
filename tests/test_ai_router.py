from amo_bot.ai.router import AIRouter, AIRouterDecision


def test_default_decision_is_passthrough_noop() -> None:
    decision = AIRouter().decide(prompt="hello")
    assert decision == AIRouterDecision(passthrough=True, reason="default_noop")


def test_default_decision_is_deterministic() -> None:
    router = AIRouter()
    first = router.decide(prompt="one")
    second = router.decide(prompt="two")
    assert first == second
