"""Diagnostics emitted while discovering user/project resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DiagnosticType = Literal["warning", "error", "collision"]


@dataclass(frozen=True, slots=True)
class ResourceDiagnostic:
    type: DiagnosticType
    message: str
    path: str | None = None
    resource_type: str | None = None
    name: str | None = None
    winner: str | None = None
    loser: str | None = None


__all__ = ["DiagnosticType", "ResourceDiagnostic"]
