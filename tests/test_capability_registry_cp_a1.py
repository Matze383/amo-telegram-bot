from amo_bot.ai.capability_registry import CapabilityDescriptor, CapabilityRegistry


def test_registry_list_and_get_are_deterministic() -> None:
    registry = CapabilityRegistry(
        descriptors=[
            CapabilityDescriptor(
                id="ki.memory.read",
                version="1.0.0",
                risk_level="low",
                actor_types=("ki",),
                scopes=("private", "topic"),
                default_enabled=False,
            ),
            CapabilityDescriptor(
                id="plugin.notify.send",
                version="1.0.0",
                risk_level="medium",
                actor_types=("user_plugin",),
                scopes=("topic",),
                default_enabled=False,
            ),
        ]
    )

    listed = registry.list_capabilities()
    assert [item.id for item in listed] == ["ki.memory.read", "plugin.notify.send"]

    found = registry.get("KI.MEMORY.READ")
    assert found is not None
    assert found.id == "ki.memory.read"


def test_unknown_capability_denies_cleanly() -> None:
    registry = CapabilityRegistry()
    decision = registry.evaluate("does.not.exist")

    assert decision.allowed is False
    assert decision.reason_code == "unknown_capability"


def test_registered_capabilities_are_default_deny() -> None:
    registry = CapabilityRegistry(
        descriptors=[
            CapabilityDescriptor(
                id="ki.topic.summarize",
                version="1.0.0",
                risk_level="low",
                actor_types=("ki",),
                scopes=("topic",),
                default_enabled=False,
            )
        ]
    )

    descriptor = registry.get("ki.topic.summarize")
    assert descriptor is not None
    assert descriptor.default_enabled is False

    decision = registry.evaluate("ki.topic.summarize")
    assert decision.allowed is False
    assert decision.reason_code == "default_deny"
