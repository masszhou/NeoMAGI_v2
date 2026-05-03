from __future__ import annotations

import asyncio

from cli.resources.loader import ResourceExtensionPaths, ResourceLoader


def test_loader_discovers_project_resources_and_context_order(tmp_path) -> None:
    agent_dir = tmp_path / "home" / ".pi" / "agent"
    cwd = tmp_path / "repo" / "sub"
    (cwd / ".pi" / "prompts").mkdir(parents=True)
    (cwd / ".pi" / "skills" / "test-skill").mkdir(parents=True)
    (cwd / ".pi" / "extensions").mkdir(parents=True)
    agent_dir.mkdir(parents=True)
    (tmp_path / "repo").mkdir(exist_ok=True)
    (agent_dir / "AGENTS.md").write_text("global context", encoding="utf-8")
    (tmp_path / "repo" / "AGENTS.md").write_text("repo context", encoding="utf-8")
    (cwd / "CLAUDE.md").write_text("cwd context", encoding="utf-8")
    (cwd / ".pi" / "prompts" / "review.md").write_text("Review $1", encoding="utf-8")
    (cwd / ".pi" / "skills" / "test-skill" / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Test skill.\n---\nBody\n",
        encoding="utf-8",
    )
    (cwd / ".pi" / "extensions" / "demo.py").write_text("def setup(api): pass\n", encoding="utf-8")

    loader = ResourceLoader(cwd=cwd, agent_dir=agent_dir)
    asyncio.run(loader.reload())

    assert [prompt.name for prompt in loader.get_prompts()] == ["review"]
    assert [skill.name for skill in loader.get_skills()] == ["test-skill"]
    assert [extension.name for extension in loader.get_extensions()] == ["demo"]
    assert [file.content for file in loader.get_context_files()] == [
        "global context",
        "repo context",
        "cwd context",
    ]


def test_loader_extend_resources_is_idempotent_and_reports_collision(tmp_path) -> None:
    cwd = tmp_path / "repo"
    agent_dir = tmp_path / "home" / ".pi" / "agent"
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

    assert [skill.description for skill in loader.get_skills()] == ["First."]
    assert any(diagnostic.type == "collision" and diagnostic.name == "same" for diagnostic in loader.snapshot.diagnostics)
