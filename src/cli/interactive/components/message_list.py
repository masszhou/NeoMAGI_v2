"""Container that renders the linear sequence of message components.

Composition + delegation only — no business semantics. The
:class:`InteractiveController` mutates the list in response to events;
this component just paints whatever is in there.
"""

from __future__ import annotations

from tui.component import Component


class MessageListComponent(Component):
    def __init__(self) -> None:
        super().__init__()
        self._children: list[Component] = []

    def append(self, child: Component) -> None:
        self._children.append(child)
        if self._request_render is not None:
            child.attach(self._request_render)
        self.request_render()

    def attach(self, request_render):  # type: ignore[override]
        super().attach(request_render)
        for child in self._children:
            child.attach(request_render)

    def detach(self) -> None:
        super().detach()
        for child in self._children:
            child.detach()

    def clear(self) -> None:
        for child in self._children:
            child.detach()
        self._children = []
        self.request_render()

    @property
    def children(self) -> list[Component]:
        return self._children

    def render(self, width: int) -> list[str]:
        rows: list[str] = []
        for child in self._children:
            rows.extend(child.render(width))
        return rows


__all__ = ["MessageListComponent"]
