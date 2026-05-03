"""Theme resource placeholders for M8 loader parity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .diagnostics import ResourceDiagnostic
from .source_info import SourceInfo


@dataclass(frozen=True, slots=True)
class ThemeResource:
    name: str
    path: Path
    source: SourceInfo | None = None


@dataclass(frozen=True, slots=True)
class LoadedThemes:
    themes: tuple[ThemeResource, ...]
    diagnostics: tuple[ResourceDiagnostic, ...] = ()


def load_themes(paths: list[Path]) -> LoadedThemes:
    themes: list[ThemeResource] = []
    for path in paths:
        if path.is_file():
            themes.append(ThemeResource(path.stem, path.resolve()))
        elif path.is_dir():
            themes.extend(
                ThemeResource(child.stem, child.resolve())
                for child in sorted(path.iterdir())
                if child.is_file()
            )
    return LoadedThemes(tuple(themes))


__all__ = ["LoadedThemes", "ThemeResource", "load_themes"]
