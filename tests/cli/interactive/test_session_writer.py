from __future__ import annotations

from ai_provider.types import TextContent, UserMessage
from cli.core.session_manager import SessionManager
from cli.core.session_types import MessageEndEvent
from cli.interactive.session_writer import DurableSessionEventWriter
from storage.session_repository import InMemorySessionRepository


def test_session_writer_persists_message_end(tmp_path) -> None:
    manager = SessionManager(InMemorySessionRepository())
    session = manager.new_session(tmp_path)
    writer = DurableSessionEventWriter(
        manager=manager,
        session_id_provider=lambda: session.id,
        runtime_session_id_provider=lambda: "runtime-1",
        run_id_provider=lambda: "run-1",
    )

    writer.record(
        MessageEndEvent(
            message=UserMessage(content=[TextContent(text="hello")], timestamp=1)
        )
    )

    context = manager.build_session_context(session.id)
    assert context.messages[0].role == "user"
    assert context.messages[0].content[0].text == "hello"
