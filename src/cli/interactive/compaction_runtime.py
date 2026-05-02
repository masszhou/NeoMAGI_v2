"""Compaction-related runtime methods kept out of the main TUI bridge."""

from __future__ import annotations

import asyncio
import time

from ai_provider.overflow import is_context_overflow
from ai_provider.types import AssistantMessage, Context
from cli.core.compaction.models import CompactionFailure
from cli.core.compaction.service import (
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
            result = await self._compaction_service().compact_session(
                self._require_durable_session_id(),
                reason="manual",
                custom_instructions=custom_instructions,
                force=True,
            )
        except Exception as exc:
            self._emit_compaction_failure("manual", exc)
            raise
        self._refresh_agent_from_durable_session()
        self._emit_compaction_success("manual", result, will_retry=False)
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
            result = await service.compact_session(
                self._durable_session.id,
                reason="threshold",
                target_budget=target_budget,
            )
        except CompactionFailure as exc:
            self._emit_compaction_failure("threshold", exc)
            raise
        self._refresh_agent_from_durable_session()
        self._emit_compaction_success("threshold", result, will_retry=False)

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
            result = await service.compact_session(
                self._require_durable_session_id(),
                reason="overflow",
                target_budget=service.settings.target_budget(self._model.context_window),
                force=True,
            )
        except Exception as exc:
            self._emit_compaction_failure("overflow", exc)
            return None
        self._refresh_agent_from_durable_session()
        self._emit_compaction_success("overflow", result, will_retry=True)
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
        result = await self._compaction_service().summarize_branch_for_tree_switch(
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
        return result.session

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
