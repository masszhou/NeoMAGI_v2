"""AGENTS.md / CLAUDE.md context-file discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .diagnostics import ResourceDiagnostic
from .paths import ancestors_root_to_cwd, default_agent_dir
from .source_info import SourceInfo

CONTEXT_FILE_CANDIDATES = ("AGENTS.md", "CLAUDE.md")


@dataclass(frozen=True, slots=True)
class ContextFile:
    path: Path
    content: str
    source: SourceInfo | None = None


@dataclass(frozen=True, slots=True)
class LoadedContextFiles:
    files: tuple[ContextFile, ...]
    diagnostics: tuple[ResourceDiagnostic, ...] = ()


def load_context_files(
    cwd: str | Path,
    *,
    agent_dir: str | Path | None = None,
) -> LoadedContextFiles:
    cwd_path = Path(cwd).resolve()
    global_dir = Path(agent_dir).expanduser().resolve() if agent_dir is not None else default_agent_dir()
    candidates: list[Path] = []
    for name in CONTEXT_FILE_CANDIDATES:
        path = global_dir / name
        if path.is_file():
            candidates.append(path)
            break
    for directory in ancestors_root_to_cwd(cwd_path):
        for name in CONTEXT_FILE_CANDIDATES:
            path = directory / name
            if path.is_file():
                candidates.append(path)
                break
    files: list[ContextFile] = []
    diagnostics: list[ResourceDiagnostic] = []
    for path in candidates:
        try:
            files.append(ContextFile(path=path.resolve(), content=path.read_text(encoding="utf-8")))
        except Exception as exc:
            diagnostics.append(
                ResourceDiagnostic(
                    type="error",
                    message=f"failed to read context file: {exc}",
                    path=str(path),
                    resource_type="context_file",
                )
            )
    return LoadedContextFiles(tuple(files), tuple(diagnostics))


__all__ = ["CONTEXT_FILE_CANDIDATES", "ContextFile", "LoadedContextFiles", "load_context_files"]
