from amo_bot.ai.model_policy import AIModelPolicyConfig, AIModelTaskType, infer_task_type, route_model


def test_infers_sports_and_news_tasks_from_prompt() -> None:
    assert infer_task_type("Wer spielt heute Bundesliga?") is AIModelTaskType.SPORTS
    assert infer_task_type("latest news about the release") is AIModelTaskType.NEWS
    assert infer_task_type("Explain quantum computing") is AIModelTaskType.GENERAL


def test_prefers_thinking_model_for_configured_task_types() -> None:
    route = route_model(
        prompt="Summarize researched sources",
        default_model="qwen3",
        default_timeout_seconds=20.0,
        default_max_prompt_chars=4000,
        task_type="answer_synthesis",
        config=AIModelPolicyConfig(
            enabled=True,
            thinking_model="kimi-thinking",
            non_thinking_model="qwen-fast",
            thinking_timeout_seconds=45.0,
            non_thinking_timeout_seconds=10.0,
            thinking_budget_max_prompt_chars=8000,
            non_thinking_budget_max_prompt_chars=2000,
        ),
    )

    assert route.task_type is AIModelTaskType.ANSWER_SYNTHESIS
    assert route.model == "kimi-thinking"
    assert route.think is True
    assert route.timeout_seconds == 45.0
    assert route.max_prompt_chars == 8000
    assert route.fallback_model == "qwen-fast"
    assert route.fallback_think is False
    assert route.fallback_timeout_seconds == 10.0
    assert route.fallback_max_prompt_chars == 2000


def test_routes_simple_low_budget_prompt_to_non_thinking_model() -> None:
    route = route_model(
        prompt="thanks",
        default_model="qwen3",
        default_timeout_seconds=20.0,
        default_max_prompt_chars=4000,
        config=AIModelPolicyConfig(
            enabled=True,
            thinking_model="kimi-thinking",
            non_thinking_model="qwen-fast",
        ),
    )

    assert route.task_type is AIModelTaskType.SIMPLE
    assert route.model == "qwen-fast"
    assert route.think is False
    assert route.decision == "non_thinking"


def test_routes_simple_answer_synthesis_to_non_thinking_model() -> None:
    route = route_model(
        prompt="What is 2+2?",
        default_model="qwen3",
        default_timeout_seconds=20.0,
        default_max_prompt_chars=4000,
        task_type="answer_synthesis",
        config=AIModelPolicyConfig(
            enabled=True,
            thinking_model="kimi-thinking",
            non_thinking_model="qwen-fast",
        ),
    )

    assert route.task_type is AIModelTaskType.ANSWER_SYNTHESIS
    assert route.model == "qwen-fast"
    assert route.think is False
    assert route.decision == "non_thinking"


def test_policy_disabled_keeps_default_model() -> None:
    route = route_model(
        prompt="latest sports news",
        default_model="qwen3",
        default_timeout_seconds=20.0,
        default_max_prompt_chars=4000,
        config=AIModelPolicyConfig(
            enabled=False,
            thinking_model="kimi-thinking",
            non_thinking_model="qwen-fast",
        ),
    )

    assert route.model == "qwen3"
    assert route.think is False
    assert route.decision == "default"
    assert route.reason == "policy_disabled"
