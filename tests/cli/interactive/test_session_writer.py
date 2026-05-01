from __future__ import annotations

from agent_core.types import AgentToolResult
from cli.core.session_types import ToolExecutionEndEvent, ToolExecutionStartEvent
from ai_provider.types import TextContent, UserMessage
from cli.core.session_manager import SessionManager
from cli.core.session_types import MessageEndEvent
from cli.interactive.session_writer import DurableSessionEventWriter
from storage.in_memory_session_repository import InMemorySessionRepository


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


def test_session_writer_preserves_tool_execution_runtime_and_run_ids(tmp_path) -> None:
    repository = InMemorySessionRepository()
    manager = SessionManager(repository)
    session = manager.new_session(tmp_path)
    writer = DurableSessionEventWriter(
        manager=manager,
        session_id_provider=lambda: session.id,
        runtime_session_id_provider=lambda: "runtime-1",
        run_id_provider=lambda: "run-1",
    )

    writer.record(
        ToolExecutionStartEvent(
            toolCallId="call-1",
            toolName="read",
            args={"path": "README.md"},
        )
    )
    writer.record(
        ToolExecutionEndEvent(
            toolCallId="call-1",
            toolName="read",
            result=AgentToolResult(
                content=[{"type": "text", "text": "ok"}],
                details={"runId": "run-1", "runtimeSessionId": "runtime-1"},
            ),
            isError=False,
        )
    )

    assert len(repository.tool_executions) == 1
    execution = repository.tool_executions[0]
    assert execution.runtime_session_id == "runtime-1"
    assert execution.run_id == "run-1"
    assert execution.result_details == {"runId": "run-1", "runtimeSessionId": "runtime-1"}
