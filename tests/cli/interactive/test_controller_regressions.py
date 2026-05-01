"""Regressions for the P1 issues caught in the M1 review."""

from __future__ import annotations

import io
import json
from pathlib import Path

from ai_provider.types import AssistantMessage, StreamDone, TextContent, Usage, UsageCost, UserMessage
from cli.core.session_types import MessageStartEvent, MessageUpdateEvent
from cli.interactive.app import InteractiveController
from cli.interactive.components import AssistantMessageComponent, UserMessageComponent
from cli.interactive.playback import PlaybackHarness
from tui.app import TUIApp
from tui.editor import EditorState
from tui.overlay import Selector
from tui.stdin_buffer import KeyEvent, MouseWheelEvent

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "pi_compat"


def _make_controller() -> tuple[TUIApp, InteractiveController]:
    app = TUIApp()
    c = InteractiveController(tui_app=app)
    c.bootstrap()
    return app, c


def _make_command_controller() -> tuple[TUIApp, InteractiveController, io.StringIO]:
    out = io.StringIO()
    app = TUIApp(render_mode="command", out_stream=out)
    c = InteractiveController(tui_app=app)
    c.bootstrap()
    return app, c, out


# -------------------------------------------------------------------- #
# P1-1: slash trigger now opens an autocomplete overlay AND keeps the  #
# slash character in the buffer so the user can keep typing.            #
# -------------------------------------------------------------------- #


def _inject(app: TUIApp, *keys: str) -> None:
    for k in keys:
        raw = k if len(k) == 1 else ""
        app.inject_input(KeyEvent(k, raw=raw))
    app.step()


def _zero_usage() -> Usage:
    return Usage(
        input=0,
        output=0,
        cacheRead=0,
        cacheWrite=0,
        totalTokens=0,
        cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
    )


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


def test_editor_cursor_moves_visibly_with_left_and_right_keys() -> None:
    app, c = _make_controller()
    for ch in "Write 20 short lines":
        c.editor.handle_input(KeyEvent(ch, raw=ch))
    app._draw()  # noqa: SLF001
    start = app._compose_cursor(app._compose_frame())  # noqa: SLF001
    assert start is not None

    c.editor.handle_input(KeyEvent("Left"))
    app._draw()  # noqa: SLF001
    left = app._compose_cursor(app._compose_frame())  # noqa: SLF001
    assert left is not None
    assert left.col == start.col - 1

    c.editor.handle_input(KeyEvent("Right"))
    app._draw()  # noqa: SLF001
    right = app._compose_cursor(app._compose_frame())  # noqa: SLF001
    assert right is not None
    assert right.col == start.col


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


def test_messages_overflow_clips_oldest_keeps_editor_visible() -> None:
    """Regression for §4.9 manual test: running ``/play`` multiple times
    let the message column grow past the terminal height. The previous
    composition clipped from the bottom, so the editor and the most
    recent assistant turn vanished off-screen and the user concluded
    history wasn't accumulating. The fix moves to a height-aware root
    that keeps status pinned at the top, the editor pinned at the
    bottom, and clips the oldest messages from the top of the message
    column when it overflows."""

    app, c = _make_controller()
    app._cols, app._rows = 80, 12  # noqa: SLF001 — small terminal so we
    # can exercise the overflow path with just a few /play runs.

    for _ in range(4):
        _type_command(app, "/play assistant_text_delta")
    assert len(c.messages.children) == 4  # all four accumulated in model

    frame = app._compose_frame()  # noqa: SLF001
    assert len(frame) == app._rows  # noqa: SLF001 — frame matches terminal

    # The editor's `> ` line must appear in the visible frame even
    # though there are 4 stacked assistant blocks above it.
    editor_visible = any(line.lstrip().startswith(">") for line in frame)
    assert editor_visible, (
        f"editor row was clipped off the visible frame: {frame!r}"
    )

    # And the cursor lands on the editor row, not on a clipped message.
    cursor = app._compose_cursor(frame)  # noqa: SLF001
    assert cursor is not None
    # Use the editor offset reported by the root — the visible editor
    # row is one past the final message row that survived clipping.
    expected_row = c._root.editor_offset(app._cols) + 1  # noqa: SLF001
    assert cursor.row == expected_row, (
        f"cursor at row {cursor.row}; expected editor row {expected_row}"
    )


def test_stream_overflow_keeps_current_user_prompt_visible() -> None:
    app, c = _make_controller()
    app._cols, app._rows = 80, 10  # noqa: SLF001
    prompt = "Write 20 short numbered lines. Include marker M4_ABORT_STREAM near the end."
    c.messages.append(UserMessageComponent(UserMessage(content=prompt, timestamp=1)))
    c.messages.append(
        AssistantMessageComponent(
            AssistantMessage(
                role="assistant",
                content=[TextContent(text="\n".join(f"{i}. line" for i in range(1, 21)))],
                api="openai-responses",
                provider="openai",
                model="gpt-4o-mini",
                usage=_zero_usage(),
                stopReason="aborted",
                errorMessage="Request was aborted",
                timestamp=2,
            )
        )
    )

    frame = app._compose_frame()  # noqa: SLF001
    visible = "\n".join(frame)
    assert "M4_ABORT_STREAM" in visible
    assert "[aborted" in visible
    assert any(line.lstrip().startswith(">") for line in frame)


def test_page_up_and_down_scroll_message_history_viewport() -> None:
    app, c = _make_controller()
    app._cols, app._rows = 80, 10  # noqa: SLF001
    for index in range(1, 7):
        c.messages.append(
            UserMessageComponent(UserMessage(content=f"prompt {index}", timestamp=index))
        )
        c.messages.append(
            AssistantMessageComponent(
                AssistantMessage(
                    role="assistant",
                    content=[TextContent(text=f"answer {index}")],
                    api="openai-responses",
                    provider="openai",
                    model="gpt-4o-mini",
                    usage=_zero_usage(),
                    stopReason="stop",
                    timestamp=index,
                )
            )
        )

    bottom = "\n".join(app._compose_frame())  # noqa: SLF001
    assert "prompt 1" not in bottom
    assert "answer 6" in bottom

    for _ in range(4):
        _inject(app, "PageUp")
    earlier = "\n".join(app._compose_frame())  # noqa: SLF001
    assert "prompt 1" in earlier or "answer 1" in earlier

    for _ in range(4):
        _inject(app, "PageDown")
    latest = "\n".join(app._compose_frame())  # noqa: SLF001
    assert "answer 6" in latest


def test_canvas_mouse_wheel_scrolls_message_history_viewport() -> None:
    app, c = _make_controller()
    app._cols, app._rows = 80, 10  # noqa: SLF001
    for index in range(1, 7):
        c.messages.append(
            UserMessageComponent(UserMessage(content=f"prompt {index}", timestamp=index))
        )
        c.messages.append(
            AssistantMessageComponent(
                AssistantMessage(
                    role="assistant",
                    content=[TextContent(text=f"answer {index}")],
                    api="openai-responses",
                    provider="openai",
                    model="gpt-4o-mini",
                    usage=_zero_usage(),
                    stopReason="stop",
                    timestamp=index,
                )
            )
        )

    bottom = "\n".join(app._compose_frame())  # noqa: SLF001
    assert "answer 6" in bottom
    for _ in range(8):
        app.inject_input(MouseWheelEvent(direction="up", col=1, row=1))
    app.step()
    earlier = "\n".join(app._compose_frame())  # noqa: SLF001
    assert "prompt 1" in earlier or "answer 1" in earlier


def test_command_mode_commits_completed_messages_to_scrollback() -> None:
    app, c, out = _make_command_controller()
    c.dispatch_event(
        MessageStartEvent(message=UserMessage(content="prompt one", timestamp=1))
    )

    written = out.getvalue()
    assert "prompt one" in written
    live = "\n".join(app._compose_command_frame())  # noqa: SLF001
    assert "prompt one" not in live


def test_command_mode_does_not_commit_empty_assistant_start_before_done() -> None:
    _, c, out = _make_command_controller()
    initial = AssistantMessage(
        role="assistant",
        content=[],
        api="openai-responses",
        provider="openai",
        model="gpt-4o-mini",
        usage=_zero_usage(),
        stopReason="stop",
        timestamp=1,
    )
    final = initial.model_copy(
        update={"content": [TextContent(text="final streamed text")], "timestamp": 2}
    )

    c.dispatch_event(MessageStartEvent(message=initial))
    written_after_start = out.getvalue()
    assert "assistant" not in written_after_start

    c.dispatch_event(
        MessageUpdateEvent(
            message=final,
            assistantMessageEvent=StreamDone(reason="stop", message=final)
        )
    )

    written_after_done = out.getvalue()
    assert "assistant" in written_after_done
    assert "final streamed text" in written_after_done


def test_command_mode_commits_done_only_assistant_text_to_scrollback() -> None:
    _, c, out = _make_command_controller()
    final = AssistantMessage(
        role="assistant",
        content=[TextContent(text="final-only provider text")],
        api="openai-responses",
        provider="openai",
        model="gpt-4o-mini",
        usage=_zero_usage(),
        stopReason="stop",
        timestamp=2,
    )

    c.dispatch_event(
        MessageUpdateEvent(
            message=final,
            assistantMessageEvent=StreamDone(reason="stop", message=final)
        )
    )

    written = out.getvalue()
    assert "assistant" in written
    assert "final-only provider text" in written


def test_command_page_up_is_noop_for_editor_and_transcript_viewport() -> None:
    app, c, _ = _make_command_controller()
    for ch in "draft":
        c.editor.handle_input(KeyEvent(ch, raw=ch))

    _inject(app, "PageUp")

    assert c.editor.buffer.text == "draft"
    assert c.messages._scroll_offset_rows == 0  # noqa: SLF001


def test_command_new_session_appends_boundary_without_erasing_scrollback() -> None:
    _, c, out = _make_command_controller()
    c.dispatch_event(
        MessageStartEvent(message=UserMessage(content="old prompt", timestamp=1))
    )
    out.truncate(0)
    out.seek(0)

    c.reset_session()

    text = out.getvalue()
    assert "[new session] previous runtime transcript cleared" in text


def test_scrolled_message_history_keeps_cursor_on_editor_row() -> None:
    app, c = _make_controller()
    app._cols, app._rows = 80, 10  # noqa: SLF001
    for index in range(1, 7):
        c.messages.append(
            UserMessageComponent(UserMessage(content=f"prompt {index}", timestamp=index))
        )
        c.messages.append(
            AssistantMessageComponent(
                AssistantMessage(
                    role="assistant",
                    content=[
                        TextContent(
                            text="\n".join(f"{index}.{line}" for line in range(5))
                        )
                    ],
                    api="openai-responses",
                    provider="openai",
                    model="gpt-4o-mini",
                    usage=_zero_usage(),
                    stopReason="stop",
                    timestamp=index,
                )
            )
        )
    for ch in "Write 50 short numbered lines":
        c.editor.handle_input(KeyEvent(ch, raw=ch))
    for _ in range(4):
        _inject(app, "PageUp")

    frame = app._compose_frame()  # noqa: SLF001
    cursor = app._compose_cursor(frame)  # noqa: SLF001
    editor_row = next(index for index, line in enumerate(frame) if line.startswith("> "))
    assert cursor is not None
    assert cursor.row == editor_row + 1


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
    assert app._wake_callbacks, "schedule_wake was never invoked"  # noqa: SLF001
    assert app._wake_callbacks[0][0] > fake_now[0]  # noqa: SLF001

    # Before TTL passes, the notification still renders.
    rows_before = c.status.render(80)
    assert any("abort" in row.lower() for row in rows_before)

    # Advance the fake clock past the wake instant; step() should
    # promote the wake into a render request and the next render finds
    # the notification filtered out by ``_alive_notifications``.
    fake_now[0] = 1004.0  # 4 s later, well past 3 s TTL + 50 ms buffer
    app.step()
    assert app._wake_callbacks == []  # noqa: SLF001 — wake consumed
    rows_after = c.status.render(80)
    assert not any("abort" in row.lower() for row in rows_after), (
        f"notification still painted after expiry: {rows_after!r}"
    )
