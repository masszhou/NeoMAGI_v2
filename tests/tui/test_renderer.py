"""W0 substrate renderer tests (ADR-0015 §验收 + plan W7)."""

from __future__ import annotations

import io

from tui.component import CursorPosition
from tui.renderer import Renderer


def _make() -> tuple[Renderer, io.StringIO]:
    out = io.StringIO()
    return Renderer(out_stream=out), out


def test_first_render_writes_full_frame() -> None:
    r, out = _make()
    r.present(["alpha", "beta"])
    text = out.getvalue()
    assert "alpha" in text and "beta" in text
    assert text.startswith("\x1b[?2026h")  # synchronized output begin
    assert text.endswith("\x1b[?2026l")
    assert r.last_changed_rows == 2


def test_second_render_only_writes_diff_rows() -> None:
    r, out = _make()
    r.present(["alpha", "beta", "gamma"])
    out.truncate(0)
    out.seek(0)
    r.present(["alpha", "BETA", "gamma"])
    text = out.getvalue()
    assert "BETA" in text
    assert "alpha" not in text  # row 0 unchanged → not rewritten
    assert "gamma" not in text
    assert r.last_changed_rows == 1


def test_resize_or_explicit_reset_full_redraws() -> None:
    r, out = _make()
    r.present(["alpha"])
    r.reset()
    out.truncate(0)
    out.seek(0)
    r.present(["alpha", "beta"])
    text = out.getvalue()
    assert "alpha" in text and "beta" in text
    assert r.last_changed_rows == 2


def test_content_shrink_clears_orphan_rows() -> None:
    r, out = _make()
    r.present(["a", "b", "c"])
    out.truncate(0)
    out.seek(0)
    r.present(["a", "b"])  # row 3 orphaned
    text = out.getvalue()
    # Row 3 receives a clear-line (^[[2K) followed by nothing.
    assert "\x1b[3;1H" in text
    assert "\x1b[2K" in text


def test_anchor_offsets_all_cursor_moves() -> None:
    r, out = _make()
    r.set_anchor(5)
    r.present(["alpha", "beta"], cursor=CursorPosition(row=2, col=3, visible=True))
    text = out.getvalue()
    assert "\x1b[5;1H" in text
    assert "\x1b[6;1H" in text
    assert "\x1b[6;3H" in text


def test_last_bottom_row_tracks_presented_frame_and_reset() -> None:
    r, _ = _make()
    assert r.last_bottom_row() is None
    r.set_anchor(4)
    r.present(["a", "b", "c"])
    assert r.last_bottom_row() == 6
    r.reset()
    assert r.last_bottom_row() is None


def test_synchronized_output_wraps_each_present() -> None:
    r, out = _make()
    r.present(["alpha"])
    out.truncate(0)
    out.seek(0)
    r.present(["beta"])
    text = out.getvalue()
    assert text.startswith("\x1b[?2026h")
    assert text.endswith("\x1b[?2026l")


def test_cursor_visibility_toggled_through_present_only() -> None:
    r, out = _make()
    r.present(["alpha"], cursor=CursorPosition(row=1, col=1, visible=True))
    assert "\x1b[?25h" in out.getvalue()
    out.truncate(0)
    out.seek(0)
    r.present(["alpha"], cursor=None)  # hide
    assert "\x1b[?25l" in out.getvalue()
    out.truncate(0)
    out.seek(0)
    r.present(["alpha"], cursor=CursorPosition(row=1, col=1, visible=True))
    assert "\x1b[?25h" in out.getvalue()


def test_first_command_live_render_does_not_clear_or_home_screen() -> None:
    r, out = _make()
    r.present_live(["> hello", "[idle]"], cursor=CursorPosition(row=1, col=3))
    text = out.getvalue()
    assert "> hello" in text
    assert text.startswith("\x1b[?2026h")
    assert text.endswith("\x1b[?2026l")
    assert "\x1b[1;1H" not in text
    assert "\x1b[J" not in text


def test_command_commit_appends_transcript_without_rewriting_history() -> None:
    r, out = _make()
    r.present_live(["> draft", "[idle]"], cursor=CursorPosition(row=1, col=3))
    out.truncate(0)
    out.seek(0)

    r.commit_lines(["\x1b[1muser\x1b[0m", "  hello", ""])
    committed = out.getvalue()
    assert "user" in committed
    assert "hello\x1b[0m\r\n" in committed
    assert "\x1b[1;1H" not in committed

    out.truncate(0)
    out.seek(0)
    r.present_live(["> next", "[idle]"], cursor=CursorPosition(row=1, col=3))
    live = out.getvalue()
    assert "> next" in live
    assert "hello" not in live


def test_command_paths_reset_sgr_after_each_row() -> None:
    r, out = _make()
    r.commit_lines(["\x1b[1mbold"])
    assert "\x1b[1mbold\x1b[0m\r\n" in out.getvalue()

    out.truncate(0)
    out.seek(0)
    r.present_live(
        ["\x1b[1mlive", "plain"],
        cursor=CursorPosition(row=2, col=1, visible=True),
    )
    text = out.getvalue()
    assert "\x1b[1mlive\x1b[0m\r\nplain\x1b[0m" in text


def test_command_live_rows_use_crlf_for_raw_terminal_output() -> None:
    r, out = _make()
    r.present_live(
        ["first row", "second row", "third row"],
        cursor=CursorPosition(row=3, col=1, visible=True),
    )
    text = out.getvalue()
    assert "first row\x1b[0m\r\nsecond row\x1b[0m\r\nthird row\x1b[0m" in text
    assert "first row\x1b[0m\nsecond row" not in text
