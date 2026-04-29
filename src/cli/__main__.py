"""``python -m cli`` entry point + the ``neomagi`` console script.

Routes argv to either:

- ``--print``: a stub one-shot mode (M1 just prints "not implemented").
- ``--playback DIR``: enter TUI then drive ``PlaybackHarness``.
- otherwise: enter the interactive TUI backed by ``agent_core.Agent``.

Real work happens inside
``cli.interactive.app.InteractiveController`` — this file is just argv +
process exit code routing.
"""

from __future__ import annotations

import sys

from .cli_args import CliOptions, parse_args


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv if argv is not None else sys.argv[1:])

    if opts.print_only:
        return _run_print(opts)
    return _run_interactive(opts)


def _run_print(opts: CliOptions) -> int:
    msg = opts.print_message or ""
    sys.stderr.write(
        "neomagi --print: not implemented in M1 "
        "(tracked for M9/M10 once real provider lands).\n"
    )
    if msg:
        sys.stderr.write(f"  echo: {msg}\n")
    return 0


def _run_interactive(opts: CliOptions) -> int:
    # Lazy import: keeps `--help` / `--print` from paying the TUI import cost
    # and keeps a clean separation between argv routing and runtime.
    from cli.interactive.app import InteractiveController
    from cli.interactive.runtime import InteractiveAgentRuntime
    from tui.app import TUIApp
    from tui.lifecycle import lifecycle

    tui_app = TUIApp(render_mode=_resolve_render_mode(opts))
    runtime = None
    if opts.playback is None:
        runtime = InteractiveAgentRuntime(
            model_ref=opts.model_ref,
            thinking_level=opts.thinking_level,
            cache_retention=opts.cache_retention,
        )
    controller = InteractiveController(
        tui_app=tui_app,
        playback_dir=opts.playback,
        runtime=runtime,
    )
    controller.bootstrap()
    with lifecycle(tui_app):
        controller.run()
    return 0


def _resolve_render_mode(opts: CliOptions) -> str:
    if opts.tui_render_mode is not None:
        return opts.tui_render_mode
    return "canvas" if opts.playback is not None else "command"


if __name__ == "__main__":
    raise SystemExit(main())
