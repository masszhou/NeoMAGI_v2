"""Interactive layer — bridges generic ``tui`` substrate to coding-agent semantics.

Pi-mono source map (commit ``97a38bf6``, see ADR-0011):
  - packages/coding-agent/src/modes/interactive/*.ts
"""

from .app import InteractiveController
from .event_router import EventRouter
from .tool_renderer_registry import (
    ToolRenderContext,
    ToolRendererRegistry,
    generic_tool_renderer,
)

__all__ = [
    "EventRouter",
    "InteractiveController",
    "ToolRenderContext",
    "ToolRendererRegistry",
    "generic_tool_renderer",
]
