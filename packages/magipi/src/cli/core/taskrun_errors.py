"""Shared TaskRun product-layer errors."""

from __future__ import annotations


class TaskRunServiceError(RuntimeError):
    """Raised when a product-level TaskRun operation is invalid."""
