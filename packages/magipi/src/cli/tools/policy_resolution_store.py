"""D11 PolicyResolutionStore — hook→wrapper policy decision relay.

Per-TaskRun-step lifetime store. The ``before_tool_call`` hook (installed
by ``TaskRunAgentSession``) runs the ``PermissionProfileResolver`` and
records its result here keyed by ``tool_call_id``; the wrapper consumes
(read-and-remove) when its policy resolver runs, then skips its legacy
``_resolve_policy_decision`` evaluation. A miss means the hook did not
pre-resolve this call (non-TaskRun call site or transitional path); the
wrapper falls back to its legacy resolver.

Read-and-remove semantics avoid stale entries piling up if a tool body is
skipped by ``_ImmediateToolCallOutcome`` before the wrapper runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from policy.types import PolicyDecision


@dataclass(frozen=True, slots=True)
class ResolvedPolicy:
    raw: PolicyDecision
    resolved: PolicyDecision
    permission_profile: dict[str, Any] | None = None


class PolicyResolutionStore:
    """Step-scope mapping ``tool_call_id -> ResolvedPolicy`` + record of
    hook-side ``block`` outcomes.

    Two parallel maps:

    * ``_entries`` carries the allowed-policy payload the wrapper consumes
      to skip its legacy decision evaluator.
    * ``_block_reasons`` carries the reason text for tool calls the hook
      rejected before the tool body ran. The session consumer drains this
      on ``tool_execution_end`` so ``StepEventCollector`` can mark the
      step ``blocked`` — the ``agent_core`` error result for a hook block
      is just an opaque ``"Tool execution was blocked"`` message with no
      ``policyDecision`` details, so without this side channel the
      collector would mis-classify the step as ``failed``.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ResolvedPolicy] = {}
        self._block_reasons: dict[str, str] = {}

    def put(
        self,
        tool_call_id: str,
        *,
        raw: PolicyDecision,
        resolved: PolicyDecision,
        permission_profile: dict[str, Any] | None = None,
    ) -> None:
        self._entries[tool_call_id] = ResolvedPolicy(
            raw=raw,
            resolved=resolved,
            permission_profile=permission_profile,
        )

    def consume(self, tool_call_id: str | None) -> ResolvedPolicy | None:
        if tool_call_id is None:
            return None
        return self._entries.pop(tool_call_id, None)

    def peek(self, tool_call_id: str) -> ResolvedPolicy | None:
        return self._entries.get(tool_call_id)

    def has_pending(self) -> bool:
        return bool(self._entries)

    def record_block(self, tool_call_id: str, reason: str) -> None:
        self._block_reasons[tool_call_id] = reason

    def consume_block_reason(self, tool_call_id: str | None) -> str | None:
        if tool_call_id is None:
            return None
        return self._block_reasons.pop(tool_call_id, None)


__all__ = ["PolicyResolutionStore", "ResolvedPolicy"]
