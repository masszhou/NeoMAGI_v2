"""W0 substrate width-guard tests (ADR-0015 §验收 + plan W7)."""

from __future__ import annotations

import pytest

from tui.component import Component, ComponentOverflowError
from tui.width import (
    pad_to_width,
    slice_by_columns,
    strip_ansi,
    truncate_to_width,
    visible_width,
    wrap_to_width,
)


def test_visible_width_ascii() -> None:
    assert visible_width("hello") == 5
    assert visible_width("") == 0


def test_visible_width_cjk_double_wide() -> None:
    assert visible_width("你好") == 4
    assert visible_width("a你b") == 4


def test_visible_width_emoji_zwj() -> None:
    # Emoji width is at least 1; tolerate terminal variation but never 0
    # for a printable character.
    assert visible_width("👍") >= 1


def test_visible_width_combining_mark() -> None:
    # 'e' + combining acute should still be 1 column.
    assert visible_width("é") == 1


def test_visible_width_strips_ansi_sgr() -> None:
    assert visible_width("a\x1b[31mb\x1b[0mc") == 3
    assert strip_ansi("\x1b[31mfoo\x1b[0m") == "foo"


def test_visible_width_tab_advances_to_8_column_stop() -> None:
    assert visible_width("\t") == 8
    assert visible_width("a\t") == 8
    assert visible_width("ab\t") == 8


def test_truncate_to_width_appends_ellipsis() -> None:
    assert truncate_to_width("hello world", 8) == "hello w…"
    assert truncate_to_width("hi", 5) == "hi"
    assert truncate_to_width("hello", 0) == ""


def test_pad_to_width_right_pads_with_spaces() -> None:
    assert pad_to_width("hi", 5) == "hi   "
    # When over budget, pad falls back to truncate (which appends an ellipsis).
    out = pad_to_width("toolong", 4)
    assert visible_width(out) <= 4
    assert out.startswith("too")


def test_slice_by_columns_handles_cjk() -> None:
    # 你好 occupies 4 columns; slicing [0,2) returns the first character.
    assert slice_by_columns("你好", 0, 2) == "你"
    assert slice_by_columns("你好", 2, 4) == "好"


def test_wrap_to_width_breaks_at_column_budget() -> None:
    out = wrap_to_width("hello world foo bar", 7)
    # Each chunk must be ≤7 columns.
    assert all(visible_width(line) <= 7 for line in out)
    assert "".join(out) == "hello world foo bar"


def test_wrap_preserves_explicit_newlines() -> None:
    assert wrap_to_width("a\nb", 10) == ["a", "b"]


def test_component_render_overflow_negative_case() -> None:
    """Plan §W7 / ADR-0015 §验收 — component MUST fail-fast or truncate."""

    class _Bad(Component):
        fail_fast_on_overflow = True

        def render(self, width: int) -> list[str]:
            return self.enforce_width(["x" * (width + 5)], width)

    with pytest.raises(ComponentOverflowError):
        _Bad().render(10)

    class _Good(Component):
        def render(self, width: int) -> list[str]:
            return self.enforce_width(["x" * (width + 5)], width)

    out = _Good().render(10)
    assert visible_width(out[0]) == 10
