from __future__ import annotations

import subprocess
from pathlib import Path

from cli.core.taskrun_workspace_state import workspace_state


def test_workspace_state_rejects_option_like_workspace_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_run(*_args, **_kwargs):
        raise AssertionError("git must not run for option-like workspace roots")

    monkeypatch.setattr(subprocess, "run", fail_run)

    state = workspace_state("-c.safe.directory=*", tmp_path)

    assert state["git"] == {"status": "unknown"}


def test_workspace_state_uses_git_path_separator(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    state = workspace_state(str(tmp_path), tmp_path / ".magipi")

    assert state["git"] == {"status": "clean", "changed_tracked_paths": 0}
    assert calls[0][-1] == "--"
