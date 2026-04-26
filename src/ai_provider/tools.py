"""Tool argument validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .types import Tool


@dataclass(slots=True)
class ToolArgumentValidationError(ValueError):
    tool_name: str
    path: list[str | int]
    message: str

    def __str__(self) -> str:
        path = ".".join(str(part) for part in self.path) or "$"
        return f"{self.tool_name} arguments invalid at {path}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "toolName": self.tool_name,
            "path": self.path,
            "message": self.message,
        }


def validate_tool_arguments(tool: Tool, arguments: dict[str, Any]) -> None:
    try:
        validator = Draft202012Validator(tool.parameters)
        validator.validate(arguments)
    except ValidationError as exc:
        raise ToolArgumentValidationError(
            tool_name=tool.name,
            path=list(exc.absolute_path),
            message=exc.message,
        ) from exc


__all__ = [
    "ToolArgumentValidationError",
    "validate_tool_arguments",
]
