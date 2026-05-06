"""Diagnostics for extension loading and handler execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DiagnosticSeverity = Literal["warning", "error"]


@dataclass(frozen=True, slots=True)
class ExtensionDiagnostic:
    severity: DiagnosticSeverity
    message: str
    extension: str | None = None
    path: str | None = None
    event: str | None = None


__all__ = ["DiagnosticSeverity", "ExtensionDiagnostic"]
