"""Compaction-related runtime methods kept out of the main TUI bridge."""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from ai_provider.overflow import is_context_overflow
from ai_provider.types import AssistantMessage, Context
from cli.core.compaction.models import BranchSummaryResult, CompactionFailure, CompactionResult
from cli.core.compaction.service import (
    BranchSwitchResult,
    CompactionAppendResult,
    CompactionService,
    ProviderSummaryGenerator,
)
from cli.core.session_types import (
    AutoRetryEndEvent,
    AutoRetryStartEvent,
    BranchSummaryMessage,
    CompactionEndEvent,
    CompactionStartEvent,
    CompactionSummaryMessage,
    MessageEndEvent,
    MessageStartEvent,
)
from cli.extensions.event_types import SessionBeforeCompactResult, SessionBeforeTreeResult
from storage.session_repository import SessionRecord


def _now_ms() -> int:
    return int(time.time() * 1000)


class CompactionRuntimeMixin:
    def select_session_leaf(self, entry_id: str) -> SessionRecord:
        self._ensure_idle_for_session_switch()
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        future = asyncio.run_coroutine_threadsafe(
            self._select_session_leaf_with_summary(entry_id),
            self._loop,
        )
        try:
            return future.result(timeout=120.0)
        except CompactionFailure as exc:
            raise RuntimeError(str(exc)) from exc

    def compact_session(
        self,
        *,
        custom_instructions: str | None = None,
    ) -> CompactionAppendResult:
        self._ensure_idle_for_session_switch()
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        future = asyncio.run_coroutine_threadsafe(
            self._compact_session(custom_instructions=custom_instructions),
            self._loop,
        )
        try:
            return future.result(timeout=120.0)
        except CompactionFailure as exc:
            raise RuntimeError(str(exc)) from exc

    def consume_tree_summary_notice(self) -> str | None:
        notice = self._last_tree_summary_notice
        self._last_tree_summary_notice = None
        return notice

    async def _compact_session(
        self,
        *,
        custom_instructions: str | None,
    ) -> CompactionAppendResult:
        self._emit_session_event(CompactionStartEvent(reason="manual"))
        try:
            result = await self._compact_session_with_extension_hook(
                reason="manual",
                custom_instructions=custom_instructions,
                force=True,
            )
        except Exception as exc:
            self._emit_compaction_failure("manual", exc)
            raise
        self._refresh_agent_from_durable_session()
        self._emit_compaction_success("manual", result, will_retry=False)
        await self._emit_session_compact(result)
        message = CompactionSummaryMessage(
            summary=result.result.summary,
            tokensBefore=result.result.tokens_before,
            timestamp=_now_ms(),
        )
        self._emit_session_event(MessageStartEvent(message=message))
        self._emit_session_event(MessageEndEvent(message=message))
        return result

    async def _auto_compact_before_prompt(self, text: str, generation: int) -> None:
        if (
            generation != self._generation
            or self._session_manager is None
            or self._durable_session is None
        ):
            return
        service = self._compaction_service()
        target_budget = service.auto_compaction_budget(
            self._durable_session.id,
            prompt_text=text,
            context_window=self._model.context_window,
        )
        if target_budget is None:
            return
        self._emit_session_event(CompactionStartEvent(reason="threshold"))
        try:
            result = await self._compact_session_with_extension_hook(
                reason="threshold",
                target_budget=target_budget,
            )
        except CompactionFailure as exc:
            self._emit_compaction_failure("threshold", exc)
            raise
        self._refresh_agent_from_durable_session()
        self._emit_compaction_success("threshold", result, will_retry=False)
        await self._emit_session_compact(result)

    async def _recover_assistant_response(
        self,
        message: AssistantMessage,
        context: Context,
        attempt: int,
        max_attempts: int,
        signal: asyncio.Event | None,
    ) -> Context | None:
        if not is_context_overflow(message, context_window=self._model.context_window):
            if attempt > 1:
                self._emit_session_event(AutoRetryEndEvent(success=True, attempt=attempt))
            return None
        if attempt >= max_attempts:
            self._emit_session_event(
                AutoRetryEndEvent(
                    success=False,
                    attempt=attempt,
                    finalError=message.error_message or "context overflow after retry",
                )
            )
            return None
        if signal is not None and signal.is_set():
            return None
        return await self._compact_for_overflow_retry(message, context, attempt, max_attempts)

    async def _compact_for_overflow_retry(
        self,
        message: AssistantMessage,
        context: Context,
        attempt: int,
        max_attempts: int,
    ) -> Context | None:
        self._emit_session_event(CompactionStartEvent(reason="overflow"))
        try:
            service = self._compaction_service()
            result = await self._compact_session_with_extension_hook(
                reason="overflow",
                target_budget=service.settings.target_budget(self._model.context_window),
                force=True,
            )
        except Exception as exc:
            self._emit_compaction_failure("overflow", exc)
            return None
        self._refresh_agent_from_durable_session()
        self._emit_compaction_success("overflow", result, will_retry=True)
        await self._emit_session_compact(result)
        self._emit_session_event(
            AutoRetryStartEvent(
                attempt=attempt + 1,
                maxAttempts=max_attempts,
                delayMs=0,
                errorMessage=message.error_message or "context overflow",
            )
        )
        return Context(
            systemPrompt=context.system_prompt,
            messages=list(self._session_context_messages),
            tools=context.tools,
        )

    async def _select_session_leaf_with_summary(self, entry_id: str) -> SessionRecord:
        hook_result = await self._branch_summary_with_extension_hook(entry_id)
        result = hook_result or await self._compaction_service().summarize_branch_for_tree_switch(
            self._require_durable_session_id(),
            target_entry_id=entry_id,
        )
        self._activate_durable_session(result.session)
        self._last_tree_summary_notice = None
        if result.entry is not None and result.result is not None:
            self._last_tree_summary_notice = (
                "branch summary appended "
                f"fromId=entry:{result.result.from_id[:8]} "
                f"entry={result.entry.pi_export_id}"
            )
            message = BranchSummaryMessage(
                summary=result.result.summary,
                fromId=result.result.from_id,
                timestamp=_now_ms(),
            )
            self._emit_session_event(MessageStartEvent(message=message))
            self._emit_session_event(MessageEndEvent(message=message))
        await self._emit_session_tree(result)
        return result.session

    async def _compact_session_with_extension_hook(
        self,
        *,
        reason: Literal["manual", "threshold", "overflow"],
        custom_instructions: str | None = None,
        target_budget: int | None = None,
        force: bool = False,
    ) -> CompactionAppendResult:
        hook_result = await self._compaction_with_extension_hook(
            reason=reason,
            custom_instructions=custom_instructions,
            target_budget=target_budget,
            force=force,
        )
        if hook_result is not None:
            return hook_result
        return await self._compaction_service().compact_session(
            self._require_durable_session_id(),
            reason=reason,
            custom_instructions=custom_instructions,
            target_budget=target_budget,
            force=force,
        )

    async def _compaction_with_extension_hook(
        self,
        *,
        reason: Literal["manual", "threshold", "overflow"],
        custom_instructions: str | None,
        target_budget: int | None,
        force: bool,
    ) -> CompactionAppendResult | None:
        if self._extension_runner is None or self._session_manager is None or self._durable_session is None:
            return None
        entries = self._session_manager.entry_path(self._durable_session.id)
        event = {
            "type": "session_before_compact",
            "preparation": {
                "reason": reason,
                "targetBudget": target_budget,
                "force": force,
            },
            "branchEntries": [_dump_entry(entry) for entry in entries],
            "customInstructions": custom_instructions,
            "signal": None,
        }
        for raw in await self._extension_runner.emit(event):
            parsed = SessionBeforeCompactResult.model_validate(raw)
            if parsed.cancel:
                raise CompactionFailure("cancelled", "compaction cancelled by extension")
            if parsed.compaction is not None:
                result = CompactionResult.model_validate(parsed.compaction).model_copy(
                    update={"reason": reason, "from_hook": True}
                )
                entry = self._session_manager.append_compaction(self._durable_session.id, result)
                return CompactionAppendResult(entry=entry, result=result)
        return None

    async def _branch_summary_with_extension_hook(self, entry_id: str) -> BranchSwitchResult | None:
        if self._extension_runner is None or self._session_manager is None or self._durable_session is None:
            return None
        session = self._durable_session
        old_leaf_id = self._current_leaf_pi_id()
        event = {
            "type": "session_before_tree",
            "preparation": {
                "targetId": entry_id,
                "oldLeafId": old_leaf_id,
                "userWantsSummary": True,
            },
            "signal": None,
        }
        for raw in await self._extension_runner.emit(event):
            parsed = SessionBeforeTreeResult.model_validate(raw)
            if parsed.cancel:
                raise CompactionFailure("cancelled", "tree navigation cancelled by extension")
            if parsed.summary is not None:
                payload = dict(parsed.summary)
                payload.setdefault("fromId", old_leaf_id or entry_id)
                result = BranchSummaryResult.model_validate(payload).model_copy(
                    update={"from_hook": True}
                )
                entry = self._session_manager.append_branch_summary(
                    session.id,
                    target_entry_id=entry_id,
                    result=result,
                )
                refreshed = self._session_manager.resume_session(session.id)
                return BranchSwitchResult(session=refreshed, entry=entry, result=result)
        return None

    async def _emit_session_compact(self, result: CompactionAppendResult) -> None:
        if self._extension_runner is None:
            return
        await self._extension_runner.emit(
            {
                "type": "session_compact",
                "compactionEntry": _dump_entry(result.entry),
                "fromExtension": result.result.from_hook,
            }
        )

    async def _emit_session_tree(self, result: BranchSwitchResult) -> None:
        if self._extension_runner is None:
            return
        await self._extension_runner.emit(
            {
                "type": "session_tree",
                "newLeafId": self._current_leaf_pi_id(),
                "oldLeafId": result.result.from_id if result.result is not None else None,
                "summaryEntry": _dump_entry(result.entry) if result.entry is not None else None,
                "fromExtension": bool(result.result and result.result.from_hook),
            }
        )

    def _current_leaf_pi_id(self) -> str | None:
        if self._session_manager is None or self._durable_session is None:
            return None
        refreshed = self._session_manager.repository.get_session(self._durable_session.id)
        if refreshed is not None:
            self._durable_session = refreshed
        if self._durable_session.current_leaf_entry_id is None:
            return None
        entry = self._session_manager.repository.get_entry(
            self._durable_session.id,
            self._durable_session.current_leaf_entry_id,
        )
        return entry.pi_export_id if entry is not None else None

    def _refresh_agent_from_durable_session(self) -> None:
        if self._session_manager is None or self._durable_session is None:
            return
        refreshed = self._session_manager.repository.get_session(self._durable_session.id)
        if refreshed is not None:
            self._durable_session = refreshed
        self._session_context_messages = self._load_session_context_messages()
        self._provider_cache_affinity_id = self._resolve_provider_cache_affinity_id()
        self._agent = self._build_agent(self._generation)

    def _compaction_service(self) -> CompactionService:
        if self._session_manager is None:
            raise RuntimeError("durable session manager is not available")
        return CompactionService(
            manager=self._session_manager,
            model=self._model,
            generator=self._summary_generator
            or ProviderSummaryGenerator(get_api_key=self._get_api_key),
        )

    def _emit_compaction_success(
        self,
        reason: str,
        result: CompactionAppendResult,
        *,
        will_retry: bool,
    ) -> None:
        self._emit_session_event(
            CompactionEndEvent(
                reason=reason,
                result=result.result,
                aborted=False,
                willRetry=will_retry,
            )
        )

    def _emit_compaction_failure(self, reason: str, exc: Exception) -> None:
        self._emit_session_event(
            CompactionEndEvent(
                reason=reason,
                result=None,
                aborted=True,
                willRetry=False,
                errorMessage=str(exc),
            )
        )


__all__ = ["CompactionRuntimeMixin"]


def _dump_entry(entry) -> dict:
    if entry is None:
        return {}
    return entry.payload.model_dump(by_alias=True, exclude_none=True)
