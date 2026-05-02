"""Interactive TUI bridge for the real ``agent_core.Agent`` runtime."""

from __future__ import annotations

import asyncio
import inspect
import queue
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_core import Agent, AgentOptions
from agent_core.cache_affinity import derive_provider_cache_affinity_id
from ai_provider.credentials import resolve_api_key
from ai_provider.model_registry import resolve_model, validate_thinking_level_for_model
from ai_provider.types import (
    AssistantMessage,
    CacheRetention,
    TextContent,
    ThinkingLevel,
    Usage,
    UsageCost,
    UserMessage,
)
from cli.core.session_types import (
    AgentEndEvent,
    AgentSessionEvent,
    BashExecutionMessage,
    MessageEndEvent,
    MessageStartEvent,
    QueueUpdateEvent,
)
from cli.core.compaction.service import SummaryGenerator
from cli.core.session_manager import BranchSessionResult, SessionManager
from cli.extensions.event_types import BashResult, UserBashEvent, UserBashEventResult
from cli.tools import (
    RuntimeArtifactStore,
    convert_coding_messages_to_llm,
    create_coding_tools,
    create_read_only_tools,
)
from cli.tools.bash import create_bash_tool_definition
from cli.tools.wrapper import ToolRuntime, wrap_tool_definition
from policy.audit import AuditSink, InMemoryAuditSink
from storage.ids import short_session_id
from storage.session_repository import SessionRecord

from .compaction_runtime import CompactionRuntimeMixin
from .runtime_events import agent_event_to_session_event
from .session_writer import DurableSessionEventWriter


@dataclass(frozen=True, slots=True)
class RuntimeState:
    is_running: bool
    queued_steering: tuple[str, ...]
    queued_follow_up: tuple[str, ...]
    model_ref: str
    runtime_session_id: str
    provider_cache_affinity_id: str | None
    durable_session_id: str | None = None
    current_leaf_entry_id: str | None = None


_DEFAULT_SYSTEM_PROMPT = "You are a helpful coding assistant."


def _now_ms() -> int:
    return int(time.time() * 1000)


def _empty_usage() -> Usage:
    return Usage(
        input=0,
        output=0,
        cacheRead=0,
        cacheWrite=0,
        totalTokens=0,
        cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
    )


class InteractiveAgentRuntime(CompactionRuntimeMixin):
    """Owns one ``Agent`` plus a background asyncio loop.

    The runtime never mutates TUI components directly. Agent listeners adapt
    events and enqueue them for the controller to drain on the UI loop.
    """

    def __init__(
        self,
        *,
        model_ref: str = "faux/faux-1",
        thinking_level: ThinkingLevel = "off",
        cache_retention: CacheRetention | None = None,
        agent_factory: Callable[[AgentOptions], Agent] = Agent,
        cwd: str | Path | None = None,
        tool_profile: str = "coding",
        audit_sink: AuditSink | None = None,
        session_manager: SessionManager | None = None,
        summary_generator: SummaryGenerator | None = None,
        user_bash_hook: Callable[
            [UserBashEvent], UserBashEventResult | Awaitable[UserBashEventResult] | Any
        ]
        | None = None,
    ) -> None:
        self._model_ref = model_ref
        self._model = resolve_model(model_ref)
        self._thinking_level = validate_thinking_level_for_model(
            self._model,
            thinking_level,
        )
        self._cache_retention = cache_retention
        self._agent_factory = agent_factory
        self._cwd = Path(cwd or Path.cwd()).resolve()
        self._tool_profile = tool_profile
        self._audit_sink = audit_sink or InMemoryAuditSink()
        self._session_manager = session_manager
        self._durable_session = self._start_durable_session()
        self._session_context_messages = self._load_session_context_messages()
        self._summary_generator = summary_generator
        self._last_tree_summary_notice: str | None = None
        self._user_bash_hook = user_bash_hook

        self._events: queue.Queue[AgentSessionEvent] = queue.Queue()
        self._lock = threading.RLock()
        self._wake: Callable[[], None] | None = None
        self._queued_steering: list[str] = []
        self._queued_follow_up: list[str] = []
        self._active_future: Future[Any] | None = None
        self._closed = False
        self._generation = 0

        self._runtime_session_id = self._mint_runtime_session_id()
        self._artifact_store = RuntimeArtifactStore(self._runtime_session_id)
        self._provider_cache_affinity_id = self._resolve_provider_cache_affinity_id()
        self._session_writer = self._build_session_writer()
        self._agent = self._build_agent(self._generation)

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="neomagi-agent-runtime",
            daemon=True,
        )
        self._thread.start()

    @property
    def state(self) -> RuntimeState:
        with self._lock:
            return RuntimeState(
                is_running=self._is_running_locked(),
                queued_steering=tuple(self._queued_steering),
                queued_follow_up=tuple(self._queued_follow_up),
                model_ref=self._model_ref,
                runtime_session_id=self._runtime_session_id,
                provider_cache_affinity_id=self._provider_cache_affinity_id,
                durable_session_id=(
                    self._durable_session.id if self._durable_session is not None else None
                ),
                current_leaf_entry_id=(
                    self._durable_session.current_leaf_entry_id
                    if self._durable_session is not None
                    else None
                ),
            )

    @property
    def footer_summary(self) -> str:
        cache = self._cache_retention or "default"
        durable = (
            f"  session={short_session_id(self._durable_session.id)}"
            f" name={_display_name(self._durable_session.display_name)}"
            if self._durable_session is not None
            else ""
        )
        return (
            f"runtime: {self._model_ref}  thinking={self._thinking_level}  "
            f"cache={cache}{durable}"
        )

    def session_switch_summary(self, action: str) -> str:
        """Compact session-aware UI summary prepared for the TUI layer."""

        stats = self.session_stats()
        if stats is None:
            return f"{action} session=none name=(unnamed) leaf=none context=0 messages"
        parent = (
            f" parent=session:{short_session_id(stats.parent_session_id)}"
            if stats.parent_session_id
            else ""
        )
        return (
            f"{action} session={short_session_id(stats.session_id)}"
            f" name={_display_name(stats.name)}"
            f" leaf={_leaf_ref(stats.current_leaf)}"
            f" context={len(self._session_context_messages)} messages"
            f"{parent}"
        )

    def set_event_wake(self, wake: Callable[[], None] | None) -> None:
        self._wake = wake

    def submit(self, text: str) -> None:
        prompt = text if text.strip() else ""
        if not prompt:
            return
        with self._lock:
            self._ensure_open()
            if self._is_running_locked():
                raise RuntimeError("runtime is already processing; use steer() or follow_up()")
            self._queued_steering.clear()
            self._queued_follow_up.clear()
            self._enqueue_queue_update_locked()
            self._active_future = asyncio.run_coroutine_threadsafe(
                self._run_prompt(prompt, self._generation),
                self._loop,
            )

    def steer(self, text: str) -> None:
        prompt = text if text.strip() else ""
        if not prompt:
            return
        with self._lock:
            if not self._is_running_locked():
                self.submit(prompt)
                return
            self._queued_steering.append(prompt)
            self._loop.call_soon_threadsafe(self._agent.steer, self._user_message(prompt))
            self._enqueue_queue_update_locked()

    def follow_up(self, text: str) -> None:
        prompt = text if text.strip() else ""
        if not prompt:
            return
        with self._lock:
            if not self._is_running_locked():
                self.submit(prompt)
                return
            self._queued_follow_up.append(prompt)
            self._loop.call_soon_threadsafe(self._agent.follow_up, self._user_message(prompt))
            self._enqueue_queue_update_locked()

    def abort(self) -> bool:
        with self._lock:
            active = self._is_running_locked()
            if active:
                self._loop.call_soon_threadsafe(self._agent.abort)
            else:
                self._queued_steering.clear()
                self._queued_follow_up.clear()
                self._enqueue_queue_update_locked()
            return active

    def reset(self, *, wait_timeout: float = 1.0) -> None:
        future: Future[Any] | None
        with self._lock:
            future = self._active_future if self._is_running_locked() else None
            if future is not None:
                self._loop.call_soon_threadsafe(self._agent.abort)

        if future is not None:
            try:
                future.result(timeout=wait_timeout)
            except (FutureTimeoutError, Exception):
                pass

        with self._lock:
            self._generation += 1
            self._artifact_store.cleanup()
            self._runtime_session_id = self._mint_runtime_session_id()
            self._artifact_store = RuntimeArtifactStore(self._runtime_session_id)
            if self._session_manager is not None:
                self._durable_session = self._session_manager.new_session(self._cwd)
                self._session_context_messages = []
            self._provider_cache_affinity_id = self._resolve_provider_cache_affinity_id()
            self._queued_steering.clear()
            self._queued_follow_up.clear()
            self._clear_event_queue_locked()
            self._agent = self._build_agent(self._generation)
            self._active_future = None
            self._enqueue_queue_update_locked()

    def session_stats(self):
        if self._session_manager is None or self._durable_session is None:
            return None
        return self._session_manager.session_stats(self._durable_session.id)

    def list_recent_sessions(self, *, limit: int = 10) -> list[SessionRecord]:
        if self._session_manager is None:
            return []
        return self._session_manager.list_recent_sessions(cwd=str(self._cwd), limit=limit)

    def rename_session(self, name: str | None) -> SessionRecord:
        self._ensure_idle_for_session_switch()
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        self._durable_session = self._session_manager.rename_session(
            self._durable_session.id,
            name,
        )
        return self._durable_session

    def resume_session(self, session_id: str) -> SessionRecord:
        self._ensure_idle_for_session_switch()
        if self._session_manager is None:
            raise RuntimeError("durable session manager is not available")
        session = self._session_manager.resume_session(session_id)
        self._activate_durable_session(session)
        return session

    def fork_session(self, entry_id: str) -> BranchSessionResult:
        self._ensure_idle_for_session_switch()
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        result = self._session_manager.fork_session(self._durable_session.id, entry_id)
        self._activate_durable_session(result.session)
        return result

    def clone_session(self) -> BranchSessionResult:
        self._ensure_idle_for_session_switch()
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        result = self._session_manager.clone_session(self._durable_session.id)
        self._activate_durable_session(result.session)
        return result

    def session_tree(self):
        if self._session_manager is None or self._durable_session is None:
            return []
        return self._session_manager.session_tree(self._durable_session.id)

    def export_jsonl(self, path: str | Path) -> Path:
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        return self._session_manager.export_jsonl(self._durable_session.id, path)

    def import_jsonl(self, path: str | Path) -> SessionRecord:
        self._ensure_idle_for_session_switch()
        if self._session_manager is None:
            raise RuntimeError("durable session manager is not available")
        session = self._session_manager.import_jsonl(path)
        self._activate_durable_session(session)
        return session

    def drain_events(self) -> list[AgentSessionEvent]:
        events: list[AgentSessionEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def shutdown(self, *, timeout: float = 2.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            future = self._active_future if self._is_running_locked() else None
            if future is not None:
                self._loop.call_soon_threadsafe(self._agent.abort)

        if future is not None:
            try:
                future.result(timeout=timeout)
            except (FutureTimeoutError, Exception):
                pass

        if self._thread.is_alive():
            cancel_future = asyncio.run_coroutine_threadsafe(
                self._cancel_pending_tasks(),
                self._loop,
            )
            try:
                cancel_future.result(timeout=timeout)
            except (FutureTimeoutError, Exception):
                pass

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=timeout)
        if not self._thread.is_alive():
            self._loop.close()
        self._artifact_store.cleanup()

    def run_user_bash(self, command: str, *, exclude_from_context: bool) -> None:
        if not command.strip():
            return
        with self._lock:
            self._ensure_open()
            if self._is_running_locked():
                raise RuntimeError("runtime is already processing; user bash is only available while idle")
            self._queued_steering.clear()
            self._queued_follow_up.clear()
            self._enqueue_queue_update_locked()
            self._active_future = asyncio.run_coroutine_threadsafe(
                self._run_user_bash(command.strip(), exclude_from_context, self._generation),
                self._loop,
            )

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _run_prompt(self, text: str, generation: int) -> None:
        try:
            await self._auto_compact_before_prompt(text, generation)
            await self._agent.prompt(text)
        except Exception as exc:
            self._enqueue_error(str(exc), generation)
        finally:
            with self._lock:
                if generation == self._generation:
                    self._queued_steering.clear()
                    self._queued_follow_up.clear()
                    self._enqueue_queue_update_locked()
                    self._active_future = None

    async def _cancel_pending_tasks(self) -> None:
        current = asyncio.current_task()
        tasks = [
            task
            for task in asyncio.all_tasks(self._loop)
            if task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _build_agent(self, generation: int) -> Agent:
        agent_ref: list[Agent] = []

        def active_run_id() -> str | None:
            return agent_ref[0].active_run_id if agent_ref else None

        agent = self._agent_factory(
            AgentOptions(
                model=self._model,
                system_prompt=_DEFAULT_SYSTEM_PROMPT,
                thinking_level=self._thinking_level,
                cache_retention=self._cache_retention,
                session_id=self._provider_cache_affinity_id,
                messages=list(self._session_context_messages),
                get_api_key=self._get_api_key,
                tools=self._build_tools(run_id_provider=active_run_id),
                convert_to_llm=convert_coding_messages_to_llm,
                recover_assistant_response=self._recover_assistant_response,
            )
        )
        agent_ref.append(agent)

        def listener(event: Any, _signal: asyncio.Event) -> None:
            if generation != self._generation:
                return
            self._emit_session_event(agent_event_to_session_event(event))

        agent.subscribe(listener)
        return agent

    def _build_tools(
        self,
        *,
        run_id_provider: Callable[[], str | None] | None = None,
    ):
        if self._tool_profile == "none":
            return []
        if self._tool_profile == "coding":
            return create_coding_tools(
                self._cwd,
                runtime_session_id=self._runtime_session_id,
                run_id_provider=run_id_provider,
                audit_sink=self._audit_sink,
                artifact_store=self._artifact_store,
            )
        if self._tool_profile == "read_only":
            return create_read_only_tools(
                self._cwd,
                runtime_session_id=self._runtime_session_id,
                run_id_provider=run_id_provider,
                audit_sink=self._audit_sink,
            )
        raise ValueError(f"unsupported tool profile: {self._tool_profile}")

    async def _run_user_bash(
        self,
        command: str,
        exclude_from_context: bool,
        generation: int,
    ) -> None:
        try:
            result = await self._resolve_user_bash_result(command, exclude_from_context)
            message = BashExecutionMessage(
                command=command,
                output=result.output,
                exitCode=result.exit_code,
                cancelled=result.cancelled,
                truncated=result.truncated,
                fullOutputPath=result.full_output_path,
                timestamp=_now_ms(),
                excludeFromContext=exclude_from_context,
            )
            self._agent.state.messages.append(message)
            self._emit_session_event(MessageStartEvent(message=message))
            self._emit_session_event(MessageEndEvent(message=message))
        except Exception as exc:
            self._enqueue_error(str(exc), generation)
        finally:
            with self._lock:
                if generation == self._generation:
                    self._active_future = None
                    self._enqueue_queue_update_locked()
            self._notify_wake()

    async def _resolve_user_bash_result(self, command: str, exclude_from_context: bool) -> BashResult:
        event = UserBashEvent(
            command=command,
            excludeFromContext=exclude_from_context,
            cwd=str(self._cwd),
        )
        if self._user_bash_hook is not None:
            hook_result = self._user_bash_hook(event)
            if inspect.isawaitable(hook_result):
                hook_result = await hook_result
            parsed = UserBashEventResult.model_validate(hook_result)
            if parsed.result is not None:
                return parsed.result

        tool = wrap_tool_definition(
            create_bash_tool_definition(artifact_store=self._artifact_store),
            ToolRuntime(
                cwd=str(self._cwd),
                runtime_session_id=self._runtime_session_id,
                run_id=self._mint_run_id(),
                actor="user",
                audit_sink=self._audit_sink,
            ),
        )
        call_id = f"userbash-{uuid.uuid4()}"
        result = await tool.execute(
            call_id,
            {"command": command},
            None,
            None,
        )
        details = result.details if isinstance(result.details, dict) else {}
        output = _tool_text(result)
        truncation = details.get("truncation") if isinstance(details.get("truncation"), dict) else {}
        return BashResult(
            output=output,
            exitCode=details.get("exitCode"),
            cancelled=bool(details.get("cancelled")),
            truncated=bool(truncation.get("truncated")),
            fullOutputPath=details.get("fullOutputPath"),
        )

    def _get_api_key(self, provider: str) -> str | None:
        if provider == "faux":
            return None
        return resolve_api_key(self._model)

    def _enqueue_error(self, message: str, generation: int) -> None:
        if generation != self._generation:
            return
        failure = AssistantMessage(
            role="assistant",
            content=[TextContent(text="")],
            api=self._model.api,
            provider=self._model.provider,
            model=self._model.id,
            usage=_empty_usage(),
            stopReason="error",
            errorMessage=message,
            timestamp=_now_ms(),
        )
        for event in (
            MessageStartEvent(message=failure),
            MessageEndEvent(message=failure),
            AgentEndEvent(messages=[failure]),
        ):
            self._emit_session_event(event)

    def _enqueue_queue_update_locked(self) -> None:
        self._emit_session_event(
            QueueUpdateEvent(
                steering=list(self._queued_steering),
                followUp=list(self._queued_follow_up),
            )
        )

    def _clear_event_queue_locked(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                return

    def _is_running_locked(self) -> bool:
        return self._active_future is not None and not self._active_future.done()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("runtime is closed")

    def _notify_wake(self) -> None:
        wake = self._wake
        if wake is not None:
            wake()

    def _emit_session_event(self, event: AgentSessionEvent) -> None:
        if self._session_writer is not None:
            self._session_writer.record(event)
            if self._durable_session is not None and self._session_manager is not None:
                refreshed = self._session_manager.repository.get_session(
                    self._durable_session.id
                )
                if refreshed is not None:
                    self._durable_session = refreshed
        self._events.put(event)
        self._notify_wake()

    def _start_durable_session(self) -> SessionRecord | None:
        if self._session_manager is None:
            return None
        return self._session_manager.start_or_create(self._cwd)

    def _build_session_writer(self) -> DurableSessionEventWriter | None:
        if self._session_manager is None:
            return None
        return DurableSessionEventWriter(
            manager=self._session_manager,
            session_id_provider=self._require_durable_session_id,
            runtime_session_id_provider=lambda: self._runtime_session_id,
            run_id_provider=self._active_run_id,
        )

    def _load_session_context_messages(self) -> list[Any]:
        if self._session_manager is None or self._durable_session is None:
            return []
        context = self._session_manager.build_session_context(self._durable_session.id)
        return convert_coding_messages_to_llm(list(context.messages))

    def _resolve_provider_cache_affinity_id(self) -> str | None:
        if self._durable_session is not None:
            return self._durable_session.provider_cache_affinity_id
        return derive_provider_cache_affinity_id(self._runtime_session_id)

    def _require_durable_session_id(self) -> str:
        if self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        return self._durable_session.id

    def _active_run_id(self) -> str | None:
        agent = getattr(self, "_agent", None)
        return agent.active_run_id if agent is not None else None

    def _ensure_idle_for_session_switch(self) -> None:
        with self._lock:
            if self._is_running_locked():
                raise RuntimeError("session switch is not available while streaming")

    def _activate_durable_session(self, session: SessionRecord) -> None:
        with self._lock:
            self._generation += 1
            self._artifact_store.cleanup()
            self._runtime_session_id = self._mint_runtime_session_id()
            self._artifact_store = RuntimeArtifactStore(self._runtime_session_id)
            self._durable_session = session
            self._session_context_messages = self._load_session_context_messages()
            self._provider_cache_affinity_id = self._resolve_provider_cache_affinity_id()
            self._queued_steering.clear()
            self._queued_follow_up.clear()
            self._clear_event_queue_locked()
            self._agent = self._build_agent(self._generation)
            self._active_future = None
            self._enqueue_queue_update_locked()

    @staticmethod
    def _mint_runtime_session_id() -> str:
        return f"runtime-{uuid.uuid4()}"

    @staticmethod
    def _mint_run_id() -> str:
        return f"run-{uuid.uuid7()}"

    @staticmethod
    def _user_message(text: str) -> UserMessage:
        return UserMessage(
            role="user",
            content=[TextContent(text=text)],
            timestamp=_now_ms(),
        )


def _tool_text(result: Any) -> str:
    parts = []
    for block in result.content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif getattr(block, "type", None) == "text":
            parts.append(str(block.text))
    return "\n".join(parts)


def _display_name(value: str | None) -> str:
    return value or "(unnamed)"


def _leaf_ref(value: str | None) -> str:
    if not value:
        return "none"
    return f"entry:{value[:8]}"


__all__ = ["InteractiveAgentRuntime", "RuntimeState"]
