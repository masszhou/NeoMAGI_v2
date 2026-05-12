"""``magipi config`` subcommands (ADR-0019/0020).

- ``config init`` writes the bundled ``database.env`` template into the user
  config secrets directory; refuses to clobber unless ``--force`` is given
  (and then backs the existing file up to ``<path>.bak``). On Unix the newly
  created secret directories are set to ``0700`` and the file to ``0600``.
- ``config path`` prints the active DATABASE_* source so users can tell
  which file (or shell environment) is winning.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping

from storage.config import (
    DatabaseConfigError,
    read_env_template,
    resolve_database_config,
    user_database_env_path,
    would_fall_back_to,
)


def run_config_command(argv: list[str], *, prog: str) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{prog} config",
        description="Inspect and bootstrap NeoMAGI database configuration.",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="SUBCOMMAND")

    init_parser = sub.add_parser(
        "init",
        help="Write the bundled database.env template to the user config secrets directory.",
        description=(
            "Write the bundled database.env template to the user config secrets directory. "
            "Refuses to overwrite unless --force is given; then backs up the "
            "existing file to <path>.bak."
        ),
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing database.env (after backing it up to <path>.bak).",
    )
    init_parser.add_argument(
        "--path",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Override the destination path (default is the user config "
            "directory per ADR-0019)."
        ),
    )

    path_parser = sub.add_parser(
        "path",
        help="Show where the active DATABASE_* configuration is coming from.",
        description=(
            "Show the active DATABASE_* source. Prints `source=env` when the "
            "shell environment wins; `source=file:<path>` otherwise. With "
            "`source=env`, also prints `would-fall-back-to:` for debugging."
        ),
    )
    path_parser.add_argument(
        "--env-file",
        dest="env_file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Treat this file as the explicit --env-file source (top priority).",
    )

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return _run_init(force=args.force, override_path=args.path)
    if args.cmd == "path":
        return _run_path(env_file=args.env_file)
    parser.error(f"unknown subcommand: {args.cmd}")
    return 2  # unreachable, parser.error exits


def _run_init(
    *,
    force: bool,
    override_path: Path | None,
    env: Mapping[str, str] | None = None,
    stdout=None,
    stderr=None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    env_values = env if env is not None else os.environ

    target = (
        override_path.expanduser()
        if override_path is not None
        else user_database_env_path(env_values)
    )

    if target.exists() and not force:
        err.write(
            f"{target} already exists; use --force to overwrite "
            "(existing file is backed up to <path>.bak).\n"
        )
        return 1

    backup: Path | None = None
    if target.exists() and force:
        backup = target.parent / (target.name + ".bak")
        target.replace(backup)

    created_dirs = _collect_dirs_to_create(target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        for created in created_dirs:
            _try_chmod(created, 0o700, stderr=err)

    template = read_env_template()
    target.write_text(template, encoding="utf-8")
    if os.name != "nt":
        _try_chmod(target, 0o600, stderr=err)

    out.write(f"wrote {target}\n")
    if backup is not None:
        out.write(f"  backup: {backup}\n")
    out.write(
        "Edit DATABASE_PASSWORD and any other placeholders before running "
        "the interactive CLI.\n"
    )
    return 0


def _run_path(
    *,
    env_file: Path | None,
    env: Mapping[str, str] | None = None,
    stdout=None,
    stderr=None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    env_values = env if env is not None else os.environ

    try:
        _, source = resolve_database_config(env=env_values, env_file=env_file)
    except DatabaseConfigError as exc:
        err.write(f"{exc}\n")
        return 1

    out.write(source.format() + "\n")
    if source.kind == "env":
        fallback = would_fall_back_to(env=env_values, env_file=env_file)
        if fallback:
            out.write(f"would-fall-back-to: {fallback}\n")
    return 0


def _collect_dirs_to_create(target_dir: Path) -> list[Path]:
    """Return ancestors of ``target_dir`` that do not yet exist (innermost first).

    Used so :func:`_run_init` only chmods directories it actually creates;
    pre-existing directories like ``~/.config`` keep their original mode.
    """

    pending: list[Path] = []
    current = target_dir
    while not current.exists():
        pending.append(current)
        if current.parent == current:
            break
        current = current.parent
    return pending


def _try_chmod(path: Path, mode: int, *, stderr) -> None:
    try:
        os.chmod(path, mode)
    except OSError as exc:
        stderr.write(
            f"warning: could not set mode {mode:#o} on {path}: {exc}\n"
        )


__all__ = ["run_config_command"]
