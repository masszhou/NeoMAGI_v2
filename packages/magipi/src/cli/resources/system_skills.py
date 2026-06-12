"""Package-shipped system skills materialization per ADR-0029.

System skills ship inside the package under ``cli/resources/system_skills/`` and are
host-materialized into the workspace at ``.magipi/skills/.system/`` on every resource
snapshot. Only workspace files are active runtime skills (ADR-0021); this module is the
host-managed materialization step that keeps the workspace copy in sync with the package.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .diagnostics import ResourceDiagnostic

SYSTEM_SKILLS_DIRNAME = ".system"

_MANAGED_MARKER = (
    "This directory is managed by magipi (ADR-0029). It is re-synced from the installed\n"
    "package on every workspace startup and /reload; manual edits here are overwritten.\n"
    "To customize a system skill, copy it to .magipi/skills/<skill-name>/ instead --\n"
    "the workspace copy takes precedence over the .system copy with the same name.\n"
)


def system_skills_source_root() -> Path:
    return Path(__file__).resolve().parent / "system_skills"


def workspace_system_skills_dir(cwd: Path) -> Path:
    return cwd / ".magipi" / "skills" / SYSTEM_SKILLS_DIRNAME


def sync_system_skills(
    cwd: Path,
    *,
    enabled: bool,
    source_root: Path | None = None,
) -> list[ResourceDiagnostic]:
    source = (source_root or system_skills_source_root()).resolve()
    destination = workspace_system_skills_dir(Path(cwd).resolve())
    if not _is_contained(destination, Path(cwd).resolve()):
        return [_warning(f"system skills destination escapes workspace: {destination}", destination)]
    if not enabled:
        return _remove_disabled_destination(destination)
    skill_names = _source_skill_names(source)
    if not skill_names:
        return [_warning(f"system skills source is missing or empty: {source}", source)]
    try:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "README.md").write_text(_MANAGED_MARKER, encoding="utf-8")
    except OSError as exc:
        return [_warning(f"cannot prepare system skills directory: {exc}", destination)]
    diagnostics: list[ResourceDiagnostic] = []
    for name in skill_names:
        diagnostics.extend(_sync_one_skill(source / name, destination / name))
    diagnostics.extend(_remove_stale_entries(destination, keep=set(skill_names)))
    return diagnostics


def _source_skill_names(source: Path) -> list[str]:
    if not source.is_dir():
        return []
    return sorted(
        child.name
        for child in source.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    )


def _sync_one_skill(skill_source: Path, skill_destination: Path) -> list[ResourceDiagnostic]:
    if _tree_matches(skill_source, skill_destination):
        return []
    try:
        if skill_destination.exists():
            shutil.rmtree(skill_destination)
        shutil.copytree(skill_source, skill_destination, symlinks=False)
    except OSError as exc:
        return [_warning(f"failed to sync system skill {skill_source.name!r}: {exc}", skill_destination)]
    return []


def _remove_stale_entries(destination: Path, *, keep: set[str]) -> list[ResourceDiagnostic]:
    diagnostics: list[ResourceDiagnostic] = []
    try:
        children = sorted(destination.iterdir())
    except OSError:
        return diagnostics
    for child in children:
        if child.name == "README.md" or child.name in keep:
            continue
        try:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        except OSError as exc:
            diagnostics.append(_warning(f"failed to remove stale system skill entry: {exc}", child))
    return diagnostics


def _remove_disabled_destination(destination: Path) -> list[ResourceDiagnostic]:
    if not destination.exists():
        return []
    try:
        shutil.rmtree(destination)
    except OSError as exc:
        return [_warning(f"failed to remove disabled system skills directory: {exc}", destination)]
    return []


def _tree_matches(source: Path, destination: Path) -> bool:
    source_files = _tree_signature(source)
    return source_files is not None and source_files == _tree_signature(destination)


def _tree_signature(root: Path) -> dict[str, bytes] | None:
    if not root.is_dir():
        return None
    signature: dict[str, bytes] = {}
    try:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                return None
            if path.is_file():
                signature[path.relative_to(root).as_posix()] = path.read_bytes()
    except OSError:
        return None
    return signature


def _is_contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _warning(message: str, path: Path) -> ResourceDiagnostic:
    return ResourceDiagnostic(
        type="warning",
        message=message,
        path=str(path),
        resource_type="skill",
        name="system-skills",
    )


__all__ = [
    "SYSTEM_SKILLS_DIRNAME",
    "sync_system_skills",
    "system_skills_source_root",
    "workspace_system_skills_dir",
]
