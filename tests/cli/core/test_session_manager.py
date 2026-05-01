from __future__ import annotations

from pathlib import Path

from ai_provider.types import AssistantMessage, TextContent, Usage, UsageCost, UserMessage
from cli.core.session_manager import SessionManager
from storage.session_repository import InMemorySessionRepository


def _usage() -> Usage:
    return Usage(
        input=1,
        output=2,
        cacheRead=0,
        cacheWrite=0,
        totalTokens=3,
        cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
    )


def test_session_manager_builds_context_and_stats(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    user = manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="hello")], timestamp=1),
    )
    manager.append_message(
        session.id,
        AssistantMessage(
            content=[TextContent(text="world")],
            api="faux",
            provider="faux",
            model="faux-1",
            usage=_usage(),
            stopReason="stop",
            timestamp=2,
        ),
    )
    manager.rename_session(session.id, "daily")

    context = manager.build_session_context(session.id)
    stats = manager.session_stats(session.id)

    assert [message.role for message in context.messages] == ["user", "assistant"]
    assert stats.name == "daily"
    assert stats.entry_count == 3
    assert stats.message_count == 2
    assert stats.current_leaf is not None
    assert user.pi_export_id


def test_session_manager_fork_and_clone_preserve_source(tmp_path: Path) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    source = manager.new_session(tmp_path)
    user = manager.append_message(
        source.id,
        UserMessage(content=[TextContent(text="rewrite me")], timestamp=1),
    )
    manager.append_message(
        source.id,
        AssistantMessage(
            content=[TextContent(text="old answer")],
            api="faux",
            provider="faux",
            model="faux-1",
            usage=_usage(),
            stopReason="stop",
            timestamp=2,
        ),
    )

    forked = manager.fork_session(source.id, user.pi_export_id)
    cloned = manager.clone_session(source.id)

    assert forked.session.parent_session_id == source.id
    assert forked.editor_prefill == "rewrite me"
    assert manager.build_session_context(forked.session.id).messages == []
    assert cloned.session.parent_session_id == source.id
    assert [message.role for message in manager.build_session_context(cloned.session.id).messages] == [
        "user",
        "assistant",
    ]
    assert manager.session_stats(source.id).message_count == 2
