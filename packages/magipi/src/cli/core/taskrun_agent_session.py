"""D13 TaskRunAgentSession adapter.

Owns one in-process ``Agent`` plus the listener queue / consumer task that
translates raw ``AgentEvent`` frames into TaskRun semantic state. The
adapter lives in the TaskRun layer per ADR-0023: agent_core's protocol
surface is not touched — all white-box semantics are TaskRun derived.

Pattern:

* ``Agent.subscribe`` is wired to a single sync listener that performs the
  *synchronous* fan-out (durable session writer + step event collector +
  tool_call state map) and then enqueues a marker for the async consumer.
  The synchronous half preserves the wrapper-side contract that
  ``agent_tool_executions`` rows exist before the wrapper's
  ``_finalize_governed_execution`` runs (legacy compatibility); the queued
  half is what W4-W6 hook in for async work (D11 back-fill, D12 evidence
  classifier writes, D14 compaction derived events).
* The consumer task drains the queue sequentially and dispatches to an
  optional ``event_hook``. Any failure (heartbeat, writer, hook, collector,
  consumer task itself) is appended to the step error sink
  (``StepEventCollector.listener_errors``); step finalize collapses the
  step to ``failed`` if the sink is non-empty. The agent's main path
  cannot mask a derived-layer failure by returning ``done``.
* A step-scope ``tool_call_id`` state map captures ``{tool_name, args}``
  on ``tool_execution_start`` so downstream consumers (D12 evidence
  classifier and D11 back-fill) can correlate without re-reading agent
  state. A missing entry when an end event arrives is a fail-closed
  condition (lost/dropped start) and never silently degrades to
  ``generic`` evidence (W5 spec).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agent_core import Agent
from agent_core.types import (
    AgentEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from ai_provider.types import AssistantMessage
from cli.core.evidence_classifier import (
    VERIFICATION_ABANDONED,
    VERIFICATION_ERROR,
    VERIFICATION_INCONSISTENT,
    VERIFICATION_MISSING_EVIDENCE,
    VERIFICATION_SUPPORTED,
    EvidenceObservation,
    VerificationResult,
    infer_verification_state,
)
from cli.core.taskrun_step import TaskRunStepOutcome
from cli.interactive.runtime_events import agent_event_to_session_event
from cli.interactive.session_writer import DurableSessionEventWriter
from cli.tools.policy_resolution_store import PolicyResolutionStore


_QUEUE_SENTINEL: Any = object()


@dataclass(slots=True)
class ToolCallState:
    tool_name: str
    args: Any
    tool_execution_id: str | None = None


@dataclass(slots=True)
class StepEventCollector:
    """Reduces session events into a ``TaskRunStepOutcome``.

    Lives in this module so the session owns the listener path end to end.
    Behavior matches the original ``taskrun_runner._StepEventCollector``;
    W4 will migrate ``policyDecision`` / ``toolFinalizeErrors`` reads off
    ``result.details`` onto derived events.
    """

    assistant_text: str = ""
    assistant_stop_reason: str | None = None
    error_message: str | None = None
    block_reason: str | None = None
    tool_error: str | None = None
    tool_count: int = 0
    permission_count: int = 0
    run_id: str | None = None
    listener_errors: list[dict[str, str]] = field(default_factory=list)
    evidence_observations: list[EvidenceObservation] = field(default_factory=list)

    def record(self, event: Any, run_id: str | None) -> None:
        if run_id:
            self.run_id = run_id
        event_type = getattr(event, "type", "")
        if event_type == "message_end":
            self._record_message(getattr(event, "message", None))
            return
        if event_type == "tool_execution_end":
            self.tool_count += 1
            self._record_tool_result(
                getattr(event, "result", None),
                bool(getattr(event, "is_error", False)),
            )

    def _record_message(self, message: Any) -> None:
        role = getattr(message, "role", None)
        if role == "assistant":
            if isinstance(message, AssistantMessage):
                if message.error_message:
                    self.error_message = message.error_message
                stop_reason = getattr(message, "stop_reason", None)
                if stop_reason:
                    self.assistant_stop_reason = stop_reason
            text = _assistant_text(message)
            if text:
                self.assistant_text = text

    def note_policy_block(self, reason: str) -> None:
        """Record a hook-side block on the current tool call.

        D11: the ``before_tool_call`` hook may reject a tool before
        ``agent_core`` runs the body. The resulting error frame from
        ``agent_core`` carries no ``policyDecision`` details (those would
        require extending the protocol surface — forbidden by ADR-0023),
        so the session adapter calls this method right before delegating
        the ``tool_execution_end`` event to ``record()``. The pre-set
        ``block_reason`` then short-circuits the ``tool_error`` branch
        and keeps the step lifecycle on ``blocked`` instead of ``failed``.
        """

        if not self.block_reason:
            self.block_reason = reason
        self.permission_count += 1

    def _record_tool_result(self, result: Any, is_error: bool) -> None:
        details = (
            result.get("details") if isinstance(result, dict)
            else getattr(result, "details", None)
        )
        if not isinstance(details, dict):
            details = {}
        decision = details.get("policyDecision")
        if isinstance(decision, dict):
            self.permission_count += 1
            if decision.get("effect") == "block":
                self.block_reason = str(decision.get("reason") or "tool blocked by policy")
        finalize_errors = details.get("toolFinalizeErrors")
        if isinstance(finalize_errors, list) and finalize_errors:
            self.listener_errors.extend(
                error for error in finalize_errors if isinstance(error, dict)
            )
        if is_error and self.block_reason is None:
            text = _tool_result_text(result)
            self.tool_error = text or "tool execution failed"

    def outcome(self) -> TaskRunStepOutcome:
        # D12: verification_state must be attached to *every* outcome —
        # the inconsistent / error states only surface if we compute the
        # verification before the early-return lifecycle dispatch.
        verification = self._compute_verification()
        base = {
            "assistant_text": self.assistant_text,
            "run_id": self.run_id,
            "tool_count": self.tool_count,
            "permission_decision_count": self.permission_count,
            "verification_state": verification.state,
            "verification_reason": verification.reason,
            # Carry the kind lists straight through so the runner does not
            # re-derive them from claim text (which lost the inconsistent
            # vs. missing distinction in the previous round).
            "verification_missing_kinds": tuple(verification.missing_kinds),
            "verification_inconsistent_kinds": tuple(verification.inconsistent_kinds),
        }
        if self.listener_errors:
            return TaskRunStepOutcome(
                status="failed",
                error_message="step finalization sink failed",
                finalize_errors=list(self.listener_errors),
                **base,
            )
        if self.error_message:
            return TaskRunStepOutcome(
                status="failed",
                error_message=self.error_message,
                **base,
            )
        if self.block_reason:
            return TaskRunStepOutcome(
                status="blocked",
                block_reason=self.block_reason,
                **base,
            )
        if self.tool_error:
            # When verification flagged ``inconsistent`` for this same
            # path the inconsistency is the more useful failure reason;
            # keep ``tool_error`` text as the lifecycle error_message so
            # callers don't lose the raw tool failure.
            return TaskRunStepOutcome(
                status="failed",
                error_message=self.tool_error,
                **base,
            )
        status, lifecycle_error, lifecycle_block = _apply_verification_to_lifecycle(
            verification.state
        )
        return TaskRunStepOutcome(
            status=status,
            error_message=lifecycle_error,
            block_reason=lifecycle_block,
            **base,
        )

    def _compute_verification(self) -> VerificationResult:
        return infer_verification_state(
            assistant_text=self.assistant_text,
            observations=tuple(self.evidence_observations),
            error_message=self.error_message,
            assistant_stop_reason=self.assistant_stop_reason,
        )


class TaskRunAgentSession:
    """Per-step adapter wrapping one ``Agent`` instance."""

    def __init__(
        self,
        *,
        agent: Agent,
        durable_writer: DurableSessionEventWriter,
        heartbeat: Callable[[], None],
        collector: StepEventCollector | None = None,
        event_hook: Callable[[AgentEvent], Awaitable[None] | None] | None = None,
        policy_resolution_store: PolicyResolutionStore | None = None,
    ) -> None:
        self._agent = agent
        self._writer = durable_writer
        self._heartbeat = heartbeat
        self._collector = collector or StepEventCollector()
        self._event_hook = event_hook
        self._policy_resolution_store = policy_resolution_store
        self._event_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._tool_call_state: dict[str, ToolCallState] = {}
        self._unsubscribe: Callable[[], None] | None = None
        self._consumer_task: asyncio.Task[None] | None = None

    @property
    def agent(self) -> Agent:
        return self._agent

    @property
    def collector(self) -> StepEventCollector:
        return self._collector

    @property
    def active_run_id(self) -> str | None:
        return self._agent.active_run_id

    def tool_call_state(self, tool_call_id: str) -> ToolCallState | None:
        return self._tool_call_state.get(tool_call_id)

    def remember_tool_execution_id(
        self,
        tool_call_id: str,
        tool_execution_id: str,
    ) -> None:
        state = self._tool_call_state.get(tool_call_id)
        if state is not None:
            state.tool_execution_id = tool_execution_id

    async def run(self, prompt_message: Any) -> TaskRunStepOutcome:
        """Run a single step end to end and return its outcome."""

        self._consumer_task = asyncio.create_task(self._consume_events())
        self._unsubscribe = self._agent.subscribe(self._on_event)
        try:
            await self._agent.prompt(prompt_message)
        finally:
            await self._drain_and_close_consumer()
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None
        return self._collector.outcome()

    def cancel(self) -> None:
        """Trigger abort on the agent. The consumer continues to drain
        in-flight events and exits once the agent's abort path emits its
        terminal events (``message_end`` for the aborted assistant frame,
        ``agent_end``)."""

        self._agent.abort()

    async def wait_for_idle(self) -> None:
        await self._agent.wait_for_idle()
        if self._consumer_task is not None:
            await self._consumer_task

    def _on_event(self, event: AgentEvent, _signal: asyncio.Event) -> None:
        # The synchronous fan-out preserves the wrapper-side contract that
        # ``agent_tool_executions`` rows are visible to
        # ``_finalize_governed_execution`` (legacy permission-decision
        # recorder). The async consumer handles work that legitimately
        # awaits (W4-W6 hooks). Both arms record into the same fail-closed
        # error sink (``StepEventCollector.listener_errors``).
        try:
            self._heartbeat()
        except Exception as exc:
            self._collector.listener_errors.append(_listener_error("heartbeat", exc))
        if isinstance(event, ToolExecutionStartEvent):
            self._tool_call_state[event.tool_call_id] = ToolCallState(
                tool_name=event.tool_name,
                args=event.args,
            )
        if isinstance(event, ToolExecutionEndEvent) and self._policy_resolution_store is not None:
            # Drain the hook-side block side-channel *before* the
            # collector reduces the end event — otherwise the
            # collector's ``is_error and block_reason is None`` branch
            # would mis-classify the hook block as a tool error.
            block_reason = self._policy_resolution_store.consume_block_reason(
                event.tool_call_id
            )
            if block_reason is not None:
                self._collector.note_policy_block(block_reason)
        try:
            session_event = agent_event_to_session_event(event)
        except Exception as exc:
            self._collector.listener_errors.append(
                _listener_error("event_translation", exc)
            )
            return
        try:
            self._writer.record(session_event)
        except Exception as exc:
            self._collector.listener_errors.append(
                _listener_error("durable_session_writer", exc)
            )
        try:
            self._collector.record(session_event, self.active_run_id)
        except Exception as exc:
            self._collector.listener_errors.append(
                _listener_error("step_event_collector", exc)
            )
        self._event_queue.put_nowait(event)

    async def _drain_and_close_consumer(self) -> None:
        await self._event_queue.put(_QUEUE_SENTINEL)
        if self._consumer_task is not None:
            try:
                await self._consumer_task
            except Exception as exc:
                self._collector.listener_errors.append(
                    _listener_error("consumer_task", exc)
                )

    async def _consume_events(self) -> None:
        while True:
            event = await self._event_queue.get()
            if event is _QUEUE_SENTINEL:
                return
            try:
                await self._dispatch_event_hook(event)
            except Exception as exc:
                self._collector.listener_errors.append(
                    _listener_error("event_hook", exc)
                )
            if isinstance(event, ToolExecutionEndEvent):
                # State map preserved until after the hook runs (D12
                # classifier reads tool_name+args on the end event).
                self._tool_call_state.pop(event.tool_call_id, None)

    async def _dispatch_event_hook(self, event: AgentEvent) -> None:
        if self._event_hook is None:
            return
        result = self._event_hook(event)
        if isinstance(result, Awaitable):
            await result


def _apply_verification_to_lifecycle(state: str) -> tuple[str, str | None, str | None]:
    """Map ``verification_state`` to ``(status, error_message, block_reason)``.

    Used only on the "no other lifecycle signal" path — ``error_message``,
    ``listener_errors``, ``block_reason``, and ``tool_error`` short-circuit
    earlier in ``outcome()``. Every D12 state has an explicit branch so an
    unrecognised state value raises rather than silently mapping to
    ``done``.
    """

    if state == VERIFICATION_SUPPORTED:
        return "done", None, None
    if state == VERIFICATION_MISSING_EVIDENCE:
        return "blocked", None, "claim lacks supporting evidence"
    if state == VERIFICATION_ABANDONED:
        return "blocked", None, "turn ended before completing tool follow-up"
    if state == VERIFICATION_INCONSISTENT:
        return "failed", "claim contradicts observed tool errors", None
    if state == VERIFICATION_ERROR:
        return "failed", "terminal assistant error", None
    raise ValueError(f"unrecognised verification_state: {state!r}")


def _listener_error(sink: str, exc: Exception) -> dict[str, str]:
    return {
        "sink": sink,
        "exceptionClass": type(exc).__name__,
        "message": str(exc),
    }


def _assistant_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    return "".join(parts).strip()


def _tool_result_text(result: Any) -> str:
    parts: list[str] = []
    content = (
        result.get("content", []) if isinstance(result, dict)
        else getattr(result, "content", [])
    )
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    return "\n".join(parts).strip()


__all__ = [
    "StepEventCollector",
    "TaskRunAgentSession",
    "ToolCallState",
]
