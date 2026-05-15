"""``magipi taskrun`` top-level commands."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ai_provider.model_registry import canonical_model_ref, resolve_model, validate_thinking_level_for_model
from cli.cli_args import CACHE_RETENTIONS, DEFAULT_MODEL_REF, THINKING_LEVELS
from cli.core.model_settings import apply_settings_models
from cli.core.settings import LoadedSettings, SettingsManager
from cli.core.taskrun_runner import TaskRunHeadlessRunner
from cli.core.session_manager import SessionManager
from cli.core.taskrun_service import (
    TaskRunResult,
    TaskRunRuntimeOptions,
    TaskRunService,
    TaskRunServiceError,
)
from policy.permission_profiles import (
    BUILTIN_PERMISSION_PROFILE_NAMES,
    PermissionProfileError,
    build_permission_profile_snapshot,
    profile_explicit_scope_keys,
)
from storage import (
    PostgresAuditRepository,
    PostgresSessionRepository,
    PostgresTaskRunRepository,
    connect_database,
    ensure_schema,
    load_database_config,
)


def run_taskrun_command(argv: list[str], *, prog: str) -> int:
    parser = _build_parser(prog)
    args = parser.parse_args(argv)
    cwd = Path.cwd()
    try:
        permission_profile = (
            _load_permission_profile_snapshot(args.permission, cwd)
            if args.cmd == "start"
            else None
        )
        runtime_options = _load_runtime_options(args, cwd) if args.cmd == "step" else None
        db_config = load_database_config(env_file=args.env_file)
        conn = connect_database(db_config)
        try:
            ensure_schema(conn, db_config)
            task_repository = PostgresTaskRunRepository(conn, db_config)
            service = TaskRunService(task_repository)
            runner = (
                TaskRunHeadlessRunner(
                    session_manager=SessionManager(
                        PostgresSessionRepository(conn, db_config),
                        include_taskrun_owned=True,
                    ),
                    task_repository=task_repository,
                    cwd=cwd,
                    audit_sink_factory=lambda session_id: _postgres_audit_sink(
                        conn,
                        db_config,
                        session_id,
                    ),
                )
                if args.cmd == "step"
                else None
            )
            result = _dispatch(
                args,
                service,
                cwd=cwd,
                permission_profile=permission_profile,
                runtime_options=runtime_options,
                runner=runner,
            )
        finally:
            close = getattr(conn, "close", None)
            if callable(close):
                close()
    except (TaskRunServiceError, KeyError, ValueError) as exc:
        sys.stderr.write(f"{prog} taskrun: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"{prog} taskrun: durable task storage unavailable: {exc}\n")
        return 2

    _print_result(result, include_summary=args.cmd == "summary")
    return result.exit_code


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
    start.add_argument(
        "--permission",
        choices=BUILTIN_PERMISSION_PROFILE_NAMES,
        default="interactive",
        help="TaskRun permission profile.",
    )
    start.add_argument("goal", nargs="+", help="Task goal.")

    status = sub.add_parser("status", help="Show TaskRun status.")
    status.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")

    summary = sub.add_parser("summary", help="Regenerate and print TaskRun summary.")
    summary.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")

    step = sub.add_parser("step", help="Execute exactly one manual TaskRun step.")
    step.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")
    step.add_argument(
        "--model",
        default=DEFAULT_MODEL_REF,
        metavar="VENDOR/AUTH/MODEL",
        help="Runtime model override for this manual step.",
    )
    step.add_argument(
        "--thinking-level",
        choices=THINKING_LEVELS,
        default="off",
        help="Runtime thinking level for this manual step.",
    )
    step.add_argument(
        "--cache-retention",
        choices=CACHE_RETENTIONS,
        default=None,
        help="Provider prompt-cache retention override.",
    )

    close = sub.add_parser("close", help="Close an unexecuted TaskRun as cancelled.")
    close.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")
    return parser


def _dispatch(
    args: argparse.Namespace,
    service: TaskRunService,
    *,
    cwd: Path,
    permission_profile: Mapping[str, Any] | None,
    runtime_options: TaskRunRuntimeOptions | None,
    runner: TaskRunHeadlessRunner | None,
) -> TaskRunResult:
    if args.cmd == "start":
        goal = " ".join(args.goal).strip()
        return service.start(goal, cwd, permission_profile=permission_profile)
    if args.cmd == "status":
        return service.status(args.id, cwd)
    if args.cmd == "summary":
        return service.summary(args.id, cwd)
    if args.cmd == "step":
        if runner is None:
            raise TaskRunServiceError("taskrun step runner is unavailable")
        return service.step(
            args.id,
            cwd,
            runtime_options=runtime_options,
            runner=runner,
        )
    if args.cmd == "close":
        return service.close(args.id, cwd)
    raise AssertionError(f"unhandled taskrun command: {args.cmd}")


def _load_permission_profile_snapshot(name: str, cwd: Path) -> dict[str, Any]:
    loaded = SettingsManager(cwd=cwd).load()
    config, sources, explicit_keys = _selected_profile_config(loaded, name)
    try:
        return build_permission_profile_snapshot(
            name,
            config,
            sources=sources,
            explicit_scope=bool(explicit_keys),
            explicit_scope_keys=explicit_keys,
        )
    except PermissionProfileError as exc:
        raise TaskRunServiceError(str(exc)) from exc


def _load_runtime_options(args: argparse.Namespace, cwd: Path) -> TaskRunRuntimeOptions:
    apply_settings_models(SettingsManager(cwd=cwd).load().settings)
    model = resolve_model(args.model)
    thinking_level = validate_thinking_level_for_model(model, args.thinking_level)
    return TaskRunRuntimeOptions(
        model_ref=canonical_model_ref(model),
        thinking_level=thinking_level,
        cache_retention=args.cache_retention,
    )


def _postgres_audit_sink(conn: Any, db_config: Any, session_id: str):
    from storage.audit_sink import PostgresAuditSink

    return PostgresAuditSink(
        repository=PostgresAuditRepository(conn, db_config),
        session_id_provider=lambda: session_id,
    )


def _selected_profile_config(
    loaded: LoadedSettings,
    name: str,
) -> tuple[dict[str, Any], list[str], list[str]]:
    global_config = _raw_profile_config(loaded.global_raw, name)
    project_config = _raw_profile_config(loaded.project_raw, name)
    config = _deep_merge(global_config, project_config)
    sources = ["builtin"]
    if global_config:
        sources.append("global")
    if project_config:
        sources.append("project")
    explicit_keys = sorted(
        set(profile_explicit_scope_keys(global_config))
        | set(profile_explicit_scope_keys(project_config))
    )
    return config, sources, explicit_keys


def _raw_profile_config(raw: Mapping[str, Any], name: str) -> dict[str, Any]:
    taskrun = raw.get("taskrun")
    if not isinstance(taskrun, Mapping):
        return {}
    profiles = taskrun.get("permissionProfiles", taskrun.get("permission_profiles"))
    if not isinstance(profiles, Mapping):
        return {}
    config = profiles.get(name)
    return dict(config) if isinstance(config, Mapping) else {}


def _deep_merge(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if isinstance(merged.get(key), Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _print_result(result: TaskRunResult, *, include_summary: bool) -> None:
    task_run = result.task_run
    summary = result.summary
    sys.stdout.write(f"id: {task_run.id}\n")
    sys.stdout.write(f"status: {task_run.status}\n")
    sys.stdout.write(f"goal: {_goal_preview(task_run.goal)}\n")
    sys.stdout.write(f"agent_session_id: {task_run.agent_session_id}\n")
    if result.step is not None:
        sys.stdout.write(f"step_id: {result.step.id}\n")
        sys.stdout.write(f"step_status: {result.step.status}\n")
        sys.stdout.write(f"conclusion: {result.step.conclusion or ''}\n")
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
