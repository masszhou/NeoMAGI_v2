from __future__ import annotations

from datetime import date

from cli.interactive.extension_runtime import _DEFAULT_SYSTEM_PROMPT
from cli.resources.context_files import ContextFile
from cli.resources.skills import Skill
from cli.resources.system_prompt import SystemPromptParts, build_system_prompt


def test_system_prompt_includes_context_skills_date_and_cwd_only_when_read_active(tmp_path) -> None:
    skill_path = tmp_path / "skill" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text("---\nname: skill\ndescription: Skill description.\n---\nBody\n", encoding="utf-8")
    skill = Skill("skill", "Skill description.", skill_path, skill_path.parent)
    context = ContextFile(tmp_path / "AGENTS.md", "Project rules")

    prompt = build_system_prompt(
        SystemPromptParts(
            base_prompt="Base",
            append_prompts=("Append",),
            context_files=(context,),
            skills=(skill,),
            active_tools=("read",),
            cwd=str(tmp_path),
            current_date=date(2026, 5, 3),
        )
    )
    no_read_prompt = build_system_prompt(SystemPromptParts(base_prompt="Base", skills=(skill,), active_tools=()))

    assert "Base" in prompt
    assert "Append" in prompt
    assert "# Project Context" in prompt
    assert 'name="skill"' in prompt
    assert "Current date: 2026-05-03" in prompt
    assert str(tmp_path) in prompt
    assert 'name="skill"' not in no_read_prompt


def test_default_system_prompt_uses_neomagi_local_agent_harness_voice() -> None:
    assert "NeoMAGI" in _DEFAULT_SYSTEM_PROMPT
    assert "local terminal agent harness" in _DEFAULT_SYSTEM_PROMPT
    assert "yourself" in _DEFAULT_SYSTEM_PROMPT
    assert "Do not merely tell the user" in _DEFAULT_SYSTEM_PROMPT


def test_default_system_prompt_is_capability_neutral() -> None:
    lowered = _DEFAULT_SYSTEM_PROMPT.lower()
    assert "shell command" not in lowered
    assert "editing" not in lowered
    assert "creating files" not in lowered
    assert "reading files" not in lowered


def test_system_prompt_renders_active_tools_summary_for_read_and_bash() -> None:
    prompt = build_system_prompt(
        SystemPromptParts(base_prompt="Base", active_tools=("read", "bash"))
    )

    assert "Available tools:" in prompt
    assert "- read: inspect files, including skill instructions" in prompt
    assert "- bash: execute shell commands, local CLIs, and skill helper scripts" in prompt
    assert "- edit:" not in prompt
    assert "- write:" not in prompt
    assert "run it via bash yourself instead of telling the user to run it" in prompt


def test_system_prompt_omits_bash_guidance_when_bash_inactive() -> None:
    prompt = build_system_prompt(
        SystemPromptParts(base_prompt="Base", active_tools=("read",))
    )

    assert "Available tools:" in prompt
    assert "- read:" in prompt
    assert "run it via bash yourself" not in prompt


def test_system_prompt_lists_read_only_profile_tools() -> None:
    prompt = build_system_prompt(
        SystemPromptParts(
            base_prompt="Base",
            active_tools=("read", "grep", "find", "ls"),
        )
    )

    assert "- read: inspect files, including skill instructions" in prompt
    assert "- grep: search file contents with regex" in prompt
    assert "- find: find files by name or path" in prompt
    assert "- ls: list directory contents" in prompt
    assert "- bash:" not in prompt
    assert "- edit:" not in prompt
    assert "- write:" not in prompt
    assert "run it via bash yourself" not in prompt


def test_system_prompt_skill_section_drops_bash_phrasing_when_bash_inactive(tmp_path) -> None:
    skill_path = tmp_path / "demo" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text("---\nname: demo\ndescription: Demo skill.\n---\nBody\n", encoding="utf-8")
    skill = Skill("demo", "Demo skill.", skill_path, skill_path.parent)

    prompt = build_system_prompt(
        SystemPromptParts(
            base_prompt="Base",
            skills=(skill,),
            active_tools=("read", "grep", "find", "ls"),
        )
    )

    assert "<available_skills>" in prompt
    assert 'name="demo"' in prompt
    assert "run them with bash" not in prompt
    assert "bash is not available" in prompt


def test_system_prompt_skips_tools_section_when_no_active_tools() -> None:
    prompt = build_system_prompt(SystemPromptParts(base_prompt="Base", active_tools=()))

    assert "Available tools:" not in prompt


def test_system_prompt_tools_section_order_is_deterministic() -> None:
    prompt = build_system_prompt(
        SystemPromptParts(
            base_prompt="Base",
            active_tools=("write", "edit", "bash", "read"),
        )
    )

    read_pos = prompt.index("- read:")
    bash_pos = prompt.index("- bash:")
    edit_pos = prompt.index("- edit:")
    write_pos = prompt.index("- write:")
    assert read_pos < bash_pos < edit_pos < write_pos


def test_system_prompt_section_order_places_tools_before_append_and_skills(tmp_path) -> None:
    skill_path = tmp_path / "demo" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text("---\nname: demo\ndescription: Demo skill.\n---\nBody\n", encoding="utf-8")
    skill = Skill("demo", "Demo skill.", skill_path, skill_path.parent)

    prompt = build_system_prompt(
        SystemPromptParts(
            base_prompt="Base",
            append_prompts=("Append text",),
            skills=(skill,),
            active_tools=("read", "bash"),
            cwd=str(tmp_path),
            current_date=date(2026, 5, 9),
        )
    )

    base_pos = prompt.index("Base")
    tools_pos = prompt.index("Available tools:")
    append_pos = prompt.index("Append text")
    skills_pos = prompt.index("<available_skills>")
    date_pos = prompt.index("Current date:")
    assert base_pos < tools_pos < append_pos < skills_pos < date_pos
