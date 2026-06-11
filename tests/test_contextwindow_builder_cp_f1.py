from amo_bot.ai.contextwindow_builder import ContextWindowSource, build_contextwindow_v1


def test_cp_f1_budget_cap() -> None:
    result = build_contextwindow_v1(
        token_budget=16,
        sources=[
            ContextWindowSource(source_id="a", source_type="soul", text="A" * 20, priority=1),
            ContextWindowSource(source_id="b", source_type="task", text="B" * 40, priority=2),
            ContextWindowSource(source_id="c", source_type="context", text="C" * 40, priority=3),
        ],
    )

    assert result.used_tokens <= result.token_budget
    assert [x.source_id for x in result.included] == ["a", "b"]
    assert [x.source_id for x in result.excluded] == ["c"]
    assert result.excluded[0].reason == "budget_exceeded"


def test_cp_f1_tool_result_source_is_bounded_before_budgeting() -> None:
    marker = "FULL_RAW_TOOL_OUTPUT_SHOULD_NOT_SURVIVE"
    result = build_contextwindow_v1(
        token_budget=1000,
        sources=[
            ContextWindowSource(
                source_id="web-fetch",
                source_type="tool_result",
                text=("A" * 2500) + marker,
                priority=1,
            ),
        ],
    )

    assert [x.source_id for x in result.included] == ["web-fetch"]
    assert len(result.context_text) <= 1600
    assert "oversized tool result omitted from active context" in result.context_text
    assert marker not in result.context_text
    assert result.included[0].metadata["truncated"] is True


def test_cp_f1_sensitive_suppression_default() -> None:
    marker = "CP_F1_BENIGN_MARKER_SENSITIVE_BLOCKED"
    result = build_contextwindow_v1(
        token_budget=100,
        sources=[
            ContextWindowSource(source_id="safe", source_type="soul", text="safe", priority=1),
            ContextWindowSource(
                source_id="private-memory",
                source_type="task",
                text=marker,
                priority=2,
                sensitive=True,
                allow_sensitive=False,
                metadata={"class": "public", "note": "free-form-should-drop", "count": 2, "flag": True},
            ),
        ],
    )

    assert [x.source_id for x in result.included] == ["safe"]
    assert [x.source_id for x in result.excluded] == ["private-memory"]
    assert result.excluded[0].reason == "sensitive_excluded_default"
    assert marker not in str(result.excluded[0])
    assert result.excluded[0].metadata == {
        "class": "public",
        "count": 2,
        "flag": True,
        "priority": 2,
        "sensitive": True,
    }


def test_cp_f1_sensitive_suppression_still_applies_when_allow_sensitive_true() -> None:
    result = build_contextwindow_v1(
        token_budget=100,
        sources=[
            ContextWindowSource(source_id="safe", source_type="soul", text="safe", priority=1),
            ContextWindowSource(
                source_id="private-memory",
                source_type="task",
                text="CP_F1_BENIGN_MARKER_ALLOW_TRUE_STILL_BLOCKED",
                priority=2,
                sensitive=True,
                allow_sensitive=True,
                metadata={"class": "public", "free_text": "drop-me", "risk": "high"},
            ),
        ],
    )

    assert [x.source_id for x in result.included] == ["safe"]
    assert [x.source_id for x in result.excluded] == ["private-memory"]
    assert result.excluded[0].reason == "sensitive_excluded_default"
    assert result.excluded[0].metadata == {"class": "public", "risk": "high", "priority": 2, "sensitive": True}


def test_cp_f1_mislabelled_memory_source_with_sensitive_false_is_excluded() -> None:
    result = build_contextwindow_v1(
        token_budget=100,
        sources=[
            ContextWindowSource(source_id="safe", source_type="soul", text="safe", priority=1),
            ContextWindowSource(
                source_id="memory-like",
                source_type="daily-memory",
                text="CP_F1_BENIGN_MARKER_MEMORY_EXCLUDED",
                priority=2,
                sensitive=False,
                allow_sensitive=True,
                metadata={"class": "public", "topic": "engineering"},
            ),
        ],
    )

    assert [x.source_id for x in result.included] == ["safe"]
    assert [x.source_id for x in result.excluded] == ["memory-like"]
    assert result.excluded[0].reason == "sensitive_source_type_excluded"
    assert result.excluded[0].metadata == {"class": "public", "topic": "engineering", "priority": 2}


def test_cp_f1_source_type_allowlist_excludes_unknown_types() -> None:
    result = build_contextwindow_v1(
        token_budget=100,
        sources=[
            ContextWindowSource(source_id="safe", source_type="soul", text="safe", priority=1),
            ContextWindowSource(
                source_id="unknown",
                source_type="external_blob",
                text="CP_F1_BENIGN_MARKER_UNKNOWN_SOURCE",
                priority=2,
                metadata={"class": "public", "topic": "engineering"},
            ),
        ],
    )

    assert [x.source_id for x in result.included] == ["safe"]
    assert [x.source_id for x in result.excluded] == ["unknown"]
    assert result.excluded[0].reason == "source_type_not_allowed"


def test_cp_f1_metadata_validation_and_bounds() -> None:
    result = build_contextwindow_v1(
        token_budget=100,
        sources=[
            ContextWindowSource(
                source_id="meta",
                source_type="task",
                text="ok",
                priority=1,
                metadata={
                    "class": "PUBLIC",
                    "scope": "session",
                    "kind": "context",
                    "tag": "normal",
                    "topic": "engineering",
                    "reason_code": "included",
                    "priority_hint": "high",
                    "risk": "critical",
                    "count": 7,
                    "flag": True,
                    "unexpected": "drop",
                    "count": 2_000_000,
                },
            )
        ],
    )

    assert [x.source_id for x in result.included] == ["meta"]
    assert result.included[0].metadata == {
        "class": "public",
        "scope": "session",
        "kind": "context",
        "tag": "normal",
        "topic": "engineering",
        "reason_code": "included",
        "priority_hint": "high",
        "risk": "critical",
        "flag": True,
        "priority": 1,
        "estimated_tokens": 1,
    }


def test_cp_f1_deterministic_ordering() -> None:
    first = build_contextwindow_v1(
        token_budget=100,
        sources=[
            ContextWindowSource(source_id="z", source_type="task", text="z", priority=2),
            ContextWindowSource(source_id="a", source_type="soul", text="a", priority=1),
            ContextWindowSource(source_id="m", source_type="task", text="m", priority=2),
        ],
    )
    second = build_contextwindow_v1(
        token_budget=100,
        sources=[
            ContextWindowSource(source_id="m", source_type="task", text="m", priority=2),
            ContextWindowSource(source_id="z", source_type="task", text="z", priority=2),
            ContextWindowSource(source_id="a", source_type="soul", text="a", priority=1),
        ],
    )

    assert [x.source_id for x in first.included] == ["a", "m", "z"]
    assert [x.source_id for x in second.included] == ["a", "m", "z"]
    assert first.context_text == second.context_text
