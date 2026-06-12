from __future__ import annotations

import asyncio
import json

from cli.resources.loader import ResourceExtensionPaths, ResourceLoader


def _workspace_skills(loader: ResourceLoader) -> list:
    """Skills excluding the package-shipped system skills (ADR-0029)."""

    return [
        skill
        for skill in loader.get_skills()
        if skill.source is None or skill.source.scope != "system"
    ]


def test_loader_discovers_project_resources_and_context_order(tmp_path) -> None:
    agent_dir = tmp_path / "home" / ".magipi" / "agent"
    cwd = tmp_path / "repo" / "sub"
    (cwd / ".magipi" / "prompts").mkdir(parents=True)
    (cwd / ".magipi" / "skills" / "test-skill").mkdir(parents=True)
    (cwd / ".magipi" / "extensions").mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    (tmp_path / "repo").mkdir(exist_ok=True)
    (agent_dir / "AGENTS.md").write_text("global context", encoding="utf-8")
    (tmp_path / "repo" / "AGENTS.md").write_text("repo context", encoding="utf-8")
    (cwd / "CLAUDE.md").write_text("cwd context", encoding="utf-8")
    (cwd / ".magipi" / "prompts" / "review.md").write_text("Review $1", encoding="utf-8")
    (cwd / ".magipi" / "skills" / "test-skill" / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Test skill.\n---\nBody\n",
        encoding="utf-8",
    )
    (cwd / ".magipi" / "extensions" / "demo.py").write_text("def setup(api): pass\n", encoding="utf-8")

    loader = ResourceLoader(cwd=cwd, agent_dir=agent_dir)
    asyncio.run(loader.reload())

    assert [prompt.name for prompt in loader.get_prompts()] == ["review"]
    assert [skill.name for skill in _workspace_skills(loader)] == ["test-skill"]
    assert [extension.name for extension in loader.get_extensions()] == ["demo"]
    assert [file.content for file in loader.get_context_files()] == [
        "global context",
        "repo context",
        "cwd context",
    ]


def test_loader_extend_resources_is_idempotent_and_reports_collision(tmp_path) -> None:
    cwd = tmp_path / "repo"
    agent_dir = tmp_path / "home" / ".magipi" / "agent"
    ext_a = tmp_path / "ext-a"
    ext_b = tmp_path / "ext-b"
    (ext_a / "skills" / "same").mkdir(parents=True)
    (ext_b / "skills" / "same").mkdir(parents=True)
    (ext_a / "skills" / "same" / "SKILL.md").write_text(
        "---\nname: same\ndescription: First.\n---\nFirst\n",
        encoding="utf-8",
    )
    (ext_b / "skills" / "same" / "SKILL.md").write_text(
        "---\nname: same\ndescription: Second.\n---\nSecond\n",
        encoding="utf-8",
    )
    cwd.mkdir()
    agent_dir.mkdir(parents=True)

    loader = ResourceLoader(cwd=cwd, agent_dir=agent_dir)
    paths = ResourceExtensionPaths(skills=(ext_a / "skills", ext_b / "skills"))
    loader.extend_resources(paths)
    loader.extend_resources(paths)
    asyncio.run(loader.reload())

    assert _workspace_skills(loader) == []
    assert any(
        "extension-contributed skill paths are ignored" in diagnostic.message
        for diagnostic in loader.snapshot.diagnostics
    )


def test_loader_only_exposes_workspace_magipi_skills(tmp_path) -> None:
    cwd = tmp_path / "repo"
    agent_dir = tmp_path / "xdg" / "neomagi" / "magipi"
    pool = tmp_path / "xdg" / "neomagi" / "skill_pool"
    (cwd / ".magipi" / "skills" / "workspace").mkdir(parents=True)
    (cwd / ".pi" / "skills" / "old").mkdir(parents=True)
    (cwd / ".agents" / "skills" / "agent").mkdir(parents=True)
    (agent_dir / "skills" / "global").mkdir(parents=True)
    (pool / "pooled").mkdir(parents=True)
    for skill_dir, name in [
        (cwd / ".magipi" / "skills" / "workspace", "workspace"),
        (cwd / ".pi" / "skills" / "old", "old"),
        (cwd / ".agents" / "skills" / "agent", "agent"),
        (agent_dir / "skills" / "global", "global"),
        (pool / "pooled", "pooled"),
    ]:
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} skill.\n---\n{name} body\n",
            encoding="utf-8",
        )

    loader = ResourceLoader(cwd=cwd, agent_dir=agent_dir)
    asyncio.run(loader.reload())

    assert [skill.name for skill in _workspace_skills(loader)] == ["workspace"]
    skill = _workspace_skills(loader)[0]
    assert skill.display_location == ".magipi/skills/workspace/SKILL.md"


def test_loader_rejects_magipi_skill_symlink_outside_workspace(tmp_path) -> None:
    cwd = tmp_path / "repo"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text(
        "---\nname: outside\ndescription: Outside skill.\n---\nBody\n",
        encoding="utf-8",
    )
    skill_dir = cwd / ".magipi" / "skills"
    skill_dir.mkdir(parents=True)
    (skill_dir / "outside").symlink_to(outside, target_is_directory=True)

    loader = ResourceLoader(cwd=cwd, agent_dir=tmp_path / "agent")
    asyncio.run(loader.reload())

    assert _workspace_skills(loader) == []
    assert any("resolves outside workspace .magipi/skills" in d.message for d in loader.snapshot.diagnostics)


def test_loader_ignores_settings_skill_paths(tmp_path) -> None:
    cwd = tmp_path / "repo"
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    (explicit / "SKILL.md").write_text(
        "---\nname: explicit\ndescription: Explicit skill.\n---\nBody\n",
        encoding="utf-8",
    )
    (cwd / ".magipi").mkdir(parents=True)
    (cwd / ".magipi" / "settings.json").write_text(
        json.dumps({"resources": {"skills": [str(explicit)]}}),
        encoding="utf-8",
    )

    loader = ResourceLoader(cwd=cwd, agent_dir=tmp_path / "agent", explicit_skills=(explicit,))
    asyncio.run(loader.reload())

    assert _workspace_skills(loader) == []
    messages = [diagnostic.message for diagnostic in loader.snapshot.diagnostics]
    assert any("resources.skills is ignored" in message for message in messages)
    assert any("explicit skill paths are ignored" in message for message in messages)
