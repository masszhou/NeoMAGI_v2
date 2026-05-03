"""Transcript divider appended when one agent run finishes."""

from __future__ import annotations

from tui.component import Component
from tui.width import pad_to_width, truncate_to_width, visible_width

_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_RULE = "─"
_DEFERRED_MAX_WIDTH = 36


class RunDividerComponent(Component):
    defer_commit_while_last = True

    def __init__(self, *, elapsed_ms: int | None = None) -> None:
        super().__init__()
        self.elapsed_ms = elapsed_ms

    def render(self, width: int) -> list[str]:
        if width <= 0:
            return [""]

        label = _divider_label(self.elapsed_ms)
        label_width = visible_width(label)
        if label_width >= width:
            row = f"{_DIM}{truncate_to_width(label.strip(), width)}{_RESET}"
            return [pad_to_width(row, width)]

        remaining = max(0, width - label_width)
        left = remaining // 2
        right = remaining - left
        row = f"{_DIM}{_RULE * left}{label}{_RULE * right}{_RESET}"
        return self.enforce_width([pad_to_width(row, width)], width)

    def render_deferred(self, width: int) -> list[str]:
        if width <= 0:
            return [""]
        target_width = min(width, _DEFERRED_MAX_WIDTH)
        label = truncate_to_width(
            _divider_label(self.elapsed_ms).strip(),
            max(1, target_width - 4),
        )
        label_width = visible_width(label)
        remaining = max(0, target_width - label_width - 2)
        if remaining <= 0:
            row_text = label
        else:
            left = max(1, remaining // 2)
            right = max(1, remaining - left)
            row_text = f"{_RULE * left} {label} {_RULE * right}"
        row = f"{_DIM}{row_text}{_RESET}"
        return self.enforce_width([pad_to_width(row, width)], width)


def _divider_label(elapsed_ms: int | None) -> str:
    if elapsed_ms is None:
        return " Run complete "
    return f" Worked for {_format_elapsed(elapsed_ms)} "


def _format_elapsed(elapsed_ms: int) -> str:
    elapsed_ms = max(0, elapsed_ms)
    if elapsed_ms < 1_000:
        return f"{elapsed_ms} ms"

    total_seconds = max(1, round(elapsed_ms / 1_000))
    minutes, seconds = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


__all__ = ["RunDividerComponent"]
