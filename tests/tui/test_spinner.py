"""Spinner primitive tests."""

from __future__ import annotations

import ast
from pathlib import Path

from tui.components.spinner import PI_FRAMES, Spinner
from tui.width import strip_ansi, visible_width

REPO = Path(__file__).resolve().parents[2]
PACKAGE_SRC = REPO / "packages" / "neomagi_pi" / "src"
SPINNER_PATH = PACKAGE_SRC / "tui" / "components" / "spinner.py"


def test_default_frames_advance_and_wrap() -> None:
    spinner = Spinner("working")
    assert len(PI_FRAMES) == 10
    assert strip_ansi(spinner.render(20)[0]).startswith(f"{PI_FRAMES[0]} working")
    for _ in range(len(PI_FRAMES)):
        spinner.tick()
    assert strip_ansi(spinner.render(20)[0]).startswith(f"{PI_FRAMES[0]} working")


def test_set_label_updates_rendered_text() -> None:
    spinner = Spinner("working")
    spinner.set_label("compacting...")
    assert "compacting..." in spinner.render(30)[0]


def test_render_truncates_to_width_and_zero_width_is_blank() -> None:
    spinner = Spinner("abcdef")
    line = spinner.render(4)[0]
    assert visible_width(line) == 4
    assert spinner.render(0) == [""]


def test_style_injection_wraps_final_text() -> None:
    spinner = Spinner("working", style=lambda s: f"\x1b[33m{s}\x1b[0m")
    assert "\x1b[33m" in spinner.render(40)[0]


def test_empty_frames_hide_indicator_without_hiding_label() -> None:
    calls: list[tuple[float, object]] = []
    spinner = Spinner("working", frames=[])
    spinner.attach_tick_scheduler(lambda when, fn: calls.append((when, fn)))
    spinner.tick()
    line = strip_ansi(spinner.render(20)[0])
    assert line.startswith("working")
    assert not line.startswith(" ")
    assert calls == []


def test_set_frames_copies_sequence_and_resets_index() -> None:
    frames = ["a", "b"]
    spinner = Spinner("working")
    for _ in range(9):
        spinner.tick()
    spinner.set_frames(frames)
    frames[0] = "changed"
    assert strip_ansi(spinner.render(20)[0]).startswith("a working")
    spinner.tick()
    assert strip_ansi(spinner.render(20)[0]).startswith("b working")


def test_set_frames_from_empty_to_non_empty_resumes_scheduler() -> None:
    now = [10.0]
    calls: list[tuple[float, object]] = []
    spinner = Spinner("working", frames=[], clock=lambda: now[0])
    spinner.attach_tick_scheduler(lambda when, fn: calls.append((when, fn)))
    assert calls == []
    spinner.set_frames(["x", "y"])
    assert calls and calls[0][0] == 10.08
    assert strip_ansi(spinner.render(20)[0]).startswith("x working")


def test_attach_scheduler_schedules_first_tick_and_tick_schedules_next() -> None:
    now = [20.0]
    calls: list[tuple[float, object]] = []
    spinner = Spinner("working", clock=lambda: now[0])
    spinner.attach_tick_scheduler(lambda when, fn: calls.append((when, fn)))
    assert len(calls) == 1
    assert calls[0][0] == 20.08
    assert calls[0][1] == spinner.tick
    assert spinner._frame == 0  # noqa: SLF001
    calls[0][1]()
    assert spinner._frame == 1  # noqa: SLF001
    assert len(calls) == 2
    assert calls[1][1] == spinner.tick


def test_attach_tick_scheduler_none_disables_auto_tick() -> None:
    calls: list[tuple[float, object]] = []
    spinner = Spinner("working")
    spinner.attach_tick_scheduler(lambda when, fn: calls.append((when, fn)))
    spinner.attach_tick_scheduler(None)
    calls[0][1]()
    assert spinner._frame == 1  # noqa: SLF001
    assert len(calls) == 1


def test_spinner_with_tui_app_callback_advances_frame(monkeypatch) -> None:
    from tui.app import TUIApp
    from tui.terminal import CursorQueryResult

    class _Terminal:
        def query_cursor_row(self):
            return CursorQueryResult(
                row=None,
                leftover=b"",
                attempted=False,
                fallback_allowed=False,
            )

        def write(self, _data: str) -> None:
            return None

        @property
        def is_active(self) -> bool:
            return False

    now = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    spinner = Spinner("working", clock=lambda: now[0])
    app = TUIApp(terminal=_Terminal())  # type: ignore[arg-type]
    spinner.attach_tick_scheduler(app.schedule_callback)
    app._render_requested = False  # noqa: SLF001
    for expected in range(1, 4):
        now[0] += 0.08
        app._check_wakeups()  # noqa: SLF001
        assert spinner._frame == expected  # noqa: SLF001


def test_pi_frames_are_the_only_braille_spinner_source_in_package_src() -> None:
    offenders: list[Path] = []
    for path in PACKAGE_SRC.rglob("*.py"):
        if "⠋" in path.read_text():
            offenders.append(path)
    assert offenders == [SPINNER_PATH]


def test_no_other_module_level_braille_frame_literals_in_package_src() -> None:
    bad: list[tuple[Path, str]] = []
    for path in PACKAGE_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign | ast.AnnAssign):
                continue
            if not _contains_spinner_literal(node.value):
                continue
            target_names = _target_names(node)
            if path != SPINNER_PATH or "PI_FRAMES" not in target_names:
                bad.append((path, ",".join(sorted(target_names))))
    assert bad == []


def _target_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    names: set[str] = set()
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _contains_spinner_literal(node: ast.AST | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _braille_count(node.value) >= 5
    if isinstance(node, ast.Tuple | ast.List):
        chars = "".join(
            elt.value
            for elt in node.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        )
        return _braille_count(chars) >= 5
    return False


def _braille_count(text: str) -> int:
    return sum(0x2800 <= ord(ch) <= 0x28FF for ch in text)
