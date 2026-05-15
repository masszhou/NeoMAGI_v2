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
from ai_provider.types import AssistantMessage, TextContent, UserMessage
from cli.core.session_manager import SessionManager
from cli.core.session_types import CustomMessage
from cli.core.taskrun_service import (
    STEP_INSTRUCTION,
    TaskRunStepContext,
    TaskRunStepOutcome,
)
from cli.interactive.extension_runtime import _DEFAULT_SYSTEM_PROMPT
from cli.interactive.runtime_events import agent_event_to_session_event
from cli.interactive.session_writer import DurableSessionEventWriter
from cli.resources.system_prompt import SystemPromptParts, build_system_prompt
from cli.tools import (
    RuntimeArtifactStore,
    TaskRunPermissionContext,
    convert_coding_messages_to_llm,
    create_coding_tools,
)
from policy.audit import AuditSink, InMemoryAuditSink
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
        collector = _StepEventCollector()
        runtime_session_id = f"runtime-{uuid.uuid4()}"
        artifact_store = RuntimeArtifactStore(runtime_session_id)
        model = resolve_model(context.runtime_options.model_ref)
        self._append_taskrun_summary(context)
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

        tools = create_coding_tools(
            self.cwd,
            runtime_session_id=runtime_session_id,
            run_id_provider=active_run_id,
            audit_sink=audit_sink,
            taskrun_permission_context=TaskRunPermissionContext(
                task_run_id=context.task_run.id,
                step_id=context.step.id,
                permission_profile=context.task_run.permission_profile,
                budget=context.task_run.budget,
                record_permission_decision=self._permission_recorder(context),
            ),
            artifact_store=artifact_store,
        )
        agent = self.agent_factory(
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
            )
        )
        agent_ref.append(agent)

        async def listener(event: Any, _signal: asyncio.Event) -> None:
            context.heartbeat()
            session_event = agent_event_to_session_event(event)
            try:
                writer.record(session_event)
            except Exception as exc:
                collector.listener_errors.append(
                    {
                        "sink": "durable_session_writer",
                        "exceptionClass": type(exc).__name__,
                        "message": str(exc),
                    }
                )
            collector.record(session_event, active_run_id())

        agent.subscribe(listener)
        try:
            asyncio.run(agent.prompt(_step_instruction_message()))
            return collector.outcome()
        finally:
            artifact_store.cleanup()

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


class _StepEventCollector:
    def __init__(self) -> None:
        self.assistant_text = ""
        self.error_message: str | None = None
        self.block_reason: str | None = None
        self.tool_error: str | None = None
        self.tool_count = 0
        self.permission_count = 0
        self.run_id: str | None = None
        self.listener_errors: list[dict[str, str]] = []

    def record(self, event: Any, run_id: str | None) -> None:
        if run_id:
            self.run_id = run_id
        event_type = getattr(event, "type", "")
        if event_type == "message_end":
            self._record_message(getattr(event, "message", None))
            return
        if event_type == "tool_execution_end":
            self.tool_count += 1
            self._record_tool_result(getattr(event, "result", None), bool(getattr(event, "is_error", False)))

    def _record_message(self, message: Any) -> None:
        role = getattr(message, "role", None)
        if role == "assistant":
            if isinstance(message, AssistantMessage) and message.error_message:
                self.error_message = message.error_message
            text = _assistant_text(message)
            if text:
                self.assistant_text = text

    def _record_tool_result(self, result: Any, is_error: bool) -> None:
        details = result.get("details") if isinstance(result, dict) else getattr(result, "details", None)
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
        if self.listener_errors:
            return TaskRunStepOutcome(
                status="failed",
                assistant_text=self.assistant_text,
                run_id=self.run_id,
                tool_count=self.tool_count,
                permission_decision_count=self.permission_count,
                error_message="step finalization sink failed",
                finalize_errors=self.listener_errors,
            )
        if self.error_message:
            return TaskRunStepOutcome(
                status="failed",
                assistant_text=self.assistant_text,
                run_id=self.run_id,
                tool_count=self.tool_count,
                permission_decision_count=self.permission_count,
                error_message=self.error_message,
            )
        if self.block_reason:
            return TaskRunStepOutcome(
                status="blocked",
                assistant_text=self.assistant_text,
                run_id=self.run_id,
                tool_count=self.tool_count,
                permission_decision_count=self.permission_count,
                block_reason=self.block_reason,
            )
        if self.tool_error:
            return TaskRunStepOutcome(
                status="failed",
                assistant_text=self.assistant_text,
                run_id=self.run_id,
                tool_count=self.tool_count,
                permission_decision_count=self.permission_count,
                error_message=self.tool_error,
            )
        return TaskRunStepOutcome(
            status="done",
            assistant_text=self.assistant_text,
            run_id=self.run_id,
            tool_count=self.tool_count,
            permission_decision_count=self.permission_count,
        )


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


def _assistant_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    return "".join(parts).strip()


def _tool_result_text(result: Any) -> str:
    parts: list[str] = []
    content = result.get("content", []) if isinstance(result, dict) else getattr(result, "content", [])
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    return "\n".join(parts).strip()


__all__ = ["TaskRunHeadlessRunner"]
