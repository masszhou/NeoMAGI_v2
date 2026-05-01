from __future__ import annotations

from pathlib import Path

from ai_provider.types import TextContent, UserMessage
from cli.core.session_manager import SessionManager
from storage.in_memory_session_repository import InMemorySessionRepository


def test_session_jsonl_exports_and_imports_round_trip(tmp_path: Path) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    session = manager.new_session(tmp_path)
    manager.append_message(
        session.id,
        UserMessage(content=[TextContent(text="persist me")], timestamp=1),
    )
    path = tmp_path / "session.jsonl"

    manager.export_jsonl(session.id, path)
    imported = manager.import_jsonl(path)

    imported_context = manager.build_session_context(imported.id)
    assert imported.id != session.id
    assert imported_context.messages[0].role == "user"
    assert imported_context.messages[0].content[0].text == "persist me"


def test_session_jsonl_round_trip_preserves_resolvable_parent_session(
    tmp_path: Path,
) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    source = manager.new_session(tmp_path)
    manager.append_message(
        source.id,
        UserMessage(content=[TextContent(text="parent context")], timestamp=1),
    )
    cloned = manager.clone_session(source.id)
    path = tmp_path / "child.jsonl"

    manager.export_jsonl(cloned.session.id, path)
    imported = manager.import_jsonl(path)

    assert imported.parent_session_id == source.id
    assert imported.source["sourceHeaderId"] == cloned.session.id


def test_session_jsonl_round_trip_preserves_fork_parent_session(
    tmp_path: Path,
) -> None:
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    source = manager.new_session(tmp_path)
    user = manager.append_message(
        source.id,
        UserMessage(content=[TextContent(text="fork me")], timestamp=1),
    )
    forked = manager.fork_session(source.id, user.pi_export_id)
    path = tmp_path / "forked.jsonl"

    manager.export_jsonl(forked.session.id, path)
    imported = manager.import_jsonl(path)

    assert imported.parent_session_id == source.id
    assert imported.source["sourceHeaderId"] == forked.session.id
