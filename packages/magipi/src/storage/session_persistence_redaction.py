"""Persistence-time redaction helpers for durable session repositories."""

from __future__ import annotations

from typing import Any

from cli.core.session_types import MessageEntry, SessionEntry, SessionEntryAdapter
from policy.redaction import redact_for_persistence

from .session_utils import dump_json as _dump_json


def redact_entry_for_persistence(entry: SessionEntry, *, cwd: str | None) -> SessionEntry:
    raw = entry.model_dump(by_alias=True, exclude_none=True)
    _remove_implicit_usage_cost(raw, entry)
    redacted = redact_json_for_persistence(raw, cwd=cwd)
    return SessionEntryAdapter.validate_python(redacted)


def redact_json_for_persistence(value: Any, *, cwd: str | None) -> Any:
    redacted, _report = redact_for_persistence(_dump_json(value), cwd=cwd)
    return redacted


def _remove_implicit_usage_cost(raw: dict[str, Any], entry: SessionEntry) -> None:
    if not isinstance(entry, MessageEntry):
        return
    message = entry.message
    if getattr(message, "role", None) != "assistant":
        return
    usage = getattr(message, "usage", None)
    if "cost" in getattr(usage, "model_fields_set", set()):
        return
    raw_message = raw.get("message")
    if isinstance(raw_message, dict) and isinstance(raw_message.get("usage"), dict):
        raw_message["usage"].pop("cost", None)


__all__ = ["redact_entry_for_persistence", "redact_json_for_persistence"]
