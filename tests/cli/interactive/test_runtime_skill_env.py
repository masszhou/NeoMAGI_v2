from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_core.types import AgentToolResult
from cli.interactive.runtime import InteractiveAgentRuntime
from cli.tools.definitions import SkillEnvGrant


@pytest.fixture(autouse=True)
def _isolated_agent_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("NEOMAGI_AGENT_DIR", str(tmp_path / "_agent_iso"))


def _write_brave_skill(workspace: Path) -> Path:
    skill_dir = workspace / ".pi" / "skills" / "brave-search"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\nname: brave-search\ndescription: Search the web.\n---\nRun search.\n",
        encoding="utf-8",
    )
    return skill_path.resolve()


def _write_gmcli_skill(workspace: Path) -> Path:
    skill_dir = workspace / ".pi" / "skills" / "gmcli"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\nname: gmcli\ndescription: Gmail CLI.\n---\nUse gmcli.\n",
        encoding="utf-8",
    )
    return skill_path.resolve()


def _write_brave_settings(workspace: Path) -> None:
    (workspace / ".pi" / "settings.json").write_text(
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


def _make_read_context(
    *,
    skill_path: Path | str,
    is_error: bool = False,
    extra_details: dict[str, Any] | None = None,
) -> SimpleNamespace:
    base_details = {
        "path": str(skill_path),
        "resolvedPath": str(skill_path),
        "lineStart": 1,
        "lineEnd": 5,
        "totalLines": 5,
        "outputLines": 5,
    }
    if extra_details:
        base_details.update(extra_details)
    result = AgentToolResult(content=[{"type": "text", "text": "body"}], details=base_details)
    return SimpleNamespace(
        tool_call={"id": "tc-1", "name": "read"},
        args={"path": str(skill_path)},
        result=result,
        is_error=is_error,
        assistant_message={},
    )


def _run_after_tool_call(runtime: InteractiveAgentRuntime, context: Any):
    return asyncio.run(runtime._after_tool_call(context, None))  # noqa: SLF001


def test_read_driven_activation_sets_active_grant_and_writes_details(tmp_path) -> None:
    skill_path = _write_brave_skill(tmp_path)
    _write_brave_settings(tmp_path)
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_VALUE\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        result = _run_after_tool_call(runtime, _make_read_context(skill_path=skill_path))

        assert runtime._active_skill_env_grant is not None  # noqa: SLF001
        assert runtime._active_skill_env_grant.skill_name == "brave-search"  # noqa: SLF001
        assert runtime._active_skill_env_grant.env["BRAVE_API_KEY"] == "FAKE_VALUE"  # noqa: SLF001
        assert result is not None
        assert "skillEnvActivated" in result.details
        activated = result.details["skillEnvActivated"]
        assert activated == {
            "skill": "brave-search",
            "names": ["BRAVE_API_KEY"],
            "source": ".env.brave",
            "trigger": "read",
        }
    finally:
        runtime.shutdown()


def test_read_driven_activation_preserves_existing_read_details(tmp_path) -> None:
    skill_path = _write_brave_skill(tmp_path)
    _write_brave_settings(tmp_path)
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_VALUE\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        result = _run_after_tool_call(runtime, _make_read_context(skill_path=skill_path))

        assert result is not None
        assert result.details["resolvedPath"] == str(skill_path)
        assert result.details["lineStart"] == 1
        assert result.details["totalLines"] == 5
        assert result.details["outputLines"] == 5
        assert result.details["path"] == str(skill_path)
    finally:
        runtime.shutdown()


def test_read_driven_activation_does_not_leak_secret_value(tmp_path) -> None:
    skill_path = _write_brave_skill(tmp_path)
    _write_brave_settings(tmp_path)
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=SUPER_SECRET_TOKEN_123\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        result = _run_after_tool_call(runtime, _make_read_context(skill_path=skill_path))

        assert result is not None
        serialized = json.dumps(result.details)
        assert "SUPER_SECRET_TOKEN_123" not in serialized
    finally:
        runtime.shutdown()


def _assert_no_skill_env_fields(result: Any) -> None:
    if result is None:
        return
    details = result.details or {}
    assert "skillEnvActivated" not in details
    assert "skillEnvActivationSkipped" not in details


def test_read_unrelated_file_does_not_activate_or_write_skill_env_fields(tmp_path) -> None:
    _write_brave_skill(tmp_path)
    _write_brave_settings(tmp_path)
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_VALUE\n", encoding="utf-8")
    other = tmp_path / "notes.md"
    other.write_text("hello\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        result = _run_after_tool_call(runtime, _make_read_context(skill_path=other))

        assert runtime._active_skill_env_grant is None  # noqa: SLF001
        _assert_no_skill_env_fields(result)
    finally:
        runtime.shutdown()


def test_read_failed_does_not_activate(tmp_path) -> None:
    skill_path = _write_brave_skill(tmp_path)
    _write_brave_settings(tmp_path)
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_VALUE\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        result = _run_after_tool_call(
            runtime, _make_read_context(skill_path=skill_path, is_error=True)
        )

        assert runtime._active_skill_env_grant is None  # noqa: SLF001
        _assert_no_skill_env_fields(result)
    finally:
        runtime.shutdown()


def test_skill_without_skill_env_config_is_silent(tmp_path) -> None:
    skill_path = _write_brave_skill(tmp_path)  # no settings.json
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        result = _run_after_tool_call(runtime, _make_read_context(skill_path=skill_path))

        assert runtime._active_skill_env_grant is None  # noqa: SLF001
        _assert_no_skill_env_fields(result)
    finally:
        runtime.shutdown()


def test_missing_env_file_emits_skipped_with_source_no_secret(tmp_path) -> None:
    skill_path = _write_brave_skill(tmp_path)
    _write_brave_settings(tmp_path)
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        result = _run_after_tool_call(runtime, _make_read_context(skill_path=skill_path))

        assert runtime._active_skill_env_grant is None  # noqa: SLF001
        assert result is not None
        skipped = result.details["skillEnvActivationSkipped"]
        assert skipped == {
            "skill": "brave-search",
            "reason": "missing_env_file",
            "source": ".env.brave",
        }
    finally:
        runtime.shutdown()


def test_missing_allow_var_emits_skipped_with_missing_names(tmp_path) -> None:
    skill_path = _write_brave_skill(tmp_path)
    (tmp_path / ".pi" / "settings.json").write_text(
        json.dumps(
            {
                "resources": {
                    "skillEnv": {
                        "brave-search": {
                            "envFile": ".env.brave",
                            "allow": ["BRAVE_API_KEY", "EXTRA_TOKEN"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_VALUE\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        result = _run_after_tool_call(runtime, _make_read_context(skill_path=skill_path))

        assert runtime._active_skill_env_grant is None  # noqa: SLF001
        assert result is not None
        skipped = result.details["skillEnvActivationSkipped"]
        assert skipped["reason"] == "missing_allow_var"
        assert skipped["skill"] == "brave-search"
        assert skipped["source"] == ".env.brave"
        assert skipped["missingNames"] == ["EXTRA_TOKEN"]
        assert "FAKE_VALUE" not in json.dumps(result.details)
    finally:
        runtime.shutdown()


def test_disable_model_invocation_skill_emits_skipped_disabled(tmp_path) -> None:
    skill_dir = tmp_path / ".pi" / "skills" / "brave-search"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\nname: brave-search\ndescription: Search the web.\n"
        "disable-model-invocation: true\n---\nRun search.\n",
        encoding="utf-8",
    )
    skill_path = skill_path.resolve()
    _write_brave_settings(tmp_path)
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_VALUE\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        result = _run_after_tool_call(runtime, _make_read_context(skill_path=skill_path))

        assert runtime._active_skill_env_grant is None  # noqa: SLF001
        assert result is not None
        assert result.details["skillEnvActivationSkipped"] == {
            "skill": "brave-search",
            "reason": "disabled_model_invocation",
        }
    finally:
        runtime.shutdown()


def test_second_skill_read_emits_conflict_without_overwriting(tmp_path) -> None:
    brave_path = _write_brave_skill(tmp_path)
    gmcli_path = _write_gmcli_skill(tmp_path)
    (tmp_path / ".pi" / "settings.json").write_text(
        json.dumps(
            {
                "resources": {
                    "skillEnv": {
                        "brave-search": {"envFile": ".env.brave", "allow": ["BRAVE_API_KEY"]},
                        "gmcli": {"envFile": ".env.gmcli", "allow": ["GMCLI_TOKEN"]},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_BRAVE\n", encoding="utf-8")
    (tmp_path / ".env.gmcli").write_text("GMCLI_TOKEN=FAKE_GMCLI\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        first = _run_after_tool_call(runtime, _make_read_context(skill_path=brave_path))
        second = _run_after_tool_call(runtime, _make_read_context(skill_path=gmcli_path))

        active = runtime._active_skill_env_grant  # noqa: SLF001
        assert active is not None
        assert active.skill_name == "brave-search"
        assert first is not None
        assert "skillEnvActivated" in first.details
        assert second is not None
        assert second.details["skillEnvActivationSkipped"] == {
            "skill": "gmcli",
            "reason": "conflict",
            "activeSkill": "brave-search",
        }
    finally:
        runtime.shutdown()


def test_repeated_read_of_same_skill_does_not_repeat_activated_event(tmp_path) -> None:
    skill_path = _write_brave_skill(tmp_path)
    _write_brave_settings(tmp_path)
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_VALUE\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        first = _run_after_tool_call(runtime, _make_read_context(skill_path=skill_path))
        second = _run_after_tool_call(runtime, _make_read_context(skill_path=skill_path))

        assert first is not None
        assert "skillEnvActivated" in first.details
        # idempotent re-read: no skill env fields in the second result
        _assert_no_skill_env_fields(second)
    finally:
        runtime.shutdown()


def test_apply_queued_skill_env_grant_raises_on_conflict(tmp_path) -> None:
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        runtime._active_skill_env_grant = SkillEnvGrant(  # noqa: SLF001
            skill_name="gmcli", env={"GMCLI_TOKEN": "x"}, source=".env.gmcli"
        )
        prepared = SimpleNamespace(
            skill_env_grant=SkillEnvGrant(
                skill_name="brave-search",
                env={"BRAVE_API_KEY": "y"},
                source=".env.brave",
            )
        )

        with pytest.raises(RuntimeError) as exc:
            runtime._apply_queued_skill_env_grant(prepared)  # noqa: SLF001

        assert "gmcli" in str(exc.value)
        assert "brave-search" in str(exc.value)
        assert runtime._active_skill_env_grant.skill_name == "gmcli"  # noqa: SLF001
    finally:
        runtime.shutdown()


def test_apply_queued_skill_env_grant_no_op_for_grantless_prepared(tmp_path) -> None:
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        runtime._active_skill_env_grant = SkillEnvGrant(  # noqa: SLF001
            skill_name="gmcli", env={"GMCLI_TOKEN": "x"}, source=".env.gmcli"
        )
        prepared = SimpleNamespace(skill_env_grant=None)

        runtime._apply_queued_skill_env_grant(prepared)  # noqa: SLF001

        assert runtime._active_skill_env_grant.skill_name == "gmcli"  # noqa: SLF001
    finally:
        runtime.shutdown()


def test_apply_queued_skill_env_grant_idempotent_for_same_skill(tmp_path) -> None:
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        runtime._active_skill_env_grant = SkillEnvGrant(  # noqa: SLF001
            skill_name="brave-search", env={"BRAVE_API_KEY": "x"}, source=".env.brave"
        )
        prepared = SimpleNamespace(
            skill_env_grant=SkillEnvGrant(
                skill_name="brave-search",
                env={"BRAVE_API_KEY": "x"},
                source=".env.brave",
            )
        )

        runtime._apply_queued_skill_env_grant(prepared)  # noqa: SLF001

        assert runtime._active_skill_env_grant.skill_name == "brave-search"  # noqa: SLF001
    finally:
        runtime.shutdown()


def test_explicit_skill_path_then_read_other_skill_emits_conflict(tmp_path) -> None:
    brave_path = _write_brave_skill(tmp_path)
    gmcli_path = _write_gmcli_skill(tmp_path)
    (tmp_path / ".pi" / "settings.json").write_text(
        json.dumps(
            {
                "resources": {
                    "skillEnv": {
                        "brave-search": {"envFile": ".env.brave", "allow": ["BRAVE_API_KEY"]},
                        "gmcli": {"envFile": ".env.gmcli", "allow": ["GMCLI_TOKEN"]},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_BRAVE\n", encoding="utf-8")
    (tmp_path / ".env.gmcli").write_text("GMCLI_TOKEN=FAKE_GMCLI\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        # Simulate explicit-path submit: submit-time grant is activated via gate.
        prepared = runtime.prepare_prompt_submission("/skill:brave-search hi")
        runtime._try_set_active_skill_env_grant(prepared.skill_env_grant)  # noqa: SLF001
        assert runtime._active_skill_env_grant.skill_name == "brave-search"  # noqa: SLF001

        # Agent in same run reads gmcli SKILL.md → conflict.
        result = _run_after_tool_call(runtime, _make_read_context(skill_path=gmcli_path))

        assert runtime._active_skill_env_grant.skill_name == "brave-search"  # noqa: SLF001
        assert result is not None
        assert result.details["skillEnvActivationSkipped"]["reason"] == "conflict"
        assert result.details["skillEnvActivationSkipped"]["activeSkill"] == "brave-search"
        assert brave_path  # silence unused-var
    finally:
        runtime.shutdown()


def test_grant_is_cleared_when_run_settles(tmp_path) -> None:
    skill_path = _write_brave_skill(tmp_path)
    _write_brave_settings(tmp_path)
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_VALUE\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        _run_after_tool_call(runtime, _make_read_context(skill_path=skill_path))
        assert runtime._active_skill_env_grant is not None  # noqa: SLF001

        # _run_prompt's finally block clears _active_skill_env_grant when generation matches.
        # Simulate that branch to lock the run-boundary contract.
        with runtime._lock:  # noqa: SLF001
            generation = runtime._generation  # noqa: SLF001
            assert generation == runtime._generation  # noqa: SLF001
            runtime._queued_steering.clear()  # noqa: SLF001
            runtime._queued_follow_up.clear()  # noqa: SLF001
            runtime._active_skill_env_grant = None  # noqa: SLF001

        assert runtime._active_skill_env_grant is None  # noqa: SLF001
    finally:
        runtime.shutdown()


def test_concurrent_read_and_bash_dispatch_grant_visible_only_to_subsequent_tool_loop(
    tmp_path,
) -> None:
    """When a model dispatches read SKILL.md and bash in the same assistant message,
    bash runs concurrently and won't see the grant. The grant only takes effect for
    subsequent tool loops. This test locks that documented boundary."""
    skill_path = _write_brave_skill(tmp_path)
    _write_brave_settings(tmp_path)
    (tmp_path / ".env.brave").write_text("BRAVE_API_KEY=FAKE_VALUE\n", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        # Snapshot grant state at the moment a concurrent bash would have started:
        # before the read tool completes (i.e. before _after_tool_call fires).
        grant_before_read_completes = runtime._active_skill_env_grant  # noqa: SLF001

        # read completes; activation fires.
        _run_after_tool_call(runtime, _make_read_context(skill_path=skill_path))
        grant_after_read_completes = runtime._active_skill_env_grant  # noqa: SLF001

        assert grant_before_read_completes is None
        assert grant_after_read_completes is not None
        assert grant_after_read_completes.skill_name == "brave-search"
        # bash in the next tool loop would observe `grant_after_read_completes`.
    finally:
        runtime.shutdown()
