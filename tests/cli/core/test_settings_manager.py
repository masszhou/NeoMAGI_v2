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
                        "compat": {
                            "apiKeyEnv": "LOCAL_AI_KEY",
                            "apiKeyHeader": "X-API-Key",
                        },
                        "headers": {"Authorization": "Bearer secret"},
                        "models": [],
                    }
                },
                "compaction": {"refreshOnInput": True},
                "retry": {"refresh": {"maxRetries": 2}},
            }
        ),
        encoding="utf-8",
    )

    loaded = SettingsManager(cwd=cwd, agent_dir=agent_dir).load()

    assert "local" in loaded.settings.providers
    assert loaded.settings.providers["local"].compat == {
        "apiKeyEnv": "LOCAL_AI_KEY",
        "apiKeyHeader": "X-API-Key",
    }
    assert loaded.settings.providers["local"].headers == {}
    assert loaded.settings.compaction == {"refreshOnInput": True}
    assert loaded.settings.retry == {"refresh": {"maxRetries": 2}}
    assert any(diagnostic.field.endswith("Authorization") for diagnostic in loaded.diagnostics)
    assert not any(diagnostic.field.endswith("apiKeyEnv") for diagnostic in loaded.diagnostics)


def test_project_skill_env_settings_keep_references_but_strip_raw_secret(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    (cwd / ".pi").mkdir(parents=True)
    agent_dir.mkdir()
    (cwd / ".pi" / "settings.json").write_text(
        json.dumps(
            {
                "resources": {
                    "skillEnv": {
                        "brave-search": {
                            "envFile": ".env.brave",
                            "allow": ["BRAVE_API_KEY"],
                        }
                    }
                },
                "apiKey": "sk-project-secret",
            }
        ),
        encoding="utf-8",
    )

    loaded = SettingsManager(cwd=cwd, agent_dir=agent_dir).load()

    assert loaded.settings.resources.skill_env["brave-search"].env_file == ".env.brave"
    assert loaded.settings.resources.skill_env["brave-search"].allow == ["BRAVE_API_KEY"]
    assert any(diagnostic.field == "apiKey" for diagnostic in loaded.diagnostics)


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


def test_resource_settings_use_product_merge_semantics(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    (cwd / ".pi").mkdir(parents=True)
    agent_dir.mkdir()
    (agent_dir / "settings.json").write_text(
        json.dumps(
            {
                "resources": {
                    "extensions": ["global.py"],
                    "prompts": ["global-prompts"],
                    "enableSkillCommands": False,
                    "nested": {"a": 1},
                }
            }
        ),
        encoding="utf-8",
    )
    (cwd / ".pi" / "settings.json").write_text(
        json.dumps(
            {
                "resources": {
                    "prompts": ["project-prompts"],
                    "nested": {"b": 2},
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = load_resource_settings(cwd, agent_dir=agent_dir)

    assert loaded.settings.extensions == ("global.py",)
    assert loaded.settings.prompts == ("project-prompts",)
    assert loaded.settings.enable_skill_commands is False
    assert loaded.settings.skill_env == {}
    assert loaded.settings.extras == {"nested": {"a": 1, "b": 2}}


def test_resource_settings_projects_skill_env(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    (cwd / ".pi").mkdir(parents=True)
    agent_dir.mkdir()
    (cwd / ".pi" / "settings.json").write_text(
        json.dumps(
            {
                "resources": {
                    "skillEnv": {
                        "brave-search": {
                            "envFile": ".env.brave",
                            "allow": ["BRAVE_API_KEY"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = load_resource_settings(cwd, agent_dir=agent_dir)

    assert loaded.settings.skill_env == {
        "brave-search": {
            "envFile": ".env.brave",
            "allow": ["BRAVE_API_KEY"],
        }
    }
