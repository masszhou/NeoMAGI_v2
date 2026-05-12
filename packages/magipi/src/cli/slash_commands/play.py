"""``/play <fixture>`` — M1-only entry into :class:`PlaybackHarness`.

Two flavors:

- ``/play`` (no args) → :class:`Selector` overlay listing the registered
  ``play_targets`` (typically ``tests/fixtures/pi_compat/`` subdirs that
  ship an ``events.jsonl``).
- ``/play <name>`` → run that fixture directly.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tui.overlay import Selector, SelectorItem

from .registry import SlashCommandContext

FixtureRoot = Path("tests/fixtures/pi_compat")


def make_play_handler(fixture_names: list[str]) -> Callable[[SlashCommandContext], None]:
    def handler(ctx: SlashCommandContext) -> None:
        if ctx.args:
            _run_fixture(ctx, ctx.args[0])
            return
        items = [SelectorItem(label=name, value=name) for name in fixture_names]
        if not items:
            ctx.controller.status.push_notification(
                "no playable fixtures registered", level="warn"
            )
            return
        overlay = Selector(
            "Pick a fixture to /play",
            items,
            on_select=lambda chosen: _run_fixture(ctx, chosen.value),
        )
        ctx.controller.open_overlay(overlay)

    return handler


def _run_fixture(ctx: SlashCommandContext, name: str) -> None:
    from cli.interactive.playback import PlaybackHarness

    fixture_dir = FixtureRoot / name
    if not fixture_dir.is_dir():
        ctx.controller.status.push_notification(
            f"fixture {name!r} not found", level="error"
        )
        return
    try:
        harness = PlaybackHarness(fixture_dir, controller=ctx.controller)
        harness.play_sync()
        ctx.controller.status.push_notification(
            f"playback complete: {name}", level="info"
        )
    except Exception as exc:
        ctx.controller.status.push_notification(
            f"playback failed: {exc}", level="error", ttl_seconds=8.0
        )


__all__ = ["FixtureRoot", "make_play_handler"]
