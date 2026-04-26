"""Shared prompt-cache contract helpers."""

from __future__ import annotations

import os
import re

from .types import CacheRetention

_AFFINITY_PATTERN = re.compile(r"^[A-Za-z0-9_.:@/-]{1,256}$")


def resolve_cache_retention(cache_retention: CacheRetention | None) -> CacheRetention:
    if cache_retention is not None:
        return cache_retention
    if os.environ.get("PI_CACHE_RETENTION") == "long":
        return "long"
    return "short"


def cache_enabled(retention: CacheRetention) -> bool:
    return retention != "none"


def sanitize_cache_affinity_id(session_id: str | None) -> str | None:
    if session_id is None:
        return None
    if not isinstance(session_id, str):
        return None
    if not session_id:
        return None
    if _AFFINITY_PATTERN.fullmatch(session_id) is None:
        return None
    return session_id


__all__ = [
    "cache_enabled",
    "resolve_cache_retention",
    "sanitize_cache_affinity_id",
]
