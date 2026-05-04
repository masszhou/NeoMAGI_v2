"""Convert extension-registered tools into governed runtime tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_core.runtime_types import RuntimeAgentTool
from agent_core.types import AgentToolResult
from cli.tools.definitions import BUILTIN_TOOL_NAMES, ToolDefinition as RuntimeToolDefinition
from cli.tools.wrapper import ToolRuntime, default_policy_decider, wrap_tool_definition
from policy.types import PolicyDecision, PolicyRequest

from .runtime import LoadedExtension
from .types import ToolDefinition as ExtensionToolDefinition


def create_extension_tools(
    extensions: list[LoadedExtension],
    *,
    cwd: str,
    runtime_session_id: str | None,
    run_id_provider: Callable[[], str | None] | None,
    audit_sink: Any,
) -> list[RuntimeAgentTool]:
    tools: list[RuntimeAgentTool] = []
    for extension in extensions:
        for definition in extension.tools:
            tools.append(
                wrap_tool_definition(
                    _to_runtime_definition(definition),
                    ToolRuntime(
                        cwd=cwd,
                        runtime_session_id=runtime_session_id,
                        run_id_provider=run_id_provider,
                        actor="extension",
                        audit_sink=audit_sink,
                        policy_decider=_extension_policy_decider,
                    ),
                )
            )
    return tools


def _to_runtime_definition(definition: ExtensionToolDefinition) -> RuntimeToolDefinition:
    execute = getattr(definition, "execute", None)
    if not callable(execute):
        async def missing_execute(*_args: Any, **_kwargs: Any) -> AgentToolResult:
            return AgentToolResult(
                content=[{"type": "text", "text": "extension tool missing execute callable"}],
                isError=True,
            )

        execute = missing_execute
    return RuntimeToolDefinition(
        name=definition.name,
        label=definition.label,
        description=definition.description,
        parameters=definition.parameters,
        execute=execute,
        prepare_arguments=getattr(definition, "prepare_arguments", None)
        or getattr(definition, "prepareArguments", None),
        execution_mode=definition.execution_mode,
    )


def _extension_policy_decider(request: PolicyRequest) -> PolicyDecision:
    if request.tool_name in BUILTIN_TOOL_NAMES:
        return default_policy_decider(request)
    return PolicyDecision.allow(audit_tags=["extension:allow"])


__all__ = ["create_extension_tools"]
