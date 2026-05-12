"""argv → typed options for the NeoMAGI Pi-compatible CLI."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_provider.model_registry import resolve_model, validate_thinking_level_for_model
from ai_provider.types import CacheRetention, ThinkingLevel

DEFAULT_MODEL_REF = "faux/local/faux-1"
THINKING_LEVELS: tuple[ThinkingLevel, ...] = (
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)
CACHE_RETENTIONS: tuple[CacheRetention, ...] = ("none", "short", "long")
TUI_RENDER_MODES: tuple[Literal["command", "canvas"], ...] = ("command", "canvas")
_RUNTIME_FLAGS = frozenset(
    {"--model", "--thinking-level", "--cache-retention", "--tui-render-mode"}
)


@dataclass(frozen=True)
class CliOptions:
    playback: Path | None
    print_only: bool
    help: bool
    print_message: str | None
    model_ref: str
    thinking_level: ThinkingLevel
    cache_retention: CacheRetention | None
    tui_render_mode: Literal["command", "canvas"] | None
    env_file: Path | None = None


def build_parser(*, prog: str = "magipi") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "NeoMAGI v2 — local-first personal agent CLI. "
            "Run with no arguments to enter the interactive TUI."
        ),
        add_help=True,
        allow_abbrev=False,
    )
    _add_session_args(parser)
    _add_runtime_args(parser)
    _add_storage_args(parser)
    return parser


def _add_session_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--playback",
        type=Path,
        metavar="DIR",
        default=None,
        help=(
            "Replay an `events.jsonl` (+ optional `playback.json` sidecar) "
            "fixture instead of contacting a real provider."
        ),
    )
    parser.add_argument(
        "--print",
        dest="print_message",
        nargs="?",
        const="",
        default=None,
        metavar="MESSAGE",
        help=(
            "Non-interactive single-shot mode (M1: returns a stub message; "
            "wired to a real provider in M9/M10)."
        ),
    )


def _add_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_REF,
        metavar="VENDOR/AUTH/MODEL",
        help=(
            "Interactive runtime model override "
            "(format: vendor/auth-channel/model; legacy provider/model still "
            "accepted; default: faux/local/faux-1)."
        ),
    )
    parser.add_argument(
        "--thinking-level",
        choices=THINKING_LEVELS,
        default="off",
        help="Interactive runtime thinking level.",
    )
    parser.add_argument(
        "--cache-retention",
        choices=CACHE_RETENTIONS,
        default=None,
        help="Provider prompt-cache retention override.",
    )
    parser.add_argument(
        "--tui-render-mode",
        choices=TUI_RENDER_MODES,
        default=None,
        help=(
            "Interactive TUI render mode: command appends transcript to terminal "
            "scrollback; canvas uses the fixed viewport."
        ),
    )


def _add_storage_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-file",
        dest="env_file",
        type=Path,
        metavar="PATH",
        default=None,
        help=(
            "Explicit DATABASE_* .env file (highest-priority source per "
            "ADR-0019). Must exist and contain all required keys; otherwise "
            "fail-fast."
        ),
    )


def parse_args(argv: list[str] | None = None, *, prog: str = "magipi") -> CliOptions:
    raw_argv = list(argv) if argv is not None else None
    parser = build_parser(prog=prog)
    args = parser.parse_args(raw_argv)
    print_only = args.print_message is not None
    explicit_runtime_flags = _RUNTIME_FLAGS & {
        item.split("=", 1)[0] for item in (raw_argv or []) if item.startswith("--")
    }
    if print_only and explicit_runtime_flags:
        parser.error("--print cannot be combined with interactive runtime flags")
    try:
        model = resolve_model(args.model)
        thinking_level = validate_thinking_level_for_model(model, args.thinking_level)
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))
    return CliOptions(
        playback=args.playback,
        print_only=print_only,
        help=False,
        print_message=args.print_message,
        model_ref=args.model,
        thinking_level=thinking_level,
        cache_retention=args.cache_retention,
        tui_render_mode=args.tui_render_mode,
        env_file=args.env_file,
    )


__all__ = ["CliOptions", "TUI_RENDER_MODES", "build_parser", "parse_args"]
