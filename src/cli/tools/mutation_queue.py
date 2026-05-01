"""Per-realpath mutation serialization for file-write tools."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")
_LOCKS: dict[Path, asyncio.Lock] = {}
_GUARD = asyncio.Lock()


async def with_file_mutation_queue(path: Path, operation: Callable[[], Awaitable[T]]) -> T:
    key = path.expanduser().resolve(strict=False)
    async with _GUARD:
        lock = _LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        return await operation()


__all__ = ["with_file_mutation_queue"]
