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


def test_lone_esc_emits_after_one_idle_drain() -> None:
    """Regression for P1-4: pressing Esc once with no follow-up must reach
    the editor. The buffer holds it for one tick to allow a multi-byte
    CSI sequence to land; the next idle drain emits the synthetic Esc."""

    sb = StdinBuffer()
    sb.feed_str("\x1b")
    first = sb.drain()
    assert all(getattr(e, "key", None) != "Esc" for e in first)
    second = sb.drain()
    assert any(getattr(e, "key", None) == "Esc" for e in second)


def test_lone_esc_is_not_emitted_when_csi_arrives_in_next_chunk() -> None:
    """Companion to the previous test: if real follow-up bytes arrive
    before the idle drain fires, the buffer must NOT also emit a stray
    Esc — the two reads compose into a single CSI."""

    sb = StdinBuffer()
    sb.feed_str("\x1b")
    sb.drain()  # marks lone-ESC pending
    sb.feed_str("[A")  # second chunk completes the CSI
    events = sb.drain()
    keys = [getattr(e, "key", None) for e in events]
    assert "Up" in keys
    assert "Esc" not in keys
