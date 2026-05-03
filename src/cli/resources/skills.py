"""Agent Skills style discovery and formatting."""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from .diagnostics import ResourceDiagnostic
from .frontmatter import split_frontmatter
from .source_info import SourceInfo

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
IGNORED_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__"}


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    path: Path
    base_dir: Path
    source: SourceInfo | None = None
    disable_model_invocation: bool = False


@dataclass(frozen=True, slots=True)
class LoadedSkills:
    skills: tuple[Skill, ...]
    diagnostics: tuple[ResourceDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillSearchRoot:
    path: Path
    allow_root_markdown: bool = False
    source: SourceInfo | None = None


def load_skills(roots: list[SkillSearchRoot | Path]) -> LoadedSkills:
    skills: list[Skill] = []
    diagnostics: list[ResourceDiagnostic] = []
    seen: dict[str, Skill] = {}
    for root in roots:
        search_root = root if isinstance(root, SkillSearchRoot) else SkillSearchRoot(Path(root))
        for path in _discover_skill_files(search_root.path, search_root.allow_root_markdown):
            skill = _load_skill(path, search_root.source, diagnostics)
            if skill is None:
                continue
            if skill.name in seen:
                diagnostics.append(
                    ResourceDiagnostic(
                        type="collision",
                        message=f"duplicate skill {skill.name!r}; keeping first",
                        path=str(skill.path),
                        resource_type="skill",
                        name=skill.name,
                        winner=str(seen[skill.name].path),
                        loser=str(skill.path),
                    )
                )
                continue
            seen[skill.name] = skill
            skills.append(skill)
    return LoadedSkills(tuple(skills), tuple(diagnostics))


def format_skills_for_prompt(skills: list[Skill]) -> str:
    visible = [skill for skill in skills if not skill.disable_model_invocation]
    if not visible:
        return ""
    lines = [
        "<available_skills>",
        "Use the read tool to inspect a skill before following details. Paths are relative to location.",
    ]
    for skill in visible:
        lines.append(
            f'<skill name="{html.escape(skill.name)}" '
            f'location="{html.escape(str(skill.base_dir))}">'
        )
        lines.append(html.escape(skill.description))
        lines.append("</skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def expand_skill_command(text: str, skills: list[Skill]) -> str | None:
    if not text.startswith("/skill:"):
        return None
    head, _, args = text.partition(" ")
    name = head.removeprefix("/skill:")
    skill = next((candidate for candidate in skills if candidate.name == name), None)
    if skill is None:
        return None
    metadata, body = split_frontmatter(skill.path.read_text(encoding="utf-8"))
    del metadata
    expanded = (
        f'<skill name="{html.escape(skill.name)}" location="{html.escape(str(skill.base_dir))}">\n'
        f"References are relative to {skill.base_dir}.\n\n"
        f"{body.strip()}\n"
        "</skill>"
    )
    return f"{expanded}\n\nUser arguments: {args}" if args else expanded


def _discover_skill_files(root: Path, allow_root_markdown: bool) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file() and root.name == "SKILL.md":
        return [root.resolve()]
    if root.is_file() and allow_root_markdown and root.suffix == ".md":
        return [root.resolve()]
    if not root.is_dir():
        return []
    files: list[Path] = []
    if allow_root_markdown:
        files.extend(sorted(child.resolve() for child in root.iterdir() if child.is_file() and child.suffix == ".md"))
    files.extend(_walk_skill_dirs(root))
    return files


def _walk_skill_dirs(root: Path) -> list[Path]:
    skill_file = root / "SKILL.md"
    if skill_file.is_file():
        return [skill_file.resolve()]
    files: list[Path] = []
    for child in sorted(root.iterdir()):
        if child.name.startswith(".") or child.name in IGNORED_DIRS:
            continue
        if child.is_dir():
            files.extend(_walk_skill_dirs(child))
    return files


def _load_skill(
    path: Path,
    source: SourceInfo | None,
    diagnostics: list[ResourceDiagnostic],
) -> Skill | None:
    try:
        metadata, _body = split_frontmatter(path.read_text(encoding="utf-8"))
    except Exception as exc:
        diagnostics.append(
            ResourceDiagnostic(type="error", message=f"failed to read skill: {exc}", path=str(path), resource_type="skill")
        )
        return None
    name = str(metadata.get("name") or path.parent.name)
    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip():
        diagnostics.append(
            ResourceDiagnostic(type="warning", message="skill description is required", path=str(path), resource_type="skill", name=name)
        )
        return None
    for warning in _name_warnings(name, path.parent.name):
        diagnostics.append(ResourceDiagnostic(type="warning", message=warning, path=str(path), resource_type="skill", name=name))
    if len(description) > MAX_DESCRIPTION_LENGTH:
        diagnostics.append(
            ResourceDiagnostic(
                type="warning",
                message=f"description exceeds {MAX_DESCRIPTION_LENGTH} characters ({len(description)})",
                path=str(path),
                resource_type="skill",
                name=name,
            )
        )
    return Skill(
        name=name,
        description=description.strip(),
        path=path.resolve(),
        base_dir=path.parent.resolve(),
        source=source,
        disable_model_invocation=bool(metadata.get("disable-model-invocation", False)),
    )


def _name_warnings(name: str, parent_dir_name: str) -> list[str]:
    warnings: list[str] = []
    if name != parent_dir_name:
        warnings.append(f'name "{name}" does not match parent directory "{parent_dir_name}"')
    if len(name) > MAX_NAME_LENGTH:
        warnings.append(f"name exceeds {MAX_NAME_LENGTH} characters ({len(name)})")
    if not name or not all(char.islower() or char.isdigit() or char == "-" for char in name):
        warnings.append("name contains invalid characters (must be lowercase a-z, 0-9, hyphens only)")
    if name.startswith("-") or name.endswith("-"):
        warnings.append("name must not start or end with a hyphen")
    if "--" in name:
        warnings.append("name must not contain consecutive hyphens")
    return warnings


__all__ = [
    "LoadedSkills",
    "Skill",
    "SkillSearchRoot",
    "expand_skill_command",
    "format_skills_for_prompt",
    "load_skills",
]
