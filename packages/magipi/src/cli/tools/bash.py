"""Governed local `bash` tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_core.runtime_types import AbortSignal, ToolUpdateCallback
from agent_core.types import AgentToolResult
from policy.redaction import redact_literal_values
from policy.sandbox import SandboxResult, run_shell_command
from policy.shell_policy import DEFAULT_TIMEOUT_SECONDS

from ._result import text_result
from .definitions import ToolDefinition, ToolExecutionContext, object_schema
from .shell import RuntimeArtifactStore
from .truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, format_size, truncate_tail


def create_bash_tool_definition(
    *,
    artifact_store: RuntimeArtifactStore | None = None,
) -> ToolDefinition:
    async def execute(
        args: dict[str, Any],
        context: ToolExecutionContext,
        signal: AbortSignal | None,
        on_update: ToolUpdateCallback | None,
    ) -> AgentToolResult:
        return await execute_bash(args, context, signal, on_update, artifact_store=artifact_store)

    return ToolDefinition(
        name="bash",
        label="bash",
        description=(
            "Execute a bash command in the current working directory. Returns merged stdout/stderr. "
            f"Output is truncated to the last {DEFAULT_MAX_LINES} lines or {format_size(DEFAULT_MAX_BYTES)}."
        ),
        parameters=object_schema(
            {
                "command": {"type": "string"},
                "timeout": {"type": "number", "minimum": 1, "maximum": 600},
            },
            required=["command"],
        ),
        execute=execute,
    )


async def execute_bash(
    args: dict[str, Any],
    context: ToolExecutionContext,
    signal: AbortSignal | None,
    on_update: ToolUpdateCallback | None,
    *,
    artifact_store: RuntimeArtifactStore | None = None,
) -> AgentToolResult:
    command = str(args["command"])
    timeout = float(args.get("timeout") or DEFAULT_TIMEOUT_SECONDS)
    cwd = Path(context.policy_decision.resolved_paths.get("cwd", context.cwd))
    skill_env = dict(context.skill_env_grant.env) if context.skill_env_grant is not None else None
    secret_values = context.skill_env_grant.values if context.skill_env_grant is not None else ()
    rolling: list[bytes] = []
    rolling_bytes = 0
    max_rolling = DEFAULT_MAX_BYTES * 2

    def on_data(chunk: bytes) -> None:
        nonlocal rolling_bytes
        rolling.append(chunk)
        rolling_bytes += len(chunk)
        while rolling_bytes > max_rolling and len(rolling) > 1:
            removed = rolling.pop(0)
            rolling_bytes -= len(removed)
        if on_update is not None:
            preview = b"".join(rolling).decode("utf-8", errors="replace")
            preview, _ = redact_literal_values(preview, secret_values)
            truncation = truncate_tail(preview)
            on_update(
                AgentToolResult(
                    content=[{"type": "text", "text": truncation.content}],
                    details={"truncation": truncation.to_details()},
                )
            )

    sandbox_result = await run_shell_command(
        command,
        cwd=cwd,
        timeout=timeout,
        signal=signal,
        on_data=on_data,
        extra_env=skill_env,
    )
    return _bash_result(command, sandbox_result, context, artifact_store, secret_values=secret_values)


def _bash_result(
    command: str,
    sandbox_result: SandboxResult,
    context: ToolExecutionContext,
    artifact_store: RuntimeArtifactStore | None,
    *,
    secret_values: tuple[str, ...] = (),
) -> AgentToolResult:
    output_text, redacted = redact_literal_values(sandbox_result.output, secret_values)
    truncation = truncate_tail(output_text)
    full_output_path = _retain_full_output(context, artifact_store, output_text, truncation.truncated)
    output = truncation.content or "(no output)"
    notes = _status_notes(sandbox_result, full_output_path, truncation)
    if notes:
        output = f"{output}\n\n{notes}"
    is_error = sandbox_result.cancelled or (sandbox_result.exit_code not in (0, None))
    details = {
        "exitCode": sandbox_result.exit_code,
        "cancelled": sandbox_result.cancelled,
        "truncation": truncation.to_details(),
        "fullOutputPath": full_output_path,
    }
    if context.skill_env_grant is not None:
        details["skillEnv"] = {
            "skill": context.skill_env_grant.skill_name,
            "names": list(context.skill_env_grant.names),
            "source": context.skill_env_grant.source,
        }
    if redacted:
        details["redactedSkillEnvOutput"] = True
    return text_result(output, details=details, is_error=is_error)


def _retain_full_output(
    context: ToolExecutionContext,
    artifact_store: RuntimeArtifactStore | None,
    output: str,
    truncated: bool,
) -> str | None:
    if not truncated or artifact_store is None:
        return None
    path = artifact_store.write_output(context.tool_call_id, output)
    return str(path)


def _status_notes(
    result: SandboxResult,
    full_output_path: str | None,
    truncation: Any,
) -> str:
    notes: list[str] = []
    if result.timed_out:
        notes.append("Command timed out")
    elif result.cancelled:
        notes.append("Command cancelled")
    elif result.exit_code not in (0, None):
        notes.append(f"Command exited with code {result.exit_code}")
    if truncation.truncated:
        notes.append(f"Output truncated. Full output: {full_output_path or '(not retained)'}")
    return "\n".join(notes)


__all__ = ["create_bash_tool_definition", "execute_bash"]
