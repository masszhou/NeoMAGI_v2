from __future__ import annotations

import asyncio
import stat
import time
from pathlib import Path
from typing import Any

import pytest
from agent_core import Agent, AgentToolResult
from ai_provider.model_registry import get_model
from ai_provider.providers.faux import faux_tool_call, stream_faux
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.tools import ToolArgumentValidationError, validate_tool_arguments
from ai_provider.types import Context, Model, TextContent, ToolResultMessage
from cli.core.session_types import BashExecutionMessage
from cli.core.session_manager import SessionManager
from cli.interactive.runtime import InteractiveAgentRuntime
from cli.tools import (
    RuntimeArtifactStore,
    create_all_tool_definitions,
    create_coding_tools,
    create_read_only_tools,
)
from cli.tools.context import convert_coding_messages_to_llm
from cli.tools.definitions import ToolDefinition, object_schema
from cli.tools.edit import prepare_edit_arguments
from cli.tools.wrapper import ToolRuntime, default_policy_decider, wrap_tool_definition
from policy.audit import InMemoryAuditSink
from policy.shell_policy import decide_shell_access
from policy.types import PolicyDecision, PolicyRequest
from storage.in_memory_session_repository import InMemorySessionRepository


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


def test_prepare_edit_arguments_is_idempotent() -> None:
    raw = {
        "path": "a.txt",
        "oldText": "alpha",
        "newText": "beta",
        "edits": '[{"oldText": "gamma", "newText": "delta"}]',
    }

    prepared = prepare_edit_arguments(raw)

    assert prepare_edit_arguments(prepared) == prepared


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
        assert result.details["lineStart"] == 1
        assert result.details["lineEnd"] == 2
        assert result.details["totalLines"] == 3
        assert result.details["outputLines"] == 2
        assert "policyDecision" in result.details
        assert {"durationMs", "startedAt", "endedAt"}.issubset(result.details)
        assert blocked.is_error is True
        assert blocked.details["policyDecision"]["effect"] == "block"
        assert [record.tool_name for record in audit.records] == ["read", "read"]
        assert [record.is_error for record in audit.records] == [False, True]

    asyncio.run(run())


def test_read_details_follow_offset_and_continuation_notice(tmp_path: Path) -> None:
    async def run() -> None:
        (tmp_path / "a.txt").write_text("\n".join(f"line-{i}" for i in range(1, 13)), encoding="utf-8")
        read = _tool_map(create_coding_tools(tmp_path))["read"]

        result = await read.execute("call-read", {"path": "a.txt", "offset": 10, "limit": 2}, None, None)

        assert result.is_error is False
        assert result.details["lineStart"] == 10
        assert result.details["lineEnd"] == 11
        assert result.details["totalLines"] == 12
        assert result.details["outputLines"] == 2
        assert "Use offset=12 to continue" in result.content[0]["text"]

    asyncio.run(run())


def test_wrapped_tool_policy_details_and_audit_use_dynamic_run_id(tmp_path: Path) -> None:
    async def run() -> None:
        (tmp_path / "a.txt").write_text("one", encoding="utf-8")
        audit = InMemoryAuditSink()
        requests: list[PolicyRequest] = []
        current_run_id = "run-00000000-0000-7000-8000-000000000001"

        def policy(request: PolicyRequest) -> PolicyDecision:
            requests.append(request)
            return default_policy_decider(request)

        read = _tool_map(
            create_coding_tools(
                tmp_path,
                runtime_session_id="rt-1",
                run_id_provider=lambda: current_run_id,
                audit_sink=audit,
                policy_decider=policy,
            )
        )["read"]

        first = await read.execute("call-read-1", {"path": "a.txt"}, None, None)
        current_run_id = "run-00000000-0000-7000-8000-000000000002"
        second = await read.execute("call-read-2", {"path": "a.txt"}, None, None)

        assert first.details["policyDecision"]["runId"].endswith("0001")
        assert second.details["policyDecision"]["runId"].endswith("0002")
        assert [record.run_id for record in audit.records] == [
            "run-00000000-0000-7000-8000-000000000001",
            "run-00000000-0000-7000-8000-000000000002",
        ]
        assert [request.run_id for request in requests] == [
            "run-00000000-0000-7000-8000-000000000001",
            "run-00000000-0000-7000-8000-000000000002",
        ]

    asyncio.run(run())


def test_wrapped_tool_audit_keeps_captured_run_id_after_provider_changes(tmp_path: Path) -> None:
    async def run() -> None:
        audit = InMemoryAuditSink()
        started = asyncio.Event()
        release = asyncio.Event()
        current_run_id = "run-00000000-0000-7000-8000-000000000001"

        async def execute(
            _args: dict[str, Any],
            context: Any,
            _signal: Any,
            _on_update: Any,
        ) -> AgentToolResult:
            started.set()
            await release.wait()
            return AgentToolResult(
                content=[{"type": "text", "text": "late"}],
                details={"contextRunId": context.run_id},
            )

        tool = wrap_tool_definition(
            ToolDefinition(
                name="bash",
                label="late",
                description="delayed test tool",
                parameters=object_schema({}),
                execute=execute,
            ),
            ToolRuntime(
                cwd=str(tmp_path),
                runtime_session_id="rt-1",
                run_id_provider=lambda: current_run_id,
                audit_sink=audit,
                policy_decider=lambda _request: PolicyDecision.allow(),
            ),
        )

        task = asyncio.create_task(tool.execute("late-call", {}, None, None))
        await started.wait()
        current_run_id = "run-00000000-0000-7000-8000-000000000002"
        release.set()
        result = await task

        assert result.details["contextRunId"].endswith("0001")
        assert result.details["policyDecision"]["runId"].endswith("0001")
        assert audit.records[0].run_id == "run-00000000-0000-7000-8000-000000000001"

    asyncio.run(run())


def test_wrapped_tool_respects_empty_normalized_args(tmp_path: Path) -> None:
    async def run() -> None:
        async def execute(
            args: dict[str, Any],
            _context: Any,
            _signal: Any,
            _on_update: Any,
        ) -> AgentToolResult:
            return AgentToolResult(
                content=[{"type": "text", "text": "ok"}],
                details={"seenArgs": args},
            )

        tool = wrap_tool_definition(
            ToolDefinition(
                name="bash",
                label="normalized",
                description="normalized args test tool",
                parameters=object_schema(
                    {"raw": {"type": "string"}},
                    required=["raw"],
                ),
                execute=execute,
            ),
            ToolRuntime(
                cwd=str(tmp_path),
                policy_decider=lambda _request: PolicyDecision.allow(normalized_args={}),
            ),
        )

        result = await tool.execute("normalized-call", {"raw": "original"}, None, None)

        assert result.details["seenArgs"] == {}

    asyncio.run(run())


def test_agent_model_tool_audit_run_ids_are_prompt_scoped(tmp_path: Path) -> None:
    async def run() -> None:
        (tmp_path / "a.txt").write_text("one", encoding="utf-8")
        (tmp_path / "b.txt").write_text("two", encoding="utf-8")
        audit = InMemoryAuditSink()
        agent, provider_contexts = _agent_with_two_read_calls(tmp_path, audit)

        await agent.prompt("first")
        await agent.prompt("second")

        run_ids = [record.run_id for record in audit.records]
        assert len(run_ids) == 4
        assert all(run_id and run_id.startswith("run-") for run_id in run_ids)
        assert run_ids[0] == run_ids[1]
        assert run_ids[2] == run_ids[3]
        assert run_ids[0] != run_ids[2]

        tool_results = [
            message
            for message in agent.state.messages
            if isinstance(message, ToolResultMessage)
        ]
        detail_run_ids = [
            message.details["policyDecision"]["runId"]
            for message in tool_results
        ]
        assert detail_run_ids == run_ids
        provider_payload_text = "\n".join(provider_contexts)
        assert "runId" not in provider_payload_text
        assert "run-" not in provider_payload_text

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


def test_read_only_tools_skip_symlink_files_resolving_outside_cwd(tmp_path: Path) -> None:
    async def run() -> None:
        cwd = tmp_path / "repo"
        outside = tmp_path / "outside"
        cwd.mkdir()
        outside.mkdir()
        (cwd / "inside.txt").write_text("needle\n", encoding="utf-8")
        (outside / "secret.txt").write_text("leak\n", encoding="utf-8")
        try:
            (cwd / "secret_link.txt").symlink_to(outside / "secret.txt")
        except OSError as exc:
            pytest.skip(f"symlinks are not available: {exc}")

        tools = _tool_map(create_read_only_tools(cwd))

        grep = await tools["grep"].execute("grep", {"pattern": "leak", "literal": True}, None, None)
        find = await tools["find"].execute("find", {"pattern": "*.txt"}, None, None)

        assert grep.content[0]["text"] == "No matches found"
        assert "inside.txt" in find.content[0]["text"]
        assert "secret_link.txt" not in find.content[0]["text"]

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


def test_tool_audit_args_are_summarized_and_redacted(tmp_path: Path) -> None:
    async def run() -> None:
        audit = InMemoryAuditSink()
        tools = _tool_map(create_coding_tools(tmp_path, audit_sink=audit))
        secret = "sk-" + ("A" * 40)

        await tools["bash"].execute(
            "bash-audit",
            {"command": f"echo {secret} $OPENAI_API_KEY", "timeout": 1},
            None,
            None,
        )
        await tools["write"].execute(
            "write-audit",
            {"path": "secret.txt", "content": "super-secret-content"},
            None,
            None,
        )

        bash_args = audit.records[0].args
        write_args = audit.records[1].args
        assert "<redacted:" in bash_args["commandPreview"]
        assert secret not in bash_args["commandPreview"]
        assert "$OPENAI_API_KEY" in bash_args["commandPreview"]
        assert bash_args["commandLength"] == len(f"echo {secret} $OPENAI_API_KEY")
        assert audit.records[0].redaction_status == "applied"
        assert write_args == {
            "path": "secret.txt",
            "contentBytes": len("super-secret-content"),
        }
        assert "super-secret-content" not in str(write_args)
        assert audit.records[1].redaction_status == "applied"

    asyncio.run(run())


def test_runtime_artifact_store_uses_private_permissions() -> None:
    store = RuntimeArtifactStore(f"pytest-{time.time_ns()}")
    try:
        output = store.write_output("tool-call", "full output")

        assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
    finally:
        store.cleanup()


def test_wrapped_tools_validate_schema_before_policy(tmp_path: Path) -> None:
    async def run() -> None:
        policy_called = False
        audit = InMemoryAuditSink()

        def policy(_request: Any) -> PolicyDecision:
            nonlocal policy_called
            policy_called = True
            return PolicyDecision.allow()

        bash = _tool_map(create_coding_tools(tmp_path, audit_sink=audit, policy_decider=policy))["bash"]
        result = await bash.execute("bad-schema", {"command": 123}, None, None)

        assert result.is_error is True
        assert "bash arguments invalid" in result.content[0]["text"]
        assert result.details["policyDecision"]["effect"] == "block"
        assert result.details["auditTags"] == ["schema:block"]
        assert policy_called is False
        assert len(audit.records) == 1
        assert audit.records[0].policy_decision.effect == "block"

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


@pytest.mark.parametrize(
    ("command", "blocked_path"),
    [
        ('bash -c "cat /etc/passwd"', "/etc/passwd"),
        ('sh -lc "ls /usr/bin"', "/usr/bin"),
        ("python -c \"open('/etc/passwd').read()\"", "/etc/passwd"),
        (
            "python3 -c \"from pathlib import Path; "
            "Path('/private/etc/hosts').read_text()\"",
            "/private/etc/hosts",
        ),
    ],
)
def test_shell_policy_blocks_nested_privileged_paths(
    tmp_path: Path,
    command: str,
    blocked_path: str,
) -> None:
    decision = _shell_decision(tmp_path, command)

    assert decision.effect == "block"
    assert blocked_path in (decision.reason or "")
    assert decision.resolved_paths["blockedPathLiteral"] == blocked_path


def test_shell_policy_keeps_top_level_privileged_path_precedence(tmp_path: Path) -> None:
    decision = _shell_decision(tmp_path, '/usr/bin/bash -c "cat /etc/passwd"')

    assert decision.effect == "block"
    assert "/usr/bin/bash" in (decision.reason or "")
    assert decision.resolved_paths["blockedPathLiteral"] == "/usr/bin/bash"


@pytest.mark.parametrize(
    "command",
    [
        'bash -c "echo P1M5_OK"',
        "python -c \"print('P1M5_OK')\"",
        "python -c \"print('https://example.com/etc/readme')\"",
        "python -c \"print('see /etc/issue.net for hostname conventions')\"",
    ],
)
def test_shell_policy_allows_harmless_nested_commands(
    tmp_path: Path,
    command: str,
) -> None:
    assert _shell_decision(tmp_path, command).effect == "allow"


def test_deep_shell_policy_block_exposes_literal_in_details_and_audit(tmp_path: Path) -> None:
    async def run() -> None:
        audit = InMemoryAuditSink()
        bash = _tool_map(create_coding_tools(tmp_path, audit_sink=audit))["bash"]

        result = await bash.execute(
            "bash-deep-block",
            {"command": "python -c \"open('/etc/passwd').read()\""},
            None,
            None,
        )

        decision = result.details["policyDecision"]
        assert result.is_error is True
        assert decision["resolvedPaths"]["blockedPathLiteral"] == "/etc/passwd"
        assert audit.records[0].policy_decision.resolved_paths["blockedPathLiteral"] == "/etc/passwd"

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


def test_coding_llm_conversion_strips_run_id_from_tool_result_details() -> None:
    run_id = "run-00000000-0000-7000-8000-000000000099"
    message = ToolResultMessage(
        toolCallId="call_1",
        toolName="read",
        content=[TextContent(text="tool output")],
        details={
            "runId": run_id,
            "policyDecision": {"effect": "allow", "runId": run_id},
        },
        isError=False,
        timestamp=1,
    )

    converted = convert_coding_messages_to_llm([message])

    assert len(converted) == 1
    assert isinstance(converted[0], ToolResultMessage)
    assert converted[0].details == {"policyDecision": {"effect": "allow"}}
    assert "runId" not in str(converted)
    assert "run-" not in str(converted)


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


def test_runtime_user_bash_audit_run_ids_are_independent(tmp_path: Path) -> None:
    audit = InMemoryAuditSink()
    runtime = InteractiveAgentRuntime(cwd=tmp_path, audit_sink=audit)
    try:
        runtime.run_user_bash("pwd", exclude_from_context=True)
        _drain_until_message_end(runtime)
        runtime.run_user_bash("pwd", exclude_from_context=True)
        _drain_until_message_end(runtime)
    finally:
        runtime.shutdown()

    user_records = [record for record in audit.records if record.actor == "user"]
    assert len(user_records) == 2
    run_ids = [record.run_id for record in user_records]
    assert all(run_id and run_id.startswith("run-") for run_id in run_ids)
    assert run_ids[0] != run_ids[1]


def test_runtime_user_bash_does_not_mutate_agent_state_when_durable_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SessionManager(InMemorySessionRepository())
    runtime = InteractiveAgentRuntime(cwd=tmp_path, session_manager=manager)
    try:
        writer = runtime._session_writer  # noqa: SLF001
        assert writer is not None
        original_record = writer.record

        def fail_bash_end(event: Any) -> None:
            if (
                getattr(event, "type", None) == "message_end"
                and getattr(getattr(event, "message", None), "role", None) == "bashExecution"
            ):
                raise RuntimeError("injected durable write failure")
            original_record(event)

        monkeypatch.setattr(writer, "record", fail_bash_end)

        runtime.run_user_bash("pwd", exclude_from_context=True)
        events = _drain_until_message_end(runtime)

        assert all(
            getattr(message, "role", None) != "bashExecution"
            for message in runtime._agent.state.messages  # noqa: SLF001
        )
        assert any(
            getattr(event, "type", None) == "message_end"
            and getattr(event.message, "role", None) == "assistant"
            and "injected durable write failure" in (event.message.error_message or "")
            for event in events
        )
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


def _agent_with_two_read_calls(
    tmp_path: Path,
    audit: InMemoryAuditSink,
) -> tuple[Agent, list[str]]:
    provider_contexts: list[str] = []
    call_batch = 0

    def stream_fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ):
        nonlocal call_batch
        provider_contexts.append(context.model_dump_json(by_alias=True, exclude_none=True))
        if context.messages and context.messages[-1].role == "toolResult":
            return stream_faux(model, context, SimpleStreamOptions(metadata={"response": "done"}))
        call_batch += 1
        response = [
            faux_tool_call("read", {"path": "a.txt"}, id=f"call_{call_batch}_a"),
            faux_tool_call("read", {"path": "b.txt"}, id=f"call_{call_batch}_b"),
        ]
        return stream_faux(model, context, SimpleStreamOptions(metadata={"response": response}))

    agent = Agent(
        model=get_model("faux", "faux-1"),
        stream_fn=stream_fn,
        convert_to_llm=convert_coding_messages_to_llm,
    )
    agent.tools = create_coding_tools(
        tmp_path,
        runtime_session_id="rt-1",
        run_id_provider=lambda: agent.active_run_id,
        audit_sink=audit,
    )
    return agent, provider_contexts


def _shell_decision(tmp_path: Path, command: str) -> PolicyDecision:
    return decide_shell_access(
        PolicyRequest(
            toolName="bash",
            args={"command": command},
            cwd=str(tmp_path),
            actor="model",
        )
    )
