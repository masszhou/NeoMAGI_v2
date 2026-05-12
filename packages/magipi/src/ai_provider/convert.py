"""Cross-provider conversion helpers.

These helpers clone and serialize Pi-compatible wire objects. Provider adapters
use them at outgoing payload boundaries so durable message records are not
mutated during provider-specific shaping.
"""

from __future__ import annotations

from .types import (
    AssistantMessage,
    AssistantMessageAdapter,
    Context,
    ContextAdapter,
    Message,
    MessageAdapter,
)


def clone_message(message: Message) -> Message:
    dumped = MessageAdapter.dump_python(message, by_alias=True, exclude_none=True)
    return MessageAdapter.validate_python(dumped)


def clone_assistant_message(message: AssistantMessage) -> AssistantMessage:
    dumped = AssistantMessageAdapter.dump_python(message, by_alias=True, exclude_none=True)
    return AssistantMessageAdapter.validate_python(dumped)


def clone_context(context: Context) -> Context:
    dumped = ContextAdapter.dump_python(context, by_alias=True, exclude_none=True)
    return ContextAdapter.validate_python(dumped)


def dump_message_for_provider(message: Message) -> dict[str, object]:
    return MessageAdapter.dump_python(message, by_alias=True, exclude_none=True)


__all__ = [
    "clone_assistant_message",
    "clone_context",
    "clone_message",
    "dump_message_for_provider",
]
