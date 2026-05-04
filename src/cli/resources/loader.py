"""Resource loader substrate for extensions, skills, prompts and context files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .context_files import ContextFile, load_context_files
from .diagnostics import ResourceDiagnostic
from .paths import default_agent_dir, resolve_resource_path
from .prompt_templates import PromptTemplate, load_prompt_templates
from .settings import ResourceSettings, load_resource_settings
from .skills import Skill, SkillSearchRoot, load_skills
from .source_info import ResourceInfo, SourceInfo
from .system_prompt import load_prompt_file
from .themes import ThemeResource, load_themes


@dataclass(frozen=True, slots=True)
class ResourceExtensionPaths:
    extensions: tuple[Path, ...] = ()
    skills: tuple[Path, ...] = ()
    prompts: tuple[Path, ...] = ()
    themes: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    extensions: tuple[ResourceInfo, ...] = ()
    skills: tuple[Skill, ...] = ()
    prompts: tuple[PromptTemplate, ...] = ()
    themes: tuple[ThemeResource, ...] = ()
    context_files: tuple[ContextFile, ...] = ()
    system_prompt: str | None = None
    append_system_prompts: tuple[str, ...] = ()
    diagnostics: tuple[ResourceDiagnostic, ...] = ()


@dataclass(slots=True)
class ResourceLoader:
    cwd: Path
    agent_dir: Path = field(default_factory=default_agent_dir)
    explicit_extensions: tuple[Path, ...] = ()
    explicit_skills: tuple[Path, ...] = ()
    explicit_prompts: tuple[Path, ...] = ()
    explicit_themes: tuple[Path, ...] = ()

    _settings: ResourceSettings = field(default_factory=ResourceSettings, init=False)
    _snapshot: ResourceSnapshot = field(default_factory=ResourceSnapshot, init=False)
    _extension_paths: ResourceExtensionPaths = field(default_factory=ResourceExtensionPaths, init=False)

    def __post_init__(self) -> None:
        self.cwd = Path(self.cwd).resolve()
        self.agent_dir = Path(self.agent_dir).expanduser().resolve()
        self.explicit_extensions = tuple(Path(path).expanduser().resolve() for path in self.explicit_extensions)
        self.explicit_skills = tuple(Path(path).expanduser().resolve() for path in self.explicit_skills)
        self.explicit_prompts = tuple(Path(path).expanduser().resolve() for path in self.explicit_prompts)
        self.explicit_themes = tuple(Path(path).expanduser().resolve() for path in self.explicit_themes)

    @property
    def snapshot(self) -> ResourceSnapshot:
        return self._snapshot

    @property
    def settings(self) -> ResourceSettings:
        return self._settings

    def get_extensions(self) -> tuple[ResourceInfo, ...]:
        return self._snapshot.extensions

    def get_skills(self) -> tuple[Skill, ...]:
        return self._snapshot.skills

    def get_prompts(self) -> tuple[PromptTemplate, ...]:
        return self._snapshot.prompts

    def get_themes(self) -> tuple[ThemeResource, ...]:
        return self._snapshot.themes

    def get_context_files(self) -> tuple[ContextFile, ...]:
        return self._snapshot.context_files

    def get_system_prompt(self) -> str | None:
        return self._snapshot.system_prompt

    def get_append_system_prompt(self) -> tuple[str, ...]:
        return self._snapshot.append_system_prompts

    def extend_resources(self, paths: ResourceExtensionPaths) -> None:
        self._extension_paths = ResourceExtensionPaths(
            extensions=_dedupe((*self._extension_paths.extensions, *paths.extensions)),
            skills=_dedupe((*self._extension_paths.skills, *paths.skills)),
            prompts=_dedupe((*self._extension_paths.prompts, *paths.prompts)),
            themes=_dedupe((*self._extension_paths.themes, *paths.themes)),
        )

    async def reload(self) -> None:
        loaded_settings = load_resource_settings(self.cwd, agent_dir=self.agent_dir)
        self._settings = loaded_settings.settings
        diagnostics = list(loaded_settings.diagnostics)
        extensions = self._discover_extensions(diagnostics)
        skills = load_skills(self._skill_roots())
        prompts = load_prompt_templates(self._prompt_paths())
        themes = load_themes(self._theme_paths())
        context_files = load_context_files(self.cwd, agent_dir=self.agent_dir)
        diagnostics.extend(skills.diagnostics)
        diagnostics.extend(prompts.diagnostics)
        diagnostics.extend(themes.diagnostics)
        diagnostics.extend(context_files.diagnostics)
        self._snapshot = ResourceSnapshot(
            extensions=extensions,
            skills=skills.skills,
            prompts=prompts.templates,
            themes=themes.themes,
            context_files=context_files.files,
            system_prompt=self._system_prompt(),
            append_system_prompts=tuple(prompt for prompt in self._append_system_prompts() if prompt is not None),
            diagnostics=tuple(diagnostics),
        )

    def _discover_extensions(self, diagnostics: list[ResourceDiagnostic]) -> tuple[ResourceInfo, ...]:
        files: list[ResourceInfo] = []
        for source in self._extension_sources():
            files.extend(_extension_files(source, diagnostics))
        return tuple(_first_by_name(files, diagnostics, resource_type="extension"))

    def _extension_sources(self) -> list[SourceInfo]:
        return [
            *_sources_from_paths(self._settings.extensions, base_dir=self.agent_dir, cwd=self.cwd, scope="user", origin="settings", priority=0),
            SourceInfo("project", "auto", self.cwd / ".pi" / "extensions", self.cwd / ".pi", 1),
            SourceInfo("user", "auto", self.agent_dir / "extensions", self.agent_dir, 2),
            *[
                SourceInfo("explicit", "explicit", path, self.cwd, 3)
                for path in self.explicit_extensions
            ],
            *[
                SourceInfo("temporary", "extension", path, path.parent, 4)
                for path in self._extension_paths.extensions
            ],
        ]

    def _skill_roots(self) -> list[SkillSearchRoot]:
        roots: list[SkillSearchRoot] = []
        roots.extend(
            SkillSearchRoot(source.path, allow_root_markdown=True, source=source)
            for source in _sources_from_paths(self._settings.skills, base_dir=self.agent_dir, cwd=self.cwd, scope="user", origin="settings", priority=0)
        )
        roots.append(SkillSearchRoot(self.cwd / ".pi" / "skills", allow_root_markdown=True, source=SourceInfo("project", "auto", self.cwd / ".pi" / "skills", self.cwd / ".pi", 1)))
        roots.append(SkillSearchRoot(self.agent_dir / "skills", allow_root_markdown=True, source=SourceInfo("user", "auto", self.agent_dir / "skills", self.agent_dir, 2)))
        home_like_dir = self.agent_dir.parent.parent if self.agent_dir.name == "agent" else self.agent_dir.parent
        agents_skills_dir = home_like_dir / ".agents" / "skills"
        roots.append(SkillSearchRoot(agents_skills_dir, allow_root_markdown=False, source=SourceInfo("user", "auto", agents_skills_dir, home_like_dir, 3)))
        for directory in [*reversed(self.cwd.parents), self.cwd]:
            root = directory / ".agents" / "skills"
            roots.append(SkillSearchRoot(root, allow_root_markdown=False, source=SourceInfo("project", "auto", root, directory, 4)))
        roots.extend(SkillSearchRoot(path, allow_root_markdown=True, source=SourceInfo("explicit", "explicit", path, self.cwd, 5)) for path in self.explicit_skills)
        roots.extend(SkillSearchRoot(path, allow_root_markdown=True, source=SourceInfo("temporary", "extension", path, path.parent, 6)) for path in self._extension_paths.skills)
        return roots

    def _prompt_paths(self) -> list[Path]:
        return [
            *[source.path for source in _sources_from_paths(self._settings.prompts, base_dir=self.agent_dir, cwd=self.cwd, scope="user", origin="settings", priority=0)],
            self.cwd / ".pi" / "prompts",
            self.agent_dir / "prompts",
            *self.explicit_prompts,
            *self._extension_paths.prompts,
        ]

    def _theme_paths(self) -> list[Path]:
        return [
            *[source.path for source in _sources_from_paths(self._settings.themes, base_dir=self.agent_dir, cwd=self.cwd, scope="user", origin="settings", priority=0)],
            self.cwd / ".pi" / "themes",
            self.agent_dir / "themes",
            *self.explicit_themes,
            *self._extension_paths.themes,
        ]

    def _system_prompt(self) -> str | None:
        return load_prompt_file(self.cwd / ".pi" / "SYSTEM.md") or load_prompt_file(self.agent_dir / "SYSTEM.md")

    def _append_system_prompts(self) -> list[str | None]:
        return [
            load_prompt_file(self.cwd / ".pi" / "APPEND_SYSTEM.md"),
            load_prompt_file(self.agent_dir / "APPEND_SYSTEM.md"),
        ]


def _extension_files(source: SourceInfo, diagnostics: list[ResourceDiagnostic]) -> list[ResourceInfo]:
    path = source.path
    if not path.exists():
        return []
    if path.is_file() and path.suffix == ".py":
        return [ResourceInfo("extension", path.stem, path.resolve(), source)]
    if path.is_dir():
        index = path / "index.py"
        if index.is_file():
            return [ResourceInfo("extension", path.name, index.resolve(), source)]
        return [
            ResourceInfo("extension", child.stem, child.resolve(), source)
            for child in sorted(path.iterdir())
            if child.is_file() and child.suffix == ".py"
        ] + [
            ResourceInfo("extension", child.name, (child / "index.py").resolve(), source)
            for child in sorted(path.iterdir())
            if child.is_dir() and (child / "index.py").is_file()
        ]
    diagnostics.append(ResourceDiagnostic(type="warning", message="unsupported extension resource path", path=str(path), resource_type="extension"))
    return []


def _sources_from_paths(
    values: tuple[str, ...],
    *,
    base_dir: Path,
    cwd: Path,
    scope: str,
    origin: str,
    priority: int,
) -> list[SourceInfo]:
    return [
        SourceInfo(scope, origin, resolve_resource_path(value, base_dir=base_dir, cwd=cwd), base_dir, priority)  # type: ignore[arg-type]
        for value in values
    ]


def _first_by_name(
    resources: list[ResourceInfo],
    diagnostics: list[ResourceDiagnostic],
    *,
    resource_type: str,
) -> list[ResourceInfo]:
    seen: dict[str, ResourceInfo] = {}
    result: list[ResourceInfo] = []
    for resource in resources:
        if resource.name in seen:
            diagnostics.append(
                ResourceDiagnostic(
                    type="collision",
                    message=f"duplicate {resource_type} {resource.name!r}; keeping first",
                    path=str(resource.path),
                    resource_type=resource_type,
                    name=resource.name,
                    winner=str(seen[resource.name].path),
                    loser=str(resource.path),
                )
            )
            continue
        seen[resource.name] = resource
        result.append(resource)
    return result


def _dedupe(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        if resolved not in seen:
            result.append(resolved)
            seen.add(resolved)
    return tuple(result)


__all__ = ["ResourceExtensionPaths", "ResourceLoader", "ResourceSnapshot"]
