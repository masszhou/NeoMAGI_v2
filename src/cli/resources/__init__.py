"""Resource discovery and prompt-resource helpers for the CLI product layer."""

from .context_files import ContextFile, load_context_files
from .diagnostics import ResourceDiagnostic
from .loader import ResourceExtensionPaths, ResourceLoader, ResourceSnapshot
from .prompt_templates import (
    PromptTemplate,
    expand_prompt_template,
    load_prompt_templates,
    parse_command_args,
    substitute_args,
)
from .settings import ResourceSettings, load_resource_settings
from .skills import Skill, expand_skill_command, format_skills_for_prompt, load_skills
from .source_info import ResourceInfo, SourceInfo
from .system_prompt import SystemPromptParts, build_system_prompt

__all__ = [
    "ContextFile",
    "PromptTemplate",
    "ResourceDiagnostic",
    "ResourceExtensionPaths",
    "ResourceInfo",
    "ResourceLoader",
    "ResourceSettings",
    "ResourceSnapshot",
    "Skill",
    "SourceInfo",
    "SystemPromptParts",
    "build_system_prompt",
    "expand_prompt_template",
    "expand_skill_command",
    "format_skills_for_prompt",
    "load_context_files",
    "load_prompt_templates",
    "load_resource_settings",
    "load_skills",
    "parse_command_args",
    "substitute_args",
]
