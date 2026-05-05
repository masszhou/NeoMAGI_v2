from __future__ import annotations

import json
from pathlib import Path

from cli.core.settings import SettingsManager
from cli.resources.settings import load_resource_settings


def test_settings_manager_merges_global_project_and_preserves_unknown_on_save(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / "home" / ".pi" / "agent"
    cwd = tmp_path / "repo"
    agent_dir.mkdir(parents=True)
    (cwd / ".pi").mkdir(parents=True)
    (agent_dir / "settings.json").write_text(
        json.dumps(
            {
                "model": {"provider": "openai", "id": "gpt-5.4"},
                "resources": {"prompts": ["global-prompts"]},
                "piFuture": {"keep": True},
            }
        ),
        encoding="utf-8",
    )
    (cwd / ".pi" / "settings.json").write_text(
        json.dumps(
            {
                "model": {"id": "gpt-4o-mini"},
                "resources": {"skills": ["project-skills"]},
                "projectOnly": 1,
            }
        ),
        encoding="utf-8",
    )

    manager = SettingsManager(cwd=cwd, agent_dir=agent_dir)
    loaded = manager.load()

    assert loaded.settings.model.provider == "openai"
    assert loaded.settings.model.id == "gpt-4o-mini"
    assert loaded.settings.resources.prompts == ["global-prompts"]
    assert loaded.settings.resources.skills == ["project-skills"]

    manager.update_value("project", "model.thinkingLevel", "high")
    saved = json.loads((cwd / ".pi" / "settings.json").read_text(encoding="utf-8"))
    assert saved["projectOnly"] == 1
    assert saved["model"]["thinkingLevel"] == "high"


def test_project_secret_like_settings_are_diagnostic_only(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    (cwd / ".pi").mkdir(parents=True)
    agent_dir.mkdir()
    (cwd / ".pi" / "settings.json").write_text(
        json.dumps(
            {
                "providers": {
                    "local": {
                        "headers": {"Authorization": "Bearer secret"},
                        "models": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = SettingsManager(cwd=cwd, agent_dir=agent_dir).load()

    assert "local" in loaded.settings.providers
    assert loaded.settings.providers["local"].headers == {}
    assert any(diagnostic.field.endswith("Authorization") for diagnostic in loaded.diagnostics)


def test_resource_settings_project_through_product_schema(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    (cwd / ".pi").mkdir(parents=True)
    agent_dir.mkdir()
    (cwd / ".pi" / "settings.json").write_text(
        json.dumps(
            {"resources": {"enableSkillCommands": False, "prompts": ["./prompts"]}}
        ),
        encoding="utf-8",
    )

    loaded = load_resource_settings(cwd, agent_dir=agent_dir)

    assert loaded.settings.enable_skill_commands is False
    assert loaded.settings.prompts == ("./prompts",)
