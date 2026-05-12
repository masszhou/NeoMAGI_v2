"""Product-layer durable session lifecycle.

This module owns session semantics above storage: new/resume/fork/clone/tree,
context hydration, labels, names, and JSONL projection. It deliberately does
not contain SQL; repository implementations live in `storage`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ai_provider.types import AssistantMessage, TextContent
from cli.core.session_types import (
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomEntry,
    CustomMessageEntry,
    CustomMessage,
    LabelEntry,
    MessageEntry,
    SessionContext,
    SessionEntry,
    SessionInfoEntry,
    SessionTreeNode,
)
from cli.core.compaction.models import BranchSummaryResult, CompactionResult
from cli.core.compaction.models import retained_fragments_from_details
from cli.core.session_export import (
    build_session_export_envelope,
    export_session_html,
    export_session_pi_jsonl,
    export_session_structured_json,
)
from storage.session_jsonl import export_session_jsonl, import_session_jsonl
from storage.session_repository import (
    EntryRecord,
    SessionRecord,
    SessionRepository,
    allocate_entry_payload,
)
from storage.ids import is_db_uuid


@dataclass(frozen=True, slots=True)
class BranchSessionResult:
    session: SessionRecord
    editor_prefill: str = ""


@dataclass(frozen=True, slots=True)
class SessionStats:
    session_id: str
    cwd: str
    name: str | None
    entry_count: int
    message_count: int
    current_leaf: str | None
    provider_cache_affinity_id: str
    parent_session_id: str | None = None


@dataclass(slots=True)
class _ContextBuildState:
    path: list[EntryRecord]
    messages: list[tuple[str, Any]]
    path_index: dict[str, int]
    provider: str | None = None
    model_id: str | None = None
    thinking_level: str | None = None


class SessionManagerError(RuntimeError):
    """Raised when a product-level session operation is invalid."""


class SessionManager:
    def __init__(self, repository: SessionRepository) -> None:
        self.repository = repository

    def start_or_create(
        self,
        cwd: str | Path,
        requested_session_id: str | None = None,
    ) -> SessionRecord:
        if requested_session_id is not None:
            return self.resume_session(requested_session_id)
        recent = self.list_recent_sessions(cwd=str(Path(cwd).resolve()), limit=1)
        if recent:
            return recent[0]
        return self.new_session(cwd)

    def new_session(
        self,
        cwd: str | Path,
        *,
        parent_session_id: str | None = None,
        source: dict[str, Any] | None = None,
    ) -> SessionRecord:
        return self.repository.create_session(
            cwd=str(Path(cwd).resolve()),
            parent_session_id=parent_session_id,
            source=source,
        )

    def resume_session(self, session_id: str) -> SessionRecord:
        session_ref = session_id.strip()
        session = (
            self.repository.get_session(session_ref) if is_db_uuid(session_ref) else None
        )
        if session is None:
            session = self._resolve_session_prefix(session_ref)
        if session is None:
            raise SessionManagerError(f"unknown session: {session_ref}")
        return session

    def _resolve_session_prefix(self, session_ref: str) -> SessionRecord | None:
        if not _is_session_id_prefix(session_ref):
            return None
        matches = [
            session
            for session in self.repository.list_recent_sessions(limit=1000)
            if session.id.startswith(session_ref)
        ]
        if len(matches) > 1:
            raise SessionManagerError(
                f"ambiguous session id prefix: {session_ref}; use the full session id"
            )
        return matches[0] if matches else None

    def fork_session(self, session_id: str, from_entry_id: str) -> BranchSessionResult:
        return self._branch_into_new_session(
            session_id=session_id,
            entry_id=from_entry_id,
            position="before",
        )

    def clone_session(
        self,
        session_id: str,
        leaf_entry_id: str | None = None,
    ) -> BranchSessionResult:
        session = self.resume_session(session_id)
        leaf_entry_id = leaf_entry_id or session.current_leaf_entry_id
        return self._branch_into_new_session(
            session_id=session_id,
            entry_id=leaf_entry_id,
            position="at",
        )

    def _branch_into_new_session(
        self,
        *,
        session_id: str,
        entry_id: str | None,
        position: Literal["before", "at"],
    ) -> BranchSessionResult:
        source = self.resume_session(session_id)
        path = self._entry_path(session_id, entry_id)
        editor_prefill = ""
        copy_path = path
        if position == "before":
            if not path:
                raise SessionManagerError("/fork requires a historical user message")
            selected = path[-1].payload
            if not isinstance(selected, MessageEntry) or selected.message.role != "user":
                raise SessionManagerError("/fork entry must be a user message")
            editor_prefill = _extract_user_text(selected)
            copy_path = path[:-1]
        child = self.new_session(
            source.cwd,
            parent_session_id=source.id,
            source={"branchedFrom": source.id, "branchPosition": position},
        )
        for entry in copy_path:
            payload = entry.payload.model_dump(by_alias=True, exclude_none=True)
            self.repository.append_entry(child.id, payload)
        return BranchSessionResult(
            session=self.repository.get_session(child.id) or child,
            editor_prefill=editor_prefill,
        )

    def rename_session(self, session_id: str, name: str | None) -> SessionRecord:
        session = self.resume_session(session_id)
        payload = self._entry_payload(
            session.id,
            "session_info",
            {"name": name},
        )
        SessionInfoEntry.model_validate(payload)
        self.repository.append_entry(session.id, payload)
        return self.repository.update_session_name(session.id, name)

    def label_entry(
        self,
        session_id: str,
        target_entry_id: str,
        label: str | None,
    ) -> EntryRecord:
        session = self.resume_session(session_id)
        target = self.repository.get_entry(session.id, target_entry_id)
        if target is None:
            raise SessionManagerError(f"unknown entry: {target_entry_id}")
        payload = self._entry_payload(
            session.id,
            "label",
            {"targetId": target.pi_export_id, "label": label},
        )
        LabelEntry.model_validate(payload)
        return self.repository.append_entry(session.id, payload)

    def delete_session(self, session_id: str) -> SessionRecord:
        return self.repository.soft_delete_session(session_id)

    def list_recent_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int = 20,
    ) -> list[SessionRecord]:
        return self.repository.list_recent_sessions(cwd=cwd, limit=limit)

    def append_message(self, session_id: str, message: Any) -> EntryRecord:
        raw_message = _dump(message)
        session = self.resume_session(session_id)
        payload = self._entry_payload(
            session.id,
            "message",
            {"message": raw_message},
        )
        return self.repository.append_entry(session.id, payload)

    def append_entry(
        self,
        session_id: str,
        payload: dict[str, Any] | SessionEntry,
    ) -> EntryRecord:
        self.resume_session(session_id)
        return self.repository.append_entry(session_id, payload)

    def append_custom_entry(
        self,
        session_id: str,
        custom_type: str,
        data: Any | None = None,
    ) -> EntryRecord:
        session = self.resume_session(session_id)
        payload = self._entry_payload(
            session.id,
            "custom",
            {"customType": custom_type, "data": data},
        )
        CustomEntry.model_validate(payload)
        return self.repository.append_entry(session.id, payload)

    def append_custom_message(
        self,
        session_id: str,
        message: CustomMessage,
    ) -> EntryRecord:
        session = self.resume_session(session_id)
        payload = self._entry_payload(
            session.id,
            "custom_message",
            {
                "customType": message.custom_type,
                "content": _dump(message.content),
                "display": message.display,
                "details": _dump(message.details),
            },
        )
        CustomMessageEntry.model_validate(payload)
        return self.repository.append_entry(session.id, payload)

    def append_model_change(
        self,
        session_id: str,
        *,
        provider: str,
        model_id: str,
    ) -> EntryRecord:
        session = self.resume_session(session_id)
        payload = self._entry_payload(
            session.id,
            "model_change",
            {"provider": provider, "modelId": model_id},
        )
        return self.repository.append_entry(session.id, payload)

    def append_thinking_level_change(
        self,
        session_id: str,
        *,
        thinking_level: str,
    ) -> EntryRecord:
        session = self.resume_session(session_id)
        payload = self._entry_payload(
            session.id,
            "thinking_level_change",
            {"thinkingLevel": thinking_level},
        )
        return self.repository.append_entry(session.id, payload)

    def append_compaction(
        self,
        session_id: str,
        result: CompactionResult,
    ) -> EntryRecord:
        session = self.resume_session(session_id)
        parent_id = self._current_leaf_pi_id(session)
        payload = self._entry_payload_with_parent(
            session.id,
            "compaction",
            {
                "summary": result.summary,
                "firstKeptEntryId": result.first_kept_entry_id,
                "tokensBefore": result.tokens_before,
                "details": result.details_payload(),
                "fromHook": result.from_hook,
            },
            parent_id=parent_id,
        )
        return self.repository.append_entry(session.id, payload)

    def append_branch_summary(
        self,
        session_id: str,
        *,
        target_entry_id: str,
        result: BranchSummaryResult,
    ) -> EntryRecord:
        session = self.resume_session(session_id)
        target = self.repository.get_entry(session.id, target_entry_id)
        if target is None:
            raise SessionManagerError(f"unknown entry: {target_entry_id}")
        payload = self._entry_payload_with_parent(
            session.id,
            "branch_summary",
            {
                "fromId": result.from_id,
                "summary": result.summary,
                "details": result.details_payload(),
                "fromHook": result.from_hook,
            },
            parent_id=target.pi_export_id,
        )
        return self.repository.append_entry(session.id, payload)

    def record_tool_execution_start(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        args: Any,
        runtime_session_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.repository.record_tool_execution_start(
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args=args,
            runtime_session_id=runtime_session_id,
            run_id=run_id,
        )

    def record_tool_execution_end(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        result_content: Any,
        result_details: Any,
        is_error: bool,
    ) -> None:
        self.repository.record_tool_execution_end(
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result_content=result_content,
            result_details=result_details,
            is_error=is_error,
        )

    def build_session_context(
        self,
        session_id: str,
        leaf_entry_id: str | None = None,
    ) -> SessionContext:
        session = self.resume_session(session_id)
        path = self._entry_path(session.id, leaf_entry_id or session.current_leaf_entry_id)
        state = _ContextBuildState(
            path=path,
            messages=[],
            path_index={entry.pi_export_id: index for index, entry in enumerate(path)},
        )
        for entry in path:
            self._apply_context_entry(state, entry.payload)
        leaf = path[-1].pi_export_id if path else None
        return SessionContext(
            header=session.header(),
            messages=[message for _entry_id, message in state.messages],
            provider=state.provider,
            modelId=state.model_id,
            thinkingLevel=state.thinking_level,
            leafEntryId=leaf,
        )

    def _apply_context_entry(
        self,
        state: _ContextBuildState,
        payload: SessionEntry,
    ) -> None:
        if payload.type == "message":
            self._append_context_message(state, payload)
        elif payload.type == "model_change":
            state.provider = payload.provider
            state.model_id = payload.model_id
        elif payload.type == "thinking_level_change":
            state.thinking_level = payload.thinking_level
        elif payload.type == "compaction":
            self._append_compaction_context(state, payload)
        elif payload.type == "branch_summary":
            self._append_branch_summary_context(state, payload)
        elif payload.type == "custom_message":
            self._append_custom_message_context(state, payload)

    def _append_context_message(
        self,
        state: _ContextBuildState,
        payload: MessageEntry,
    ) -> None:
        if getattr(payload.message, "exclude_from_context", False):
            return
        state.messages.append((payload.id, payload.message))

    def _append_compaction_context(
        self,
        state: _ContextBuildState,
        payload,
    ) -> None:
        cutoff = state.path_index.get(payload.first_kept_entry_id)
        if cutoff is not None:
            keep_ids = {candidate.pi_export_id for candidate in state.path[cutoff:]}
            state.messages = [
                (entry_id, message)
                for entry_id, message in state.messages
                if entry_id in keep_ids
            ]
        fragments = retained_fragments_from_details(getattr(payload, "details", None))
        if fragments:
            fragments_by_source: dict[str, list] = {}
            for fragment in fragments:
                fragments_by_source.setdefault(fragment.source_entry_id, []).append(fragment)
            state.messages = [
                (
                    entry_id,
                    _apply_retained_fragments(message, fragments_by_source.get(entry_id)),
                )
                for entry_id, message in state.messages
            ]
        state.messages.append(
            (
                payload.id,
                CompactionSummaryMessage(
                    summary=payload.summary,
                    tokensBefore=payload.tokens_before,
                    timestamp=_entry_ms(payload.timestamp),
                ),
            )
        )

    def _append_branch_summary_context(self, state: _ContextBuildState, payload) -> None:
        state.messages.append(
            (
                payload.id,
                BranchSummaryMessage(
                    summary=payload.summary,
                    fromId=payload.from_id,
                    timestamp=_entry_ms(payload.timestamp),
                ),
            )
        )

    def _append_custom_message_context(self, state: _ContextBuildState, payload) -> None:
        state.messages.append(
            (
                payload.id,
                CustomMessage(
                    customType=payload.custom_type,
                    content=payload.content,
                    display=payload.display,
                    details=payload.details,
                    timestamp=_entry_ms(payload.timestamp),
                ),
            )
        )

    def session_stats(self, session_id: str) -> SessionStats:
        session = self.resume_session(session_id)
        entries = self.repository.list_entries(session.id)
        current_leaf = None
        if session.current_leaf_entry_id:
            leaf = self.repository.get_entry(session.id, session.current_leaf_entry_id)
            current_leaf = leaf.pi_export_id if leaf is not None else None
        return SessionStats(
            session_id=session.id,
            cwd=session.cwd,
            name=session.display_name,
            entry_count=len(entries),
            message_count=sum(1 for entry in entries if entry.entry_type == "message"),
            current_leaf=current_leaf,
            provider_cache_affinity_id=session.provider_cache_affinity_id,
            parent_session_id=session.parent_session_id,
        )

    def session_tree(self, session_id: str) -> list[SessionTreeNode]:
        self.resume_session(session_id)
        entries = self.repository.list_entries(session_id)
        children: dict[str | None, list[EntryRecord]] = {}
        for entry in entries:
            children.setdefault(entry.payload.parent_id, []).append(entry)

        def build(entry: EntryRecord) -> SessionTreeNode:
            return SessionTreeNode(
                entry=entry.payload,
                children=[build(child) for child in children.get(entry.pi_export_id, [])],
            )

        return [build(entry) for entry in children.get(None, [])]

    def select_leaf(self, session_id: str, leaf_entry_id: str) -> SessionRecord:
        session = self.resume_session(session_id)
        entry = self.repository.get_entry(session.id, leaf_entry_id)
        if entry is None:
            raise SessionManagerError(f"unknown entry: {leaf_entry_id}")
        return self.repository.update_session_leaf(session.id, entry.id)

    def entry_path(
        self,
        session_id: str,
        leaf_entry_id: str | None = None,
    ) -> list[EntryRecord]:
        session = self.resume_session(session_id)
        return self._entry_path(session.id, leaf_entry_id or session.current_leaf_entry_id)

    def list_tool_executions(self, session_id: str):
        self.resume_session(session_id)
        return self.repository.list_tool_executions(session_id)

    def build_export_envelope(self, session_id: str, *, clock=None):
        self.resume_session(session_id)
        return build_session_export_envelope(
            self.repository,
            session_id,
            clock=clock,
        )

    def export_jsonl(
        self,
        session_id: str,
        path: str | Path,
        *,
        allowed_root: str | Path | None = None,
    ) -> Path:
        self.resume_session(session_id)
        return export_session_jsonl(self.repository, session_id, path, allowed_root=allowed_root)

    def export_pi_jsonl(
        self,
        session_id: str,
        path: str | Path,
        *,
        allowed_root: str | Path | None = None,
        clock=None,
    ) -> Path:
        self.resume_session(session_id)
        return export_session_pi_jsonl(
            self.repository,
            session_id,
            path,
            allowed_root=allowed_root,
            clock=clock,
        )

    def export_structured_json(
        self,
        session_id: str,
        path: str | Path,
        *,
        allowed_root: str | Path | None = None,
        clock=None,
    ) -> Path:
        self.resume_session(session_id)
        return export_session_structured_json(
            self.repository,
            session_id,
            path,
            allowed_root=allowed_root,
            clock=clock,
        )

    def export_html(
        self,
        session_id: str,
        path: str | Path,
        *,
        allowed_root: str | Path | None = None,
        clock=None,
    ) -> Path:
        self.resume_session(session_id)
        return export_session_html(
            self.repository,
            session_id,
            path,
            allowed_root=allowed_root,
            clock=clock,
        )

    def import_jsonl(
        self,
        path: str | Path,
        *,
        allowed_root: str | Path | None = None,
    ) -> SessionRecord:
        return import_session_jsonl(self.repository, path, allowed_root=allowed_root)

    def last_assistant_text(self, session_id: str) -> str | None:
        context = self.build_session_context(session_id)
        for message in reversed(context.messages):
            if not isinstance(message, AssistantMessage):
                continue
            if message.stop_reason == "aborted" and not message.content:
                continue
            text = "".join(
                block.text
                for block in message.content
                if isinstance(block, TextContent)
            ).strip()
            if text:
                return text
        return None

    def _entry_payload(
        self,
        session_id: str,
        entry_type: str,
        payload: dict[str, Any],
        *,
        force_parent: bool = True,
    ) -> dict[str, Any]:
        entries = self.repository.list_entries(session_id)
        parent_id = None
        if force_parent:
            session = self.resume_session(session_id)
            if session.current_leaf_entry_id is not None:
                parent = self.repository.get_entry(session_id, session.current_leaf_entry_id)
                parent_id = parent.pi_export_id if parent is not None else None
        return allocate_entry_payload(
            entry_type=entry_type,
            parent_id=parent_id,
            payload=payload,
            existing_ids=(entry.pi_export_id for entry in entries),
        )

    def _entry_payload_with_parent(
        self,
        session_id: str,
        entry_type: str,
        payload: dict[str, Any],
        *,
        parent_id: str | None,
    ) -> dict[str, Any]:
        entries = self.repository.list_entries(session_id)
        return allocate_entry_payload(
            entry_type=entry_type,
            parent_id=parent_id,
            payload=payload,
            existing_ids=(entry.pi_export_id for entry in entries),
        )

    def _current_leaf_pi_id(self, session: SessionRecord) -> str | None:
        if session.current_leaf_entry_id is None:
            return None
        entry = self.repository.get_entry(session.id, session.current_leaf_entry_id)
        return entry.pi_export_id if entry is not None else None

    def _entry_path(
        self,
        session_id: str,
        leaf_entry_id: str | None,
    ) -> list[EntryRecord]:
        entries = self.repository.list_entries(session_id)
        if leaf_entry_id is None:
            return []
        by_pi = {entry.pi_export_id: entry for entry in entries}
        by_db = {entry.id: entry for entry in entries}
        leaf = by_pi.get(leaf_entry_id) or by_db.get(leaf_entry_id)
        if leaf is None:
            raise SessionManagerError(f"unknown entry: {leaf_entry_id}")
        path: list[EntryRecord] = []
        cursor: EntryRecord | None = leaf
        seen: set[str] = set()
        while cursor is not None:
            if cursor.pi_export_id in seen:
                raise SessionManagerError("session entry tree contains a cycle")
            seen.add(cursor.pi_export_id)
            path.append(cursor)
            parent_id = cursor.payload.parent_id
            cursor = by_pi.get(parent_id) if parent_id is not None else None
        path.reverse()
        return path


def _extract_user_text(entry: MessageEntry) -> str:
    content = entry.message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if block_type == "text" and isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _entry_ms(value: str) -> int:
    try:
        parsed = value.replace("Z", "+00:00")
        return int(datetime_from_iso(parsed).timestamp() * 1000)
    except Exception:
        return 0


def _is_session_id_prefix(value: str) -> bool:
    if len(value) < 8:
        return False
    return all(char in "0123456789abcdefABCDEF-" for char in value)


def datetime_from_iso(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)


def _dump(value: Any) -> Any:
    if isinstance(value, AssistantMessage):
        raw = value.model_dump(by_alias=True, exclude_none=True)
        if "cost" not in getattr(value.usage, "model_fields_set", set()):
            raw.get("usage", {}).pop("cost", None)
        return raw
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    if isinstance(value, TextContent):
        return value.model_dump(by_alias=True, exclude_none=True)
    return value


def _apply_retained_fragments(message: Any, fragments: list[Any] | None) -> Any:
    if not fragments or getattr(message, "role", None) not in {"user", "assistant"}:
        return message
    content = [TextContent(text=fragment.text) for fragment in fragments]
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"content": content})
    return message


__all__ = [
    "BranchSessionResult",
    "SessionManager",
    "SessionManagerError",
    "SessionStats",
]
