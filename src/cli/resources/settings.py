"""Resource settings loading and merging."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .diagnostics import ResourceDiagnostic
from .paths import default_agent_dir, resolve_resource_path


@dataclass(frozen=True, slots=True)
class ResourceSettings:
    packages: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    prompts: tuple[str, ...] = ()
    themes: tuple[str, ...] = ()
    enable_skill_commands: bool | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoadedResourceSettings:
    settings: ResourceSettings
    diagnostics: tuple[ResourceDiagnostic, ...] = ()


def load_resource_settings(
    cwd: str | Path,
    *,
    agent_dir: str | Path | None = None,
) -> LoadedResourceSettings:
    cwd_path = Path(cwd).resolve()
    global_dir = Path(agent_dir).expanduser().resolve() if agent_dir is not None else default_agent_dir()
    diagnostics: list[ResourceDiagnostic] = []
    global_settings = _read_settings(global_dir / "settings.json", diagnostics)
    project_settings = _read_settings(cwd_path / ".pi" / "settings.json", diagnostics)
    return LoadedResourceSettings(
        settings=merge_settings(global_settings, project_settings),
        diagnostics=tuple(diagnostics),
    )


def merge_settings(global_settings: ResourceSettings, project_settings: ResourceSettings) -> ResourceSettings:
    extras = _deep_merge(global_settings.extras, project_settings.extras)
    enable_skill_commands = (
        project_settings.enable_skill_commands
        if project_settings.enable_skill_commands is not None
        else global_settings.enable_skill_commands
    )
    return ResourceSettings(
        packages=project_settings.packages or global_settings.packages,
        extensions=project_settings.extensions or global_settings.extensions,
        skills=project_settings.skills or global_settings.skills,
        prompts=project_settings.prompts or global_settings.prompts,
        themes=project_settings.themes or global_settings.themes,
        enable_skill_commands=True if enable_skill_commands is None else enable_skill_commands,
        extras=extras,
    )


def resolve_settings_paths(
    values: tuple[str, ...],
    *,
    settings_dir: Path,
    cwd: Path,
) -> list[Path]:
    return [resolve_resource_path(value, base_dir=settings_dir, cwd=cwd) for value in values]


def _read_settings(path: Path, diagnostics: list[ResourceDiagnostic]) -> ResourceSettings:
    if not path.exists():
        return ResourceSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        diagnostics.append(
            ResourceDiagnostic(type="error", message=f"failed to read resource settings: {exc}", path=str(path))
        )
        return ResourceSettings()
    if not isinstance(data, dict):
        diagnostics.append(
            ResourceDiagnostic(type="error", message="resource settings must be a JSON object", path=str(path))
        )
        return ResourceSettings()
    return _settings_from_dict(data)


def _settings_from_dict(data: dict[str, Any]) -> ResourceSettings:
    known = {
        "packages",
        "extensions",
        "skills",
        "prompts",
        "themes",
        "enableSkillCommands",
        "enable_skill_commands",
    }
    return ResourceSettings(
        packages=tuple(_string_list(data.get("packages"))),
        extensions=tuple(_string_list(data.get("extensions"))),
        skills=tuple(_string_list(data.get("skills"))),
        prompts=tuple(_string_list(data.get("prompts"))),
        themes=tuple(_string_list(data.get("themes"))),
        enable_skill_commands=_bool_or_none(
            data.get("enableSkillCommands", data.get("enable_skill_commands"))
        ),
        extras={key: value for key, value in data.items() if key not in known},
    )


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)


def _deep_merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


__all__ = [
    "LoadedResourceSettings",
    "ResourceSettings",
    "load_resource_settings",
    "merge_settings",
    "resolve_settings_paths",
]
