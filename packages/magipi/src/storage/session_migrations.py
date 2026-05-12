"""Pi session JSONL migration helpers.

M6 keeps the historical migration strategy explicit: v3 is the only native
shape, while v1/v2 imports are accepted only when their entries already carry
the discriminated fields needed for a lossless v3 validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cli.core.session_types import (
    CURRENT_SESSION_VERSION,
    SessionEntryAdapter,
    SessionHeaderAdapter,
)


class SessionMigrationError(ValueError):
    """Raised for unsupported or malformed historical session JSONL."""


def migrate_header(raw: Mapping[str, Any]) -> dict[str, Any]:
    header = dict(raw)
    if header.get("type") != "session":
        raise SessionMigrationError("line 1 must be a session header")
    version = header.get("version", CURRENT_SESSION_VERSION)
    if version not in {1, 2, CURRENT_SESSION_VERSION}:
        raise SessionMigrationError(f"unsupported session JSONL version: {version}")
    header["version"] = CURRENT_SESSION_VERSION
    SessionHeaderAdapter.validate_python(header)
    return header


def migrate_entry(raw: Mapping[str, Any], *, line_number: int) -> dict[str, Any]:
    entry = dict(raw)
    version = entry.pop("version", CURRENT_SESSION_VERSION)
    if version not in {1, 2, CURRENT_SESSION_VERSION}:
        entry_id = entry.get("id", "<unknown>")
        raise SessionMigrationError(
            f"line {line_number} entry {entry_id}: unsupported entry version {version}"
        )
    if "type" not in entry:
        raise SessionMigrationError(f"line {line_number}: missing entry type")
    try:
        validated = SessionEntryAdapter.validate_python(entry)
    except Exception as exc:
        entry_id = entry.get("id", "<unknown>")
        raise SessionMigrationError(
            f"line {line_number} entry {entry_id}: cannot migrate to v3: {exc}"
        ) from exc
    return validated.model_dump(by_alias=True, exclude_none=True)


__all__ = ["SessionMigrationError", "migrate_entry", "migrate_header"]
