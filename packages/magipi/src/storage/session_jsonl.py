"""Pi-compatible JSONL projection for durable sessions."""

from __future__ import annotations

import json
import os
import tempfile
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
    *,
    allowed_root: str | Path | None = None,
) -> Path:
    target = _resolve_jsonl_path(path, allowed_root=allowed_root)
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
    lines = [json.dumps(header, ensure_ascii=False, separators=(",", ":"))]
    for entry in entries:
        payload = entry.model_dump(by_alias=True, exclude_none=True)
        lines.append(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    _atomic_write_text(target, "\n".join(lines) + "\n")
    return target


def import_session_jsonl(
    repository: SessionRepository,
    path: str | Path,
    *,
    cwd_override: str | None = None,
    allowed_root: str | Path | None = None,
) -> SessionRecord:
    source = _resolve_jsonl_path(path, allowed_root=allowed_root)
    if source.suffix != ".jsonl":
        raise SessionJsonlError("M6 import only supports .jsonl; structured imports are M10")
    header, entries = _load_migrated_jsonl(source)
    parent_session_id = _resolvable_parent_session_id(repository, header.get("parentSession"))
    session = repository.create_session(
        cwd=cwd_override or header["cwd"],
        parent_session_id=parent_session_id,
        source=_source_metadata(source, header, parent_session_id),
    )
    for entry in entries:
        repository.append_entry(session.id, entry)
    return repository.get_session(session.id) or session


def _resolve_jsonl_path(path: str | Path, *, allowed_root: str | Path | None) -> Path:
    raw = Path(path).expanduser()
    if allowed_root is None:
        return raw
    try:
        root = Path(allowed_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise SessionJsonlError(f"session JSONL allowed root is unavailable: {allowed_root}") from exc
    if not root.is_dir():
        raise SessionJsonlError(f"session JSONL allowed root is not a directory: {allowed_root}")
    target = raw.resolve(strict=False) if raw.is_absolute() else (root / raw).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SessionJsonlError(f"session JSONL path escapes allowed root: {path}") from exc
    return target


def _load_migrated_jsonl(source: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    objects = _read_jsonl_objects(source)
    try:
        header = migrate_header(objects[0])
        entries = [
            migrate_entry(raw, line_number=index)
            for index, raw in enumerate(objects[1:], start=2)
        ]
    except SessionMigrationError as exc:
        raise SessionJsonlError(str(exc)) from exc
    return header, entries


def _read_jsonl_objects(source: Path) -> list[dict[str, Any]]:
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
    return objects


def _atomic_write_text(target: Path, body: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        fd, raw_tmp_path = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            text=True,
        )
        tmp_path = Path(raw_tmp_path)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except OSError as exc:
        raise SessionJsonlError(f"failed to write session JSONL {target}: {exc}") from exc
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _resolvable_parent_session_id(
    repository: SessionRepository,
    parent_session: object,
) -> str | None:
    parent_session_id = _parse_neomagi_parent(parent_session)
    if parent_session_id is not None and repository.get_session(parent_session_id) is None:
        return None
    return parent_session_id


def _source_metadata(
    source: Path,
    header: dict[str, Any],
    parent_session_id: str | None,
) -> dict[str, Any]:
    source_meta: dict[str, Any] = {
        "importedFrom": str(source),
        "sourceHeaderId": header.get("id"),
    }
    if header.get("parentSession") and parent_session_id is None:
        source_meta["parentSessionPath"] = header["parentSession"]
    return source_meta


def _parse_neomagi_parent(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    prefix = "neomagi://session/"
    if not value.startswith(prefix):
        return None
    return value[len(prefix) :]


__all__ = ["SessionJsonlError", "export_session_jsonl", "import_session_jsonl"]
