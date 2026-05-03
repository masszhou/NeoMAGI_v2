"""Pi-style prompt template discovery and slash expansion."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .diagnostics import ResourceDiagnostic
from .frontmatter import split_frontmatter
from .source_info import SourceInfo


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    path: Path
    body: str
    source: SourceInfo | None = None
    description: str | None = None
    argument_hint: str | None = None


@dataclass(frozen=True, slots=True)
class LoadedPromptTemplates:
    templates: tuple[PromptTemplate, ...]
    diagnostics: tuple[ResourceDiagnostic, ...] = ()


def load_prompt_templates(
    paths: list[Path],
    *,
    source_by_path: dict[Path, SourceInfo] | None = None,
) -> LoadedPromptTemplates:
    templates: list[PromptTemplate] = []
    diagnostics: list[ResourceDiagnostic] = []
    seen: dict[str, PromptTemplate] = {}
    for path in _iter_prompt_files(paths):
        try:
            template = _load_prompt_file(path, source_by_path.get(path) if source_by_path else None)
        except Exception as exc:
            diagnostics.append(
                ResourceDiagnostic(
                    type="error",
                    message=f"failed to load prompt template: {exc}",
                    path=str(path),
                    resource_type="prompt_template",
                )
            )
            continue
        if template.name in seen:
            diagnostics.append(
                ResourceDiagnostic(
                    type="collision",
                    message=f"duplicate prompt template {template.name!r}; keeping first",
                    path=str(path),
                    resource_type="prompt_template",
                    name=template.name,
                    winner=str(seen[template.name].path),
                    loser=str(path),
                )
            )
            continue
        seen[template.name] = template
        templates.append(template)
    return LoadedPromptTemplates(tuple(templates), tuple(diagnostics))


def parse_command_args(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def expand_prompt_template(text: str, templates: list[PromptTemplate]) -> str | None:
    if not text.startswith("/"):
        return None
    command, _, raw_args = text[1:].partition(" ")
    template_by_name = {template.name: template for template in templates}
    template = template_by_name.get(command)
    if template is None:
        return None
    args = parse_command_args(raw_args)
    return substitute_args(template.body, args)


def substitute_args(template: str, args: list[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in {"$@", "$ARGUMENTS"}:
            return " ".join(args)
        if match.group("index") is not None:
            index = int(match.group("index")) - 1
            return args[index] if 0 <= index < len(args) else ""
        start = int(match.group("start"))
        length = match.group("length")
        offset = max(start - 1, 0)
        selected = args[offset:] if length is None else args[offset : offset + int(length)]
        return " ".join(selected)

    return re.sub(
        r"\$\{@:(?P<start>\d+)(?::(?P<length>\d+))?\}|\$(?P<index>\d+)|\$ARGUMENTS|\$@",
        replace,
        template,
    )


def _iter_prompt_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".md":
            files.append(path.resolve())
        elif path.is_dir():
            files.extend(sorted(child.resolve() for child in path.iterdir() if child.is_file() and child.suffix == ".md"))
    return files


def _load_prompt_file(path: Path, source: SourceInfo | None) -> PromptTemplate:
    text = path.read_text(encoding="utf-8")
    metadata, body = split_frontmatter(text)
    description = _description(metadata.get("description"), body)
    argument_hint = metadata.get("argument-hint") or metadata.get("argumentHint")
    return PromptTemplate(
        name=path.stem,
        path=path.resolve(),
        body=body,
        source=source,
        description=description,
        argument_hint=str(argument_hint) if argument_hint is not None else None,
    )


def _description(value: object, body: str) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:60]
    return None


__all__ = [
    "LoadedPromptTemplates",
    "PromptTemplate",
    "expand_prompt_template",
    "load_prompt_templates",
    "parse_command_args",
    "substitute_args",
]
