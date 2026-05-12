"""Ordered substrate component container."""

from __future__ import annotations

from typing import Literal

from tui.component import Component, RequestRender


class Container(Component):
    def __init__(self, *, direction: Literal["vertical"] = "vertical") -> None:
        super().__init__()
        if direction != "vertical":
            raise ValueError("only vertical containers are supported in M1")
        self.direction = direction
        self._children: list[Component] = []

    def append(self, child: Component) -> None:
        self._children.append(child)
        if self._request_render is not None:
            child.attach(self._request_render)
        self.request_render()

    def clear(self) -> None:
        for child in self._children:
            child.detach()
        self._children = []
        self.request_render()

    @property
    def children(self) -> list[Component]:
        return self._children

    def attach(self, request_render: RequestRender) -> None:
        super().attach(request_render)
        for child in self._children:
            child.attach(request_render)

    def detach(self) -> None:
        super().detach()
        for child in self._children:
            child.detach()

    def render(self, width: int) -> list[str]:
        rows: list[str] = []
        for child in self._children:
            rows.extend(child.render(width))
        return rows


__all__ = ["Container"]
