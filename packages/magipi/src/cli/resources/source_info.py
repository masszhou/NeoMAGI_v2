"""Resource source metadata shared across loader modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ResourceScope = Literal["project", "user", "temporary", "package", "explicit", "system"]
ResourceOrigin = Literal["settings", "auto", "extension", "package", "explicit"]


@dataclass(frozen=True, slots=True)
class SourceInfo:
    scope: ResourceScope
    origin: ResourceOrigin
    path: Path
    base_dir: Path
    priority: int
    owner: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceInfo:
    type: str
    name: str
    path: Path
    source: SourceInfo


__all__ = ["ResourceInfo", "ResourceOrigin", "ResourceScope", "SourceInfo"]
