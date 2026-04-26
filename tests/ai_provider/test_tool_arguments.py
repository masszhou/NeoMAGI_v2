from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_provider.tools import ToolArgumentValidationError, validate_tool_arguments
from ai_provider.types import Tool

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "pi_compat"


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
    fixture = json.loads((FIXTURE_ROOT / "tool_argument_validation" / "fixture.json").read_text())
    tool = Tool(
        name=fixture["tool"],
        description="Read a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        validate_tool_arguments(tool, fixture["invalidArguments"])

    assert exc_info.value.to_dict() == fixture["expectedError"]
