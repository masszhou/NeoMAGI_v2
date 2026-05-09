"""Skill env grant resolution and gate logic shared across submit/queued/read entries.

This module is the single source of truth for:

- structured failure objects (`SkillEnvGrantFailure`) so audit fields don't depend on error
  message parsing;
- envFile / allow resolution (`resolve_skill_env_grant`) shared by `/skill:<name>` expansion
  and agent-initiated `read` activation;
- the gate decision (`decide_skill_env_gate_state`) shared by submit / steer / follow_up /
  read-driven entries to enforce single-grant-per-run with first-match-wins;
- separate user-visible formatters for missing-env failures and active-skill conflicts so
  the two semantically different errors stay distinct.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cli.core.settings import LoadedSettings
from cli.tools.definitions import SkillEnvGrant

SkillEnvGateState = Literal["no_op", "set", "idempotent", "conflict"]


@dataclass(frozen=True, slots=True)
class SkillEnvGrantFailure:
    reason: Literal["missing_env_file", "missing_allow_var"]
    source: str
    missing_names: tuple[str, ...] = ()


def decide_skill_env_gate_state(
    current: SkillEnvGrant | None,
    candidate: SkillEnvGrant | None,
) -> SkillEnvGateState:
    if candidate is None:
        return "no_op"
    if current is None:
        return "set"
    if current.skill_name == candidate.skill_name:
        return "idempotent"
    return "conflict"


def resolve_skill_env_grant(
    skill_name: str,
    *,
    loaded_settings: LoadedSettings,
    cwd: Path,
    agent_dir: Path,
) -> tuple[SkillEnvGrant | None, SkillEnvGrantFailure | None]:
    config = loaded_settings.settings.resources.skill_env.get(skill_name)
    if config is None:
        return None, None
    base_dir = (
        cwd
        if _raw_skill_env_config(loaded_settings.project_raw, skill_name) is not None
        else agent_dir
    )
    env_file = config.env_file
    source_path = _resolve_env_file(env_file, base_dir)
    if not source_path.is_file():
        return None, SkillEnvGrantFailure(reason="missing_env_file", source=env_file)
    env_values = _read_env_file(source_path)
    missing = tuple(name for name in config.allow if name not in env_values)
    if missing:
        return None, SkillEnvGrantFailure(
            reason="missing_allow_var",
            source=env_file,
            missing_names=missing,
        )
    grant_values = {name: env_values[name] for name in config.allow}
    if not grant_values:
        return None, None
    return SkillEnvGrant(skill_name=skill_name, env=grant_values, source=env_file), None


def format_skill_env_setup_error(skill_name: str, failure: SkillEnvGrantFailure) -> str:
    if failure.reason == "missing_env_file":
        return f"skill env for {skill_name!r} references missing envFile {failure.source!r}"
    names = ", ".join(failure.missing_names)
    return (
        f"skill env for {skill_name!r} is missing allowed variable(s) {names} "
        f"in envFile {failure.source!r}"
    )


def format_skill_env_conflict_error(active_skill: str, attempted_skill: str) -> str:
    return (
        f"skill env grant already active for {active_skill!r}; queued "
        f"/skill:{attempted_skill} did not activate. Wait for the current run to "
        f"settle or abort it before retrying."
    )


def _raw_skill_env_config(raw: dict[str, Any], skill_name: str) -> dict[str, Any] | None:
    resources = raw.get("resources")
    if not isinstance(resources, dict):
        return None
    skill_env = resources.get("skillEnv") or resources.get("skill_env")
    if not isinstance(skill_env, dict):
        return None
    config = skill_env.get(skill_name)
    return config if isinstance(config, dict) else None


def _resolve_env_file(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve(strict=False)
    return (base_dir / path).resolve(strict=False)


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").strip()
        key, sep, value = stripped.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        values[key] = _unquote_env_value(value.strip())
    return values


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


__all__ = [
    "SkillEnvGateState",
    "SkillEnvGrantFailure",
    "decide_skill_env_gate_state",
    "format_skill_env_conflict_error",
    "format_skill_env_setup_error",
    "resolve_skill_env_grant",
]
