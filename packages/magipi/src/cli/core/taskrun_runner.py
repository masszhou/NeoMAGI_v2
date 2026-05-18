"""Headless TaskRun step runner backed by the durable AgentSession runtime."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_core import Agent, AgentOptions
from ai_provider.credentials import resolve_api_key
from ai_provider.model_registry import resolve_model
from ai_provider.types import TextContent, UserMessage
from cli.core.session_manager import SessionManager
from cli.core.session_types import CustomMessage
from dataclasses import replace

from cli.core.taskrun_agent_session import StepEventCollector, TaskRunAgentSession
from cli.core.evidence_classifier import (
    VERIFICATION_SUPPORTED,
    EvidenceObservation,
)
from cli.core.taskrun_event_payloads import (
    TASK_STEP_BLOCKER_DETECTED,
    TASK_STEP_EVIDENCE_MISSING,
    TASK_STEP_EVIDENCE_RECORDED,
    TASK_STEP_OUTCOME_SUPPORTED,
    TASK_STEP_OUTCOME_UNSUPPORTED,
    TASK_STEP_RESUME_CONTEXT_GENERATED,
    build_step_blocker_detected_payload,
    build_step_evidence_missing_payload,
    build_step_evidence_recorded_payload,
    build_step_outcome_supported_payload,
    build_step_outcome_unsupported_payload,
    build_step_resume_context_generated_payload,
)
from cli.core.taskrun_policy_hook import (
    build_back_fill_event_hook,
    build_before_tool_call_hook,
    build_evidence_event_hook,
    chain_event_hooks,
)
from cli.core.taskrun_service import (
    STEP_INSTRUCTION,
    TaskRunStepContext,
    TaskRunStepOutcome,
)
from cli.interactive.extension_runtime import _DEFAULT_SYSTEM_PROMPT
from cli.interactive.session_writer import DurableSessionEventWriter
from cli.resources.system_prompt import SystemPromptParts, build_system_prompt
from cli.tools import (
    RuntimeArtifactStore,
    TaskRunPermissionContext,
    convert_coding_messages_to_llm,
    create_coding_tools,
)
from cli.tools.policy_resolution_store import PolicyResolutionStore
from policy.audit import AuditSink, InMemoryAuditSink
from policy.permission_profiles import PermissionBudgetState
from storage.taskrun_repository import TaskRunRepository


def _now_ms() -> int:
    return int(time.time() * 1000)


class TaskRunHeadlessRunner:
    def __init__(
        self,
        *,
        session_manager: SessionManager,
        task_repository: TaskRunRepository,
        cwd: str | Path,
        audit_sink_factory: Callable[[str], AuditSink] | None = None,
        agent_factory: Callable[[AgentOptions], Agent] = Agent,
    ) -> None:
        self.session_manager = session_manager
        self.task_repository = task_repository
        self.cwd = Path(cwd).resolve()
        self.audit_sink_factory = audit_sink_factory or (lambda _session_id: InMemoryAuditSink())
        self.agent_factory = agent_factory

    def run(self, context: TaskRunStepContext) -> TaskRunStepOutcome:
        runtime_session_id = f"runtime-{uuid.uuid4()}"
        artifact_store = RuntimeArtifactStore(runtime_session_id)
        self._append_taskrun_summary(context)
        agent, writer, policy_resolution_store = self._prepare_agent(
            context,
            runtime_session_id=runtime_session_id,
            artifact_store=artifact_store,
        )
        collector = StepEventCollector()
        try:
            return asyncio.run(
                self._run_session(
                    agent=agent,
                    writer=writer,
                    collector=collector,
                    heartbeat=context.heartbeat,
                    context=context,
                    policy_resolution_store=policy_resolution_store,
                )
            )
        finally:
            artifact_store.cleanup()

    def _prepare_agent(
        self,
        context: TaskRunStepContext,
        *,
        runtime_session_id: str,
        artifact_store: RuntimeArtifactStore,
    ) -> tuple[Agent, DurableSessionEventWriter, PolicyResolutionStore]:
        model = resolve_model(context.runtime_options.model_ref)
        session = self.session_manager.resume_session(context.task_run.agent_session_id)
        session_context = self.session_manager.build_session_context(
            context.task_run.agent_session_id
        )
        messages = convert_coding_messages_to_llm(list(session_context.messages))
        audit_sink = self.audit_sink_factory(context.task_run.agent_session_id)
        agent_ref: list[Agent] = []

        def active_run_id() -> str | None:
            return agent_ref[0].active_run_id if agent_ref else None

        writer = DurableSessionEventWriter(
            manager=self.session_manager,
            session_id_provider=lambda: context.task_run.agent_session_id,
            runtime_session_id_provider=lambda: runtime_session_id,
            run_id_provider=active_run_id,
        )
        policy_resolution_store = PolicyResolutionStore()
        budget_state = PermissionBudgetState()
        tools = self._create_tools(
            context,
            runtime_session_id=runtime_session_id,
            run_id_provider=active_run_id,
            audit_sink=audit_sink,
            artifact_store=artifact_store,
            policy_resolution_store=policy_resolution_store,
            budget_state=budget_state,
        )
        before_tool_call = build_before_tool_call_hook(
            task_repository=self.task_repository,
            task_run_id=context.task_run.id,
            step_id=context.step.id,
            agent_session_id=context.task_run.agent_session_id,
            permission_profile=context.task_run.permission_profile,
            budget=context.task_run.budget or None,
            budget_state=budget_state,
            policy_resolution_store=policy_resolution_store,
            cwd=str(self.cwd),
            runtime_session_id=runtime_session_id,
            run_id_provider=active_run_id,
        )
        agent = self._create_agent(
            context,
            session,
            model,
            messages,
            tools,
            before_tool_call=before_tool_call,
        )
        agent_ref.append(agent)
        return agent, writer, policy_resolution_store

    async def _run_session(
        self,
        *,
        agent: Agent,
        writer: DurableSessionEventWriter,
        collector: StepEventCollector,
        heartbeat: Callable[[], None],
        context: TaskRunStepContext,
        policy_resolution_store: PolicyResolutionStore,
    ) -> TaskRunStepOutcome:
        session_ref: list[TaskRunAgentSession] = []

        def remember(tool_call_id: str, tool_execution_id: str) -> None:
            if session_ref:
                session_ref[0].remember_tool_execution_id(tool_call_id, tool_execution_id)

        def lookup_state(tool_call_id: str):
            if session_ref:
                return session_ref[0].tool_call_state(tool_call_id)
            return None

        def record_observation(observation: EvidenceObservation) -> None:
            collector.evidence_observations.append(observation)

        back_fill_hook = build_back_fill_event_hook(
            task_repository=self.task_repository,
            task_run_id=context.task_run.id,
            step_id=context.step.id,
            agent_session_id=context.task_run.agent_session_id,
            remember_tool_execution_id=remember,
        )
        evidence_hook = build_evidence_event_hook(
            task_repository=self.task_repository,
            task_run_id=context.task_run.id,
            step_id=context.step.id,
            tool_call_state_lookup=lookup_state,
            record_observation=record_observation,
        )
        session = TaskRunAgentSession(
            agent=agent,
            durable_writer=writer,
            heartbeat=heartbeat,
            collector=collector,
            event_hook=chain_event_hooks(back_fill_hook, evidence_hook),
            policy_resolution_store=policy_resolution_store,
        )
        session_ref.append(session)
        outcome = await session.run(_step_instruction_message())
        outcome = self._enforce_back_fill_invariant(context, outcome)
        self._emit_step_outcome_events(context, outcome, collector)
        return outcome

    def _enforce_back_fill_invariant(
        self,
        context: TaskRunStepContext,
        outcome: TaskRunStepOutcome,
    ) -> TaskRunStepOutcome:
        """W4 finalize NULL check: any ``task_permission_decisions`` row
        for this step that still has ``tool_execution_id IS NULL`` after
        the consumer drained means a back-fill was missed (e.g., abort
        before consumer ran, or an event lost in the queue). Per D11 spec
        we write a ``task_step_blocker_detected`` derived event and demote
        the step to ``blocked`` with the invariant recorded in
        ``finalize_errors`` so callers can see *why*."""

        decisions = self.task_repository.list_permission_decisions(
            context.task_run.id,
            step_id=context.step.id,
        )
        null_decisions = [
            decision for decision in decisions if decision.tool_execution_id is None
        ]
        if not null_decisions:
            return outcome
        tool_call_ids = [
            ((decision.policy_request or {}).get("source") or {}).get("tool_call_id", "?")
            for decision in null_decisions
        ]
        self.task_repository.append_event(
            task_run_id=context.task_run.id,
            step_id=context.step.id,
            event_type=TASK_STEP_BLOCKER_DETECTED,
            payload=build_step_blocker_detected_payload(
                reason="permission decisions missing tool_execution_id back-fill",
                detail={
                    "tool_call_ids": tool_call_ids,
                    "null_row_count": len(null_decisions),
                },
            ),
        )
        finalize_errors = list(outcome.finalize_errors) + [
            {
                "sink": "permission_decision_back_fill",
                "exceptionClass": "BackFillInvariantViolation",
                "message": (
                    f"{len(null_decisions)} permission decision(s) without "
                    "tool_execution_id at step finalize"
                ),
            }
        ]
        # If the lifecycle already failed (e.g. back-fill hook raised and
        # tripped the listener-error sink), the more specific ``failed``
        # status wins — we still record the invariant for diagnostics.
        if outcome.status != "done":
            return replace(outcome, finalize_errors=finalize_errors)
        return replace(
            outcome,
            status="blocked",
            block_reason="permission decisions missing tool_execution_id back-fill",
            finalize_errors=finalize_errors,
        )

    def _emit_step_outcome_events(
        self,
        context: TaskRunStepContext,
        outcome: TaskRunStepOutcome,
        collector: StepEventCollector,
    ) -> None:
        if outcome.verification_state is None:
            return
        self._emit_evidence_recorded(context, outcome, collector)
        # Supported event requires BOTH (a) the lifecycle status is
        # ``done`` AND (b) the verification state is ``supported``.
        # ``verification_state="supported"`` alone is not enough — a
        # listener failure, tool error, or policy block can still land
        # ``verification_state="supported"`` (no claim → no evidence
        # required) while the step itself failed. Writing
        # ``task_step_outcome_supported`` for a failed step is wrong
        # fact, so the dispatch checks both.
        is_lifecycle_success = (
            outcome.status == "done"
            and outcome.verification_state == VERIFICATION_SUPPORTED
        )
        if is_lifecycle_success:
            self._emit_outcome_supported(context, outcome)
        else:
            self._emit_outcome_unsupported(context, outcome)
        # The step boundary is the natural place to emit the resume
        # context marker — the service-layer summarizer that hydrates the
        # next step reads ``assistant_text`` from session state, so this
        # records the boundary snapshot it will see.
        self.task_repository.append_event(
            task_run_id=context.task_run.id,
            step_id=context.step.id,
            event_type=TASK_STEP_RESUME_CONTEXT_GENERATED,
            payload=build_step_resume_context_generated_payload(
                context_summary=outcome.assistant_text,
            ),
        )

    def _emit_evidence_recorded(
        self,
        context: TaskRunStepContext,
        outcome: TaskRunStepOutcome,
        collector: StepEventCollector,
    ) -> None:
        # One event per successful evidence_kind (grouped), so history view
        # shows "tests passed via these tool_calls" without scanning Tier 1.
        grouped: dict[str, list[str]] = {}
        for observation in collector.evidence_observations:
            if observation.is_error:
                continue
            grouped.setdefault(observation.evidence_kind, []).append(observation.tool_call_id)
        for evidence_kind, call_ids in sorted(grouped.items()):
            self.task_repository.append_event(
                task_run_id=context.task_run.id,
                step_id=context.step.id,
                event_type=TASK_STEP_EVIDENCE_RECORDED,
                payload=build_step_evidence_recorded_payload(
                    evidence_kind=evidence_kind,
                    source_tool_call_ids=call_ids,
                    claim_summary=outcome.assistant_text,
                ),
            )

    def _emit_outcome_unsupported(
        self,
        context: TaskRunStepContext,
        outcome: TaskRunStepOutcome,
    ) -> None:
        # Reads ``verification_missing_kinds`` straight from the outcome
        # instead of re-deriving from the assistant text — that re-derive
        # path silently turned ``inconsistent`` evidence into
        # ``evidence_missing`` because it only counted non-error
        # observations.
        for evidence_kind in outcome.verification_missing_kinds:
            self.task_repository.append_event(
                task_run_id=context.task_run.id,
                step_id=context.step.id,
                event_type=TASK_STEP_EVIDENCE_MISSING,
                payload=build_step_evidence_missing_payload(
                    evidence_kind=evidence_kind,
                    claim_summary=outcome.assistant_text,
                ),
            )
        self.task_repository.append_event(
            task_run_id=context.task_run.id,
            step_id=context.step.id,
            event_type=TASK_STEP_OUTCOME_UNSUPPORTED,
            payload=build_step_outcome_unsupported_payload(
                verification_state=outcome.verification_state or "",
                claim_summary=outcome.assistant_text,
                reason=outcome.verification_reason,
            ),
        )

    def _emit_outcome_supported(
        self,
        context: TaskRunStepContext,
        outcome: TaskRunStepOutcome,
    ) -> None:
        self.task_repository.append_event(
            task_run_id=context.task_run.id,
            step_id=context.step.id,
            event_type=TASK_STEP_OUTCOME_SUPPORTED,
            payload=build_step_outcome_supported_payload(
                verification_state=outcome.verification_state or "",
                claim_summary=outcome.assistant_text,
            ),
        )

    def _create_tools(
        self,
        context: TaskRunStepContext,
        *,
        runtime_session_id: str,
        run_id_provider: Callable[[], str | None],
        audit_sink: AuditSink,
        artifact_store: RuntimeArtifactStore,
        policy_resolution_store: PolicyResolutionStore,
        budget_state: PermissionBudgetState,
    ):
        return create_coding_tools(
            self.cwd,
            runtime_session_id=runtime_session_id,
            run_id_provider=run_id_provider,
            audit_sink=audit_sink,
            taskrun_permission_context=TaskRunPermissionContext(
                task_run_id=context.task_run.id,
                step_id=context.step.id,
                permission_profile=context.task_run.permission_profile,
                budget=context.task_run.budget,
                budget_state=budget_state,
                record_permission_decision=self._permission_recorder(context),
                policy_resolution_store=policy_resolution_store,
            ),
            artifact_store=artifact_store,
        )

    def _create_agent(
        self,
        context: TaskRunStepContext,
        session: Any,
        model: Any,
        messages: list[Any],
        tools: list[Any],
        *,
        before_tool_call: Any | None = None,
    ) -> Agent:
        return self.agent_factory(
            AgentOptions(
                model=model,
                system_prompt=_system_prompt(self.cwd, [tool.name for tool in tools]),
                thinking_level=context.runtime_options.thinking_level,
                cache_retention=context.runtime_options.cache_retention,
                session_id=session.provider_cache_affinity_id,
                messages=messages,
                tools=tools,
                tool_execution="sequential",
                convert_to_llm=convert_coding_messages_to_llm,
                get_api_key=lambda _provider: None
                if model.provider == "faux"
                else resolve_api_key(model),
                before_tool_call=before_tool_call,
            )
        )

    def _append_taskrun_summary(self, context: TaskRunStepContext) -> None:
        content = _taskrun_summary_block(context)
        self.session_manager.append_custom_message(
            context.task_run.agent_session_id,
            CustomMessage(
                customType="taskRunSummary",
                content=content,
                display=False,
                details={
                    "taskRunId": context.task_run.id,
                    "stepId": context.step.id,
                    "stepIndex": context.step.step_index,
                    "summaryVersion": "p2-m3-v1",
                },
                timestamp=_now_ms(),
            ),
        )

    def _permission_recorder(self, context: TaskRunStepContext):
        def record(**kwargs: Any) -> None:
            tool_call_id = kwargs.pop("tool_call_id", None)
            tool_execution_id = kwargs.get("tool_execution_id")
            if tool_execution_id is None and isinstance(tool_call_id, str):
                tool_execution_id = self.task_repository.find_tool_execution_id(
                    session_id=context.task_run.agent_session_id,
                    tool_call_id=tool_call_id,
                )
            if tool_execution_id is None:
                raise RuntimeError(
                    f"could not link task permission decision to tool execution: {tool_call_id}"
                )
            self.task_repository.append_permission_decision(
                **{**kwargs, "tool_execution_id": tool_execution_id}
            )

        return record


def _step_instruction_message() -> UserMessage:
    return UserMessage(
        role="user",
        content=[TextContent(text=STEP_INSTRUCTION)],
        timestamp=_now_ms(),
    )


def _taskrun_summary_block(context: TaskRunStepContext) -> str:
    payload = {
        "goal": context.task_run.goal,
        "status": context.task_run.status,
        "step_id": context.step.id,
        "step_index": context.step.step_index,
        "summary": context.summary,
        "permission_profile_name": context.task_run.permission_profile.get("name"),
        "workspace_root": context.workspace_root,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _system_prompt(cwd: Path, active_tools: list[str]) -> str:
    return build_system_prompt(
        SystemPromptParts(
            base_prompt=_DEFAULT_SYSTEM_PROMPT,
            active_tools=tuple(active_tools),
            cwd=str(cwd),
        )
    )


__all__ = ["TaskRunHeadlessRunner"]
