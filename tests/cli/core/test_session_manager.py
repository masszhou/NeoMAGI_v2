from __future__ import annotations

from pathlib import Path

import pytest

from ai_provider.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    Usage,
    UsageCost,
    UserMessage,
)
from cli.core.session_manager import SessionManager, SessionManagerError
from storage.in_memory_session_repository import InMemorySessionRepository


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


def test_session_manager_resumes_by_unique_session_id_prefix(tmp_path: Path) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    session = repo.create_session(
        cwd=str(tmp_path),
        session_id="019de884-0000-7000-8000-000000000001",
    )
    repo.create_session(
        cwd=str(tmp_path),
        session_id="119de884-0000-7000-8000-000000000002",
    )

    assert manager.resume_session("019de884").id == session.id
    assert manager.resume_session(f" {session.id} ").id == session.id


def test_session_manager_rejects_unknown_or_ambiguous_session_prefix(
    tmp_path: Path,
) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    repo.create_session(
        cwd=str(tmp_path),
        session_id="019de884-0000-7000-8000-000000000001",
    )
    repo.create_session(
        cwd=str(tmp_path),
        session_id="019de884-0000-7000-8000-000000000002",
    )

    with pytest.raises(SessionManagerError, match="ambiguous session id prefix"):
        manager.resume_session("019de884")
    with pytest.raises(SessionManagerError, match="unknown session"):
        manager.resume_session("not-a-session")


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
    assert cloned.editor_prefill == ""
    assert forked.session.provider_cache_affinity_id != source.provider_cache_affinity_id
    assert cloned.session.provider_cache_affinity_id != source.provider_cache_affinity_id
    assert [message.role for message in manager.build_session_context(cloned.session.id).messages] == [
        "user",
        "assistant",
    ]
    assert manager.session_stats(source.id).message_count == 2


def test_session_manager_fork_copies_ancestor_chain_before_selected_user(
    tmp_path: Path,
) -> None:
    manager = SessionManager(InMemorySessionRepository())
    source = manager.new_session(tmp_path)
    manager.append_message(
        source.id,
        UserMessage(content=[TextContent(text="first prompt")], timestamp=1),
    )
    manager.append_message(
        source.id,
        AssistantMessage(
            content=[TextContent(text="first answer")],
            api="faux",
            provider="faux",
            model="faux-1",
            usage=_usage(),
            stopReason="stop",
            timestamp=2,
        ),
    )
    second_user = manager.append_message(
        source.id,
        UserMessage(content=[TextContent(text="rewrite second")], timestamp=3),
    )

    forked = manager.fork_session(source.id, second_user.pi_export_id)
    fork_context = manager.build_session_context(forked.session.id)

    assert forked.editor_prefill == "rewrite second"
    assert [message.role for message in fork_context.messages] == ["user", "assistant"]
    assert fork_context.messages[0].content[0].text == "first prompt"
    assert fork_context.messages[1].content[0].text == "first answer"


def test_session_manager_fork_prefill_uses_only_text_blocks(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    source = manager.new_session(tmp_path)
    user = manager.append_message(
        source.id,
        UserMessage(
            content=[
                TextContent(text="line one"),
                ImageContent(data="base64", mimeType="image/png"),
                TextContent(text="line two"),
            ],
            timestamp=1,
        ),
    )

    forked = manager.fork_session(source.id, user.pi_export_id)

    assert forked.editor_prefill == "line oneline two"


def test_custom_entry_is_durable_but_not_context_visible(tmp_path: Path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)

    entry = manager.append_custom_entry(session.id, "research_note", {"metric": 0.72})

    context = manager.build_session_context(session.id)
    stats = manager.session_stats(session.id)
    stored = manager.repository.get_entry(session.id, entry.pi_export_id)

    assert stored is not None
    assert stored.payload.type == "custom"
    assert context.messages == []
    assert stats.entry_count == 1
    assert stats.message_count == 0


def test_custom_message_entry_participates_in_context(tmp_path: Path) -> None:
    from cli.core.session_types import CustomMessage

    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)

    manager.append_custom_message(
        session.id,
        CustomMessage(
            customType="status_panel",
            content="hello",
            display=True,
            details={"ok": True},
            timestamp=1,
        ),
    )

    context = manager.build_session_context(session.id)

    assert len(context.messages) == 1
    assert context.messages[0].role == "custom"
    assert context.messages[0].custom_type == "status_panel"
    assert context.messages[0].content == "hello"
