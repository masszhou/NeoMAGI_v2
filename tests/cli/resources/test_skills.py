from __future__ import annotations

from cli.resources.skills import (
    SkillSearchRoot,
    expand_skill_command,
    format_skills_for_prompt,
    load_skills,
)


def test_skill_loader_keeps_invalid_name_with_warning(tmp_path) -> None:
    skill_dir = tmp_path / "parent-name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Bad--Name\ndescription: Use when testing.\n---\nBody\n",
        encoding="utf-8",
    )

    loaded = load_skills([tmp_path])

    assert [skill.name for skill in loaded.skills] == ["Bad--Name"]
    assert any("invalid characters" in diagnostic.message for diagnostic in loaded.diagnostics)
    assert any("does not match parent directory" in diagnostic.message for diagnostic in loaded.diagnostics)


def test_skill_loader_skips_missing_description(tmp_path) -> None:
    skill_dir = tmp_path / "missing-description"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: missing-description\n---\nBody\n", encoding="utf-8")

    loaded = load_skills([tmp_path])

    assert loaded.skills == ()
    assert any("description is required" in diagnostic.message for diagnostic in loaded.diagnostics)


def test_skill_prompt_and_expansion(tmp_path) -> None:
    visible = tmp_path / "visible"
    hidden = tmp_path / "hidden"
    visible.mkdir()
    hidden.mkdir()
    (visible / "SKILL.md").write_text(
        "---\nname: visible\ndescription: Visible skill.\n---\nVisible body\n",
        encoding="utf-8",
    )
    (hidden / "SKILL.md").write_text(
        "---\nname: hidden\ndescription: Hidden skill.\ndisable-model-invocation: true\n---\nHidden body\n",
        encoding="utf-8",
    )

    loaded = load_skills([tmp_path])
    prompt = format_skills_for_prompt(list(loaded.skills))

    assert 'name="visible"' in prompt
    assert 'name="hidden"' not in prompt
    assert "Visible body" in (expand_skill_command("/skill:visible args", list(loaded.skills)) or "")
    assert "User arguments: args" in (expand_skill_command("/skill:visible args", list(loaded.skills)) or "")


def test_agents_root_markdown_is_ignored_but_pi_root_markdown_loads(tmp_path) -> None:
    (tmp_path / "root.md").write_text("---\nname: root\ndescription: Root skill.\n---\nRoot\n", encoding="utf-8")

    ignored = load_skills([SkillSearchRoot(tmp_path, allow_root_markdown=False)])
    loaded = load_skills([SkillSearchRoot(tmp_path, allow_root_markdown=True)])

    assert ignored.skills == ()
    assert [skill.name for skill in loaded.skills] == ["root"]
