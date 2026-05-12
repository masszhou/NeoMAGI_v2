"""Resource settings loading and merging.

M9 keeps this module as a compatibility adapter. The product settings schema in
``cli.core.settings`` is the authoritative source; resource loading projects
``effective_settings.resources`` into the older ``ResourceSettings`` shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cli.core.settings import SettingsManager

from .diagnostics import ResourceDiagnostic
from .paths import resolve_resource_path


@dataclass(frozen=True, slots=True)
class ResourceSettings:
    packages: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    prompts: tuple[str, ...] = ()
    themes: tuple[str, ...] = ()
    enable_skill_commands: bool | None = None
    skill_env: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoadedResourceSettings:
    settings: ResourceSettings
    diagnostics: tuple[ResourceDiagnostic, ...] = ()


def load_resource_settings(
    cwd: str | Path,
    *,
    agent_dir: str | Path | None = None,
    settings_manager: SettingsManager | None = None,
) -> LoadedResourceSettings:
    cwd_path = Path(cwd).resolve()
    manager = settings_manager or SettingsManager(cwd=cwd_path, agent_dir=agent_dir)
    loaded = manager.load()
    diagnostics = [
        ResourceDiagnostic(
            type=diagnostic.severity,
            message=diagnostic.message,
            path=diagnostic.path,
            resource_type="settings",
            name=diagnostic.field,
        )
        for diagnostic in loaded.diagnostics
    ]
    return LoadedResourceSettings(
        settings=_settings_from_product(loaded.settings.resources),
        diagnostics=tuple(diagnostics),
    )


def resolve_settings_paths(
    values: tuple[str, ...],
    *,
    settings_dir: Path,
    cwd: Path,
) -> list[Path]:
    return [resolve_resource_path(value, base_dir=settings_dir, cwd=cwd) for value in values]


def _settings_from_product(resources: Any) -> ResourceSettings:
    dumped = resources.model_dump(by_alias=True, exclude_none=True)
    known = {
        "packages",
        "extensions",
        "skills",
        "prompts",
        "themes",
        "enableSkillCommands",
        "skillEnv",
    }
    return ResourceSettings(
        packages=tuple(resources.packages),
        extensions=tuple(resources.extensions),
        skills=tuple(resources.skills),
        prompts=tuple(resources.prompts),
        themes=tuple(resources.themes),
        enable_skill_commands=resources.enable_skill_commands,
        skill_env=dumped.get("skillEnv") if isinstance(dumped.get("skillEnv"), dict) else {},
        extras={key: value for key, value in dumped.items() if key not in known},
    )


__all__ = [
    "LoadedResourceSettings",
    "ResourceSettings",
    "load_resource_settings",
    "resolve_settings_paths",
]
