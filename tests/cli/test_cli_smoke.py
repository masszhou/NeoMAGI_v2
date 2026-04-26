"""Subprocess-level CLI smoke tests.

These run ``python -m cli`` in a fresh interpreter so we exercise the real
``__main__`` entry, argv parsing, and (for ``--playback``) the lifecycle +
threaded harness path. They catch regressions that pure unit tests miss —
e.g. an entry-point typo, a missing ``[project.scripts]`` mapping, or the
``--playback`` hang the previous review caught.

Each test caps with a generous timeout so a future hang would surface as a
``subprocess.TimeoutExpired`` instead of stalling CI indefinitely.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "pi_compat"


def _run_cli(*args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m cli`` with the project's ``src`` layout on PYTHONPATH.

    We deliberately avoid ``uv run`` here so the smoke is closer to what an
    installed wheel would look like — fewer moving parts, fewer reasons to
    skip in restricted environments."""

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return subprocess.run(  # noqa: S603 — args are constants, not user input
        [sys.executable, "-m", "cli", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def test_help_lists_all_three_p1_flags() -> None:
    result = _run_cli("--help")
    assert result.returncode == 0
    out = result.stdout
    assert "--playback" in out
    assert "--print" in out
    assert "--help" in out


def test_print_returns_stub_message() -> None:
    result = _run_cli("--print", "hello")
    # `--print` is M1 stub; exit code is 0, message goes to stderr.
    assert result.returncode == 0
    assert "not implemented in M1" in result.stderr
    assert "hello" in result.stderr


def test_playback_assistant_text_delta_exits_within_timeout() -> None:
    """Regression for the previous --playback hang. The fixture's combined
    delays are well under 1s; an 8s subprocess timeout catches any future
    regression that would let the loop run forever."""

    fixture = FIXTURE_ROOT / "assistant_text_delta"
    assert (fixture / "events.jsonl").is_file()
    try:
        result = _run_cli("--playback", str(fixture), timeout=8.0)
    except subprocess.TimeoutExpired:
        pytest.fail(
            "neomagi --playback did not exit within 8s — playback hang regressed"
        )
    assert result.returncode == 0


def test_playback_unknown_fixture_does_not_hang() -> None:
    """``--playback`` against a missing directory must surface an error and
    still exit cleanly (the controller's notification + exit path)."""

    try:
        result = _run_cli(
            "--playback", str(REPO_ROOT / "no-such-fixture"), timeout=8.0
        )
    except subprocess.TimeoutExpired:
        pytest.fail("neomagi --playback hung on missing fixture")
    # Exit code should be 0 (graceful) — the harness pushes a notification
    # and bows out via controller.exit().
    assert result.returncode == 0
