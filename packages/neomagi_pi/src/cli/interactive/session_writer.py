"""Durable writer for agent session events."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cli.core.session_manager import SessionManager
from cli.core.session_types import (
    AgentSessionEvent,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    MessageEndEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)


class DurableSessionEventWriter:
    """Maps runtime events to append-only durable session state."""

    def __init__(
        self,
        *,
        manager: SessionManager,
        session_id_provider: Callable[[], str],
        runtime_session_id_provider: Callable[[], str | None],
        run_id_provider: Callable[[], str | None],
    ) -> None:
        self._manager = manager
        self._session_id_provider = session_id_provider
        self._runtime_session_id_provider = runtime_session_id_provider
        self._run_id_provider = run_id_provider

    def record(self, event: AgentSessionEvent) -> None:
        session_id = self._session_id_provider()
        if isinstance(event, MessageEndEvent):
            if isinstance(event.message, BranchSummaryMessage | CompactionSummaryMessage):
                return
            if isinstance(event.message, CustomMessage):
                self._manager.append_custom_message(session_id, event.message)
                return
            self._manager.append_message(session_id, event.message)
            return
        if isinstance(event, ToolExecutionStartEvent):
            self._manager.record_tool_execution_start(
                session_id=session_id,
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                args=event.args,
                runtime_session_id=self._runtime_session_id_provider(),
                run_id=self._run_id_provider(),
            )
            return
        if isinstance(event, ToolExecutionEndEvent):
            self._manager.record_tool_execution_end(
                session_id=session_id,
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                result_content=_result_content(event.result),
                result_details=_result_details(event.result),
                is_error=event.is_error,
            )


def _result_content(result: Any) -> Any:
    if hasattr(result, "content"):
        return result.content
    if isinstance(result, dict):
        return result.get("content")
    return None


def _result_details(result: Any) -> Any:
    if hasattr(result, "details"):
        return result.details
    if isinstance(result, dict):
        return result.get("details")
    return None


__all__ = ["DurableSessionEventWriter"]
