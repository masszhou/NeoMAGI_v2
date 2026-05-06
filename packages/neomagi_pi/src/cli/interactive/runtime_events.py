"""Adapter from core ``AgentEvent`` frames to TUI session events."""

from __future__ import annotations

from typing import Any

from agent_core import types as core_events
from cli.core import session_types
from cli.core.session_types import AgentSessionEvent, AgentSessionEventAdapter


def agent_event_to_session_event(event: core_events.AgentEvent) -> AgentSessionEvent:
    """Convert one core ``AgentEvent`` into a validated session event.

    Message-bearing frames are widened to the cli/core wrappers so the TUI
    keeps consuming one ``AgentSessionEvent`` contract. Lifecycle and tool
    frames still round-trip through the same adapter before dispatch.
    """

    if isinstance(event, core_events.AgentStartEvent | core_events.TurnStartEvent):
        return _validate(event)
    if isinstance(
        event,
        core_events.ToolExecutionStartEvent
        | core_events.ToolExecutionUpdateEvent
        | core_events.ToolExecutionEndEvent,
    ):
        return _validate(event)
    if isinstance(event, core_events.AgentEndEvent):
        return _validate(session_types.AgentEndEvent(messages=event.messages))
    if isinstance(event, core_events.TurnEndEvent):
        return _validate(
            session_types.TurnEndEvent(
                message=event.message,
                toolResults=event.tool_results,
            )
        )
    if isinstance(event, core_events.MessageStartEvent):
        return _validate(session_types.MessageStartEvent(message=event.message))
    if isinstance(event, core_events.MessageUpdateEvent):
        return _validate(
            session_types.MessageUpdateEvent(
                message=event.message,
                assistantMessageEvent=event.assistant_message_event,
            )
        )
    if isinstance(event, core_events.MessageEndEvent):
        return _validate(session_types.MessageEndEvent(message=event.message))
    raise TypeError(f"unsupported agent event type: {type(event).__name__}")


def _validate(event: Any) -> AgentSessionEvent:
    raw = event.model_dump(by_alias=True, exclude_none=True)
    return AgentSessionEventAdapter.validate_python(raw)


__all__ = ["agent_event_to_session_event"]
