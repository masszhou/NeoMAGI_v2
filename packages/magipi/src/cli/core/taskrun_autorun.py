"""Bounded foreground TaskRun auto-loop semantics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from cli.core.taskrun_errors import TaskRunServiceError
from cli.core.taskrun_autorun_common import (
    AutoRunBudget as _AutoRunBudget,
    AutoRunCounters as _AutoRunCounters,
    AutoRunStopDecision as _AutoRunStopDecision,
    TaskRunAutoRunIteration,
    TaskRunAutoRunOptions,
    TaskRunAutoRunResult,
    advance_auto_run_counters as _advance_auto_run_counters,
    append_auto_run_event as _append_auto_run_event,
    count_step_denies as _count_step_denies,
    evaluate_auto_run_stop,
    validate_auto_run_ready as _validate_run_ready,
    validate_headless_profile as _validate_headless_profile,
)
from cli.core.taskrun_host_contract import TaskRunHostContext, normalize_host_context
from cli.core.taskrun_experiment_loop import execute_experiment_auto_run_iteration
from cli.core.taskrun_experiments import (
    TaskRunExperimentAttempt,
    TaskRunExperimentOptions,
    validate_experiment_permission_profile,
    validate_experiment_options,
)
from cli.core.taskrun_service_internals import TaskRunServiceInternals
from cli.core.taskrun_step import TaskRunRuntimeOptions, TaskRunStepRunner
from policy.permission_profiles import (
    DEFAULT_MAX_CONSECUTIVE_DENIES,
    DEFAULT_MAX_TOTAL_DENIES,
    PermissionProfileError,
    normalize_permission_profile_snapshot,
)
from storage.ids import new_db_uuid
from storage.taskrun_repository import (
    TERMINAL_TASKRUN_STATUSES,
    TaskRunRecord,
    TaskStepRecord,
)


MAX_AUTO_RUN_STEPS = 50
DEFAULT_MAX_CONSECUTIVE_FAILURES = 2
AUTO_RUN_STOP_REASONS = frozenset(
    {
        "max_steps_reached",
        "consecutive_failures_exhausted",
        "consecutive_denies_exhausted",
        "total_denies_exhausted",
        "permission_block",
        "user_cancelled",
        "budget_exhausted",
        "no_runnable_taskrun",
        "runner_error",
        "experiment_blocked",
        "experiment_reverted",
        "benchmark_failed",
        "metric_parse_failed",
        "unsafe_revert",
    }
)
_STEP_ATTRIBUTED_STOP_REASONS = frozenset(
    {
        "consecutive_failures_exhausted",
        "consecutive_denies_exhausted",
        "total_denies_exhausted",
        "permission_block",
        "runner_error",
        "experiment_blocked",
        "experiment_reverted",
        "benchmark_failed",
        "metric_parse_failed",
        "unsafe_revert",
    }
)


def run_taskrun_auto_loop(
    service: TaskRunServiceInternals,
    id_or_prefix: str | None,
    cwd: str,
    *,
    options: TaskRunAutoRunOptions,
    runner: TaskRunStepRunner,
    permission_profile: Mapping[str, Any] | None = None,
    host_context: TaskRunHostContext | Mapping[str, object] | None = None,
) -> TaskRunAutoRunResult:
    workspace_root = str(cwd)
    host_context = normalize_host_context(host_context)
    max_steps = _validate_auto_run_max_steps(options.max_steps)
    runtime_options = options.runtime_options
    _validate_experiment_options_for_run(options.experiment_options)
    profile_snapshot = _normalize_optional_run_profile(permission_profile)
    record, budget = _prepare_auto_run(
        service,
        workspace_root,
        id_or_prefix,
        max_steps,
        effective_permission_profile=profile_snapshot,
    )
    auto_run_id = new_db_uuid()

    if _deadline_expired(budget.deadline_utc, service.clock()):
        return _stop_auto_run_for_expired_deadline(
            service,
            record,
            auto_run_id=auto_run_id,
            max_steps=max_steps,
            runtime_options=runtime_options,
            experiment_options=options.experiment_options,
            host_context=host_context,
        )
    _validate_experiment_profile_for_run(
        record,
        options.experiment_options,
        effective_permission_profile=profile_snapshot,
    )
    if profile_snapshot is not None:
        record = _update_permission_profile_for_run(service, record, profile_snapshot)
    record = _record_auto_run_started(
        service,
        record,
        auto_run_id=auto_run_id,
        max_steps=max_steps,
        runtime_options=runtime_options,
        experiment_options=options.experiment_options,
        host_context=host_context,
    )
    return _execute_auto_run_loop(
        service,
        record,
        workspace_root=workspace_root,
        auto_run_id=auto_run_id,
        options=replace(options, max_steps=max_steps),
        runner=runner,
        host_context=host_context,
    )


def _stop_auto_run_for_expired_deadline(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    *,
    auto_run_id: str,
    max_steps: int,
    runtime_options: TaskRunRuntimeOptions,
    experiment_options: TaskRunExperimentOptions | None,
    host_context: TaskRunHostContext,
) -> TaskRunAutoRunResult:
    return _start_and_stop_auto_run(
        service,
        record,
        auto_run_id=auto_run_id,
        max_steps=max_steps,
        runtime_options=runtime_options,
        experiment_options=experiment_options,
        stop_reason="budget_exhausted",
        counters=_AutoRunCounters(),
        exit_code=1,
        host_context=host_context,
    )


def _validate_experiment_options_for_run(
    experiment_options: TaskRunExperimentOptions | None,
) -> None:
    if experiment_options is None:
        return
    try:
        validate_experiment_options(experiment_options)
    except ValueError as exc:
        _raise_service_error(str(exc))


def _prepare_auto_run(
    service: TaskRunServiceInternals,
    workspace_root: str,
    id_or_prefix: str | None,
    max_steps: int,
    *,
    effective_permission_profile: Mapping[str, Any] | None,
) -> tuple[TaskRunRecord, _AutoRunBudget]:
    record = _select_task_run_for_run(service, workspace_root, id_or_prefix)
    if record.status in TERMINAL_TASKRUN_STATUSES:
        _raise_service_error(f"cannot run terminal TaskRun {record.id}: {record.status}")
    _validate_headless_profile(
        effective_permission_profile or record.permission_profile,
        command="run",
    )
    _validate_auto_run_stop_conditions(record.stop_conditions)
    budget = _auto_run_budget(record.budget)
    _validate_auto_run_budget_cap(max_steps, budget)
    service.recover_stale_running(workspace_root)
    record = _select_task_run_for_run(service, workspace_root, id_or_prefix)
    _validate_run_ready(
        service,
        record,
        workspace_root,
        explicit=bool(id_or_prefix),
        effective_permission_profile=effective_permission_profile,
    )
    return record, budget


def _record_auto_run_started(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    *,
    auto_run_id: str,
    max_steps: int,
    runtime_options: TaskRunRuntimeOptions,
    experiment_options: TaskRunExperimentOptions | None,
    host_context: TaskRunHostContext,
) -> TaskRunRecord:
    _append_auto_run_event(
        service,
        record,
        event_type="task_run_auto_run_started",
        auto_run_id=auto_run_id,
        max_steps=max_steps,
        runtime_options=runtime_options,
        experiment_options=experiment_options,
        counters=_AutoRunCounters(),
        iteration_index=0,
        stop_reason=None,
        host_context=host_context,
    )
    return service._summarize_and_project(record).task_run


def _validate_experiment_profile_for_run(
    record: TaskRunRecord,
    experiment_options: TaskRunExperimentOptions | None,
    *,
    effective_permission_profile: Mapping[str, Any] | None = None,
) -> None:
    if experiment_options is None:
        return
    try:
        validate_experiment_permission_profile(
            experiment_options,
            workspace_root=record.workspace_root,
            permission_profile=effective_permission_profile or record.permission_profile,
            budget=record.budget,
        )
    except ValueError as exc:
        _raise_service_error(str(exc))


def _execute_auto_run_loop(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    *,
    workspace_root: str,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    runner: TaskRunStepRunner,
    host_context: TaskRunHostContext,
) -> TaskRunAutoRunResult:
    iterations: list[TaskRunAutoRunIteration] = []
    experiment_attempts: list[TaskRunExperimentAttempt] = []
    baseline_metrics: dict[str, float] | None = None
    counters = _AutoRunCounters()
    stop_reason = "max_steps_reached"
    exit_code = 0
    try:
        while counters.steps_run < options.max_steps:
            budget = _auto_run_budget(record.budget)
            if _deadline_expired(budget.deadline_utc, service.clock()):
                stop_reason, exit_code = "budget_exhausted", 1
                break
            record, counters, decision, attempts, baseline_metrics = (
                _execute_next_auto_run_iteration(
                    service,
                    record,
                    workspace_root=workspace_root,
                    auto_run_id=auto_run_id,
                    options=options,
                    runner=runner,
                    counters=counters,
                    budget=budget,
                    baseline_metrics=baseline_metrics,
                    host_context=host_context,
                )
            )
            iterations.append(decision[0])
            experiment_attempts.extend(attempts)
            if decision[1].should_stop:
                stop_reason = decision[1].stop_reason or "runner_error"
                exit_code = decision[1].exit_code
                break
    except KeyboardInterrupt:
        stop_reason, exit_code = "user_cancelled", 130
    return _finish_auto_run(
        service,
        record,
        auto_run_id=auto_run_id,
        options=options,
        iterations=iterations,
        counters=counters,
        stop_reason=stop_reason,
        exit_code=exit_code,
        experiment_attempts=experiment_attempts,
    )


def _execute_next_auto_run_iteration(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    *,
    workspace_root: str,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    runner: TaskRunStepRunner,
    counters: _AutoRunCounters,
    budget: _AutoRunBudget,
    baseline_metrics: dict[str, float] | None,
    host_context: TaskRunHostContext,
) -> tuple[
    TaskRunRecord,
    _AutoRunCounters,
    tuple[TaskRunAutoRunIteration, _AutoRunStopDecision],
    list[TaskRunExperimentAttempt],
    dict[str, float] | None,
]:
    if options.experiment_options is None:
        record, counters, decision = _execute_auto_run_iteration(
            service,
            record,
            workspace_root=workspace_root,
            auto_run_id=auto_run_id,
            options=options,
            runner=runner,
            counters=counters,
            budget=budget,
            host_context=host_context,
        )
        return record, counters, decision, [], baseline_metrics
    return execute_experiment_auto_run_iteration(
        service,
        record,
        workspace_root=workspace_root,
        auto_run_id=auto_run_id,
        options=options,
        runner=runner,
        counters=counters,
        budget=budget,
        baseline_metrics=baseline_metrics,
        host_context=host_context,
    )


def _execute_auto_run_iteration(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    *,
    workspace_root: str,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    runner: TaskRunStepRunner,
    counters: _AutoRunCounters,
    budget: _AutoRunBudget,
    host_context: TaskRunHostContext,
) -> tuple[TaskRunRecord, _AutoRunCounters, tuple[TaskRunAutoRunIteration, _AutoRunStopDecision]]:
    _validate_run_ready(service, record, workspace_root, explicit=True)
    pre_summary, running_run, step = service._start_step(
        record,
        options.runtime_options,
        host_context=host_context,
    )
    outcome = service._run_step_runner(
        runner,
        task_run=running_run,
        step=step,
        summary=pre_summary,
        runtime_options=options.runtime_options,
        workspace_root=workspace_root,
    )
    step_result = service._finalize_step(
        task_run=running_run,
        step=step,
        previous_status=record.status,
        outcome=outcome,
        runtime_options=options.runtime_options,
        rebuild_projection=False,
    )
    if step_result.step is None:
        _raise_service_error("taskrun run finalized without a step record")
    return _finalize_auto_run_iteration(
        service,
        step_result.task_run,
        step_result.step,
        auto_run_id=auto_run_id,
        options=options,
        counters=counters,
        budget=budget,
    )


def _finalize_auto_run_iteration(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    counters: _AutoRunCounters,
    budget: _AutoRunBudget,
) -> tuple[TaskRunRecord, _AutoRunCounters, tuple[TaskRunAutoRunIteration, _AutoRunStopDecision]]:
    step_denies = _count_step_denies(
        service.repository.list_permission_decisions(record.id),
        step.id,
    )
    counters = _advance_auto_run_counters(counters, step, step_denies)
    decision = evaluate_auto_run_stop(
        last_step=step,
        counters=counters,
        options=options,
        budget=budget,
        step_denies=step_denies,
    )
    iteration = TaskRunAutoRunIteration(
        step=step,
        task_run_status=record.status,
        stop_candidate=decision.stop_reason,
    )
    _append_auto_run_event(
        service,
        record,
        event_type="task_run_auto_run_iteration_finished",
        auto_run_id=auto_run_id,
        max_steps=options.max_steps,
        runtime_options=options.runtime_options,
        experiment_options=options.experiment_options,
        counters=counters,
        iteration_index=counters.steps_run,
        step=step,
        stop_reason=decision.stop_reason,
    )
    if decision.next_task_status and record.status != decision.next_task_status:
        record = service.repository.update_task_run_status(
            record.id,
            status=decision.next_task_status,
            heartbeat_at=None,
            updated_at=service._now_iso(),
        )
    return record, counters, (iteration, decision)


def _finish_auto_run(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    *,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    iterations: list[TaskRunAutoRunIteration],
    counters: _AutoRunCounters,
    stop_reason: str,
    exit_code: int,
    experiment_attempts: list[TaskRunExperimentAttempt] | None = None,
) -> TaskRunAutoRunResult:
    event_type = (
        "task_run_auto_run_cancelled"
        if stop_reason == "user_cancelled"
        else "task_run_auto_run_stopped"
    )
    _append_auto_run_event(
        service,
        record,
        event_type=event_type,
        auto_run_id=auto_run_id,
        max_steps=options.max_steps,
        runtime_options=options.runtime_options,
        experiment_options=options.experiment_options,
        counters=counters,
        iteration_index=counters.steps_run,
        step=_auto_run_stop_event_step(iterations, stop_reason),
        stop_reason=stop_reason,
    )
    final = service._summarize_and_project(record)
    return TaskRunAutoRunResult(
        task_run=final.task_run,
        iterations=iterations,
        stop_reason=stop_reason,
        projection=final.projection,
        events=final.events,
        exit_code=exit_code,
        experiment_attempts=list(experiment_attempts or []),
    )


def _start_and_stop_auto_run(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    *,
    auto_run_id: str,
    max_steps: int,
    runtime_options: TaskRunRuntimeOptions,
    experiment_options: TaskRunExperimentOptions | None = None,
    stop_reason: str,
    counters: _AutoRunCounters,
    exit_code: int,
    host_context: TaskRunHostContext,
) -> TaskRunAutoRunResult:
    options = TaskRunAutoRunOptions(
        max_steps=max_steps,
        runtime_options=runtime_options,
        experiment_options=experiment_options,
    )
    _append_auto_run_event(
        service,
        record,
        event_type="task_run_auto_run_started",
        auto_run_id=auto_run_id,
        max_steps=max_steps,
        runtime_options=runtime_options,
        experiment_options=experiment_options,
        counters=counters,
        iteration_index=0,
        stop_reason=None,
        host_context=host_context,
    )
    record = service._summarize_and_project(record).task_run
    return _finish_auto_run(
        service,
        record,
        auto_run_id=auto_run_id,
        options=options,
        iterations=[],
        counters=counters,
        stop_reason=stop_reason,
        exit_code=exit_code,
    )


def _select_task_run_for_run(
    service: TaskRunServiceInternals,
    workspace_root: str,
    id_or_prefix: str | None,
) -> TaskRunRecord:
    if id_or_prefix:
        return service._select_task_run(workspace_root, id_or_prefix)
    candidates = [
        record
        for record in service.repository.list_task_runs_for_workspace(
            workspace_root,
            include_terminal=False,
        )
        if record.status == "pending"
    ]
    if not candidates:
        _raise_service_error(
            "no pending TaskRun in this workspace; pass an id to run a blocked TaskRun"
        )
    if len(candidates) > 1:
        _raise_service_error(
            _ambiguous_message(
                "multiple pending TaskRuns in this workspace",
                candidates,
            )
        )
    return candidates[0]


def _update_permission_profile_for_run(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    profile_snapshot: Mapping[str, Any],
) -> TaskRunRecord:
    now = service._now_iso()
    previous = _normalize_profile(record.permission_profile)
    updated = service.repository.update_task_run_permission_profile(
        record.id,
        profile_snapshot,
        updated_at=now,
    )
    service.repository.append_event(
        task_run_id=updated.id,
        event_type="task_run_permission_profile_updated",
        payload={
            "previous_profile_name": previous.get("name"),
            "new_profile_name": profile_snapshot.get("name"),
            "sources": list(profile_snapshot.get("sources") or []),
            "explicit_scope_keys": list(profile_snapshot.get("explicitScopeKeys") or []),
            "reason": "run --permission",
        },
        occurred_at=now,
    )
    return updated


def _auto_run_stop_event_step(
    iterations: list[TaskRunAutoRunIteration],
    stop_reason: str,
) -> TaskStepRecord | None:
    if not iterations:
        return None
    last_step = iterations[-1].step
    if stop_reason == "user_cancelled" and last_step.status == "cancelled":
        return last_step
    if stop_reason in _STEP_ATTRIBUTED_STOP_REASONS:
        return last_step
    return None


def _validate_auto_run_max_steps(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _raise_service_error("--max-steps must be an integer")
    if value < 1:
        _raise_service_error("--max-steps must be >= 1")
    if value > MAX_AUTO_RUN_STEPS:
        _raise_service_error(f"--max-steps must be <= {MAX_AUTO_RUN_STEPS}")
    return value


def _normalize_optional_run_profile(
    permission_profile: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if permission_profile is None:
        return None
    snapshot = _normalize_profile(permission_profile)
    if not bool(snapshot.get("nonInteractive")):
        _raise_service_error(
            "taskrun run is headless and cannot use interactive permission profile; "
            "use --permission guarded or --permission full"
        )
    return snapshot


def _auto_run_budget(budget: Mapping[str, Any]) -> _AutoRunBudget:
    return _AutoRunBudget(
        max_steps=_budget_optional_positive_int(budget, "max_steps"),
        max_consecutive_failures=_budget_positive_int(
            budget,
            "max_consecutive_failures",
            DEFAULT_MAX_CONSECUTIVE_FAILURES,
        ),
        max_consecutive_denies=_budget_positive_int(
            budget,
            "max_consecutive_denies",
            DEFAULT_MAX_CONSECUTIVE_DENIES,
        ),
        max_total_denies=_budget_positive_int(
            budget,
            "max_total_denies",
            DEFAULT_MAX_TOTAL_DENIES,
        ),
        deadline_utc=_budget_deadline_utc(budget),
    )


def _validate_auto_run_budget_cap(max_steps: int, budget: _AutoRunBudget) -> None:
    if budget.max_steps is None or max_steps <= budget.max_steps:
        return
    _raise_service_error(
        f"--max-steps exceeds task_run budget ({budget.max_steps}); "
        f"rerun with --max-steps <= {budget.max_steps}"
    )


def _validate_auto_run_stop_conditions(stop_conditions: Mapping[str, Any]) -> None:
    _validate_auto_run_stop_condition(
        stop_conditions,
        "on_workspace_dirty",
        "stop_conditions.on_workspace_dirty='fail' is unsupported in M5 "
        "without host-validated mutation sets",
    )
    _validate_auto_run_stop_condition(
        stop_conditions,
        "on_irrecoverable_test_failure",
        "stop_conditions.on_irrecoverable_test_failure='fail' is unsupported "
        "in M5 without host-validated test failure signals",
    )


def _validate_auto_run_stop_condition(
    stop_conditions: Mapping[str, Any],
    key: str,
    unsupported_fail_message: str,
) -> None:
    value = stop_conditions.get(key)
    if value is None:
        return
    if value != "fail":
        _raise_service_error(f"stop_conditions.{key} unsupported value: {value!r}")
    _raise_service_error(unsupported_fail_message)


def _budget_optional_positive_int(
    budget: Mapping[str, Any],
    key: str,
) -> int | None:
    if key not in budget or budget.get(key) is None:
        return None
    return _budget_positive_int(budget, key, default=None)


def _budget_positive_int(
    budget: Mapping[str, Any],
    key: str,
    default: int | None,
) -> int:
    if key not in budget or budget.get(key) is None:
        if default is None:
            _raise_service_error(f"budget.{key} must be a positive integer")
        return default
    value = budget.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _raise_service_error(f"budget.{key} must be a positive integer")
    return value


def _budget_deadline_utc(budget: Mapping[str, Any]) -> datetime | None:
    if "deadline_utc" not in budget or budget.get("deadline_utc") is None:
        return None
    value = budget.get("deadline_utc")
    if not isinstance(value, str) or not value.strip():
        _raise_service_error("budget.deadline_utc must be an ISO-8601 UTC timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TaskRunServiceError(
            "budget.deadline_utc must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _raise_service_error("budget.deadline_utc must be an ISO-8601 UTC timestamp")
    return parsed.astimezone(UTC)


def _deadline_expired(deadline: datetime | None, now: datetime) -> bool:
    if deadline is None:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC) >= deadline


def _normalize_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return normalize_permission_profile_snapshot(profile)
    except PermissionProfileError as exc:
        raise TaskRunServiceError(str(exc)) from exc


def _raise_service_error(message: str) -> None:
    raise TaskRunServiceError(message)


def _ambiguous_message(prefix: str, matches: list[TaskRunRecord]) -> str:
    details = [
        f"{record.id} {record.status} {_goal_preview(record.goal)}"
        for record in matches
    ]
    return prefix + "; pass an id. Candidates: " + "; ".join(details)


def _goal_preview(goal: str, limit: int = 64) -> str:
    collapsed = " ".join(goal.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


__all__ = [
    "AUTO_RUN_STOP_REASONS",
    "MAX_AUTO_RUN_STEPS",
    "TaskRunAutoRunIteration",
    "TaskRunAutoRunOptions",
    "TaskRunAutoRunResult",
    "evaluate_auto_run_stop",
    "run_taskrun_auto_loop",
]
