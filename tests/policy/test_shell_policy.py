from __future__ import annotations

import os
from pathlib import Path

import pytest

from policy.sandbox import sandbox_environment
from policy.shell_policy import decide_shell_access
from policy.types import PolicyRequest


@pytest.mark.parametrize(
    "command",
    [
        "rm --recursive --force /",
        "rm --force --recursive /tmp/work",
        "A=/; rm -rf $A",
        "rm -rf ${TARGET_DIR}",
        "rm --no-preserve-root /",
    ],
)
def test_shell_policy_blocks_dangerous_rm_forms(tmp_path: Path, command: str) -> None:
    decision = _decision(tmp_path, command)

    assert decision.effect == "block"
    assert "destructive shell command" in (decision.reason or "")


def test_shell_policy_fails_closed_on_unparseable_shell(tmp_path: Path) -> None:
    decision = _decision(tmp_path, 'bash -c "unterminated')

    assert decision.effect == "block"
    assert decision.reason == "shell command cannot be parsed safely"


def test_shell_policy_blocks_root_literal(tmp_path: Path) -> None:
    decision = _decision(tmp_path, "cat /")

    assert decision.effect == "block"
    assert decision.resolved_paths["blockedPathLiteral"] == "/"


def test_bash_env_allowlist_strips_sensitive_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/Users/tester")
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("LOGNAME", "tester")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("LC_CTYPE", "en_US.UTF-8")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("TMPDIR", "/tmp")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("CUSTOM_TOKEN", "token-secret")
    monkeypatch.setenv("PASSWORD", "password-secret")
    monkeypatch.setenv("SECRET", "plain-secret")
    monkeypatch.setenv("AUTHORIZATION", "Bearer secret")
    monkeypatch.setenv("COOKIE", "cookie-secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    monkeypatch.setenv("PYENV_VERSION", "3.12")
    monkeypatch.setenv("npm_config_token", "npm-secret")

    env = sandbox_environment(tmp_path)

    assert env == {
        "PATH": "/usr/bin:/bin",
        "HOME": "/Users/tester",
        "USER": "tester",
        "LOGNAME": "tester",
        "SHELL": "/bin/zsh",
        "LANG": "en_US.UTF-8",
        "TERM": "xterm-256color",
        "LC_ALL": "en_US.UTF-8",
        "LC_CTYPE": "en_US.UTF-8",
        "TZ": "UTC",
        "TMPDIR": "/tmp",
        "PWD": str(tmp_path),
    }
    assert "SSH_AUTH_SOCK" in os.environ


def test_sandbox_environment_accepts_explicit_extra_env_without_global_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("BRAVE_API_KEY", "parent-secret")

    ordinary = sandbox_environment(tmp_path)
    granted = sandbox_environment(tmp_path, extra_env={"BRAVE_API_KEY": "granted-secret"})

    assert "BRAVE_API_KEY" not in ordinary
    assert granted["BRAVE_API_KEY"] == "granted-secret"


def _decision(tmp_path: Path, command: str):
    return decide_shell_access(
        PolicyRequest(
            toolName="bash",
            args={"command": command},
            cwd=str(tmp_path),
            actor="model",
        )
    )
