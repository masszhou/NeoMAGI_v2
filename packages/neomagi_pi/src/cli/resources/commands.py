"""Structured slash resource command expansion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ResourceCommandExpansion:
    original: str
    expanded: str
    display: str
    resource_type: Literal["skill", "prompt"]
    name: str

    def to_message_extra(self, *, display_mode: str = "compact") -> dict[str, object]:
        return {
            "original": self.original,
            "display": self.display,
            "resourceType": self.resource_type,
            "name": self.name,
            "displayMode": display_mode,
        }


__all__ = ["ResourceCommandExpansion"]
