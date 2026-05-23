from __future__ import annotations

from storage.audit_read_models import shape_audit_dashboard_row


def test_shapes_bash_audit_subject_and_metric_from_safe_metadata() -> None:
    row = shape_audit_dashboard_row(
        event_id="019e5000-0000-7000-8000-000000000001",
        session_id="019e5000-0000-7000-8000-000000000002",
        event_type="tool_execution",
        actor_type="model",
        action="bash",
        decision={"effect": "allow"},
        metadata={
            "args": {
                "commandPreview": "git status --short",
                "commandLength": 30,
                "cwd": "/repo",
            },
            "durationMs": 12,
            "isError": False,
            "truncation": {"outputLines": 4},
            "redactionStatus": "not_required",
        },
        occurred_at="2026-05-23T00:00:00+00:00",
        age_seconds=1,
    )

    assert row.subject == "git status --short ... (full 30 chars)"
    assert row.metric == "12ms · 4 lines"
    assert row.cwd == "/repo"
    assert row.effect == "allow"


def test_unknown_tool_hides_args_without_raw_join_fields() -> None:
    row = shape_audit_dashboard_row(
        event_id="019e5000-0000-7000-8000-000000000001",
        session_id="019e5000-0000-7000-8000-000000000002",
        event_type="tool_execution",
        actor_type="model",
        action="custom",
        decision={"effect": "block"},
        metadata={"args": {"path": "a.txt"}, "isError": True},
        occurred_at="2026-05-23T00:00:00+00:00",
    )

    payload = row.to_dict()
    assert row.subject == "args hidden; unsupported tool mapping"
    assert row.metric == "ERR"
    assert payload["tool"] == "custom"
    assert "args" not in payload
