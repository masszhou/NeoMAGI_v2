from __future__ import annotations

import pytest

from ai_provider.tools import ToolArgumentValidationError, validate_tool_arguments
from ai_provider.types import Tool


def test_validate_tool_arguments_accepts_valid_payload() -> None:
    tool = Tool(
        name="read",
        description="Read a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    validate_tool_arguments(tool, {"path": "README.md"})


def test_validate_tool_arguments_failure_is_serializable() -> None:
    tool = Tool(
        name="read",
        description="Read a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(tool, {})

    assert exc_info.value.to_dict() == {
        "toolName": "read",
        "path": [],
        "message": "'path' is a required property",
    }

