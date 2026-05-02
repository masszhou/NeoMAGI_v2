"""ID helpers for durable sessions and Pi JSONL projection."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable

from agent_core.cache_affinity import derive_provider_cache_affinity_id


def new_db_uuid() -> str:
    return str(uuid.uuid7())


def new_pi_export_id(exists: Callable[[str], bool] | None = None) -> str:
    exists = exists or (lambda _value: False)
    for _ in range(100):
        candidate = secrets.token_hex(4)
        if not exists(candidate):
            return candidate
    raise RuntimeError("could not allocate a unique Pi export id")


def provider_cache_affinity_for_session(session_id: str) -> str:
    affinity = derive_provider_cache_affinity_id(session_id)
    if affinity is None:
        raise ValueError("session id could not produce a provider cache affinity id")
    return affinity


def short_session_id(session_id: str | None) -> str:
    if not session_id:
        return "none"
    return session_id.split("-", 1)[0]


def is_db_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError):
        return False
    return True


__all__ = [
    "is_db_uuid",
    "new_db_uuid",
    "new_pi_export_id",
    "provider_cache_affinity_for_session",
    "short_session_id",
]
