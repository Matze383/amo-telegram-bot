from amo_bot.ai import AIToolCapability, AIToolDescriptor, AIToolPolicy, AIToolRegistry


def test_ai_tool_registry_register_and_lookup_by_name_and_capability() -> None:
    registry = AIToolRegistry()
    tool = AIToolDescriptor(name="weather_lookup", capability=AIToolCapability.QUERY, description="Lookup weather")

    registry.register(tool)

    assert registry.get("weather_lookup") == tool
    assert registry.get("WEATHER_LOOKUP") == tool
    assert [t.name for t in registry.list_tools()] == ["weather_lookup"]
    assert [t.name for t in registry.list_by_capability(AIToolCapability.QUERY)] == ["weather_lookup"]
    assert registry.list_by_capability(AIToolCapability.COMPUTE) == []


def test_ai_tool_registry_duplicate_registration_denied() -> None:
    registry = AIToolRegistry()
    registry.register(
        AIToolDescriptor(name="weather_lookup", capability=AIToolCapability.QUERY, description="Lookup weather")
    )

    try:
        registry.register(
            AIToolDescriptor(name="weather_lookup", capability=AIToolCapability.QUERY, description="Duplicate")
        )
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("expected duplicate tool registration to fail")


def test_ai_tool_policy_default_deny_all() -> None:
    registry = AIToolRegistry()
    registry.register(
        AIToolDescriptor(name="safe_tool", capability=AIToolCapability.READ, description="Descriptor only")
    )

    policy = AIToolPolicy()

    assert policy.is_allowed(tool_name="safe_tool") is False
    assert policy.is_allowed(tool_name="unknown_tool") is False
