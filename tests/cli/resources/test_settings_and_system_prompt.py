from __future__ import annotations

from datetime import date

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
