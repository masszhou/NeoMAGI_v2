"""``python -m cli`` entry point plus ``magipi`` / ``neomagi`` scripts.

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
from pathlib import Path

from .cli_args import CliOptions, parse_args
from .logging_setup import configure_logging


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    raw_argv = list(argv if argv is not None else sys.argv[1:])
    if raw_argv and raw_argv[0] == "config":
        from .config_commands import run_config_command

        return run_config_command(raw_argv[1:], prog=_program_name())
    if raw_argv and raw_argv[0] == "taskrun":
        from .taskrun_commands import run_taskrun_command

        return run_taskrun_command(raw_argv[1:], prog=_program_name())

    opts = parse_args(raw_argv, prog=_program_name())

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
    conn = None
    if opts.playback is None:
        try:
            conn, session_manager, audit_sink = _open_durable_storage(
                opts,
                runtime_provider=lambda: runtime,
            )
        except Exception as exc:
            sys.stderr.write(f"neomagi: durable session storage unavailable: {exc}\n")
            return 2
    try:
        if opts.playback is None:
            runtime = InteractiveAgentRuntime(
                model_ref=opts.model_ref,
                thinking_level=opts.thinking_level,
                cache_retention=opts.cache_retention,
                session_manager=session_manager,
                audit_sink=audit_sink,
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
    finally:
        if runtime is not None:
            runtime.shutdown()
        _close_if_possible(conn)


def _open_durable_storage(opts: CliOptions, *, runtime_provider):
    from cli.core.session_manager import SessionManager
    from storage import (
        PostgresAuditRepository,
        PostgresAuditSink,
        PostgresSessionRepository,
        connect_database,
        ensure_schema,
        load_database_config,
    )

    conn = None
    try:
        db_config = load_database_config(env_file=opts.env_file)
        conn = connect_database(db_config)
        ensure_schema(conn, db_config)
        session_manager = SessionManager(PostgresSessionRepository(conn, db_config))
        audit_sink = PostgresAuditSink(
            repository=PostgresAuditRepository(conn, db_config),
            session_id_provider=lambda: _runtime_session_id(runtime_provider()),
        )
        return conn, session_manager, audit_sink
    except Exception:
        _close_if_possible(conn)
        raise


def _runtime_session_id(runtime) -> str:
    if runtime is None or runtime.state.durable_session_id is None:
        return ""
    return runtime.state.durable_session_id


def _close_if_possible(resource: object | None) -> None:
    if resource is None:
        return
    close = getattr(resource, "close", None)
    if callable(close):
        close()


def _resolve_render_mode(opts: CliOptions) -> str:
    if opts.tui_render_mode is not None:
        return opts.tui_render_mode
    return "canvas" if opts.playback is not None else "command"


def _program_name() -> str:
    name = Path(sys.argv[0]).name
    if name == "__main__.py":
        return "python -m cli"
    return name or "magipi"


if __name__ == "__main__":
    raise SystemExit(main())
