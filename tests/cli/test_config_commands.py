"""Coverage for ``magipi config init`` / ``config path`` and ``--env-file``."""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import cli.config_commands as config_commands_module
import storage.config as config_module
from cli.cli_args import parse_args
from cli.config_commands import _run_init, _run_path, run_config_command

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SRC_ROOT = REPO_ROOT / "packages" / "neomagi_pi" / "src"


def _write_env(path: Path, *, host: str = "explicit-host") -> None:
    path.write_text(
        "\n".join(
            [
                f"DATABASE_HOST={host}",
                "DATABASE_PORT=5432",
                "DATABASE_USER=neo",
                "DATABASE_PASSWORD=secret",
                "DATABASE_NAME=neomagi",
                "DATABASE_SCHEMA=neomagi",
            ]
        ),
        encoding="utf-8",
    )


def test_parse_args_collects_env_file(tmp_path) -> None:
    env_file = tmp_path / "explicit.env"
    env_file.write_text("DATABASE_HOST=x\n", encoding="utf-8")

    opts = parse_args(["--env-file", str(env_file)])

    assert opts.env_file == env_file


def test_config_init_writes_template(tmp_path) -> None:
    target = tmp_path / "neomagi" / "secrets" / "database.env"
    out = io.StringIO()
    err = io.StringIO()

    rc = _run_init(force=False, override_path=target, stdout=out, stderr=err)

    assert rc == 0
    assert target.is_file()
    body = target.read_text(encoding="utf-8")
    assert "DATABASE_HOST" in body
    assert "DATABASE_PASSWORD=change-me" in body
    assert f"wrote {target}" in out.getvalue()


def test_config_init_refuses_overwrite_without_force(tmp_path) -> None:
    target = tmp_path / "neomagi" / "secrets" / "database.env"
    target.parent.mkdir(parents=True)
    target.write_text("DATABASE_HOST=existing\n", encoding="utf-8")
    out = io.StringIO()
    err = io.StringIO()

    rc = _run_init(force=False, override_path=target, stdout=out, stderr=err)

    assert rc == 1
    assert "already exists" in err.getvalue()
    assert target.read_text(encoding="utf-8") == "DATABASE_HOST=existing\n"


def test_config_init_force_backs_up_existing(tmp_path) -> None:
    target = tmp_path / "neomagi" / "secrets" / "database.env"
    target.parent.mkdir(parents=True)
    target.write_text("DATABASE_HOST=existing\n", encoding="utf-8")
    out = io.StringIO()
    err = io.StringIO()

    rc = _run_init(force=True, override_path=target, stdout=out, stderr=err)

    backup = target.parent / (target.name + ".bak")
    assert rc == 0
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == "DATABASE_HOST=existing\n"
    assert "DATABASE_HOST=127.0.0.1" in target.read_text(encoding="utf-8")
    assert "backup:" in out.getvalue()


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only permission expectations")
def test_config_init_sets_unix_permissions(tmp_path) -> None:
    target = tmp_path / "private" / "secrets" / "database.env"
    out = io.StringIO()
    err = io.StringIO()

    rc = _run_init(force=False, override_path=target, stdout=out, stderr=err)

    assert rc == 0
    file_mode = stat.S_IMODE(target.stat().st_mode)
    dir_mode = stat.S_IMODE(target.parent.stat().st_mode)
    assert file_mode == 0o600
    assert dir_mode == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only permission expectations")
def test_config_init_preserves_existing_parent_mode(tmp_path) -> None:
    """Pre-existing parents (e.g. ``~/.config``) keep their original mode."""

    shared = tmp_path / "shared-config"
    shared.mkdir()
    shared.chmod(0o755)
    target = shared / "neomagi" / "secrets" / "database.env"
    out = io.StringIO()
    err = io.StringIO()

    rc = _run_init(force=False, override_path=target, stdout=out, stderr=err)

    assert rc == 0
    # The pre-existing ancestor must NOT be re-permissioned.
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    # The leaf we created is 0700.
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    # The file we wrote is 0600.
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only permission expectations")
def test_config_init_warns_when_chmod_fails(tmp_path, monkeypatch) -> None:
    target = tmp_path / "neomagi" / "secrets" / "database.env"
    out = io.StringIO()
    err = io.StringIO()

    def fail_chmod(path, mode):
        raise OSError("operation not permitted")

    monkeypatch.setattr(config_commands_module.os, "chmod", fail_chmod)

    rc = _run_init(force=False, override_path=target, stdout=out, stderr=err)

    assert rc == 0  # template still written
    assert target.is_file()
    assert "warning" in err.getvalue()
    assert "could not set mode" in err.getvalue()


def test_config_path_reports_file_source(tmp_path) -> None:
    env_file = tmp_path / "explicit.env"
    _write_env(env_file)
    out = io.StringIO()
    err = io.StringIO()

    rc = _run_path(env_file=env_file, env={}, stdout=out, stderr=err)

    assert rc == 0
    assert out.getvalue().startswith(f"source=file:{env_file}")


def test_config_path_reports_env_with_fallback(tmp_path, monkeypatch) -> None:
    user_path = tmp_path / "userconfig" / "secrets" / "database.env"
    user_path.parent.mkdir(parents=True)
    _write_env(user_path, host="user-host")
    monkeypatch.setattr(
        config_module,
        "user_database_env_path",
        lambda env_values: user_path,
    )
    monkeypatch.setattr(config_module, "_app_root_dotenv_path", lambda: None)
    out = io.StringIO()
    err = io.StringIO()
    env = {
        "DATABASE_HOST": "shell-host",
        "DATABASE_PORT": "5432",
        "DATABASE_USER": "u",
        "DATABASE_PASSWORD": "p",
        "DATABASE_NAME": "n",
    }

    rc = _run_path(env_file=None, env=env, stdout=out, stderr=err)

    assert rc == 0
    output = out.getvalue()
    assert output.startswith("source=env\n")
    assert "would-fall-back-to:" in output
    assert str(user_path) in output


def test_config_path_propagates_no_source_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        config_module,
        "user_database_env_path",
        lambda env_values: tmp_path / "missing" / "secrets" / "database.env",
    )
    monkeypatch.setattr(config_module, "_app_root_dotenv_path", lambda: None)
    out = io.StringIO()
    err = io.StringIO()

    rc = _run_path(env_file=None, env={}, stdout=out, stderr=err)

    assert rc == 1
    assert "missing database configuration" in err.getvalue()


def test_run_config_command_dispatches_init(tmp_path, capsys) -> None:
    target = tmp_path / "neomagi" / "secrets" / "database.env"

    rc = run_config_command(["init", "--path", str(target)], prog="magipi")

    captured = capsys.readouterr()
    assert rc == 0
    assert target.is_file()
    assert f"wrote {target}" in captured.out


def test_run_config_command_path_subcommand(tmp_path, capsys) -> None:
    env_file = tmp_path / "explicit.env"
    _write_env(env_file)

    rc = run_config_command(["path", "--env-file", str(env_file)], prog="magipi")

    captured = capsys.readouterr()
    assert rc == 0
    assert f"source=file:{env_file}" in captured.out


def _run_module(*args: str, env_overrides: dict[str, str] | None = None,
                cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(PACKAGE_SRC_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(  # noqa: S603 — args constants
        [sys.executable, "-m", "cli", *args],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=15.0,
        stdin=subprocess.DEVNULL,
    )


def test_module_config_init_subprocess_writes_template(tmp_path) -> None:
    target = tmp_path / "neomagi" / "secrets" / "database.env"

    result = _run_module("config", "init", "--path", str(target))

    assert result.returncode == 0, result.stderr
    assert target.is_file()
    assert "DATABASE_HOST" in target.read_text(encoding="utf-8")


def test_module_config_path_subprocess_uses_env_file(tmp_path) -> None:
    env_file = tmp_path / "explicit.env"
    _write_env(env_file, host="subprocess-host")

    result = _run_module("config", "path", "--env-file", str(env_file))

    assert result.returncode == 0, result.stderr
    assert f"source=file:{env_file}" in result.stdout


def test_help_lists_env_file_flag() -> None:
    result = _run_module("--help")

    assert result.returncode == 0
    assert "--env-file" in result.stdout
