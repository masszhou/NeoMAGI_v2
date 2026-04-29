"""agent_core — Agent loop, tools, events, steering / follow-up queues.

Architecture: design_docs/architecture/p1_pi_cli_technical_architecture.md
              §`agent_core` Protocol (line 281–402).
Pi-mono source map (commit 97a38bf6, see ADR-0011):
  - packages/agent/src/types.ts
  - packages/agent/src/agent.ts
  - packages/agent/src/agent-loop.ts
"""

from .agent import Agent
from .cache_affinity import derive_provider_cache_affinity_id, mint_provider_cache_affinity_id
from .loop import default_convert_to_llm, run_agent_loop, run_agent_loop_continue
from .runtime_types import AgentLoopConfig, AgentOptions, RuntimeAgentTool
from .types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEvent,
    AgentEventAdapter,
    AgentState,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
)

__all__ = [
    "AfterToolCallContext",
    "AfterToolCallResult",
    "Agent",
    "AgentContext",
    "AgentEvent",
    "AgentEventAdapter",
    "AgentLoopConfig",
    "AgentOptions",
    "AgentState",
    "AgentTool",
    "AgentToolResult",
    "BeforeToolCallContext",
    "BeforeToolCallResult",
    "RuntimeAgentTool",
    "default_convert_to_llm",
    "derive_provider_cache_affinity_id",
    "mint_provider_cache_affinity_id",
    "run_agent_loop",
    "run_agent_loop_continue",
]
