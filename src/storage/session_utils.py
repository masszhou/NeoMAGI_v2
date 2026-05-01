"""Shared helpers for durable session repositories."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from cli.core.session_types import MessageEntry, SessionEntry, SessionEntryAdapter

from .ids import new_pi_export_id


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def validate_entry(payload: Mapping[str, Any] | SessionEntry) -> SessionEntry:
    if hasattr(payload, "model_dump"):
        raw = payload.model_dump(by_alias=True, exclude_none=True)  # type: ignore[union-attr]
    else:
        raw = dict(payload)
    return SessionEntryAdapter.validate_python(raw)


def context_participates(entry: SessionEntry) -> bool:
    if entry.type in {"custom", "label", "session_info"}:
        return False
    if isinstance(entry, MessageEntry) and getattr(entry.message, "exclude_from_context", False):
        return False
    return True


def iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ModuleNotFoundError:  # pragma: no cover - dependency bootstrap
        return value
    return Jsonb(value)


def dump_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [dump_json(item) for item in value]
    if isinstance(value, dict):
        return {key: dump_json(item) for key, item in value.items()}
    return value


def duration_from_details(details: Any) -> int | None:
    if not isinstance(details, dict):
        return None
    value = details.get("durationMs")
    return value if isinstance(value, int) else None


def allocate_entry_payload(
    *,
    entry_type: str,
    parent_id: str | None,
    payload: dict[str, Any],
    existing_ids: Iterable[str] = (),
    timestamp: str | None = None,
) -> dict[str, Any]:
    existing = set(existing_ids)
    base = {
        "type": entry_type,
        "id": new_pi_export_id(existing.__contains__),
        "parentId": parent_id,
        "timestamp": timestamp or utc_now_iso(),
    }
    base.update(payload)
    return {key: value for key, value in base.items() if value is not None}


__all__ = [
    "allocate_entry_payload",
    "context_participates",
    "dump_json",
    "duration_from_details",
    "iso",
    "jsonb",
    "utc_now",
    "utc_now_iso",
    "validate_entry",
]
