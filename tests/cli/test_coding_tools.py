from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from ai_provider.tools import ToolArgumentValidationError, validate_tool_arguments
from ai_provider.types import TextContent
from cli.core.session_types import BashExecutionMessage
from cli.interactive.runtime import InteractiveAgentRuntime
from cli.tools import (
    create_all_tool_definitions,
    create_coding_tools,
    create_read_only_tools,
)
from cli.tools.context import convert_coding_messages_to_llm
from policy.audit import InMemoryAuditSink
from policy.types import PolicyDecision


def _tool_map(tools):
    return {tool.name: tool for tool in tools}


def test_tool_profiles_match_m5_membership(tmp_path: Path) -> None:
    definitions = create_all_tool_definitions(tmp_path)

    assert set(definitions) == {"read", "bash", "edit", "write", "grep", "find", "ls"}
    assert "download" not in definitions
    assert "list" not in definitions
    assert list(_tool_map(create_coding_tools(tmp_path))) == ["read", "bash", "edit", "write"]
    assert list(_tool_map(create_read_only_tools(tmp_path))) == ["read", "grep", "find", "ls"]


def test_tool_schemas_validate_canonical_inputs(tmp_path: Path) -> None:
    tools = _tool_map(create_coding_tools(tmp_path))

    validate_tool_arguments(tools["read"].to_provider_tool(), {"path": "a.txt"})
    validate_tool_arguments(tools["bash"].to_provider_tool(), {"command": "pwd", "timeout": 1})
    validate_tool_arguments(
        tools["edit"].to_provider_tool(),
        {"path": "a.txt", "edits": [{"oldText": "a", "newText": "b"}]},
    )
    validate_tool_arguments(tools["write"].to_provider_tool(), {"path": "a.txt", "content": "x"})

    try:
        validate_tool_arguments(tools["read"].to_provider_tool(), {"file": "a.txt"})
    except ToolArgumentValidationError:
        pass
    else:  # pragma: no cover - assertion path
        raise AssertionError("read schema must reject wrong argument shape")


def test_read_policy_audit_and_details(tmp_path: Path) -> None:
    async def run() -> None:
        (tmp_path / "a.txt").write_text("one\ntwo\nthree", encoding="utf-8")
        audit = InMemoryAuditSink()
        read = _tool_map(create_coding_tools(tmp_path, runtime_session_id="rt-1", audit_sink=audit))["read"]

        result = await read.execute("call-read", {"path": "a.txt", "limit": 2}, None, None)
        blocked = await read.execute("call-block", {"path": "../escape.txt"}, None, None)

        assert result.is_error is False
        assert "one\ntwo" in result.content[0]["text"]
        assert result.details["path"] == "a.txt"
        assert result.details["resolvedPath"].endswith("a.txt")
        assert "policyDecision" in result.details
        assert {"durationMs", "startedAt", "endedAt"}.issubset(result.details)
        assert blocked.is_error is True
        assert blocked.details["policyDecision"]["effect"] == "block"
        assert [record.tool_name for record in audit.records] == ["read", "read"]
        assert [record.is_error for record in audit.records] == [False, True]

    asyncio.run(run())


def test_read_only_tools_search_hidden_files(tmp_path: Path) -> None:
    async def run() -> None:
        (tmp_path / ".hidden.txt").write_text("needle\n", encoding="utf-8")
        (tmp_path / "visible.py").write_text("print('needle')\n", encoding="utf-8")
        tools = _tool_map(create_read_only_tools(tmp_path))

        grep = await tools["grep"].execute("grep", {"pattern": "needle", "literal": True}, None, None)
        find = await tools["find"].execute("find", {"pattern": "*.txt"}, None, None)
        ls = await tools["ls"].execute("ls", {}, None, None)

        assert ".hidden.txt:1: needle" in grep.content[0]["text"]
        assert ".hidden.txt" in find.content[0]["text"]
        assert ".hidden.txt" in ls.content[0]["text"]

    asyncio.run(run())


def test_edit_and_write_are_cwd_bound_and_preserve_locked_details(tmp_path: Path) -> None:
    async def run() -> None:
        target = tmp_path / "a.txt"
        target.write_text("alpha\nbeta\n", encoding="utf-8")
        tools = _tool_map(create_coding_tools(tmp_path))

        edit = await tools["edit"].execute(
            "edit",
            {"path": "a.txt", "edits": [{"oldText": "beta", "newText": "gamma"}]},
            None,
            None,
        )
        write = await tools["write"].execute("write", {"path": "b.txt", "content": "new"}, None, None)
        blocked = await tools["write"].execute("blocked", {"path": "../b.txt", "content": "x"}, None, None)

        assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"
        assert edit.details["firstChangedLine"] == 2
        assert "unifiedDiff" in edit.details
        assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "new"
        assert write.details["path"] == "b.txt"
        assert blocked.is_error is True
        assert blocked.details["policyDecision"]["effect"] == "block"

    asyncio.run(run())


def test_bash_success_block_and_audit(tmp_path: Path) -> None:
    async def run() -> None:
        audit = InMemoryAuditSink()
        bash = _tool_map(create_coding_tools(tmp_path, audit_sink=audit))["bash"]

        result = await bash.execute("bash-ok", {"command": "pwd"}, None, None)
        blocked = await bash.execute("bash-block", {"command": "sudo echo no"}, None, None)

        assert result.is_error is False
        assert result.details["exitCode"] == 0
        assert result.details["cancelled"] is False
        assert blocked.is_error is True
        assert "sudo is blocked" in blocked.content[0]["text"]
        assert [record.policy_decision.effect for record in audit.records] == ["allow", "block"]

    asyncio.run(run())


def test_shell_policy_blocks_compact_output_escape_syntax(tmp_path: Path) -> None:
    async def run() -> None:
        bash = _tool_map(create_coding_tools(tmp_path))["bash"]

        redirect = await bash.execute("redirect", {"command": "printf x >../outside.txt"}, None, None)
        curl_output = await bash.execute(
            "curl",
            {"command": "curl -o../outside.txt https://example.com/file.txt"},
            None,
            None,
        )

        assert redirect.is_error is True
        assert "redirect path escapes cwd" in redirect.content[0]["text"]
        assert curl_output.is_error is True
        assert "output path escapes cwd" in curl_output.content[0]["text"]

    asyncio.run(run())


def test_confirm_decision_becomes_denied_result(tmp_path: Path) -> None:
    async def run() -> None:
        audit = InMemoryAuditSink()

        def confirm_policy(_request: Any) -> PolicyDecision:
            return PolicyDecision.confirm("needs approval")

        read = _tool_map(create_coding_tools(tmp_path, audit_sink=audit, policy_decider=confirm_policy))["read"]
        result = await read.execute("confirm", {"path": "a.txt"}, None, None)

        assert result.is_error is True
        assert result.details["policyDecision"]["effect"] == "block"
        assert "confirm:denied" in result.details["auditTags"]
        assert audit.records[0].policy_decision.effect == "block"

    asyncio.run(run())


def test_bash_execution_context_conversion_filters_double_bang() -> None:
    included = BashExecutionMessage(
        command="pwd",
        output="/repo",
        exitCode=0,
        cancelled=False,
        truncated=False,
        timestamp=1,
        excludeFromContext=False,
    )
    excluded = BashExecutionMessage(
        command="secret",
        output="hidden",
        exitCode=0,
        cancelled=False,
        truncated=False,
        timestamp=2,
        excludeFromContext=True,
    )

    converted = convert_coding_messages_to_llm([included, excluded])

    assert len(converted) == 1
    assert converted[0].role == "user"
    assert isinstance(converted[0].content, list)
    assert isinstance(converted[0].content[0], TextContent)
    assert "Ran `pwd`" in converted[0].content[0].text
    assert "secret" not in converted[0].content[0].text


def test_runtime_defaults_to_coding_tools_and_user_bash(tmp_path: Path) -> None:
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        assert [tool.name for tool in runtime._agent.tools] == ["read", "bash", "edit", "write"]  # noqa: SLF001

        runtime.run_user_bash("pwd", exclude_from_context=True)
        events = _drain_until_message_end(runtime)
        bash_messages = [
            event.message
            for event in events
            if getattr(event, "type", None) == "message_end"
            and getattr(event.message, "role", None) == "bashExecution"
        ]
        assert bash_messages
        assert bash_messages[-1].exclude_from_context is True
        assert runtime._agent.state.messages[-1].role == "bashExecution"  # noqa: SLF001
    finally:
        runtime.shutdown()


def test_runtime_read_only_profile_omits_mutation_tools(tmp_path: Path) -> None:
    runtime = InteractiveAgentRuntime(cwd=tmp_path, tool_profile="read_only")
    try:
        assert [tool.name for tool in runtime._agent.tools] == ["read", "grep", "find", "ls"]  # noqa: SLF001
    finally:
        runtime.shutdown()


def _drain_until_message_end(runtime: InteractiveAgentRuntime):
    deadline = time.monotonic() + 3
    events = []
    while time.monotonic() < deadline:
        events.extend(runtime.drain_events())
        if not runtime.state.is_running and any(getattr(event, "type", None) == "message_end" for event in events):
            events.extend(runtime.drain_events())
            return events
        time.sleep(0.01)
    raise AssertionError("runtime did not emit user bash message")
