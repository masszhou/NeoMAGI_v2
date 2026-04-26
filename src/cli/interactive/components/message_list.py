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
        self.request_render()

    @property
    def children(self) -> list[Component]:
        return self._container.children

    def render(self, width: int) -> list[str]:
        return self._container.render(width)


__all__ = ["MessageListComponent"]
