"""argv → typed options for the ``neomagi`` CLI.

P1-M1 only wires three flags: ``--playback`` (path), ``--print``
(placeholder), and ``--help``. M9/M10 will fold in settings / login /
export.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CliOptions:
    playback: Path | None
    print_only: bool
    help: bool
    print_message: str | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neomagi",
        description=(
            "NeoMAGI v2 — local-first personal agent CLI (P1-M1 skeleton). "
            "Run with no arguments to enter the interactive TUI."
        ),
        add_help=True,
    )
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
    return parser


def parse_args(argv: list[str] | None = None) -> CliOptions:
    parser = build_parser()
    args = parser.parse_args(argv)
    print_only = args.print_message is not None
    return CliOptions(
        playback=args.playback,
        print_only=print_only,
        help=False,
        print_message=args.print_message,
    )


__all__ = ["CliOptions", "build_parser", "parse_args"]
