from __future__ import annotations

from pathlib import Path

from ai_provider.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
)
from cli.core.compaction.cut_points import select_cut_point
from cli.core.compaction.files import extract_file_context
from cli.core.compaction.tokens import calculate_context_tokens, estimate_entry_tokens
from cli.core.session_manager import SessionManager
from storage.in_memory_session_repository import InMemorySessionRepository


def _usage() -> Usage:
    return Usage(
        input=10,
        output=20,
        cacheRead=3,
        cacheWrite=4,
        totalTokens=37,
        cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
    )


def _manager_with_tool_turn(tmp_path: Path):
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="old" * 200)], timestamp=1),
    )
    assistant = manager.append_message(
        session.id,
        AssistantMessage(
            content=[ToolCall(id="call_read", name="read", arguments={"path": "a.py"})],
            api="faux",
            provider="faux",
            model="faux-1",
            usage=_usage(),
            stopReason="toolUse",
            timestamp=2,
        ),
    )
    result = manager.append_message(
        session.id,
        ToolResultMessage(
            toolCallId="call_read",
            toolName="read",
            content=[TextContent(text="tool output")],
            details={"path": "a.py"},
            isError=False,
            timestamp=3,
        ),
    )
    return manager, session, assistant, result


def test_calculate_context_tokens_includes_cache_dimensions() -> None:
    assert calculate_context_tokens(_usage()) == 37


def test_cut_point_refuses_to_keep_orphan_tool_result(tmp_path: Path) -> None:
    manager, session, assistant, result = _manager_with_tool_turn(tmp_path)
    entries = manager.entry_path(session.id)
    budget = estimate_entry_tokens(result)

    selection = select_cut_point(entries, keep_recent_tokens=budget)

    assert selection.ok is False
    assert selection.reason == "no-safe-cut"

    safe_budget = estimate_entry_tokens(assistant) + estimate_entry_tokens(result) + 1
    selection = select_cut_point(entries, keep_recent_tokens=safe_budget)

    assert selection.ok is True
    assert selection.first_kept_entry_id == assistant.pi_export_id


def test_file_extractor_uses_tool_execution_args_and_details(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    manager.record_tool_execution_start(
        session_id=session.id,
        tool_call_id="call_read",
        tool_name="read",
        args={"path": "src/app.py", "offset": 3},
    )
    manager.record_tool_execution_end(
        session_id=session.id,
        tool_call_id="call_read",
        tool_name="read",
        result_content=[],
        result_details={"path": "src/app.py", "lineStart": 3, "lineEnd": 8},
        is_error=False,
    )
    manager.record_tool_execution_start(
        session_id=session.id,
        tool_call_id="call_edit",
        tool_name="edit",
        args={"path": "src/app.py"},
    )
    manager.record_tool_execution_start(
        session_id=session.id,
        tool_call_id="call_bash",
        tool_name="bash",
        args={"command": "cat secret", "excludeFromContext": True},
    )

    context = extract_file_context(manager.list_tool_executions(session.id))

    assert context.read_files == ["src/app.py:3-8"]
    assert context.modified_files == ["src/app.py"]
