from __future__ import annotations

from datetime import date

from cli.resources.context_files import ContextFile
from cli.resources.settings import ResourceSettings, merge_settings
from cli.resources.skills import Skill
from cli.resources.system_prompt import SystemPromptParts, build_system_prompt


def test_resource_settings_merge_project_overrides_arrays_and_merges_extras() -> None:
    global_settings = ResourceSettings(
        extensions=("global.py",),
        prompts=("global-prompts",),
        extras={"nested": {"a": 1}, "global": True},
    )
    project_settings = ResourceSettings(prompts=("project-prompts",), extras={"nested": {"b": 2}})

    merged = merge_settings(global_settings, project_settings)

    assert merged.extensions == ("global.py",)
    assert merged.prompts == ("project-prompts",)
    assert merged.extras == {"nested": {"a": 1, "b": 2}, "global": True}


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
