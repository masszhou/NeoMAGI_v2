from __future__ import annotations

import inspect
from typing import Literal, get_args, get_origin

from ai_provider.types import Message, MessageItem
from agent_core.agent import Agent
from agent_core import types as core_types
from agent_core.types import (
    AfterToolCallContext,
    AfterToolCallResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
)


EXPECTED_AGENT_EVENT_FACE = [
    ("AgentStartEvent", "agent_start", [("type", None)]),
    ("AgentEndEvent", "agent_end", [("type", None), ("messages", None)]),
    ("TurnStartEvent", "turn_start", [("type", None)]),
    (
        "TurnEndEvent",
        "turn_end",
        [("type", None), ("message", None), ("tool_results", "toolResults")],
    ),
    ("MessageStartEvent", "message_start", [("type", None), ("message", None)]),
    (
        "MessageUpdateEvent",
        "message_update",
        [
            ("type", None),
            ("message", None),
            ("assistant_message_event", "assistantMessageEvent"),
        ],
    ),
    ("MessageEndEvent", "message_end", [("type", None), ("message", None)]),
    (
        "ToolExecutionStartEvent",
        "tool_execution_start",
        [
            ("type", None),
            ("tool_call_id", "toolCallId"),
            ("tool_name", "toolName"),
            ("args", None),
        ],
    ),
    (
        "ToolExecutionUpdateEvent",
        "tool_execution_update",
        [
            ("type", None),
            ("tool_call_id", "toolCallId"),
            ("tool_name", "toolName"),
            ("args", None),
            ("partial_result", "partialResult"),
        ],
    ),
    (
        "ToolExecutionEndEvent",
        "tool_execution_end",
        [
            ("type", None),
            ("tool_call_id", "toolCallId"),
            ("tool_name", "toolName"),
            ("result", None),
            ("is_error", "isError"),
        ],
    ),
]


def test_agent_core_event_protocol_face_is_pinned_by_adr_0023() -> None:
    """ADR-0023: do not drift agent_core protocol face without a new ADR.

    Updating this baseline requires an ADR that explicitly revises or
    supersedes ADR-0023; TaskRun-only observability belongs in derived
    TaskRun events instead of the pi-mono parity surface.
    """

    assert [
        (
            cls.__name__,
            _event_type_literal(cls),
            _model_field_aliases(cls),
        )
        for cls in get_args(core_types.AgentEvent)
    ] == EXPECTED_AGENT_EVENT_FACE


def test_agent_core_hook_context_protocol_face_is_pinned_by_adr_0023() -> None:
    assert _model_field_aliases(BeforeToolCallContext) == [
        ("assistant_message", "assistantMessage"),
        ("tool_call", "toolCall"),
        ("args", None),
        ("context", None),
    ]
    assert _model_field_aliases(BeforeToolCallResult) == [
        ("block", None),
        ("reason", None),
    ]
    assert _model_field_aliases(AfterToolCallContext) == [
        ("assistant_message", "assistantMessage"),
        ("tool_call", "toolCall"),
        ("args", None),
        ("result", None),
        ("is_error", "isError"),
        ("context", None),
    ]
    assert _model_field_aliases(AfterToolCallResult) == [
        ("content", None),
        ("details", None),
        ("is_error", "isError"),
    ]


def test_agent_core_public_runtime_methods_are_pinned_by_adr_0023() -> None:
    assert {
        name: str(inspect.signature(getattr(Agent, name)))
        for name in (
            "subscribe",
            "abort",
            "wait_for_idle",
            "steer",
            "follow_up",
        )
    } == {
        "subscribe": "(self, listener: 'Listener') -> 'Callable[[], None]'",
        "abort": "(self) -> 'None'",
        "wait_for_idle": "(self) -> 'None'",
        "steer": "(self, message: 'Any') -> 'None'",
        "follow_up": "(self, message: 'Any') -> 'None'",
    }


def test_agent_core_message_exports_stay_at_ai_provider_wire_boundary() -> None:
    assert core_types.AgentMessage is Message
    assert core_types.AgentMessageItem is MessageItem


def _event_type_literal(model_cls: type) -> str:
    annotation = model_cls.model_fields["type"].annotation
    assert get_origin(annotation) is Literal
    (value,) = get_args(annotation)
    return value


def _model_field_aliases(model_cls: type) -> list[tuple[str, str | None]]:
    return [
        (name, field.alias)
        for name, field in model_cls.model_fields.items()
    ]
