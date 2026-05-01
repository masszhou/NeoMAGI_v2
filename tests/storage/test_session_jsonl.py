from __future__ import annotations

from pathlib import Path

from ai_provider.types import TextContent, UserMessage
from cli.core.session_manager import SessionManager
from storage.session_repository import InMemorySessionRepository


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
