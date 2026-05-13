"""``magipi taskrun`` top-level commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cli.core.taskrun_service import TaskRunResult, TaskRunService, TaskRunServiceError
from storage import (
    PostgresTaskRunRepository,
    connect_database,
    ensure_schema,
    load_database_config,
)


def run_taskrun_command(argv: list[str], *, prog: str) -> int:
    parser = _build_parser(prog)
    args = parser.parse_args(argv)
    try:
        db_config = load_database_config(env_file=args.env_file)
        conn = connect_database(db_config)
        try:
            ensure_schema(conn, db_config)
            service = TaskRunService(PostgresTaskRunRepository(conn, db_config))
            result = _dispatch(args, service)
        finally:
            close = getattr(conn, "close", None)
            if callable(close):
                close()
    except (TaskRunServiceError, KeyError) as exc:
        sys.stderr.write(f"{prog} taskrun: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"{prog} taskrun: durable task storage unavailable: {exc}\n")
        return 2

    _print_result(result, include_summary=args.cmd == "summary")
    return 0


def _build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{prog} taskrun",
        description="Create, inspect, summarize, and close workspace TaskRuns.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--env-file",
        dest="env_file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Load DATABASE_* settings from this explicit env file.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="SUBCOMMAND")

    start = sub.add_parser("start", help="Create a pending TaskRun.")
    start.add_argument("goal", nargs="+", help="Task goal.")

    status = sub.add_parser("status", help="Show TaskRun status.")
    status.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")

    summary = sub.add_parser("summary", help="Regenerate and print TaskRun summary.")
    summary.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")

    close = sub.add_parser("close", help="Close an unexecuted TaskRun as cancelled.")
    close.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")
    return parser


def _dispatch(args: argparse.Namespace, service: TaskRunService) -> TaskRunResult:
    cwd = Path.cwd()
    if args.cmd == "start":
        goal = " ".join(args.goal).strip()
        return service.start(goal, cwd)
    if args.cmd == "status":
        return service.status(args.id, cwd)
    if args.cmd == "summary":
        return service.summary(args.id, cwd)
    if args.cmd == "close":
        return service.close(args.id, cwd)
    raise AssertionError(f"unhandled taskrun command: {args.cmd}")


def _print_result(result: TaskRunResult, *, include_summary: bool) -> None:
    task_run = result.task_run
    summary = result.summary
    sys.stdout.write(f"id: {task_run.id}\n")
    sys.stdout.write(f"status: {task_run.status}\n")
    sys.stdout.write(f"goal: {_goal_preview(task_run.goal)}\n")
    sys.stdout.write(f"agent_session_id: {task_run.agent_session_id}\n")
    sys.stdout.write(f"projection_path: {result.projection.path}\n")
    sys.stdout.write(f"next_action: {summary.get('next_action', '')}\n")
    if include_summary:
        sys.stdout.write("summary:\n")
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        sys.stdout.write("\n")


def _goal_preview(goal: str, limit: int = 96) -> str:
    collapsed = " ".join(goal.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


__all__ = ["run_taskrun_command"]
