"""Overlay widget regression tests."""

from __future__ import annotations

from tui.components.spinner import PI_FRAMES
from tui.overlay import CancellableLoader, Loader
from tui.stdin_buffer import KeyEvent


def test_loader_uses_spinner_frames_and_tick_behavior() -> None:
    loader = Loader("working")
    assert loader.render_body(30)[0].startswith(f"{PI_FRAMES[0]} working")
    loader.tick()
    assert loader.render_body(30)[0].startswith(f"{PI_FRAMES[1]} working")


def test_loader_allows_no_indicator_frames() -> None:
    loader = Loader("working", frames=[])
    loader.tick()
    line = loader.render_body(30)[0]
    assert line.startswith("working")
    assert not line.startswith(" ")


def test_cancellable_loader_renders_cancel_hint_and_esc_closes() -> None:
    calls: list[str] = []
    loader = CancellableLoader("working", on_cancel=lambda: calls.append("cancel"))
    assert "Esc to cancel" in loader.render_body(50)[0]
    loader.handle_input(KeyEvent("Esc", raw="\x1b"))
    assert calls == ["cancel"]
    assert loader.visible is False
