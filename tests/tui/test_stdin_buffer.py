"""W0 substrate stdin parser tests (ADR-0015 §验收 + plan W7)."""

from __future__ import annotations

from tui.stdin_buffer import KeyEvent, PasteEvent, StdinBuffer


def _drain(text: str) -> list:
    sb = StdinBuffer()
    sb.feed_str(text)
    return sb.drain()


def test_plain_ascii_keys() -> None:
    events = _drain("abc")
    assert [e.key for e in events] == ["a", "b", "c"]


def test_enter_tab_backspace_normalised() -> None:
    events = _drain("\r\t\x7f")
    assert [e.key for e in events] == ["Enter", "Tab", "Backspace"]


def test_arrow_keys_csi() -> None:
    events = _drain("\x1b[A\x1b[D")
    assert [e.key for e in events] == ["Up", "Left"]


def test_function_keys_tilde_form() -> None:
    events = _drain("\x1b[15~")  # F5
    assert events and events[0].key == "F5"


def test_modified_arrow_shift_via_xterm_param() -> None:
    events = _drain("\x1b[1;2A")  # Shift+Up
    assert events[0].key == "Shift+Up"
    assert "Shift" in events[0].modifiers


def test_alt_letter_via_esc_prefix() -> None:
    events = _drain("\x1ba")
    assert events[0].key == "Alt+a"
    assert "Alt" in events[0].modifiers


def test_double_esc_yields_synthetic_event() -> None:
    events = _drain("\x1b\x1b")
    assert events and events[0].key == "Esc Esc"


def test_partial_csi_buffered_until_complete() -> None:
    sb = StdinBuffer()
    sb.feed_str("\x1b[")
    assert sb.drain() == []
    sb.feed_str("A")
    events = sb.drain()
    assert events and events[0].key == "Up"


def test_partial_osc_consumed_without_emit() -> None:
    sb = StdinBuffer()
    sb.feed_str("\x1b]0;some title\x07rest")
    events = sb.drain()
    # OSC is dropped; the trailing 'rest' becomes plain keys.
    assert [e.key for e in events] == ["r", "e", "s", "t"]


def test_bracketed_paste_round_trips_as_one_event() -> None:
    sb = StdinBuffer()
    sb.feed_str("\x1b[200~hello\nworld\x1b[201~")
    events = sb.drain()
    assert len(events) == 1
    assert isinstance(events[0], PasteEvent)
    assert events[0].text == "hello\nworld"


def test_bracketed_paste_split_across_reads() -> None:
    sb = StdinBuffer()
    sb.feed_str("\x1b[200~part1 ")
    assert sb.drain() == []
    sb.feed_str("part2\x1b[201~")
    events = sb.drain()
    assert len(events) == 1
    assert isinstance(events[0], PasteEvent)
    assert events[0].text == "part1 part2"


def test_csi_u_modify_other_keys_enter_with_shift() -> None:
    # Kitty / xterm `modifyOtherKeys=2` Shift+Enter encoding: CSI 13;2u
    events = _drain("\x1b[13;2u")
    assert events and events[0].key == "Shift+Enter"
    assert "Shift" in events[0].modifiers


def test_csi_u_ctrl_letter_is_normalised_to_uppercase() -> None:
    """Regression: terminals that honour our Kitty/modifyOtherKeys
    negotiation may encode Ctrl+C as ``CSI 99 ; 5 u`` (lowercase 'c'
    + Ctrl). The parser must emit the same ``"Ctrl+C"`` (uppercase)
    keystring that the raw-byte path produces, so the global hook
    matches and the editor's keymap binding triggers."""

    events = _drain("\x1b[99;5u")  # Ctrl+c via CSI-u
    assert events and events[0].key == "Ctrl+C"
    assert "Ctrl" in events[0].modifiers


def test_csi_27_modify_other_keys_form_for_ctrl_c() -> None:
    """Regression: xterm modifyOtherKeys=2 alternate encoding
    ``CSI 27 ; 5 ; 99 ~`` for Ctrl+C used to fall into the function-key
    ``~`` branch and get silently dropped (code "27" wasn't in
    ``_TILDE_NAMES``). Now it emits Ctrl+C with the modifier set."""

    events = _drain("\x1b[27;5;99~")
    assert events and events[0].key == "Ctrl+C"
    assert "Ctrl" in events[0].modifiers


def test_ctrl_letter_emitted_as_ctrl_modifier() -> None:
    events = _drain("\x03")  # Ctrl+C
    assert events[0].key == "Ctrl+C"
    assert "Ctrl" in events[0].modifiers


def test_no_half_escape_ever_leaks_as_plain_key() -> None:
    sb = StdinBuffer()
    sb.feed_str("\x1b")  # lone ESC, no follow-up yet
    events = sb.drain()
    # Allowed to emit Esc here, but we MUST NOT emit any plain '['.
    if events:
        assert all(isinstance(e, KeyEvent) and e.key in {"Esc"} for e in events)
    sb.feed_str("[A")
    next_events = sb.drain()
    # The follow-up is a plain CSI sequence that becomes Up.
    assert any(getattr(e, "key", None) == "Up" for e in next_events) or next_events == []


def test_lone_esc_emits_after_debounce_window() -> None:
    """Regression for P1-4: pressing Esc once with no follow-up must
    reach the editor. The buffer holds the keystroke for the lone-ESC
    debounce window, then flushes a synthetic ``Esc``. Tests inject a
    fake clock so the assertion is deterministic instead of waiting on
    wall-clock time."""

    now = [0.0]
    sb = StdinBuffer(clock=lambda: now[0])
    sb.feed_str("\x1b")
    now[0] = 0.001
    assert all(getattr(e, "key", None) != "Esc" for e in sb.drain())
    now[0] = 0.05  # within 100 ms debounce
    assert all(getattr(e, "key", None) != "Esc" for e in sb.drain())
    now[0] = 0.5  # past debounce window
    events = sb.drain()
    assert any(getattr(e, "key", None) == "Esc" for e in events)


def test_lone_esc_is_not_emitted_when_csi_arrives_in_next_chunk() -> None:
    """Companion to the previous test: if real follow-up bytes arrive
    before the debounce expires, the buffer must NOT emit a stray Esc —
    the two reads compose into a single CSI sequence."""

    now = [0.0]
    sb = StdinBuffer(clock=lambda: now[0])
    sb.feed_str("\x1b")
    now[0] = 0.05
    sb.drain()  # within debounce — no Esc yet, lone-ESC seen_at recorded
    sb.feed_str("[A")  # second chunk completes the CSI
    now[0] = 0.5  # well past the debounce window
    events = sb.drain()
    keys = [getattr(e, "key", None) for e in events]
    assert "Up" in keys
    assert "Esc" not in keys


def test_slow_double_esc_still_collapses_to_single_event() -> None:
    """Regression for §3.9 manual test on macOS Terminal.app: a human
    double-tap on Esc lands ~100–250 ms apart, well past one event-loop
    tick. The earlier "flush after one idle drain" logic emitted a
    single ``Esc`` immediately after the first byte, so the second Esc
    arrived to an empty buffer and produced a *second* ``Esc`` event
    instead of the expected ``Esc Esc`` gesture. With the debounce
    window now set wider than human reflex, the second feed lands while
    the first is still pending and ``_parse_escape`` collapses both
    into one event."""

    now = [0.0]
    sb = StdinBuffer(clock=lambda: now[0])
    sb.feed_str("\x1b")
    now[0] = 0.02
    assert sb.drain() == []  # first byte parked, no event yet
    now[0] = 0.07  # 70 ms later — still inside the 100 ms debounce
    sb.feed_str("\x1b")
    events = sb.drain()
    keys = [getattr(e, "key", None) for e in events]
    assert keys == ["Esc Esc"], (
        f"expected one Esc Esc event, got {keys!r} — slow double-tap "
        f"degenerated into single Esc events again"
    )
