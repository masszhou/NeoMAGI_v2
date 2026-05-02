"""Coding-agent product-layer message conversion."""

from __future__ import annotations

import time
from typing import Any

from agent_core.loop import default_convert_to_llm
from ai_provider.types import Message, TextContent, ToolResultMessage, UserMessage
from cli.core.session_types import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
)


def convert_coding_messages_to_llm(messages: list[Any]) -> list[Message]:
    converted: list[Any] = []
    for message in messages:
        if isinstance(message, BashExecutionMessage):
            if message.exclude_from_context:
                continue
            converted.append(_bash_execution_to_user_message(message))
            continue
        if isinstance(message, ToolResultMessage):
            converted.append(_strip_run_metadata(message))
            continue
        if isinstance(message, CompactionSummaryMessage):
            converted.append(_compaction_summary_to_user_message(message))
            continue
        if isinstance(message, BranchSummaryMessage):
            converted.append(_branch_summary_to_user_message(message))
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


def _compaction_summary_to_user_message(message: CompactionSummaryMessage) -> UserMessage:
    text = (
        "<session-context type=\"compactionSummary\" "
        f"tokensBefore=\"{message.tokens_before}\">\n"
        f"{message.summary}\n"
        "</session-context>"
    )
    return UserMessage(
        role="user",
        content=[TextContent(text=text)],
        timestamp=int(time.time() * 1000),
    )


def _branch_summary_to_user_message(message: BranchSummaryMessage) -> UserMessage:
    text = (
        f"<session-context type=\"branchSummary\" fromId=\"{message.from_id}\">\n"
        f"{message.summary}\n"
        "</session-context>"
    )
    return UserMessage(
        role="user",
        content=[TextContent(text=text)],
        timestamp=int(time.time() * 1000),
    )


def _strip_run_metadata(message: ToolResultMessage) -> ToolResultMessage:
    if not isinstance(message.details, dict):
        return message
    return message.model_copy(update={"details": _without_run_metadata(message.details)})


def _without_run_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_run_metadata(item)
            for key, item in value.items()
            if key not in {"runId", "run_id"}
        }
    if isinstance(value, list):
        return [_without_run_metadata(item) for item in value]
    return value


__all__ = ["bash_execution_to_text", "convert_coding_messages_to_llm"]
