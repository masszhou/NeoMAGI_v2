"""Regressions for the P1 issues caught in the M1 review."""

from __future__ import annotations

import json
from pathlib import Path

from cli.interactive.app import InteractiveController
from cli.interactive.playback import PlaybackHarness
from tui.app import TUIApp
from tui.editor import EditorState
from tui.overlay import Selector
from tui.stdin_buffer import KeyEvent

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "pi_compat"


def _make_controller() -> tuple[TUIApp, InteractiveController]:
    app = TUIApp()
    c = InteractiveController(tui_app=app)
    c.bootstrap()
    return app, c


# -------------------------------------------------------------------- #
# P1-1: slash trigger now opens an autocomplete overlay AND keeps the  #
# slash character in the buffer so the user can keep typing.            #
# -------------------------------------------------------------------- #


def _inject(app: TUIApp, *keys: str) -> None:
    for k in keys:
        raw = k if len(k) == 1 else ""
        app.inject_input(KeyEvent(k, raw=raw))
    app.step()


def test_slash_keystroke_opens_non_focused_autocomplete_overlay() -> None:
    """Typing ``/`` opens the autocomplete strip but MUST NOT steal focus —
    otherwise the user can't keep typing the command."""

    app, c = _make_controller()
    _inject(app, "/")
    assert c.editor.buffer.text == "/"
    # The autocomplete overlay exists but focus stays on the editor.
    assert c._slash_overlay is not None  # noqa: SLF001
    assert app.focused is c.editor


def test_typing_slash_quit_via_inject_input_dispatches_quit() -> None:
    """End-to-end through the substrate's focus dispatch: every keystroke
    of ``/quit\\n`` must reach the editor, not the open Selector. Enter
    fires the slash registry which opens a Confirm dialog."""

    from tui.overlay import Confirm

    app, c = _make_controller()
    _inject(app, "/", "q", "u", "i", "t", "Enter")
    # Editor buffer was consumed by submit; the only overlay left is the
    # Confirm dialog spawned by /quit (the autocomplete strip closed on
    # submit). Crucially: focus never sat on the Selector during typing.
    assert c.editor.buffer.text == ""
    assert any(isinstance(o, Confirm) for o in app._overlays)  # noqa: SLF001
    assert isinstance(app.focused, Confirm)


def test_autocomplete_filters_live_as_user_types() -> None:
    app, c = _make_controller()
    _inject(app, "/")
    initial_count = len(c._slash_overlay.items)  # noqa: SLF001
    _inject(app, "q")
    filtered = [it.label for it in c._slash_overlay.items]  # noqa: SLF001
    assert filtered == ["/quit"]
    assert initial_count > 1  # overlay had many items before the filter


def test_tab_moves_focus_into_picker_then_enter_inserts_back() -> None:
    """The explicit ``Tab`` path: editor → Selector → Enter inserts the
    chosen command back into the editor and returns focus."""

    app, c = _make_controller()
    _inject(app, "/", "q")  # filter to /quit
    assert app.focused is c.editor
    assert c.editor.focused is True
    _inject(app, "Tab")
    selector = app.focused
    assert isinstance(selector, Selector)
    # Visual focus signal — covers the §4.4 macOS Terminal regression
    # where Tab moved focus but the picker looked identical to the
    # unfocused state, so the user thought the system had locked up.
    assert selector.focused is True
    assert c.editor.focused is False
    rendered = selector.render(80)
    body = "\n".join(rendered)
    assert "[active" in body, (
        f"focused selector must paint a visible focus marker; got {body!r}"
    )
    assert "\x1b[7m" in body, (
        "focused selector must use inverse video on the selected row"
    )
    _inject(app, "Enter")
    assert c.editor.buffer.text == "/quit "
    assert app.focused is c.editor
    assert c.editor.focused is True
    assert c._slash_overlay is None  # noqa: SLF001


def test_typing_a_non_slash_character_closes_autocomplete() -> None:
    app, c = _make_controller()
    _inject(app, "/", "q")
    assert c._slash_overlay is not None  # noqa: SLF001
    # Backspace removes '/q' → buffer text becomes '' → overlay closes.
    _inject(app, "Backspace", "Backspace")
    assert c.editor.buffer.text == ""
    assert c._slash_overlay is None  # noqa: SLF001


def test_esc_closes_autocomplete_before_falling_through_to_abort() -> None:
    app, c = _make_controller()
    _inject(app, "/")
    assert c._slash_overlay is not None  # noqa: SLF001
    _inject(app, "Esc")
    # First Esc dismissed the overlay; abort path was NOT triggered, so no
    # "aborted" status notification was pushed.
    assert c._slash_overlay is None  # noqa: SLF001
    notes = c.status._notifications  # noqa: SLF001
    assert not any("abort" in n.text.lower() for n in notes)


# -------------------------------------------------------------------- #
# P1-3: Ctrl+C aborts when there's active work, exits when idle.        #
# -------------------------------------------------------------------- #


def test_ctrl_c_when_idle_exits_the_app() -> None:
    app, c = _make_controller()
    app._running = True  # noqa: SLF001 — pretend the run loop is live, so a
    # subsequent False reading actually proves ``app.exit()`` was invoked
    # rather than just observing the constructor default.
    assert c._has_active_work() is False  # noqa: SLF001
    handled = c._global_input_hook(KeyEvent("Ctrl+C", raw="\x03"))  # noqa: SLF001
    assert handled is True
    assert app._running is False  # noqa: SLF001 — exit() flipped it off


def test_ctrl_c_during_streaming_aborts_instead_of_exiting() -> None:
    app, c = _make_controller()
    c.editor.set_state(EditorState.STREAMING)
    app._running = True  # noqa: SLF001 — same rationale: True before, must
    # remain True after to prove the streaming-abort path doesn't exit.
    handled = c._global_input_hook(KeyEvent("Ctrl+C", raw="\x03"))  # noqa: SLF001
    assert handled is True
    # Editor flipped back to idle.
    assert c.editor.state == EditorState.IDLE
    # Abort signals via a transient status notification, not by pinning
    # "aborted" into the footer (which had no auto-revert and stuck
    # forever — manual §3.9 follow-up bug).
    notes = c.status._notifications  # noqa: SLF001
    assert any("abort" in n.text.lower() for n in notes)
    # And app.exit() was NOT called — abort kept the loop alive.
    assert app._running is True  # noqa: SLF001


# -------------------------------------------------------------------- #
# P2: editor cursor stays visible when nested inside _RootComponent.   #
# -------------------------------------------------------------------- #


def test_focus_offset_provider_locates_nested_editor_cursor() -> None:
    app, c = _make_controller()
    # Type something so the cursor marker has a non-zero column.
    for ch in "abc":
        c.editor.handle_input(KeyEvent(ch, raw=ch))
    # Force a render so editor caches its body width.
    c.editor.render(80)
    # The substrate should now be able to compute an absolute cursor.
    cursor = app._compose_cursor(app._compose_frame())  # noqa: SLF001
    assert cursor is not None
    assert cursor.visible is True
    assert cursor.col >= 1
    # The provider should resolve the editor offset to (status + messages).
    expected = (
        len(c.status.render(app._cols))  # noqa: SLF001
        + len(c.messages.render(app._cols))  # noqa: SLF001
    )
    # row is 1-based; marker.row 0 + offset + 1 == expected + 1
    assert cursor.row == expected + 1


# -------------------------------------------------------------------- #
# P1-2: --playback drives events through the loop and exits when done. #
# -------------------------------------------------------------------- #


def test_background_playback_thread_calls_controller_exit_when_done() -> None:
    app, c = _make_controller()
    app._running = True  # noqa: SLF001 — simulate run loop active so that a
    # False reading after join() actually proves the thread invoked exit().
    c._exit_when_playback_finishes = True  # noqa: SLF001
    c._start_playback_thread(  # noqa: SLF001
        FIXTURE_ROOT / "assistant_text_delta"
    )
    assert c._playback_thread is not None  # noqa: SLF001
    c._playback_thread.join(timeout=5.0)  # noqa: SLF001
    assert not c._playback_thread.is_alive()  # noqa: SLF001
    assert app._running is False  # noqa: SLF001 — exit() ran
    # The fixture produced a final assistant message.
    assert any(
        type(child).__name__ == "AssistantMessageComponent"
        for child in c.messages.children
    )


def test_play_sync_with_sleep_routes_delays_through_sleeper() -> None:
    """``--playback`` mode uses ``play_sync(sleep=True)`` so the user sees
    streaming pacing. Inject a recording sleeper so the assertion is on
    the requested delays, not wall-clock timing — that keeps the test
    deterministic and fast."""

    fixture_dir = FIXTURE_ROOT / "abort_during_stream"
    sidecar = json.loads((fixture_dir / "playback.json").read_text())
    expected_calls = [d / 1000.0 for d in sidecar["delays_ms"] if d > 0]

    recorded: list[float] = []
    app, c = _make_controller()
    harness = PlaybackHarness(
        fixture_dir,
        controller=c,
        sleeper=lambda seconds: recorded.append(seconds),
    )
    harness.play_sync(sleep=True)

    assert recorded == expected_calls, (
        f"sleeper saw {recorded!r}; expected {expected_calls!r}"
    )
    # Also verify the abort inject side-effect: the editor returns to idle.
    assert c.editor.state == EditorState.IDLE


def test_play_sync_without_sleep_never_calls_sleeper() -> None:
    """Default ``play_sync()`` (no ``sleep=True``) must not pay the timing
    cost — it's the path used by tests that only care about end-state."""

    recorded: list[float] = []
    app, c = _make_controller()
    harness = PlaybackHarness(
        FIXTURE_ROOT / "assistant_text_delta",
        controller=c,
        sleeper=lambda seconds: recorded.append(seconds),
    )
    harness.play_sync()  # default sleep=False
    assert recorded == []


# -------------------------------------------------------------------- #
# Live slash dispatch (W6) — drive each command end to end via         #
# ``inject_input`` so the focus dispatch + slash autocomplete + handler #
# composition is actually exercised.                                    #
# -------------------------------------------------------------------- #


def _type_command(app: TUIApp, command: str) -> None:
    """Type a slash command (one printable character at a time) followed by
    Enter. Mirrors what a real keyboard would deliver."""

    for ch in command:
        _inject(app, ch)
    _inject(app, "Enter")


def test_slash_new_clears_messages_and_resets_state() -> None:
    """Typing ``/new`` must clear the message column and put the editor
    back to idle — the live ``new.handle_new`` handler is wired."""

    from ai_provider.types import UserMessage
    from cli.interactive.components import UserMessageComponent

    app, c = _make_controller()
    c.messages.append(
        UserMessageComponent(UserMessage(role="user", content="leftover", timestamp=0))
    )
    c.editor.set_state(EditorState.STREAMING)
    assert len(c.messages.children) == 1

    _type_command(app, "/new")

    assert c.messages.children == []
    assert c.editor.state == EditorState.IDLE
    assert "new session" in c.editor._footer.lower()  # noqa: SLF001


def test_slash_hotkeys_opens_settings_overlay_with_keymap_rows() -> None:
    """Typing ``/hotkeys`` must open the ``SettingsList`` overlay populated
    from ``default_bindings()``."""

    from tui.keymap import default_bindings
    from tui.overlay import SettingsList

    app, c = _make_controller()
    _type_command(app, "/hotkeys")
    overlays = [o for o in app._overlays if isinstance(o, SettingsList)]  # noqa: SLF001
    assert len(overlays) == 1
    expected_keys = {b.key for b in default_bindings()}
    actual_keys = {row.label for row in overlays[0].rows}
    assert expected_keys.issubset(actual_keys)


def test_slash_play_with_known_fixture_drives_harness_synchronously() -> None:
    """``/play <name>`` runs the harness via the slash handler. Default
    ``play_sync()`` ignores delays so the test is deterministic without an
    injected sleeper."""

    app, c = _make_controller()
    _type_command(app, "/play assistant_text_delta")
    # Harness ran inline → at least one assistant component now exists.
    assert any(
        type(child).__name__ == "AssistantMessageComponent"
        for child in c.messages.children
    )


def test_slash_play_with_unknown_fixture_pushes_error_notification() -> None:
    app, c = _make_controller()
    _type_command(app, "/play nonexistent-fixture-xyz")
    notifications = c.status._notifications  # noqa: SLF001
    assert any("not found" in n.text.lower() for n in notifications)


def test_unknown_slash_command_pushes_warning_notification() -> None:
    """Plan §W6 — unknown commands route through the registry's fallback
    notification rather than the LLM submit path."""

    app, c = _make_controller()
    _type_command(app, "/totally-fake-command")
    notifications = c.status._notifications  # noqa: SLF001
    assert any("unknown command" in n.text.lower() for n in notifications)


def test_status_notification_expires_via_scheduled_wake(monkeypatch) -> None:
    """Regression: TTL'd notifications used to stay painted forever
    because the render loop is event-driven — no input meant no render,
    so ``_alive_notifications`` never re-evaluated past expiry. The
    fix wires ``StatusComponent`` into ``TUIApp.schedule_wake`` so the
    expected expiry time becomes a render trigger."""

    import time as time_module

    fake_now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: fake_now[0])

    app, c = _make_controller()
    c.status.push_notification("aborted", ttl_seconds=3.0)
    assert any("abort" in n.text.lower() for n in c.status._notifications)  # noqa: SLF001
    # A wake-up has been scheduled just past the expiry instant.
    assert app._wake_at, "schedule_wake was never invoked"  # noqa: SLF001
    assert app._wake_at[0] > fake_now[0]  # noqa: SLF001

    # Before TTL passes, the notification still renders.
    rows_before = c.status.render(80)
    assert any("abort" in row.lower() for row in rows_before)

    # Advance the fake clock past the wake instant; step() should
    # promote the wake into a render request and the next render finds
    # the notification filtered out by ``_alive_notifications``.
    fake_now[0] = 1004.0  # 4 s later, well past 3 s TTL + 50 ms buffer
    app.step()
    assert app._wake_at == []  # noqa: SLF001 — wake consumed
    rows_after = c.status.render(80)
    assert not any("abort" in row.lower() for row in rows_after), (
        f"notification still painted after expiry: {rows_after!r}"
    )
