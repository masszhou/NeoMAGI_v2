"""Pi-compatible JSONL projection for durable sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cli.core.session_types import SessionEntryAdapter, SessionHeaderAdapter

from .session_migrations import SessionMigrationError, migrate_entry, migrate_header
from .session_repository import SessionRecord, SessionRepository


class SessionJsonlError(ValueError):
    """Raised when JSONL import/export cannot be completed safely."""


def export_session_jsonl(
    repository: SessionRepository,
    session_id: str,
    path: str | Path,
) -> Path:
    target = Path(path)
    if target.suffix != ".jsonl":
        raise SessionJsonlError("M6 export only supports .jsonl; structured exports are M10")
    session = repository.get_session(session_id)
    if session is None:
        raise SessionJsonlError(f"unknown session: {session_id}")
    header = session.header().model_dump(by_alias=True, exclude_none=True)
    SessionHeaderAdapter.validate_python(header)
    entries = [
        SessionEntryAdapter.validate_python(entry.payload.model_dump(by_alias=True, exclude_none=True))
        for entry in repository.list_entries(session_id)
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header, ensure_ascii=False, separators=(",", ":")) + "\n")
        for entry in entries:
            payload = entry.model_dump(by_alias=True, exclude_none=True)
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return target


def import_session_jsonl(
    repository: SessionRepository,
    path: str | Path,
    *,
    cwd_override: str | None = None,
) -> SessionRecord:
    source = Path(path)
    if source.suffix != ".jsonl":
        raise SessionJsonlError("M6 import only supports .jsonl; structured imports are M10")
    try:
        raw_lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SessionJsonlError(f"failed to read session JSONL {source}: {exc}") from exc
    if not raw_lines:
        raise SessionJsonlError("session JSONL is empty")

    objects: list[dict[str, Any]] = []
    for index, line in enumerate(raw_lines, start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SessionJsonlError(f"line {index}: invalid JSON: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise SessionJsonlError(f"line {index}: expected a JSON object")
        objects.append(parsed)
    if not objects:
        raise SessionJsonlError("session JSONL has no JSON objects")

    try:
        header = migrate_header(objects[0])
        entries = [
            migrate_entry(raw, line_number=index)
            for index, raw in enumerate(objects[1:], start=2)
        ]
    except SessionMigrationError as exc:
        raise SessionJsonlError(str(exc)) from exc

    parent_session_id = _parse_neomagi_parent(header.get("parentSession"))
    if parent_session_id is not None and repository.get_session(parent_session_id) is None:
        parent_session_id = None
    source_meta: dict[str, Any] = {
        "importedFrom": str(source),
        "sourceHeaderId": header.get("id"),
    }
    if header.get("parentSession") and parent_session_id is None:
        source_meta["parentSessionPath"] = header["parentSession"]

    session = repository.create_session(
        cwd=cwd_override or header["cwd"],
        parent_session_id=parent_session_id,
        source=source_meta,
    )
    for entry in entries:
        repository.append_entry(session.id, entry)
    return repository.get_session(session.id) or session


def _parse_neomagi_parent(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    prefix = "neomagi://session/"
    if not value.startswith(prefix):
        return None
    return value[len(prefix) :]


__all__ = ["SessionJsonlError", "export_session_jsonl", "import_session_jsonl"]
