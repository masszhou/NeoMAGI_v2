"""Container that renders the linear sequence of message components.

Composition + delegation only — no business semantics. The
:class:`InteractiveController` mutates the list in response to events;
this component just paints whatever is in there.
"""

from __future__ import annotations

from tui.component import Component
from tui.components.container import Container


class MessageListComponent(Component):
    def __init__(self) -> None:
        super().__init__()
        self._container = Container()
        self._scroll_offset_rows = 0
        self._last_budget = 0
        self._last_total_rows = 0
        self._committed_children_count = 0

    def append(self, child: Component) -> None:
        self._container.append(child)
        self.request_render()

    def attach(self, request_render):  # type: ignore[override]
        super().attach(request_render)
        self._container.attach(request_render)

    def detach(self) -> None:
        super().detach()
        self._container.detach()

    def clear(self) -> None:
        self._container.clear()
        self._scroll_offset_rows = 0
        self._last_total_rows = 0
        self._committed_children_count = 0
        self.request_render()

    @property
    def children(self) -> list[Component]:
        return self._container.children

    def render(self, width: int) -> list[str]:
        return self._container.render(width)

    def render_tail(self, width: int, budget: int) -> list[str]:
        return self._render_tail_rows(
            [child.render(width) for child in self._container.children],
            budget,
        )

    def render_uncommitted_tail(self, width: int, budget: int) -> list[str]:
        children = self._container.children
        groups = []
        for index, child in enumerate(children[self._committed_children_count :]):
            absolute_index = self._committed_children_count + index
            groups.append(
                _render_uncommitted_child(
                    child,
                    width,
                    is_last_child=absolute_index == len(children) - 1,
                )
            )
        return self._render_tail_rows(groups, budget)

    def commit_ready_rows(self, width: int) -> list[str]:
        rows: list[str] = []
        children = self._container.children
        while self._committed_children_count < len(children):
            child = children[self._committed_children_count]
            is_last_child = self._committed_children_count == len(children) - 1
            if is_last_child and getattr(child, "defer_commit_while_last", False):
                break
            if not _is_committable(child):
                break
            rows.extend(child.render(width))
            self._committed_children_count += 1
        if rows:
            self.request_render()
        return rows

    def _render_tail_rows(self, groups: list[list[str]], budget: int) -> list[str]:
        self._last_budget = max(0, budget)
        if budget <= 0:
            return []

        rows = [line for group in groups for line in group]
        self._last_total_rows = len(rows)
        self._clamp_scroll_offset()
        if len(rows) <= budget:
            return rows

        if self._scroll_offset_rows:
            end = max(0, len(rows) - self._scroll_offset_rows)
            start = max(0, end - budget)
            return rows[start:end]

        if len(groups) >= 2 and budget > 1:
            context = groups[-2]
            latest = groups[-1]
            context_budget = min(len(context), max(1, min(4, budget - 1)))
            latest_budget = budget - context_budget
            if latest_budget > 0:
                return context[:context_budget] + latest[-latest_budget:]

        return rows[-budget:]

    def scroll_page_up(self) -> None:
        page = max(1, self._last_budget - 1)
        self._scroll_offset_rows += page
        self._clamp_scroll_offset()
        self.request_render()

    def scroll_page_down(self) -> None:
        page = max(1, self._last_budget - 1)
        self.scroll_lines(-page)

    def scroll_lines(self, lines: int) -> None:
        self._scroll_offset_rows = max(0, self._scroll_offset_rows + lines)
        self._clamp_scroll_offset()
        self.request_render()

    def scroll_to_bottom(self) -> None:
        if self._scroll_offset_rows:
            self._scroll_offset_rows = 0
            self.request_render()

    def _clamp_scroll_offset(self) -> None:
        if self._last_budget <= 0:
            self._scroll_offset_rows = 0
            return
        max_offset = max(0, self._last_total_rows - self._last_budget)
        self._scroll_offset_rows = min(max(0, self._scroll_offset_rows), max_offset)


def _is_committable(child: Component) -> bool:
    if getattr(child, "ended", False):
        return True
    if getattr(child, "completed", False):
        return True
    if getattr(child, "aborted", False):
        return True
    if getattr(child, "error_text", None):
        return True
    return not _is_live_component(child)


def _is_live_component(child: Component) -> bool:
    return hasattr(child, "completed") or hasattr(child, "ended")


def _render_uncommitted_child(
    child: Component,
    width: int,
    *,
    is_last_child: bool,
) -> list[str]:
    if is_last_child and getattr(child, "defer_commit_while_last", False):
        render_deferred = getattr(child, "render_deferred", None)
        if callable(render_deferred):
            return render_deferred(width)
    return child.render(width)


__all__ = ["MessageListComponent"]
