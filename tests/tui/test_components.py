"""Substrate UI primitive tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tui.component import Component
from tui.components.box import Box
from tui.components.container import Container
from tui.components.spacer import Spacer
from tui.components.text import Text
from tui.components.truncated_text import TruncatedText
from tui.width import strip_ansi, visible_width

REPO = Path(__file__).resolve().parents[2]


def test_text_wraps_wide_content_and_preserves_empty_line() -> None:
    assert Text("abcdef").render(3) == ["abc", "def"]
    assert Text("").render(10) == [""]


def test_text_handles_wide_characters_and_style() -> None:
    rows = Text("你好世界", style=lambda s: f"\x1b[31m{s}\x1b[0m").render(4)
    assert [strip_ansi(row) for row in rows] == ["你好", "世界"]
    assert all("\x1b[31m" in row for row in rows)


def test_spacer_rows_and_zero_width() -> None:
    assert Spacer().render(3) == ["   "]
    assert Spacer(rows=0).render(3) == []
    assert Spacer(rows=2).render(0) == ["", ""]


class _Fixed(Component):
    def __init__(self, rows: list[str]) -> None:
        super().__init__()
        self.rows = rows

    def render(self, width: int) -> list[str]:
        return self.enforce_width(self.rows, width)


def test_box_padding_and_border_keep_cjk_width_aligned() -> None:
    box = Box(Text("你好"), padding=1, border=True)
    rows = box.render(8)
    assert rows[0] == "┌──────┐"
    assert rows[-1] == "└──────┘"
    assert all(visible_width(row) == 8 for row in rows)


def test_box_propagates_child_request_render() -> None:
    child = _Fixed(["x"])
    box = Box(child)
    calls: list[str] = []
    box.attach(lambda: calls.append("render"))
    child.request_render()
    assert calls == ["render"]


def test_container_append_clear_order_and_propagation() -> None:
    container = Container()
    first = _Fixed(["a"])
    second = _Fixed(["b", "c"])
    calls: list[str] = []
    container.attach(lambda: calls.append("render"))
    container.append(first)
    container.append(second)
    assert container.children == [first, second]
    assert container.render(10) == ["a", "b", "c"]
    first.request_render()
    assert calls
    container.clear()
    assert container.children == []
    assert container.render(10) == []


def test_container_rejects_unsupported_direction() -> None:
    with pytest.raises(ValueError, match="vertical"):
        Container(direction="horizontal")  # type: ignore[arg-type]


def test_truncated_text_is_single_line_and_uses_ellipsis() -> None:
    assert TruncatedText("hello").render(10) == ["hello"]
    assert TruncatedText("hello world").render(6) == ["hello…"]
    assert TruncatedText("a\nb").render(10) == ["a b"]


def test_truncated_text_handles_cjk_boundary() -> None:
    row = TruncatedText("你好世界").render(6)[0]
    assert visible_width(row) <= 6
    assert row.endswith("…")


def test_tui_components_do_not_import_protocol_modules() -> None:
    forbidden = ("agent_core", "cli.core", "ai_provider")
    bad: list[tuple[Path, str]] = []
    for path in (REPO / "src" / "tui" / "components").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                top = module.split(".")[0]
                second = ".".join(module.split(".")[:2])
                if top in forbidden or second in forbidden:
                    bad.append((path, module))
    assert bad == []
