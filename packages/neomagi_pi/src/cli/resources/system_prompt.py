"""System prompt builder for resource-derived prompt context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .context_files import ContextFile
from .skills import Skill, format_skills_for_prompt

_TOOL_DESCRIPTIONS: dict[str, str] = {
    "read": "inspect files, including skill instructions",
    "bash": "execute shell commands, local CLIs, and skill helper scripts",
    "edit": "make precise file edits",
    "write": "create or overwrite files",
    "grep": "search file contents with regex",
    "find": "find files by name or path",
    "ls": "list directory contents",
}

_BASH_GUIDANCE = (
    "Use bash to execute local CLIs and skill helper scripts when the user asks for results. "
    "When a skill, helper script, or local CLI can produce the result, run it via bash "
    "yourself instead of telling the user to run it."
)


@dataclass(frozen=True, slots=True)
class SystemPromptParts:
    base_prompt: str
    append_prompts: tuple[str, ...] = ()
    context_files: tuple[ContextFile, ...] = ()
    skills: tuple[Skill, ...] = ()
    active_tools: tuple[str, ...] = ()
    cwd: str | None = None
    current_date: date | None = None


def build_system_prompt(parts: SystemPromptParts) -> str:
    active = set(parts.active_tools)
    sections = [parts.base_prompt.strip()]
    tools_section = _format_active_tools(active)
    if tools_section:
        sections.append(tools_section)
    sections.extend(prompt.strip() for prompt in parts.append_prompts if prompt.strip())
    if parts.context_files:
        sections.append(_format_context_files(parts.context_files))
    if "read" in active:
        skills_xml = format_skills_for_prompt(list(parts.skills), bash_active="bash" in active)
        if skills_xml:
            sections.append(skills_xml)
    footer: list[str] = []
    if parts.current_date is not None:
        footer.append(f"Current date: {parts.current_date.isoformat()}")
    if parts.cwd:
        footer.append(f"Current working directory: {parts.cwd}")
    if footer:
        sections.append("\n".join(footer))
    return "\n\n".join(section for section in sections if section)


def load_prompt_file(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _format_active_tools(active: set[str]) -> str:
    visible = [name for name in _TOOL_DESCRIPTIONS if name in active]
    if not visible:
        return ""
    lines = ["Available tools:"]
    lines.extend(f"- {name}: {_TOOL_DESCRIPTIONS[name]}" for name in visible)
    if "bash" in active:
        lines.append("")
        lines.append(_BASH_GUIDANCE)
    return "\n".join(lines)


def _format_context_files(files: tuple[ContextFile, ...]) -> str:
    sections = ["# Project Context"]
    for file in files:
        sections.append(f"## {file.path}")
        sections.append(file.content.strip())
    return "\n\n".join(sections)


__all__ = ["SystemPromptParts", "build_system_prompt", "load_prompt_file"]
