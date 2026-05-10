from __future__ import annotations

import pytest

from ai_provider.types import TextContent, UserMessage
from cli.core.session_manager import SessionManager
from cli.interactive.runtime import InteractiveAgentRuntime
from storage.in_memory_session_repository import InMemorySessionRepository


class FailingSummaryGenerator:
    async def generate(self, _prompt: str, *, model) -> str:
        raise AssertionError("default summary generator should not run")


def test_extension_compaction_hook_replaces_summary_with_from_hook(tmp_path) -> None:
    (tmp_path / ".magipi" / "extensions").mkdir(parents=True)
    (tmp_path / ".magipi" / "extensions" / "compact_hook.py").write_text(
        """
def setup(api):
    def before(event):
        first_kept = event["branchEntries"][-1]["id"]
        return {
            "compaction": {
                "summary": "HOOK_COMPACTION_SUMMARY",
                "firstKeptEntryId": first_kept,
                "tokensBefore": 123,
                "tokensAfter": 12,
                "reason": "manual",
                "fromHook": True,
            }
        }
    api.on("session_before_compact", before)
""",
        encoding="utf-8",
    )
    manager = SessionManager(InMemorySessionRepository())
    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        summary_generator=FailingSummaryGenerator(),
    )
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        manager.append_message(session_id, UserMessage(content=[TextContent(text="old")], timestamp=1))
        manager.append_message(session_id, UserMessage(content=[TextContent(text="recent")], timestamp=2))

        result = runtime.compact_session()
        entries = manager.repository.list_entries(session_id)
    finally:
        runtime.shutdown()

    assert result.result.summary == "HOOK_COMPACTION_SUMMARY"
    assert result.result.from_hook is True
    assert entries[-1].payload.type == "compaction"
    assert entries[-1].payload.from_hook is True


def test_extension_compaction_hook_can_cancel(tmp_path) -> None:
    (tmp_path / ".magipi" / "extensions").mkdir(parents=True)
    (tmp_path / ".magipi" / "extensions" / "compact_cancel.py").write_text(
        """
def setup(api):
    api.on("session_before_compact", lambda _event: {"cancel": True})
""",
        encoding="utf-8",
    )
    manager = SessionManager(InMemorySessionRepository())
    runtime = InteractiveAgentRuntime(cwd=tmp_path, session_manager=manager)
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        manager.append_message(session_id, UserMessage(content=[TextContent(text="old")], timestamp=1))
        manager.append_message(session_id, UserMessage(content=[TextContent(text="recent")], timestamp=2))

        with pytest.raises(RuntimeError, match="cancelled by extension"):
            runtime.compact_session()
        entries = manager.repository.list_entries(session_id)
    finally:
        runtime.shutdown()

    assert [entry.payload.type for entry in entries] == ["message", "message"]


def test_extension_tree_hook_replaces_branch_summary_with_from_hook(tmp_path) -> None:
    (tmp_path / ".magipi" / "extensions").mkdir(parents=True)
    (tmp_path / ".magipi" / "extensions" / "tree_hook.py").write_text(
        """
def setup(api):
    def before(event):
        return {"summary": {"summary": "HOOK_BRANCH_SUMMARY"}}
    api.on("session_before_tree", before)
""",
        encoding="utf-8",
    )
    manager = SessionManager(InMemorySessionRepository())
    runtime = InteractiveAgentRuntime(
        cwd=tmp_path,
        session_manager=manager,
        summary_generator=FailingSummaryGenerator(),
    )
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        root = manager.append_message(session_id, UserMessage(content=[TextContent(text="root")], timestamp=1))
        target = manager.append_message(session_id, UserMessage(content=[TextContent(text="target")], timestamp=2))
        manager.select_leaf(session_id, root.pi_export_id)
        old_leaf = manager.append_message(session_id, UserMessage(content=[TextContent(text="old branch")], timestamp=3))

        runtime.select_session_leaf(target.pi_export_id)
        stats = manager.session_stats(session_id)
        entries = manager.repository.list_entries(session_id)
    finally:
        runtime.shutdown()

    summary_entry = entries[-1]
    assert summary_entry.payload.type == "branch_summary"
    assert summary_entry.payload.summary == "HOOK_BRANCH_SUMMARY"
    assert summary_entry.payload.from_id == old_leaf.pi_export_id
    assert summary_entry.payload.from_hook is True
    assert stats.current_leaf == summary_entry.pi_export_id
