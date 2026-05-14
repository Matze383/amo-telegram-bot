from __future__ import annotations

from sqlalchemy import create_engine, text

from amo_bot.ai.capability_audit import CapabilityAuditTrail, InMemoryCapabilityAuditSink
from amo_bot.ai.sql_coreplugin_cp_h1 import (
    SQLCorepluginRequest,
    SQLCorepluginService,
    SQLExecutionResult,
    SQLTemplate,
)


def _service(*, sink: InMemoryCapabilityAuditSink | None = None) -> SQLCorepluginService:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE topic_daily_memories (id INTEGER PRIMARY KEY, summary_text TEXT)"))
        conn.execute(text("CREATE TABLE v_topic_activity_summary (chat_id INTEGER, topic_id INTEGER, message_count INTEGER, active_users INTEGER)"))
        conn.execute(text("CREATE TABLE v_plugin_health_overview (plugin_id TEXT, status TEXT, last_run_at TEXT, fail_count INTEGER)"))
        for idx in range(1, 6):
            conn.execute(
                text(
                    "INSERT INTO v_topic_activity_summary(chat_id, topic_id, message_count, active_users) "
                    "VALUES (:chat_id, :topic_id, :message_count, :active_users)"
                ),
                {"chat_id": 100, "topic_id": idx, "message_count": 100 - idx, "active_users": idx},
            )
        conn.execute(
            text(
                "INSERT INTO v_plugin_health_overview(plugin_id, status, last_run_at, fail_count) "
                "VALUES ('plug-a', 'ok', '2026-05-14T20:00:00Z', 0)"
            )
        )

    templates = {
        "topic_activity_summary": SQLTemplate(
            template_id="topic_activity_summary",
            sql=(
                "SELECT chat_id, topic_id, message_count, active_users "
                "FROM v_topic_activity_summary "
                "WHERE chat_id = :chat_id "
                "ORDER BY message_count DESC "
                "LIMIT :_cap_limit"
            ),
            allowed_views=("v_topic_activity_summary",),
            allowed_params=("chat_id",),
            max_rows=3,
        ),
        "plugin_health_overview": SQLTemplate(
            template_id="plugin_health_overview",
            sql=(
                "SELECT plugin_id, status, last_run_at, fail_count "
                "FROM v_plugin_health_overview "
                "WHERE status = :status "
                "LIMIT :_cap_limit"
            ),
            allowed_views=("v_plugin_health_overview",),
            allowed_params=("status",),
            max_rows=5,
        ),
    }
    return SQLCorepluginService(
        engine=engine,
        templates=templates,
        max_rows_global=4,
        audit_trail=CapabilityAuditTrail(recorder=sink) if sink is not None else None,
    )


def test_cp_h1_unknown_template_denied() -> None:
    service = _service()

    response = service.execute(
        SQLCorepluginRequest(template_id="does.not.exist", params={}, actor_type="ki", scope_type="chat")
    )

    assert response.result is SQLExecutionResult.DENIED
    assert response.reason_code == "unknown_template"


def test_cp_h1_forbidden_table_denied() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"))

    service = SQLCorepluginService(
        engine=engine,
        templates={
            "bad": SQLTemplate(
                template_id="bad",
                sql="SELECT id, name FROM users LIMIT :_cap_limit",
                allowed_views=("users",),
                allowed_params=(),
                max_rows=5,
            )
        },
    )

    response = service.execute(SQLCorepluginRequest(template_id="bad", params={}, actor_type="ki", scope_type="chat"))

    assert response.result is SQLExecutionResult.DENIED
    assert response.reason_code == "forbidden_table"


def test_cp_h1_injection_attempt_fails() -> None:
    service = _service()

    response = service.execute(
        SQLCorepluginRequest(
            template_id="plugin_health_overview",
            params={"status": "ok' UNION SELECT * FROM topic_daily_memories --"},
            actor_type="ki",
            scope_type="chat",
        )
    )

    assert response.result is SQLExecutionResult.DENIED
    assert response.reason_code == "injection_detected"


def test_cp_h1_result_cap_and_masking() -> None:
    service = _service()

    response = service.execute(
        SQLCorepluginRequest(
            template_id="topic_activity_summary",
            params={"chat_id": 100},
            actor_type="ki",
            scope_type="chat",
        )
    )

    assert response.result is SQLExecutionResult.SUCCESS
    assert response.reason_code == "ok"
    assert response.row_count == 3
    assert response.truncated is True
    assert all(row["chat_id"] == "***MASKED***" for row in response.rows)


def test_cp_h1_unknown_param_denied() -> None:
    service = _service()

    response = service.execute(
        SQLCorepluginRequest(
            template_id="topic_activity_summary",
            params={"chat_id": 100, "extra": "x"},
            actor_type="ki",
            scope_type="chat",
        )
    )

    assert response.result is SQLExecutionResult.DENIED
    assert response.reason_code == "invalid_params"


def test_cp_h1_default_deny_missing_actor_scope() -> None:
    service = _service()

    response = service.execute(SQLCorepluginRequest(template_id="topic_activity_summary", params={"chat_id": 100}))

    assert response.result is SQLExecutionResult.DENIED
    assert response.reason_code == "missing_or_invalid_actor"


def test_cp_h1_default_deny_invalid_actor_scope() -> None:
    service = _service()

    response = service.execute(
        SQLCorepluginRequest(
            template_id="topic_activity_summary",
            params={"chat_id": 100},
            actor_type="system",
            scope_type="global",
        )
    )

    assert response.result is SQLExecutionResult.DENIED
    assert response.reason_code == "missing_or_invalid_actor"


def test_cp_h1_explicit_deny_elevated_context_flags() -> None:
    service = _service()

    for flag in ("admin", "tunnel", "elevated"):
        response = service.execute(
            SQLCorepluginRequest(
                template_id="topic_activity_summary",
                params={"chat_id": 100},
                actor_type="ki",
                scope_type="chat",
                context_flags=(flag,),
            )
        )

        assert response.result is SQLExecutionResult.DENIED
        assert response.reason_code == "elevated_context_denied"


def test_cp_h1_audit_records_denied_and_allowed_without_sql_or_params() -> None:
    sink = InMemoryCapabilityAuditSink()
    service = _service(sink=sink)

    denied = service.execute(
        SQLCorepluginRequest(
            template_id="topic_activity_summary",
            params={"chat_id": 100, "extra": "sensitive-value"},
            actor_type="ki",
            scope_type="chat",
        )
    )
    assert denied.result is SQLExecutionResult.DENIED
    assert denied.reason_code == "invalid_params"

    allowed = service.execute(
        SQLCorepluginRequest(
            template_id="topic_activity_summary",
            params={"chat_id": 100},
            actor_type="ki",
            scope_type="chat",
        )
    )
    assert allowed.result is SQLExecutionResult.SUCCESS

    decisions = [event for event in sink.events if event.summary == "policy_decision"]
    assert {event.reason_code for event in decisions} >= {"invalid_params", "ok"}

    rendered = "\n".join(
        f"{event.request_id}|{event.summary}|{event.reason_code or ''}|{event.details}" for event in sink.events
    )
    assert "sensitive-value" not in rendered
    assert "select " not in rendered.lower()
    assert "from v_topic_activity_summary" not in rendered.lower()
