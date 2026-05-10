from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.core.settings import SettingsManager
from cli.interactive.skill_env_grant import (
    SkillEnvGrantFailure,
    decide_skill_env_gate_state,
    format_skill_env_conflict_error,
    format_skill_env_setup_error,
    resolve_skill_env_grant,
)
from cli.tools.definitions import SkillEnvGrant


def _grant(skill: str, env: dict[str, str] | None = None) -> SkillEnvGrant:
    return SkillEnvGrant(skill_name=skill, env=env or {"K": "v"}, source=".env.x")


def test_decide_gate_state_no_op_when_candidate_is_none() -> None:
    assert decide_skill_env_gate_state(None, None) == "no_op"
    assert decide_skill_env_gate_state(_grant("a"), None) == "no_op"


def test_decide_gate_state_set_when_no_active() -> None:
    assert decide_skill_env_gate_state(None, _grant("brave-search")) == "set"


def test_decide_gate_state_idempotent_when_same_skill() -> None:
    active = _grant("brave-search")
    candidate = _grant("brave-search", env={"OTHER": "value"})
    assert decide_skill_env_gate_state(active, candidate) == "idempotent"


def test_decide_gate_state_conflict_when_different_skill() -> None:
    assert decide_skill_env_gate_state(_grant("brave-search"), _grant("gmcli")) == "conflict"


def _write_settings(cwd: Path, payload: dict) -> None:
    (cwd / ".magipi").mkdir(parents=True, exist_ok=True)
    (cwd / ".magipi" / "settings.json").write_text(json.dumps(payload), encoding="utf-8")


def _resolver_inputs(cwd: Path, agent_dir: Path) -> tuple[Path, Path, object]:
    manager = SettingsManager(cwd=cwd, agent_dir=agent_dir)
    loaded = manager.load()
    return cwd, agent_dir, loaded


def test_resolve_grant_success_for_project_settings(tmp_path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    _write_settings(
        tmp_path,
        {
            "resources": {
                "skillEnv": {
                    "brave-search": {
                        "envFile": ".magipi/secrets/brave-search.env",
                        "allow": ["BRAVE_API_KEY"],
                    }
                }
            }
        },
    )
    (tmp_path / ".magipi" / "secrets").mkdir(parents=True)
    (tmp_path / ".magipi" / "secrets" / "brave-search.env").write_text(
        "BRAVE_API_KEY=FAKE_VALUE\n",
        encoding="utf-8",
    )
    cwd, agent_dir, loaded = _resolver_inputs(tmp_path, agent_dir)

    grant, failure = resolve_skill_env_grant(
        "brave-search", loaded_settings=loaded, cwd=cwd, agent_dir=agent_dir
    )

    assert failure is None
    assert grant is not None
    assert grant.skill_name == "brave-search"
    assert grant.env["BRAVE_API_KEY"] == "FAKE_VALUE"
    assert grant.source == ".magipi/secrets/brave-search.env"


def test_resolve_grant_returns_none_when_skillenv_unconfigured(tmp_path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    _write_settings(tmp_path, {})
    cwd, agent_dir, loaded = _resolver_inputs(tmp_path, agent_dir)

    grant, failure = resolve_skill_env_grant(
        "brave-search", loaded_settings=loaded, cwd=cwd, agent_dir=agent_dir
    )

    assert grant is None
    assert failure is None


def test_resolve_grant_reports_missing_env_file(tmp_path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    _write_settings(
        tmp_path,
        {
            "resources": {
                "skillEnv": {
                    "brave-search": {"envFile": ".env.brave", "allow": ["BRAVE_API_KEY"]}
                }
            }
        },
    )
    cwd, agent_dir, loaded = _resolver_inputs(tmp_path, agent_dir)

    grant, failure = resolve_skill_env_grant(
        "brave-search", loaded_settings=loaded, cwd=cwd, agent_dir=agent_dir
    )

    assert grant is None
    assert failure == SkillEnvGrantFailure(reason="missing_env_file", source=".env.brave")


def test_resolve_grant_reports_missing_allow_var(tmp_path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    _write_settings(
        tmp_path,
        {
            "resources": {
                "skillEnv": {
                    "brave-search": {
                        "envFile": ".env.brave",
                        "allow": ["BRAVE_API_KEY", "EXTRA_TOKEN"],
                    }
                }
            }
        },
    )
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_VALUE\n", encoding="utf-8")
    cwd, agent_dir, loaded = _resolver_inputs(tmp_path, agent_dir)

    grant, failure = resolve_skill_env_grant(
        "brave-search", loaded_settings=loaded, cwd=cwd, agent_dir=agent_dir
    )

    assert grant is None
    assert failure is not None
    assert failure.reason == "missing_allow_var"
    assert failure.source == ".env.brave"
    assert failure.missing_names == ("EXTRA_TOKEN",)


def test_resolve_grant_ignores_global_skill_env(tmp_path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "settings.json").write_text(
        json.dumps(
            {
                "resources": {
                    "skillEnv": {
                        "brave-search": {"envFile": ".env.brave", "allow": ["BRAVE_API_KEY"]}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / ".env.brave").write_text("BRAVE_API_KEY=FAKE_VALUE\n", encoding="utf-8")
    cwd, agent_dir, loaded = _resolver_inputs(tmp_path, agent_dir)

    grant, failure = resolve_skill_env_grant(
        "brave-search", loaded_settings=loaded, cwd=cwd, agent_dir=agent_dir
    )

    assert failure is None
    assert grant is None


def test_resolve_grant_rejects_low_quality_allowed_value(tmp_path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    _write_settings(
        tmp_path,
        {
            "resources": {
                "skillEnv": {
                    "brave-search": {"envFile": ".env.brave", "allow": ["BRAVE_API_KEY"]}
                }
            }
        },
    )
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=token\n", encoding="utf-8")
    cwd, agent_dir, loaded = _resolver_inputs(tmp_path, agent_dir)

    grant, failure = resolve_skill_env_grant(
        "brave-search", loaded_settings=loaded, cwd=cwd, agent_dir=agent_dir
    )

    assert grant is None
    assert failure is not None
    assert failure.reason == "low_quality_value"
    assert failure.missing_names == ("BRAVE_API_KEY",)


def test_format_setup_error_missing_env_file_matches_legacy_string() -> None:
    failure = SkillEnvGrantFailure(reason="missing_env_file", source=".env.brave")

    message = format_skill_env_setup_error("brave-search", failure)

    assert message == "skill env for 'brave-search' references missing envFile '.env.brave'"


def test_format_setup_error_missing_allow_var_matches_legacy_string() -> None:
    failure = SkillEnvGrantFailure(
        reason="missing_allow_var",
        source=".env.brave",
        missing_names=("BRAVE_API_KEY", "OTHER"),
    )

    message = format_skill_env_setup_error("brave-search", failure)

    assert message == (
        "skill env for 'brave-search' is missing allowed variable(s) "
        "BRAVE_API_KEY, OTHER in envFile '.env.brave'"
    )


def test_format_conflict_error_mentions_both_skills() -> None:
    message = format_skill_env_conflict_error("gmcli", "brave-search")

    assert "gmcli" in message
    assert "brave-search" in message
    assert "already active" in message


def test_setup_and_conflict_formatters_produce_distinct_text() -> None:
    setup = format_skill_env_setup_error(
        "brave-search",
        SkillEnvGrantFailure(reason="missing_env_file", source=".env.brave"),
    )
    conflict = format_skill_env_conflict_error("gmcli", "brave-search")

    assert setup != conflict
    assert "missing envFile" in setup
    assert "missing envFile" not in conflict
    assert "already active" in conflict
    assert "already active" not in setup


def test_format_setup_error_does_not_leak_env_values() -> None:
    failure = SkillEnvGrantFailure(reason="missing_env_file", source=".env.brave")

    message = format_skill_env_setup_error("brave-search", failure)

    assert "FAKE_VALUE" not in message
    assert "API_KEY=" not in message


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    """Keep XDG-derived defaults inside each test when a default manager is used."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "_xdg"))
