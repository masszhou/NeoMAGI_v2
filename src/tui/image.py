"""Inline image placeholder primitive (M1 only renders a fallback block).

True terminal-image protocols (Kitty / iTerm / sixel) are deferred to M2/M5;
this module exposes :func:`render_placeholder` + :func:`detect_protocol`
so the interactive layer has a stable surface to swap implementations
under later.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

ImageProtocol = Literal["none", "kitty", "iterm", "sixel"]


@dataclass(frozen=True)
class ImageMeta:
    source: str
    """Path on disk, MCP resource id, or any short label."""
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None


def detect_protocol() -> ImageProtocol:
    """Best-effort terminal-protocol detection.

    M1 always returns ``"none"`` so we render the placeholder. The function
    exists now so callers don't need to grow a new code path when M2 turns
    Kitty / iTerm support on.
    """

    if os.environ.get("KITTY_WINDOW_ID"):
        return "none"  # Will become "kitty" once we wire the protocol.
    if os.environ.get("TERM_PROGRAM") == "iTerm.app":
        return "none"  # Will become "iterm" later.
    return "none"


def render_placeholder(meta: ImageMeta) -> str:
    """Single-line fallback that's safe to embed in any message component."""

    dims = ""
    if meta.width and meta.height:
        dims = f" {meta.width}x{meta.height}"
    return f"[image: {meta.source}{dims} (terminal preview unavailable)]"


__all__ = ["ImageMeta", "ImageProtocol", "detect_protocol", "render_placeholder"]
