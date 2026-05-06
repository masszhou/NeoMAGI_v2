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

from cli.__main__ import _resolve_render_mode
from cli.cli_args import CliOptions

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SRC_ROOT = REPO_ROOT / "packages" / "neomagi_pi" / "src"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "pi_compat"


def _run_cli(*args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m cli`` with the package source layout on PYTHONPATH.

    We deliberately avoid ``uv run`` here so the smoke is closer to what an
    installed wheel would look like — fewer moving parts, fewer reasons to
    skip in restricted environments."""

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PACKAGE_SRC_ROOT), env.get("PYTHONPATH", "")]
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


def _run_console(
    *args: str,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — args are constants, not user input
        ["magipi", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def _opts(
    *,
    playback: Path | None,
    tui_render_mode: str | None,
) -> CliOptions:
    return CliOptions(
        playback=playback,
        print_only=False,
        help=False,
        print_message=None,
        model_ref="faux/faux-1",
        thinking_level="off",
        cache_retention=None,
        tui_render_mode=tui_render_mode,  # type: ignore[arg-type]
    )


def test_help_lists_runtime_and_fixture_flags() -> None:
    result = _run_cli("--help")
    assert result.returncode == 0
    out = result.stdout
    assert "--playback" in out
    assert "--print" in out
    assert "--model" in out
    assert "--thinking-level" in out
    assert "--cache-retention" in out
    assert "--tui-render-mode" in out
    assert "--help" in out


def test_magipi_help_lists_runtime_and_fixture_flags() -> None:
    result = _run_console("--help")
    assert result.returncode == 0
    assert "--playback" in result.stdout
    assert "--model" in result.stdout


def test_default_tui_render_mode_is_command_for_runtime() -> None:
    opts = _opts(playback=None, tui_render_mode=None)
    assert _resolve_render_mode(opts) == "command"


def test_default_tui_render_mode_is_canvas_for_playback() -> None:
    opts = _opts(playback=FIXTURE_ROOT / "assistant_text_delta", tui_render_mode=None)
    assert _resolve_render_mode(opts) == "canvas"


def test_explicit_tui_render_mode_wins_for_playback() -> None:
    opts = _opts(
        playback=FIXTURE_ROOT / "assistant_text_delta",
        tui_render_mode="command",
    )
    assert _resolve_render_mode(opts) == "command"


def test_print_returns_stub_message() -> None:
    result = _run_cli("--print", "hello")
    # `--print` is M1 stub; exit code is 0, message goes to stderr.
    assert result.returncode == 0
    assert "not implemented in M1" in result.stderr
    assert "hello" in result.stderr


def test_print_rejects_explicit_runtime_flags() -> None:
    result = _run_cli("--print", "hello", "--model", "faux/faux-1")
    assert result.returncode == 2
    assert "cannot be combined" in result.stderr


def test_runtime_flag_abbreviations_are_rejected() -> None:
    result = _run_cli("--print", "hello", "--mod", "faux/faux-1")
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_unknown_model_fails_before_interactive_tui() -> None:
    result = _run_cli("--model", "missing/nope", timeout=8.0)
    assert result.returncode == 2
    assert "unknown model" in result.stderr


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


def test_magipi_playback_assistant_text_delta_exits_within_timeout() -> None:
    fixture = FIXTURE_ROOT / "assistant_text_delta"
    assert (fixture / "events.jsonl").is_file()
    try:
        result = _run_console("--playback", str(fixture), timeout=8.0)
    except subprocess.TimeoutExpired:
        pytest.fail("magipi --playback did not exit within 8s")
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
