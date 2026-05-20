"""``magipi taskrun`` top-level commands."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ai_provider.model_registry import canonical_model_ref, resolve_model, validate_thinking_level_for_model
from cli.cli_args import CACHE_RETENTIONS, DEFAULT_MODEL_REF, THINKING_LEVELS
from cli.core.model_settings import apply_settings_models
from cli.core.settings import LoadedSettings, SettingsManager
from cli.core.session_manager import SessionManager
from cli.core.taskrun_autorun import (
    MAX_AUTO_RUN_STEPS,
    TaskRunAutoRunOptions,
    TaskRunAutoRunResult,
)
from cli.core.taskrun_experiments import TaskRunExperimentOptions
from cli.core.taskrun_projection import task_event_to_dict
from cli.core.taskrun_runner import TaskRunHeadlessRunner
from cli.core.taskrun_service import (
    TaskRunResult,
    TaskRunRuntimeOptions,
    TaskRunService,
    TaskRunServiceError,
)
from cli.core.taskrun_views import (
    TaskRunEventsResult,
    TaskRunHistoryResult,
    TaskRunListResult,
    TaskRunNextResult,
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
from storage.taskrun_repository import TaskStepRecord


TaskRunCommandResult = (
    TaskRunResult
    | TaskRunAutoRunResult
    | TaskRunListResult
    | TaskRunHistoryResult
    | TaskRunNextResult
    | TaskRunEventsResult
)


def run_taskrun_command(argv: list[str], *, prog: str) -> int:
    parser = _build_parser(prog)
    args = parser.parse_args(argv)
    cwd = Path.cwd()
    try:
        result = _execute_command(args, cwd)
    except (TaskRunServiceError, KeyError, ValueError) as exc:
        sys.stderr.write(f"{prog} taskrun: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"{prog} taskrun: durable task storage unavailable: {exc}\n")
        return 2

    _print_result(result, include_summary=args.cmd == "summary")
    return result.exit_code


def _execute_command(args: argparse.Namespace, cwd: Path) -> TaskRunCommandResult:
    permission_profile = (
        _load_permission_profile_snapshot(args.permission, cwd)
        if args.cmd == "start" or (args.cmd == "run" and args.permission)
        else None
    )
    if args.cmd == "run" and permission_profile is not None:
        _validate_run_permission_profile(permission_profile)
    runtime_options = (
        _load_runtime_options(args, cwd) if args.cmd in {"step", "run"} else None
    )
    experiment_options = _load_experiment_options(args) if args.cmd == "run" else None
    db_config = load_database_config(env_file=args.env_file)
    conn = connect_database(db_config)
    try:
        ensure_schema(conn, db_config)
        task_repository = PostgresTaskRunRepository(conn, db_config)
        service = TaskRunService(task_repository)
        runner = _taskrun_runner(args, conn, db_config, task_repository, cwd)
        return _dispatch(
            args,
            service,
            cwd=cwd,
            permission_profile=permission_profile,
            runtime_options=runtime_options,
            experiment_options=experiment_options,
            runner=runner,
        )
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()


def _taskrun_runner(
    args: argparse.Namespace,
    conn: Any,
    db_config: Any,
    task_repository: Any,
    cwd: Path,
) -> TaskRunHeadlessRunner | None:
    if args.cmd not in {"step", "run"}:
        return None
    return TaskRunHeadlessRunner(
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

    _add_start_command(sub)
    _add_read_commands(sub)
    _add_step_command(sub)
    _add_run_command(sub)
    _add_cancel_command(sub)
    _add_close_command(sub)
    return parser


def _add_start_command(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    start = sub.add_parser("start", help="Create a pending TaskRun.")
    start.add_argument(
        "--permission",
        choices=BUILTIN_PERMISSION_PROFILE_NAMES,
        default="interactive",
        help="TaskRun permission profile.",
    )
    start.add_argument("goal", nargs="+", help="Task goal.")


def _add_read_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    status = sub.add_parser("status", help="Show TaskRun status.")
    status.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")

    summary = sub.add_parser("summary", help="Regenerate and print TaskRun summary.")
    summary.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")

    sub.add_parser("list", help="List workspace TaskRuns.")

    history = sub.add_parser("history", help="Show TaskRun step timeline and key events.")
    history.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")

    next_cmd = sub.add_parser("next", help="Show deterministic TaskRun next-step view.")
    next_cmd.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")

    events = sub.add_parser("events", help="Print TaskRun task_events as JSONL.")
    events.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")


def _add_step_command(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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


def _add_run_command(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    run = sub.add_parser("run", help="Run a bounded foreground TaskRun auto loop.")
    run.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")
    run.add_argument(
        "--max-steps",
        type=_parse_auto_run_max_steps,
        required=True,
        metavar="N",
        help="Hard maximum number of bounded steps to execute.",
    )
    run.add_argument(
        "--permission",
        choices=BUILTIN_PERMISSION_PROFILE_NAMES,
        default=None,
        help="Persist a new TaskRun permission profile before running.",
    )
    _add_run_runtime_flags(run)
    _add_experiment_flags(run)


def _add_run_runtime_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_REF,
        metavar="VENDOR/AUTH/MODEL",
        help="Runtime model override for this auto run.",
    )
    parser.add_argument(
        "--thinking-level",
        choices=THINKING_LEVELS,
        default="off",
        help="Runtime thinking level for this auto run.",
    )
    parser.add_argument(
        "--cache-retention",
        choices=CACHE_RETENTIONS,
        default=None,
        help="Provider prompt-cache retention override.",
    )


def _add_experiment_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--benchmark-command",
        default=None,
        metavar="CMD",
        help="Enable experiment mode and run this benchmark before/after each step.",
    )
    parser.add_argument(
        "--metric",
        default=None,
        metavar="NAME",
        help="Primary METRIC name to compare in experiment mode.",
    )
    parser.add_argument(
        "--metric-direction",
        choices=("lower", "higher"),
        default=None,
        help="Whether lower or higher primary metric values are better.",
    )
    parser.add_argument(
        "--min-delta",
        type=_parse_min_delta,
        default=None,
        metavar="N",
        help="Minimum primary metric improvement threshold. Defaults to 0.",
    )
    parser.add_argument(
        "--revert-on-regression",
        action="store_true",
        help="Safely revert tracked-file regressions when experiment mode is enabled.",
    )


def _add_close_command(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    close = sub.add_parser("close", help="Close an unexecuted TaskRun as cancelled.")
    close.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")


def _add_cancel_command(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    cancel = sub.add_parser("cancel", help="Cancel a pending or running TaskRun.")
    cancel.add_argument("id", nargs="?", default=None, help="TaskRun id or unique prefix.")


def _parse_auto_run_max_steps(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--max-steps must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("--max-steps must be >= 1")
    if parsed > MAX_AUTO_RUN_STEPS:
        raise argparse.ArgumentTypeError(
            f"--max-steps must be <= {MAX_AUTO_RUN_STEPS}"
        )
    return parsed


def _parse_min_delta(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--min-delta must be a number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("--min-delta must be a finite non-negative number")
    return parsed


def _dispatch(
    args: argparse.Namespace,
    service: TaskRunService,
    *,
    cwd: Path,
    permission_profile: Mapping[str, Any] | None,
    runtime_options: TaskRunRuntimeOptions | None,
    experiment_options: TaskRunExperimentOptions | None,
    runner: TaskRunHeadlessRunner | None,
) -> TaskRunCommandResult:
    match args.cmd:
        case "start":
            goal = " ".join(args.goal).strip()
            return service.start(goal, cwd, permission_profile=permission_profile)
        case "status":
            return service.status(args.id, cwd)
        case "summary":
            return service.summary(args.id, cwd)
        case "list":
            return service.list(cwd)
        case "history":
            return service.history(args.id, cwd)
        case "next":
            return service.next(args.id, cwd)
        case "events":
            return service.events(args.id, cwd)
        case "step":
            if runner is None:
                raise TaskRunServiceError("taskrun step runner is unavailable")
            return service.step(
                args.id,
                cwd,
                runtime_options=runtime_options,
                runner=runner,
            )
        case "run":
            if runner is None or runtime_options is None:
                raise TaskRunServiceError("taskrun run runner is unavailable")
            return service.run(
                args.id,
                cwd,
                options=TaskRunAutoRunOptions(
                    max_steps=args.max_steps,
                    runtime_options=runtime_options,
                    experiment_options=experiment_options,
                ),
                runner=runner,
                permission_profile=permission_profile,
            )
        case "cancel":
            return service.cancel(args.id, cwd)
        case "close":
            return service.close(args.id, cwd)
        case _:
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


def _validate_run_permission_profile(profile: Mapping[str, Any]) -> None:
    if not bool(profile.get("nonInteractive")):
        raise TaskRunServiceError(
            "taskrun run is headless and cannot use interactive permission profile; "
            "use --permission guarded or --permission full"
        )


def _load_runtime_options(args: argparse.Namespace, cwd: Path) -> TaskRunRuntimeOptions:
    apply_settings_models(SettingsManager(cwd=cwd).load().settings)
    model = resolve_model(args.model)
    thinking_level = validate_thinking_level_for_model(model, args.thinking_level)
    return TaskRunRuntimeOptions(
        model_ref=canonical_model_ref(model),
        thinking_level=thinking_level,
        cache_retention=args.cache_retention,
    )


def _load_experiment_options(args: argparse.Namespace) -> TaskRunExperimentOptions | None:
    benchmark_command = args.benchmark_command
    has_experiment_flag = any(
        [
            benchmark_command is not None,
            args.metric is not None,
            args.metric_direction is not None,
            args.min_delta is not None,
            bool(args.revert_on_regression),
        ]
    )
    if benchmark_command is None:
        if has_experiment_flag:
            raise TaskRunServiceError(
                "experiment flags require --benchmark-command"
            )
        return None
    if not str(benchmark_command).strip():
        raise TaskRunServiceError("--benchmark-command must not be empty")
    if args.metric is None or args.metric_direction is None:
        raise TaskRunServiceError(
            "--benchmark-command requires --metric and --metric-direction"
        )
    return TaskRunExperimentOptions(
        benchmark_command=str(benchmark_command),
        primary_metric=str(args.metric),
        metric_direction=args.metric_direction,
        min_delta=0.0 if args.min_delta is None else float(args.min_delta),
        revert_on_regression=bool(args.revert_on_regression),
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


def _print_result(
    result: TaskRunCommandResult,
    *,
    include_summary: bool,
) -> None:
    if isinstance(result, TaskRunListResult):
        _print_list_result(result)
        return
    if isinstance(result, TaskRunHistoryResult):
        _print_history_result(result)
        return
    if isinstance(result, TaskRunNextResult):
        _print_next_result(result)
        return
    if isinstance(result, TaskRunEventsResult):
        _print_events_result(result)
        return
    if isinstance(result, TaskRunAutoRunResult):
        _print_auto_run_result(result)
        return
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


def _print_auto_run_result(result: TaskRunAutoRunResult) -> None:
    task_run = result.task_run
    sys.stdout.write(f"id: {task_run.id}\n")
    sys.stdout.write(f"status: {task_run.status}\n")
    sys.stdout.write(f"goal: {_goal_preview(task_run.goal)}\n")
    sys.stdout.write(f"agent_session_id: {task_run.agent_session_id}\n")
    sys.stdout.write("iterations:\n")
    if not result.iterations:
        sys.stdout.write("- none\n")
    for index, iteration in enumerate(result.iterations, start=1):
        step = iteration.step
        sys.stdout.write(f"- iteration: {index}\n")
        sys.stdout.write(f"  step_id: {step.id}\n")
        sys.stdout.write(f"  step_status: {step.status}\n")
        sys.stdout.write(f"  task_status: {iteration.task_run_status}\n")
        sys.stdout.write(f"  conclusion: {_goal_preview(step.conclusion or '')}\n")
        sys.stdout.write(f"  next_action: {_goal_preview(str(step.output.get('next_action') or ''))}\n")
        sys.stdout.write(f"  stop_candidate: {iteration.stop_candidate or ''}\n")
    sys.stdout.write(f"stop_reason: {result.stop_reason}\n")
    sys.stdout.write(f"steps_run: {len(result.iterations)}\n")
    sys.stdout.write(f"projection_path: {result.projection.path}\n")


def _print_list_result(result: TaskRunListResult) -> None:
    if not result.items:
        sys.stdout.write("No TaskRuns in this workspace.\n")
        return
    for index, item in enumerate(result.items):
        if index:
            sys.stdout.write("\n")
        task_run = item.task_run
        sys.stdout.write(f"id: {task_run.id}\n")
        sys.stdout.write(f"status: {task_run.status}\n")
        sys.stdout.write(f"current_step: {_current_step_label(item.current_step)}\n")
        sys.stdout.write(f"updated_at: {task_run.updated_at}\n")
        sys.stdout.write(f"permission_profile: {item.permission_profile_name}\n")
        sys.stdout.write(f"goal: {_goal_preview(task_run.goal)}\n")
        sys.stdout.write(f"next_action: {_goal_preview(item.next_action)}\n")


def _print_history_result(result: TaskRunHistoryResult) -> None:
    task_run = result.task_run
    sys.stdout.write(f"id: {task_run.id}\n")
    sys.stdout.write(f"status: {task_run.status}\n")
    sys.stdout.write(f"goal: {_goal_preview(task_run.goal)}\n")
    sys.stdout.write(f"next_action: {result.next_action}\n")
    sys.stdout.write("steps:\n")
    if not result.steps:
        sys.stdout.write("- none\n")
    for item in result.steps:
        step = item.step
        sys.stdout.write(f"- step_index: {step.step_index}\n")
        sys.stdout.write(f"  step_id: {step.id}\n")
        sys.stdout.write(f"  step_status: {step.status}\n")
        sys.stdout.write(f"  title: {step.title}\n")
        sys.stdout.write(f"  started_at: {step.started_at or ''}\n")
        sys.stdout.write(f"  ended_at: {step.ended_at or ''}\n")
        sys.stdout.write(f"  conclusion: {_goal_preview(step.conclusion or '')}\n")
        sys.stdout.write(f"  reason: {_goal_preview(item.reason or '')}\n")
        sys.stdout.write(f"  tool_count: {item.counts.tool_count}\n")
        sys.stdout.write(
            f"  permission_decision_count: {item.counts.permission_decision_count}\n"
        )
        if item.experiments:
            sys.stdout.write("  experiments:\n")
            for experiment in item.experiments:
                metric = experiment.result.get("primaryMetric")
                value = experiment.metrics.get(metric) if isinstance(metric, str) else None
                reason = experiment.result.get("reason") or ""
                sys.stdout.write(
                    f"  - {experiment.decision} metric={metric or ''} "
                    f"value={value if value is not None else ''} "
                    f"reason={_goal_preview(str(reason))}\n"
                )
    sys.stdout.write("key_events:\n")
    if not result.key_events:
        sys.stdout.write("- none\n")
    for event in result.key_events:
        step = event.step_id or ""
        sys.stdout.write(
            f"- {event.occurred_at} {event.event_type} step_id={step}\n"
        )


def _print_next_result(result: TaskRunNextResult) -> None:
    sys.stdout.write(f"task_run_id: {result.task_run.id}\n")
    sys.stdout.write(f"task_status: {result.task_run.status}\n")
    sys.stdout.write(f"pending_step: {_step_record_label(result.pending_step)}\n")
    sys.stdout.write(f"current_step: {_step_record_label(result.current_step)}\n")
    sys.stdout.write(f"last_attempt: {_step_record_label(result.last_attempt)}\n")
    sys.stdout.write(f"next_action: {result.next_action}\n")
    sys.stdout.write(
        f"blocked_or_failed_reason: {result.blocked_or_failed_reason or ''}\n"
    )
    sys.stdout.write(
        f"permission_profile: {result.permission_profile.get('name', '')}\n"
    )
    sys.stdout.write("summary_snapshot:\n")
    sys.stdout.write(
        json.dumps(
            result.summary_snapshot,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    sys.stdout.write("\n")


def _print_events_result(result: TaskRunEventsResult) -> None:
    for event in result.events:
        sys.stdout.write(
            json.dumps(
                task_event_to_dict(event),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        sys.stdout.write("\n")


def _current_step_label(current_step: Mapping[str, object] | str | None) -> str:
    if current_step is None:
        return "none"
    if isinstance(current_step, str):
        return current_step
    index = current_step.get("step_index")
    status = current_step.get("status")
    title = current_step.get("title")
    step_id = current_step.get("id")
    parts = [
        f"#{index}" if index is not None else None,
        str(status) if status else None,
        str(title) if title else None,
        str(step_id) if step_id else None,
    ]
    return " ".join(part for part in parts if part) or "none"


def _step_record_label(step: TaskStepRecord | None) -> str:
    if step is None:
        return "none"
    return f"#{step.step_index} {step.status} {step.id} {step.title}"


def _goal_preview(goal: str, limit: int = 96) -> str:
    collapsed = " ".join(goal.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


__all__ = ["run_taskrun_command"]
