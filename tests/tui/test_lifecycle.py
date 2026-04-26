"""W1 lifecycle tests.

Heavy stty-level checks need a real PTY, which CI doesn't have. These
tests cover the abstraction layer:

- ``lifecycle()`` calls ``enter`` / ``exit`` even on exception.
- ``atexit`` callback runs only once (idempotency).
- SIGINT inside the loop flips ``app.exit``.
"""

from __future__ import annotations

import io
import signal
import time

import pytest

from tui.app import TUIApp
from tui.lifecycle import lifecycle
from tui.renderer import Renderer
from tui.terminal import TerminalSession


def _make_app() -> TUIApp:
    out = io.StringIO()
    return TUIApp(
        terminal=TerminalSession(out_stream=out),
        renderer=Renderer(out_stream=out),
        out_stream=out,
    )


def test_lifecycle_runs_exit_on_normal_path() -> None:
    app = _make_app()
    with lifecycle(app):
        app.exit()
    assert app.terminal.is_active is False


def test_lifecycle_runs_exit_on_exception() -> None:
    app = _make_app()
    with pytest.raises(RuntimeError):
        with lifecycle(app):
            raise RuntimeError("boom")
    assert app.terminal.is_active is False


def test_lifecycle_idempotent_cleanup() -> None:
    app = _make_app()
    counter = {"calls": 0}

    def cb() -> None:
        counter["calls"] += 1

    with lifecycle(app, on_exit=cb):
        app.exit()
    assert counter["calls"] == 1


def test_sigint_inside_lifecycle_calls_app_exit() -> None:
    """SIGINT delivered while the lifecycle is active must flip ``_running``
    to False — that's how the run loop tears down. We start with
    ``_running=True`` so a False reading after the signal proves the
    lifecycle handler actually ran (rather than just observing the
    constructor default)."""

    if not hasattr(signal, "SIGINT"):
        pytest.skip("SIGINT not available")

    app = _make_app()
    with lifecycle(app):
        app._running = True  # noqa: SLF001 — simulate an active run loop
        signal.raise_signal(signal.SIGINT)
        # Python invokes signal handlers at the next bytecode checkpoint.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and app._running:  # noqa: SLF001
            time.sleep(0.01)
        assert app._running is False, (  # noqa: SLF001
            "SIGINT did not propagate through the lifecycle handler"
        )
