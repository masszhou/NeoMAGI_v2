from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ai_provider.types import (
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
)
from cli.core.compaction.models import BranchSummaryResult, CompactionResult
from cli.core.session_types import BashExecutionMessage
from cli.core.session_export import (
    SessionExportError,
    build_session_export_envelope,
    export_session_html,
    export_session_pi_jsonl,
    export_session_structured_json,
    validate_session_export_envelope,
)
from cli.core.session_manager import SessionManager
from storage.in_memory_session_repository import InMemorySessionRepository

FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "pi_compat" / "session_export_full_demo"


def _clock() -> datetime:
    return datetime(2026, 5, 6, 0, 0, 3, 456, tzinfo=UTC)


def test_structured_export_contains_full_tree_analytics_and_redaction(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path, source={"Authorization": "Bearer secret-token"})
    manager.rename_session(session.id, "demo")
    manager.append_model_change(session.id, provider="openai", model_id="gpt-5.4")
    manager.append_thinking_level_change(session.id, thinking_level="high")
    user = manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="read .env")], timestamp=1),
    )
    manager.append_message(session.id, _assistant("I will read it.", timestamp=2))
    manager.record_tool_execution_start(
        session_id=session.id,
        tool_call_id="call-read",
        tool_name="read",
        args={"path": ".env", "apiKeyEnv": "OPENAI_API_KEY"},
        runtime_session_id="runtime-1",
        run_id="run-1",
    )
    manager.append_message(
        session.id,
        ToolResultMessage(
            toolCallId="call-read",
            toolName="read",
            content=[TextContent(text="OPENAI_API_KEY=sk-test-secret")],
            details={
                "path": ".env",
                "durationMs": 7,
                "truncation": {"omittedBytes": 100},
                "policyDecision": {"effect": "allow"},
                "sandbox": {"mode": "workspace"},
            },
            isError=False,
            timestamp=3,
        ),
    )
    manager.append_message(
        session.id,
        ToolResultMessage(
            toolCallId="call-error",
            toolName="read",
            content=[TextContent(text="missing file")],
            details={"durationMs": 3},
            isError=True,
            timestamp=4,
        ),
    )
    manager.append_message(
        session.id,
        BashExecutionMessage(
            command="echo excluded",
            output="excluded output",
            exitCode=0,
            cancelled=False,
            truncated=False,
            timestamp=5,
            excludeFromContext=True,
        ),
    )
    manager.append_message(
        session.id,
        _assistant("partial before abort", timestamp=6).model_copy(
            update={"stop_reason": "aborted"}
        ),
    )
    manager.append_compaction(
        session.id,
        CompactionResult(
            summary="kept recent",
            firstKeptEntryId=user.pi_export_id,
            tokensBefore=120,
            tokensAfter=40,
            reason="manual",
        ),
    )
    manager.append_branch_summary(
        session.id,
        target_entry_id=user.pi_export_id,
        result=BranchSummaryResult(summary="branch recap", fromId=user.pi_export_id),
    )

    envelope = build_session_export_envelope(manager.repository, session.id, clock=_clock)
    payload = envelope.model_dump(by_alias=True, exclude_none=True)
    expected_subset = json.loads((FIXTURE_ROOT / "expected_subset.json").read_text())
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["type"] == expected_subset["type"]
    assert payload["schemaVersion"] == expected_subset["schemaVersion"]
    assert payload["generatedAt"] == expected_subset["generatedAt"]
    assert payload["pi"]["header"]["id"] == session.id
    assert len(payload["pi"]["entries"]) >= 7
    assert payload["neomagi"]["session"]["name"] == "demo"
    assert payload["neomagi"]["session"]["modelId"] == "gpt-5.4"
    assert payload["neomagi"]["session"]["thinkingLevel"] == "high"
    assert payload["neomagi"]["activePath"][-1] == envelope.pi.leaf_id
    entry_types = {entry["type"] for entry in payload["pi"]["entries"]}
    assert set(expected_subset["requiredEntryTypes"]).issubset(entry_types)
    message_roles = {
        entry["message"]["role"]
        for entry in payload["pi"]["entries"]
        if entry["type"] == "message"
    }
    assert set(expected_subset["requiredMessageRoles"]).issubset(message_roles)
    assert any(
        entry["type"] == "message"
        and entry["message"].get("role") == "assistant"
        and entry["message"].get("stopReason") == "aborted"
        for entry in payload["pi"]["entries"]
    )
    assert any(
        entry["type"] == "message"
        and entry["message"].get("role") == "toolResult"
        and entry["message"].get("isError") is True
        for entry in payload["pi"]["entries"]
    )

    tool = payload["neomagi"]["analytics"]["toolExecutions"][0]
    assert tool["toolCallId"] == expected_subset["toolExecution"]["toolCallId"]
    assert tool["toolName"] == expected_subset["toolExecution"]["toolName"]
    assert tool["durationMs"] == expected_subset["toolExecution"]["durationMs"]
    assert tool["truncation"] == {"omittedBytes": 100}
    assert tool["policyDecision"] == {"effect": "allow"}
    assert tool["sandbox"] == {"mode": "workspace"}
    assert payload["neomagi"]["analytics"]["usage"]["cacheRead"] == 4
    assert payload["neomagi"]["analytics"]["usage"]["cost"]["total"] == 0.08
    assert payload["neomagi"]["redaction"]["status"] == "applied"
    diagnostic_ids = {item["ruleId"] for item in payload["neomagi"]["diagnostics"]}
    assert set(expected_subset["requiredDiagnosticRuleIds"]).issubset(diagnostic_ids)
    assert "sk-test-secret" not in rendered
    assert "Bearer secret-token" not in rendered
    assert "OPENAI_API_KEY" in rendered


def test_missing_usage_cost_exports_null_and_diagnostic(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        AssistantMessage(
            content=[TextContent(text="no cost")],
            api="responses",
            provider="openai",
            model="gpt-5.4",
            usage=Usage(input=3, output=2, totalTokens=5),
            stopReason="stop",
            timestamp=1,
        ),
    )

    path = export_session_structured_json(
        manager.repository,
        session.id,
        "missing-cost.session.json",
        allowed_root=tmp_path,
        clock=_clock,
    )
    payload = json.loads(path.read_text())

    assert payload["neomagi"]["analytics"]["usage"]["cost"] is None
    assert any(
        item["ruleId"] == "usage_cost_unavailable"
        for item in payload["neomagi"]["diagnostics"]
    )


def test_structured_json_and_html_exports_are_deterministic_and_local(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="<script>alert(1)</script>")], timestamp=1),
    )
    manager.append_message(session.id, _assistant("done", timestamp=2))

    json_path = export_session_structured_json(
        manager.repository,
        session.id,
        "demo.session.json",
        allowed_root=tmp_path,
        clock=_clock,
    )
    html_path = export_session_html(
        manager.repository,
        session.id,
        "demo.html",
        allowed_root=tmp_path,
        clock=_clock,
    )

    parsed = validate_session_export_envelope(json.loads(json_path.read_text()))
    assert parsed.generated_at == "2026-05-06T00:00:03+00:00"
    html = html_path.read_text()
    assert "http://" not in html
    assert "https://" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    marker = 'id="pi-session-data" type="application/json" data-base64="'
    encoded = html.split(marker, 1)[1].split('"', 1)[0]
    session_data = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert session_data["header"]["id"] == session.id


def test_pi_jsonl_exports_only_active_branch_with_linear_parent_ids(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    first = manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="first")], timestamp=1),
    )
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="side")], timestamp=2),
    )
    manager.select_leaf(session.id, first.pi_export_id)
    active = manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="active")], timestamp=3),
    )

    path = export_session_pi_jsonl(
        manager.repository,
        session.id,
        "branch.pi.jsonl",
        allowed_root=tmp_path,
        clock=_clock,
    )
    lines = [json.loads(line) for line in path.read_text().splitlines()]

    assert lines[0]["type"] == "session"
    assert lines[0]["timestamp"] == "2026-05-06T00:00:03+00:00"
    assert "parentSession" not in lines[0]
    assert [entry["id"] for entry in lines[1:]] == [first.pi_export_id, active.pi_export_id]
    assert [entry.get("parentId") for entry in lines[1:]] == [None, first.pi_export_id]


def test_unknown_export_schema_version_fails_clearly() -> None:
    with pytest.raises(SessionExportError, match="unsupported session export schemaVersion: 999"):
        validate_session_export_envelope({"schemaVersion": 999})


def _assistant(text: str, *, timestamp: int) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="responses",
        provider="openai",
        model="gpt-5.4",
        usage=Usage(
            input=10,
            output=4,
            cacheRead=2,
            cacheWrite=1,
            totalTokens=17,
            cost=UsageCost(input=0.01, output=0.02, cacheRead=0.003, cacheWrite=0.007, total=0.04),
        ),
        stopReason="stop",
        timestamp=timestamp,
    )
