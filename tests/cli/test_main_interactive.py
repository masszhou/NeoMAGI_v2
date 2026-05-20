from __future__ import annotations

from contextlib import contextmanager

import pytest

import cli.__main__ as cli_main
from cli.cli_args import CliOptions


class _Conn:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def _options() -> CliOptions:
    return CliOptions(
        playback=None,
        print_only=False,
        help=False,
        print_message=None,
        model_ref="faux/local/faux-1",
        thinking_level="off",
        cache_retention=None,
        tui_render_mode="command",
        env_file=None,
    )


def test_interactive_main_closes_db_connection_when_controller_raises(
    monkeypatch,
) -> None:
    conn = _Conn()
    runtimes: list[object] = []
    _patch_interactive_dependencies(monkeypatch, conn=conn, run_error=RuntimeError("boom"), runtimes=runtimes)

    with pytest.raises(RuntimeError, match="boom"):
        cli_main._run_interactive(_options())  # noqa: SLF001

    assert conn.closed == 1
    assert getattr(runtimes[0], "shutdown_count") == 1


def test_interactive_main_closes_db_connection_when_schema_bootstrap_fails(
    monkeypatch,
) -> None:
    conn = _Conn()
    _patch_interactive_dependencies(monkeypatch, conn=conn, schema_error=RuntimeError("schema failed"))

    assert cli_main._run_interactive(_options()) == 2  # noqa: SLF001
    assert conn.closed == 1


def test_main_configures_logging_before_routing(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(cli_main, "configure_logging", lambda: calls.append("logging"))
    monkeypatch.setattr(
        cli_main,
        "parse_args",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stop")),
    )

    with pytest.raises(RuntimeError, match="stop"):
        cli_main.main([])

    assert calls == ["logging"]


def _patch_interactive_dependencies(
    monkeypatch,
    *,
    conn: _Conn,
    run_error: Exception | None = None,
    schema_error: Exception | None = None,
    runtimes: list[object] | None = None,
) -> None:
    import cli.core.session_manager as session_manager_module
    import cli.interactive.app as app_module
    import cli.interactive.runtime as runtime_module
    import storage
    import tui.app as tui_app_module
    import tui.lifecycle as lifecycle_module

    monkeypatch.setattr(storage, "load_database_config", lambda **_kwargs: object())
    monkeypatch.setattr(storage, "connect_database", lambda _config: conn)

    def ensure_schema(_conn, _config) -> None:
        if schema_error is not None:
            raise schema_error

    monkeypatch.setattr(storage, "ensure_schema", ensure_schema)
    monkeypatch.setattr(storage, "PostgresSessionRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(storage, "PostgresAuditRepository", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(storage, "PostgresAuditSink", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(session_manager_module, "SessionManager", lambda *_args, **_kwargs: object())

    class Runtime:
        def __init__(self, **_kwargs) -> None:
            self.shutdown_count = 0
            if runtimes is not None:
                runtimes.append(self)

        def shutdown(self) -> None:
            self.shutdown_count += 1

    class Controller:
        def __init__(self, **_kwargs) -> None:
            pass

        def bootstrap(self) -> None:
            pass

        def run(self) -> None:
            if run_error is not None:
                raise run_error

    class TUIApp:
        def __init__(self, **_kwargs) -> None:
            pass

    @contextmanager
    def lifecycle(app):
        yield app

    monkeypatch.setattr(runtime_module, "InteractiveAgentRuntime", Runtime)
    monkeypatch.setattr(app_module, "InteractiveController", Controller)
    monkeypatch.setattr(tui_app_module, "TUIApp", TUIApp)
    monkeypatch.setattr(lifecycle_module, "lifecycle", lifecycle)
