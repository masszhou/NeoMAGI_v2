"""Compaction and branch-summary orchestration."""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from dataclasses import replace
from typing import Protocol

from ai_provider.api_registry import stream_simple
from ai_provider.runtime_types import SimpleStreamOptions, SimpleStreamFunction
from ai_provider.types import Context, Model, TextContent, UserMessage
from cli.core.session_types import MessageEntry
from cli.core.session_manager import SessionManager
from storage.session_repository import EntryRecord, SessionRecord

from .cut_points import CutPointSelection, select_cut_point
from .files import FileContext, extract_file_context
from .models import (
    BranchSummaryResult,
    CompactionFailure,
    CompactionResult,
    RetainedFragment,
    retained_fragments_from_details,
)
from .prompts import (
    build_branch_summary_prompt,
    build_compaction_prompt,
    ensure_summary_outline,
)
from .settings import BranchSummarySettings, CompactionSettings
from .tokens import estimate_entry_tokens, estimate_messages_tokens, estimate_text_tokens

ApiKeyResolver = Callable[[str], str | None | Awaitable[str | None]]


class SummaryGenerator(Protocol):
    async def generate(self, prompt: str, *, model: Model) -> str:
        ...


@dataclass(frozen=True, slots=True)
class CompactionAppendResult:
    entry: EntryRecord
    result: CompactionResult


@dataclass(frozen=True, slots=True)
class BranchSwitchResult:
    session: SessionRecord
    entry: EntryRecord | None
    result: BranchSummaryResult | None
    skipped: bool = False


class ProviderSummaryGenerator:
    def __init__(
        self,
        *,
        get_api_key: ApiKeyResolver | None = None,
        stream_fn: SimpleStreamFunction | None = None,
    ) -> None:
        self._get_api_key = get_api_key
        self._stream_fn = stream_fn or stream_simple

    async def generate(self, prompt: str, *, model: Model) -> str:
        api_key = None
        if self._get_api_key is not None:
            value = self._get_api_key(model.provider)
            api_key = await value if inspect.isawaitable(value) else value
        message = UserMessage(
            content=[TextContent(text=prompt)],
            timestamp=int(time.time() * 1000),
        )
        stream = self._stream_fn(
            model,
            Context(systemPrompt="Summarize session context.", messages=[message]),
            SimpleStreamOptions(
                cache_retention="none",
                session_id=None,
                api_key=api_key,
            ),
        )
        async for _event in stream:
            pass
        result = await stream.result()
        if result.error_message:
            raise CompactionFailure("summary-generation-failed", result.error_message)
        text = "\n".join(
            block.text
            for block in result.content
            if getattr(block, "type", None) == "text"
        )
        return text


class CompactionService:
    def __init__(
        self,
        *,
        manager: SessionManager,
        model: Model,
        settings: CompactionSettings | None = None,
        branch_settings: BranchSummarySettings | None = None,
        generator: SummaryGenerator | None = None,
    ) -> None:
        self._manager = manager
        self._model = model
        self._settings = settings or CompactionSettings()
        self._branch_settings = branch_settings or BranchSummarySettings()
        self._generator = generator or ProviderSummaryGenerator()

    @property
    def settings(self) -> CompactionSettings:
        return self._settings

    async def compact_session(
        self,
        session_id: str,
        *,
        reason: str,
        custom_instructions: str | None = None,
        target_budget: int | None = None,
        force: bool = False,
    ) -> CompactionAppendResult:
        path = self._manager.entry_path(session_id)
        hydrated_path = _hydrate_retained_fragments(path)
        selection = self._select(hydrated_path, target_budget=target_budget, force=force)
        if not selection.ok or selection.keep_from_index is None or selection.first_kept_entry_id is None:
            raise CompactionFailure(selection.reason or "no-safe-cut")

        summarized_entries = hydrated_path[: selection.keep_from_index]
        surviving_entries = hydrated_path[selection.keep_from_index :]
        if not summarized_entries:
            raise CompactionFailure("no-history-to-compact")

        file_context = self._file_context(session_id)
        prompt = build_compaction_prompt(
            messages=[entry.payload for entry in summarized_entries],
            file_context=file_context,
            custom_instructions=custom_instructions,
            surviving_messages=[entry.payload for entry in surviving_entries],
            retained_fragments=list(selection.retained_fragments),
        )
        summary = ensure_summary_outline(
            await self._generator.generate(prompt, model=self._model),
            fallback_context=prompt,
        )
        tokens_after = (
            estimate_text_tokens(summary)
            + sum(estimate_entry_tokens(entry) for entry in surviving_entries)
            + sum(estimate_text_tokens(fragment.text) for fragment in selection.retained_fragments)
        )
        if target_budget is not None and tokens_after > target_budget:
            raise CompactionFailure(
                "over-budget",
                "compaction summary and retained context still exceed target budget",
            )
        result = CompactionResult(
            summary=summary,
            firstKeptEntryId=selection.first_kept_entry_id,
            tokensBefore=selection.tokens_before,
            tokensAfter=tokens_after,
            readFiles=file_context.read_files,
            modifiedFiles=file_context.modified_files,
            reason=reason,
            fromHook=False,
            retainedFragments=list(selection.retained_fragments),
        )
        entry = self._manager.append_compaction(session_id, result)
        return CompactionAppendResult(entry=entry, result=result)

    async def auto_compact_if_needed(
        self,
        session_id: str,
        *,
        prompt_text: str,
        context_window: int | None = None,
    ) -> CompactionAppendResult | None:
        decision = self.auto_compaction_budget(
            session_id,
            prompt_text=prompt_text,
            context_window=context_window,
        )
        if decision is None:
            return None
        return await self.compact_session(
            session_id,
            reason="threshold",
            target_budget=decision,
        )

    def auto_compaction_budget(
        self,
        session_id: str,
        *,
        prompt_text: str,
        context_window: int | None = None,
    ) -> int | None:
        if not self._settings.enabled:
            return None
        window = context_window or self._model.context_window
        target = self._settings.target_budget(window)
        context = self._manager.build_session_context(session_id)
        prompt_tokens = estimate_text_tokens(prompt_text)
        if prompt_tokens > target:
            raise CompactionFailure("current-prompt-over-budget")
        tokens = estimate_messages_tokens(list(context.messages)) + prompt_tokens
        if tokens <= target:
            return None
        return max(0, target - prompt_tokens)

    async def summarize_branch_for_tree_switch(
        self,
        session_id: str,
        *,
        target_entry_id: str,
    ) -> BranchSwitchResult:
        session = self._manager.resume_session(session_id)
        target_path = self._manager.entry_path(session.id, target_entry_id)
        old_path = self._manager.entry_path(session.id)
        if not target_path:
            raise CompactionFailure("unknown-entry", f"unknown entry: {target_entry_id}")
        if not old_path:
            selected = self._manager.select_leaf(session.id, target_entry_id)
            return BranchSwitchResult(session=selected, entry=None, result=None, skipped=True)
        target = target_path[-1]
        old_leaf = old_path[-1]
        if target.pi_export_id == old_leaf.pi_export_id:
            selected = self._manager.select_leaf(session.id, target.pi_export_id)
            return BranchSwitchResult(session=selected, entry=None, result=None, skipped=True)
        if self._branch_settings.skip_prompt:
            selected = self._manager.select_leaf(session.id, target.pi_export_id)
            return BranchSwitchResult(session=selected, entry=None, result=None, skipped=True)

        branch_entries = _left_branch_entries(old_path, target_path)
        if not branch_entries:
            selected = self._manager.select_leaf(session.id, target.pi_export_id)
            return BranchSwitchResult(session=selected, entry=None, result=None, skipped=True)

        file_context = self._file_context(session.id)
        prompt = build_branch_summary_prompt(
            entries=branch_entries,
            from_id=old_leaf.pi_export_id,
            target_id=target.pi_export_id,
            file_context=file_context,
        )
        summary = ensure_summary_outline(
            await self._generator.generate(prompt, model=self._model),
            fallback_context=prompt,
        )
        result = BranchSummaryResult(
            summary=summary,
            fromId=old_leaf.pi_export_id,
            readFiles=file_context.read_files,
            modifiedFiles=file_context.modified_files,
            fromHook=False,
        )
        entry = self._manager.append_branch_summary(
            session.id,
            target_entry_id=target.pi_export_id,
            result=result,
        )
        refreshed = self._manager.resume_session(session.id)
        return BranchSwitchResult(session=refreshed, entry=entry, result=result)

    def _select(
        self,
        path: list[EntryRecord],
        *,
        target_budget: int | None,
        force: bool,
    ) -> CutPointSelection:
        selection = select_cut_point(
            path,
            keep_recent_tokens=self._settings.keep_recent_tokens,
            target_budget=target_budget,
        )
        if selection.ok or not force or selection.reason != "under-budget" or len(path) < 2:
            return selection
        tokens_before = sum(estimate_entry_tokens(entry) for entry in path)
        tokens_after = estimate_entry_tokens(path[-1])
        return CutPointSelection(
            ok=True,
            first_kept_entry_id=path[-1].pi_export_id,
            keep_from_index=len(path) - 1,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )

    def _file_context(self, session_id: str) -> FileContext:
        return extract_file_context(self._manager.list_tool_executions(session_id))


def _left_branch_entries(
    old_path: list[EntryRecord],
    target_path: list[EntryRecord],
) -> list[EntryRecord]:
    common = 0
    limit = min(len(old_path), len(target_path))
    while common < limit and old_path[common].pi_export_id == target_path[common].pi_export_id:
        common += 1
    return old_path[common:]


def _hydrate_retained_fragments(path: list[EntryRecord]) -> list[EntryRecord]:
    fragments_by_source: dict[str, list[RetainedFragment]] = {}
    for entry in path:
        if getattr(entry.payload, "type", None) != "compaction":
            continue
        for fragment in retained_fragments_from_details(getattr(entry.payload, "details", None)):
            fragments_by_source.setdefault(fragment.source_entry_id, []).append(fragment)

    if not fragments_by_source:
        return path

    hydrated: list[EntryRecord] = []
    for entry in path:
        fragments = fragments_by_source.get(entry.pi_export_id)
        if not fragments or not isinstance(entry.payload, MessageEntry):
            hydrated.append(entry)
            continue
        message = entry.payload.message
        if message.role not in {"user", "assistant"}:
            hydrated.append(entry)
            continue
        content = [TextContent(text=fragment.text) for fragment in fragments]
        updated_message = message.model_copy(update={"content": content})
        hydrated.append(replace(entry, payload=entry.payload.model_copy(update={"message": updated_message})))
    return hydrated


__all__ = [
    "BranchSwitchResult",
    "CompactionAppendResult",
    "CompactionService",
    "ProviderSummaryGenerator",
    "SummaryGenerator",
]
