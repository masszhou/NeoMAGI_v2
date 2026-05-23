"""Command-line entry point for ``magipi-webui``."""

from __future__ import annotations

import argparse
import getpass
import sys
from dataclasses import replace
from pathlib import Path

from .app import create_app
from .auth import hash_password
from .config import WebUIConfigError, load_webui_config


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "hash-password":
        password = args.password or getpass.getpass("Password: ")
        sys.stdout.write(hash_password(password) + "\n")
        return 0
    if args.command == "serve":
        return _serve(args)
    parser.print_help()
    return 0


def _serve(args: argparse.Namespace) -> int:
    try:
        config = load_webui_config(database_env_file=args.database_env_file)
    except WebUIConfigError as exc:
        sys.stderr.write(f"magipi-webui: {exc}\n")
        return 2
    if args.host is not None or args.port is not None:
        config = replace(
            config,
            host=args.host or config.host,
            port=args.port or config.port,
        )
    import uvicorn

    uvicorn.run(create_app(config=config), host=config.host, port=config.port)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="magipi-webui")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="start the local WebUI server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--database-env-file", type=Path, default=None)

    hash_cmd = sub.add_parser("hash-password", help="generate WEBUI_PASSWORD_HASH")
    hash_cmd.add_argument("password", nargs="?")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
