"""Coding-agent product-layer message conversion."""

from __future__ import annotations

import time
from typing import Any

from agent_core.loop import default_convert_to_llm
from ai_provider.types import Message, TextContent, UserMessage
from cli.core.session_types import BashExecutionMessage


def convert_coding_messages_to_llm(messages: list[Any]) -> list[Message]:
    converted: list[Any] = []
    for message in messages:
        if isinstance(message, BashExecutionMessage):
            if message.exclude_from_context:
                continue
            converted.append(_bash_execution_to_user_message(message))
            continue
        converted.append(message)
    return default_convert_to_llm(converted)


def bash_execution_to_text(message: BashExecutionMessage) -> str:
    lines = [f"Ran `{message.command}`", ""]
    output = message.output or "(no output)"
    lines.extend(["```text", output, "```"])
    notes = []
    if message.cancelled:
        notes.append("Command was cancelled.")
    elif message.exit_code not in (0, None):
        notes.append(f"Command exited with code {message.exit_code}.")
    if message.truncated:
        suffix = f" Full output: {message.full_output_path}" if message.full_output_path else ""
        notes.append(f"Output was truncated.{suffix}")
    if notes:
        lines.extend(["", *notes])
    return "\n".join(lines)


def _bash_execution_to_user_message(message: BashExecutionMessage) -> UserMessage:
    return UserMessage(
        role="user",
        content=[TextContent(text=bash_execution_to_text(message))],
        timestamp=int(time.time() * 1000),
    )


__all__ = ["bash_execution_to_text", "convert_coding_messages_to_llm"]
