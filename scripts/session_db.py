"""Local session-storage schema maintenance.

This is intentionally a development/test helper, not a production migration
runner. It loads the same DATABASE_* settings as the CLI, then calls the M6
idempotent schema bootstrap code.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from storage import connect_database, ensure_schema, load_database_config  # noqa: E402
from storage.schema import _quote_identifier  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    dotenv_path = Path(args.dotenv) if args.dotenv is not None else REPO_ROOT / ".env"
    config = load_database_config(dotenv_path=dotenv_path)
    schema = _quote_identifier(config.schema)

    with connect_database(config) as conn:
        if args.command == "ensure":
            ensure_schema(conn, config)
            _print_status(conn, schema)
            return 0
        if args.command == "status":
            _print_status(conn, schema)
            return 0
        if args.command == "reset":
            if not args.yes:
                parser.error("reset drops the configured schema; pass --yes to confirm")
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.commit()
            ensure_schema(conn, config)
            _print_status(conn, schema)
            return 0

    raise AssertionError(f"unhandled command: {args.command}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, inspect, or reset the local NeoMAGI session schema."
    )
    parser.add_argument(
        "--dotenv",
        help="Path to .env; defaults to the repository root .env. Environment variables win.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ensure", help="Create missing session tables and schema metadata.")
    subparsers.add_parser("status", help="Show session schema metadata if present.")
    reset = subparsers.add_parser(
        "reset",
        help="Drop the configured schema and recreate the session tables.",
    )
    reset.add_argument("--yes", action="store_true", help="Confirm schema data deletion.")
    return parser


def _print_status(conn: Any, schema: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = %s
                  AND table_name = 'agent_schema_meta'
            )
            """,
            (schema.strip('"'),),
        )
        exists = bool(cur.fetchone()[0])
        if not exists:
            print(f"{schema}.agent_schema_meta: missing")
            return

        cur.execute(
            f"""
            SELECT key, value, updated_at
            FROM {schema}.agent_schema_meta
            ORDER BY key
            """
        )
        rows = cur.fetchall()

    print(f"{schema}.agent_schema_meta:")
    for key, value, updated_at in rows:
        print(f"  {key}={value} updated_at={updated_at}")


if __name__ == "__main__":
    raise SystemExit(main())
