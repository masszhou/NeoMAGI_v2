from __future__ import annotations

from ai_provider.types import AssistantMessage, Usage, UsageCost
from cli.core.session_types import MessageEndEvent, MessageStartEvent
from cli.interactive.components import MessageListComponent, StatusComponent
from cli.interactive.event_router import EventRouter
from cli.interactive.tool_renderer_registry import ToolRendererRegistry


def _make_router() -> tuple[EventRouter, MessageListComponent]:
    messages = MessageListComponent()
    status = StatusComponent()
    registry = ToolRendererRegistry()
    return EventRouter(messages, status, registry), messages


def _assistant_message(
    *,
    stop_reason: str = "stop",
    error_message: str | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[],
        api="anthropic-messages",
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        usage=Usage(
            input=0,
            output=0,
            cacheRead=0,
            cacheWrite=0,
            totalTokens=0,
            cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
        ),
        stopReason=stop_reason,
        errorMessage=error_message,
        timestamp=1,
    )


def test_message_end_refreshes_active_assistant_with_final_error() -> None:
    router, messages = _make_router()
    router.route(MessageStartEvent(message=_assistant_message()))
    router.route(
        MessageEndEvent(
            message=_assistant_message(
                stop_reason="error",
                error_message="Error code: 401 - invalid x-api-key",
            )
        )
    )

    rendered = "\n".join(messages.children[-1].render(100))
    assert "invalid x-api-key" in rendered
