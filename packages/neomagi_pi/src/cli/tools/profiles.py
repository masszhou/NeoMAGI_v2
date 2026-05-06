"""Built-in coding tool profiles."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_core.runtime_types import RuntimeAgentTool
from policy.audit import AuditSink

from .bash import create_bash_tool_definition
from .definitions import ToolDefinition, ToolName
from .edit import create_edit_tool_definition
from .find import create_find_tool_definition
from .grep import create_grep_tool_definition
from .ls import create_ls_tool_definition
from .read import create_read_tool_definition
from .shell import RuntimeArtifactStore
from .wrapper import PolicyDecider, ToolRuntime, wrap_tool_definition
from .write import create_write_tool_definition

CODING_PROFILE: tuple[ToolName, ...] = ("read", "bash", "edit", "write")
READ_ONLY_PROFILE: tuple[ToolName, ...] = ("read", "grep", "find", "ls")
ALL_TOOLS: tuple[ToolName, ...] = ("read", "bash", "edit", "write", "grep", "find", "ls")


def create_coding_tool_definitions(
    cwd: str | Path,
    options: dict[str, Any] | None = None,
) -> list[ToolDefinition]:
    return [create_all_tool_definitions(cwd, options)[name] for name in CODING_PROFILE]


def create_read_only_tool_definitions(
    cwd: str | Path,
    options: dict[str, Any] | None = None,
) -> list[ToolDefinition]:
    return [create_all_tool_definitions(cwd, options)[name] for name in READ_ONLY_PROFILE]


def create_all_tool_definitions(
    cwd: str | Path,
    options: dict[str, Any] | None = None,
) -> dict[ToolName, ToolDefinition]:
    artifact_store = None
    if options and isinstance(options.get("artifact_store"), RuntimeArtifactStore):
        artifact_store = options["artifact_store"]
    return {
        "read": create_read_tool_definition(),
        "bash": create_bash_tool_definition(artifact_store=artifact_store),
        "edit": create_edit_tool_definition(),
        "write": create_write_tool_definition(),
        "grep": create_grep_tool_definition(),
        "find": create_find_tool_definition(),
        "ls": create_ls_tool_definition(),
    }


def create_coding_tools(
    cwd: str | Path,
    *,
    runtime_session_id: str | None = None,
    run_id: str | None = None,
    run_id_provider: Callable[[], str | None] | None = None,
    audit_sink: AuditSink | None = None,
    policy_decider: PolicyDecider | None = None,
    artifact_store: RuntimeArtifactStore | None = None,
) -> list[RuntimeAgentTool]:
    return _wrap_definitions(
        create_coding_tool_definitions(cwd, {"artifact_store": artifact_store}),
        cwd=cwd,
        runtime_session_id=runtime_session_id,
        run_id=run_id,
        run_id_provider=run_id_provider,
        audit_sink=audit_sink,
        policy_decider=policy_decider,
    )


def create_read_only_tools(
    cwd: str | Path,
    *,
    runtime_session_id: str | None = None,
    run_id: str | None = None,
    run_id_provider: Callable[[], str | None] | None = None,
    audit_sink: AuditSink | None = None,
    policy_decider: PolicyDecider | None = None,
) -> list[RuntimeAgentTool]:
    return _wrap_definitions(
        create_read_only_tool_definitions(cwd),
        cwd=cwd,
        runtime_session_id=runtime_session_id,
        run_id=run_id,
        run_id_provider=run_id_provider,
        audit_sink=audit_sink,
        policy_decider=policy_decider,
    )


def create_all_tools(
    cwd: str | Path,
    *,
    runtime_session_id: str | None = None,
    run_id: str | None = None,
    run_id_provider: Callable[[], str | None] | None = None,
    audit_sink: AuditSink | None = None,
    policy_decider: PolicyDecider | None = None,
    artifact_store: RuntimeArtifactStore | None = None,
) -> dict[ToolName, RuntimeAgentTool]:
    definitions = create_all_tool_definitions(cwd, {"artifact_store": artifact_store})
    runtime = ToolRuntime(
        cwd=str(cwd),
        runtime_session_id=runtime_session_id,
        run_id=run_id,
        run_id_provider=run_id_provider,
        audit_sink=audit_sink,
        policy_decider=policy_decider,
    )
    return {name: wrap_tool_definition(definition, runtime) for name, definition in definitions.items()}


def _wrap_definitions(
    definitions: list[ToolDefinition],
    *,
    cwd: str | Path,
    runtime_session_id: str | None,
    run_id: str | None,
    run_id_provider: Callable[[], str | None] | None,
    audit_sink: AuditSink | None,
    policy_decider: PolicyDecider | None,
) -> list[RuntimeAgentTool]:
    runtime = ToolRuntime(
        cwd=str(cwd),
        runtime_session_id=runtime_session_id,
        run_id=run_id,
        run_id_provider=run_id_provider,
        audit_sink=audit_sink,
        policy_decider=policy_decider,
    )
    return [wrap_tool_definition(definition, runtime) for definition in definitions]


__all__ = [
    "ALL_TOOLS",
    "CODING_PROFILE",
    "READ_ONLY_PROFILE",
    "create_all_tool_definitions",
    "create_all_tools",
    "create_coding_tool_definitions",
    "create_coding_tools",
    "create_read_only_tool_definitions",
    "create_read_only_tools",
]
