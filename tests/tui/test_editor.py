"""W2 editor tests."""

from __future__ import annotations

from tui.editor import Editor, EditorState, EditorSubmission
from tui.keymap import Action, Keymap
from tui.stdin_buffer import KeyEvent, PasteEvent
from tui.width import visible_width


def _editor() -> tuple[Editor, list[EditorSubmission], list[Action]]:
    submissions: list[EditorSubmission] = []
    actions: list[Action] = []
    e = Editor(
        Keymap(),
        on_submit=lambda s: submissions.append(s),
        on_action=lambda a: actions.append(a),
    )
    return e, submissions, actions


def test_enter_submits_and_clears_buffer() -> None:
    e, subs, _ = _editor()
    for ch in "hello":
        e.handle_input(KeyEvent(ch, raw=ch))
    e.handle_input(KeyEvent("Enter", raw="\r"))
    assert subs and subs[0].text == "hello"
    assert e.buffer.text == ""


def test_shift_enter_inserts_newline_without_submitting() -> None:
    e, subs, _ = _editor()
    e.handle_input(KeyEvent("a", raw="a"))
    e.handle_input(KeyEvent("Shift+Enter", raw=""))
    e.handle_input(KeyEvent("b", raw="b"))
    assert e.buffer.text == "a\nb"
    assert not subs


def test_alt_enter_propagates_as_action() -> None:
    e, _, actions = _editor()
    e.handle_input(KeyEvent("Alt+Enter", raw=""))
    assert Action.QUEUE_FOLLOWUP in actions


def test_esc_propagates_abort_action() -> None:
    e, _, actions = _editor()
    e.handle_input(KeyEvent("Esc", raw=""))
    assert Action.ABORT in actions


def test_double_esc_propagates_double_escape_action() -> None:
    e, _, actions = _editor()
    e.handle_input(KeyEvent("Esc Esc", raw=""))
    assert Action.DOUBLE_ESCAPE in actions


def test_chinese_input_caret_column_matches_visible_width() -> None:
    e, _, _ = _editor()
    for ch in "你好":
        e.handle_input(KeyEvent(ch, raw=ch))
    e.render(80)  # populate _last_body_width
    marker = e.cursor_marker()
    assert marker is not None
    # PROMPT '> ' is 2 columns; '你好' is 4 columns; cursor sits after both.
    assert marker.col == 2 + visible_width("你好")


def test_bracketed_paste_inserts_full_payload() -> None:
    e, subs, _ = _editor()
    e.handle_input(PasteEvent("multi\nline paste"))
    assert e.buffer.text == "multi\nline paste"
    assert not subs


def test_state_label_renders_in_footer() -> None:
    e, _, _ = _editor()
    e.set_state(EditorState.STREAMING)
    rendered = e.render(80)
    footer = rendered[-1]
    assert "[streaming]" in footer


def test_slash_trigger_inserts_character_and_bubbles_action() -> None:
    """Regression for P1-1: typing ``/`` must leave the slash in the buffer
    so the user can keep typing ``/quit`` while autocomplete pops up."""

    e, _, actions = _editor()
    e.handle_input(KeyEvent("/", raw="/"))
    assert e.buffer.text == "/"
    assert Action.SLASH_TRIGGER in actions


def test_at_and_bang_triggers_also_insert() -> None:
    e, _, actions = _editor()
    e.handle_input(KeyEvent("@", raw="@"))
    e.handle_input(KeyEvent("!", raw="!"))
    assert e.buffer.text == "@!"
    assert Action.AT_TRIGGER in actions
    assert Action.BANG_TRIGGER in actions


def test_tab_does_not_insert_character() -> None:
    """Tab is autocomplete — must NOT insert the literal Tab byte."""

    e, _, actions = _editor()
    e.handle_input(KeyEvent("Tab", raw="\t"))
    assert e.buffer.text == ""
    assert Action.AUTOCOMPLETE in actions
