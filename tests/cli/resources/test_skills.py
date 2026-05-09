from __future__ import annotations

from cli.resources.skills import (
    SkillSearchRoot,
    expand_skill_command,
    expand_skill_command_detail,
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
    assert "Visible body" not in prompt
    assert "Hidden body" not in prompt
    assert "Visible body" in (expand_skill_command("/skill:visible args", list(loaded.skills)) or "")
    assert "User arguments: args" in (expand_skill_command("/skill:visible args", list(loaded.skills)) or "")

    detail = expand_skill_command_detail("/skill:visible args", list(loaded.skills))
    assert detail is not None
    assert detail.original == "/skill:visible args"
    assert detail.display == "/skill:visible args"
    assert detail.resource_type == "skill"
    assert detail.name == "visible"
    assert "Visible body" in detail.expanded


def test_skill_prompt_uses_agent_facing_internal_capability_voice(tmp_path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\nname: demo\ndescription: Demo skill description.\n---\nBody\n",
        encoding="utf-8",
    )

    loaded = load_skills([tmp_path])
    prompt = format_skills_for_prompt(list(loaded.skills), bash_active=True)

    assert "internal capability packages" in prompt
    assert "Users do not need to invoke them explicitly" in prompt
    assert "read the skill's SKILL.md" in prompt
    assert "run them with bash" in prompt
    assert "Resolve relative paths against the directory containing SKILL.md" in prompt
    assert "Do not merely tell the user the command" in prompt


def test_skill_prompt_omits_bash_phrasing_when_bash_inactive(tmp_path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill description.\n---\nBody\n",
        encoding="utf-8",
    )

    loaded = load_skills([tmp_path])
    prompt = format_skills_for_prompt(list(loaded.skills), bash_active=False)

    assert "internal capability packages" in prompt
    assert "Resolve relative paths against the directory containing SKILL.md" in prompt
    assert "run them with bash" not in prompt
    assert "bash is not available" in prompt


def test_skill_prompt_location_points_at_skill_md_file(tmp_path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\nname: demo\ndescription: Demo skill description.\n---\nBody\n",
        encoding="utf-8",
    )

    loaded = load_skills([tmp_path])
    prompt = format_skills_for_prompt(list(loaded.skills))

    assert f'location="{skill_path.resolve()}"' in prompt
    assert f'location="{skill_dir.resolve()}"' not in prompt


def test_skill_prompt_keeps_compact_attribute_xml_shape(tmp_path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill description.\n---\nBody\n",
        encoding="utf-8",
    )

    loaded = load_skills([tmp_path])
    prompt = format_skills_for_prompt(list(loaded.skills))

    assert "<skill name=" in prompt
    assert "<name>" not in prompt
    assert "<description>" not in prompt
    assert "<location>" not in prompt


def test_natural_language_prompt_is_not_host_expanded_into_skill_body(tmp_path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill description.\n---\nDemo body content\n",
        encoding="utf-8",
    )

    loaded = load_skills([tmp_path])

    assert expand_skill_command_detail("please run the demo skill", list(loaded.skills)) is None
    assert expand_skill_command("please run the demo skill", list(loaded.skills)) is None


def test_skill_expansion_replaces_base_dir_in_body_only(tmp_path) -> None:
    skill_dir = tmp_path / "helper-backed"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: helper-backed\n"
        "description: Use {baseDir} only when expanded.\n"
        "---\n"
        "Run {baseDir}/script.js\n",
        encoding="utf-8",
    )

    loaded = load_skills([tmp_path])
    prompt = format_skills_for_prompt(list(loaded.skills))
    expanded = expand_skill_command("/skill:helper-backed", list(loaded.skills)) or ""

    assert "Use {baseDir} only when expanded." in prompt
    assert f"Run {skill_dir.resolve()}/script.js" in expanded
    assert "{baseDir}" not in expanded


def test_agents_root_markdown_is_ignored_but_pi_root_markdown_loads(tmp_path) -> None:
    (tmp_path / "root.md").write_text("---\nname: root\ndescription: Root skill.\n---\nRoot\n", encoding="utf-8")

    ignored = load_skills([SkillSearchRoot(tmp_path, allow_root_markdown=False)])
    loaded = load_skills([SkillSearchRoot(tmp_path, allow_root_markdown=True)])

    assert ignored.skills == ()
    assert [skill.name for skill in loaded.skills] == ["root"]
