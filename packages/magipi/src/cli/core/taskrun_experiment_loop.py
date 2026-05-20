"""Experiment-mode bounded TaskRun auto-loop iteration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cli.core.taskrun_autorun_common import (
    AutoRunBudget,
    AutoRunCounters,
    AutoRunStopDecision,
    TaskRunAutoRunIteration,
    TaskRunAutoRunOptions,
    advance_auto_run_counters,
    append_auto_run_event,
    count_step_denies,
    evaluate_auto_run_stop,
    validate_auto_run_ready,
)
from cli.core.taskrun_errors import TaskRunServiceError
from cli.core.taskrun_experiment_attempts import (
    append_blocked_attempt,
    attempt_stop_reason,
    blocked_baseline_outcome,
    blocked_trial_outcome,
    require_primary_metric,
)
from cli.core.taskrun_experiment_trial import run_trial_attempt
from cli.core.taskrun_experiments import (
    MetricParseError,
    TaskRunExperimentAttempt,
    TaskRunExperimentOptions,
    append_experiment_record,
    capture_diff_ref,
    capture_workspace_snapshot,
    command_record,
    parse_metric_lines,
    run_host_command,
)
from cli.core.taskrun_host_contract import TaskRunHostContext
from cli.core.taskrun_service_internals import TaskRunServiceInternals
from cli.core.taskrun_step import TaskRunStepOutcome, TaskRunStepRunner
from storage.taskrun_repository import TaskRunRecord, TaskStepRecord


def execute_experiment_auto_run_iteration(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    *,
    workspace_root: str,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    runner: TaskRunStepRunner,
    counters: AutoRunCounters,
    budget: AutoRunBudget,
    baseline_metrics: dict[str, float] | None,
    host_context: TaskRunHostContext,
) -> tuple[
    TaskRunRecord,
    AutoRunCounters,
    tuple[TaskRunAutoRunIteration, AutoRunStopDecision],
    list[TaskRunExperimentAttempt],
    dict[str, float] | None,
]:
    experiment_options = options.experiment_options
    if experiment_options is None:
        raise AssertionError("experiment iteration requires experiment options")
    validate_auto_run_ready(service, record, workspace_root, explicit=True)
    pre_summary, running_run, step = service._start_step(
        record,
        options.runtime_options,
        host_context=host_context,
    )
    try:
        return _run_experiment_iteration(
            service,
            record,
            running_run,
            step,
            auto_run_id=auto_run_id,
            options=options,
            runner=runner,
            counters=counters,
            budget=budget,
            baseline_metrics=baseline_metrics,
            pre_summary=pre_summary,
            experiment_options=experiment_options,
            workspace_root=workspace_root,
        )
    except KeyboardInterrupt:
        return _finalize_interrupted_experiment_iteration(
            service,
            record,
            running_run,
            step,
            auto_run_id=auto_run_id,
            options=options,
            counters=counters,
            budget=budget,
            baseline_metrics=baseline_metrics,
        )


def _finalize_interrupted_experiment_iteration(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    running_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    counters: AutoRunCounters,
    budget: AutoRunBudget,
    baseline_metrics: dict[str, float] | None,
):
    return _finalize_experiment_iteration(
        service,
        record,
        running_run,
        step,
        auto_run_id=auto_run_id,
        options=options,
        counters=counters,
        budget=budget,
        outcome=TaskRunStepOutcome(
            status="cancelled",
            error_message="cancelled by user interrupt",
        ),
        experiment_stop_reason="user_cancelled",
        attempts=[],
        baseline_metrics=baseline_metrics,
    )


def _run_experiment_iteration(
    service: TaskRunServiceInternals,
    previous_record: TaskRunRecord,
    running_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    runner: TaskRunStepRunner,
    counters: AutoRunCounters,
    budget: AutoRunBudget,
    baseline_metrics: dict[str, float] | None,
    pre_summary: dict[str, object],
    experiment_options: TaskRunExperimentOptions,
    workspace_root: str,
):
    attempts: list[TaskRunExperimentAttempt] = []
    baseline_attempt, baseline_metrics = _ensure_baseline(
        service,
        running_run,
        step,
        auto_run_id=auto_run_id,
        experiment_options=experiment_options,
        baseline_metrics=baseline_metrics,
    )
    if baseline_attempt is not None:
        attempts.append(baseline_attempt)
    if baseline_metrics is None and baseline_attempt is not None:
        return _finalize_experiment_iteration(
            service,
            previous_record,
            running_run,
            step,
            auto_run_id=auto_run_id,
            options=options,
            counters=counters,
            budget=budget,
            outcome=blocked_baseline_outcome(baseline_attempt),
            experiment_stop_reason=attempt_stop_reason(baseline_attempt),
            attempts=attempts,
            baseline_metrics=None,
        )
    return _run_trial_phase(
        service,
        previous_record,
        running_run,
        step,
        auto_run_id=auto_run_id,
        options=options,
        runner=runner,
        counters=counters,
        budget=budget,
        baseline_metrics=baseline_metrics or {},
        attempts=attempts,
        pre_summary=pre_summary,
        experiment_options=experiment_options,
        workspace_root=workspace_root,
    )


def _run_trial_phase(
    service: TaskRunServiceInternals,
    previous_record: TaskRunRecord,
    running_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    runner: TaskRunStepRunner,
    counters: AutoRunCounters,
    budget: AutoRunBudget,
    baseline_metrics: dict[str, float],
    attempts: list[TaskRunExperimentAttempt],
    pre_summary: dict[str, object],
    experiment_options: TaskRunExperimentOptions,
    workspace_root: str,
):
    before_snapshot = capture_workspace_snapshot(
        service,
        running_run,
        step,
        auto_run_id=auto_run_id,
    )
    outcome = service._run_step_runner(
        runner,
        task_run=running_run,
        step=step,
        summary=pre_summary,
        runtime_options=options.runtime_options,
        workspace_root=workspace_root,
    )
    if outcome.status != "done":
        return _finalize_non_done_trial(
            service,
            previous_record,
            running_run,
            step,
            auto_run_id=auto_run_id,
            options=options,
            counters=counters,
            budget=budget,
            outcome=outcome,
            attempts=attempts,
            baseline_metrics=baseline_metrics,
        )
    return _finalize_trial_benchmark(
        service,
        previous_record,
        running_run,
        step,
        auto_run_id=auto_run_id,
        options=options,
        counters=counters,
        budget=budget,
        baseline_metrics=baseline_metrics,
        attempts=attempts,
        outcome=outcome,
        experiment_options=experiment_options,
        before_snapshot=before_snapshot,
    )


def _finalize_non_done_trial(
    service: TaskRunServiceInternals,
    previous_record: TaskRunRecord,
    running_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    counters: AutoRunCounters,
    budget: AutoRunBudget,
    outcome: TaskRunStepOutcome,
    attempts: list[TaskRunExperimentAttempt],
    baseline_metrics: dict[str, float],
):
    return _finalize_experiment_iteration(
        service,
        previous_record,
        running_run,
        step,
        auto_run_id=auto_run_id,
        options=options,
        counters=counters,
        budget=budget,
        outcome=outcome,
        experiment_stop_reason=None,
        attempts=attempts,
        baseline_metrics=baseline_metrics,
    )


def _finalize_trial_benchmark(
    service: TaskRunServiceInternals,
    previous_record: TaskRunRecord,
    running_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    counters: AutoRunCounters,
    budget: AutoRunBudget,
    baseline_metrics: dict[str, float],
    attempts: list[TaskRunExperimentAttempt],
    outcome: TaskRunStepOutcome,
    experiment_options: TaskRunExperimentOptions,
    before_snapshot: Mapping[str, object],
):
    diff_ref = capture_diff_ref(
        service,
        running_run,
        step,
        auto_run_id=auto_run_id,
        before_snapshot=before_snapshot,
    )
    trial_attempt, stop_reason = run_trial_attempt(
        service,
        running_run,
        step,
        auto_run_id=auto_run_id,
        experiment_options=experiment_options,
        baseline_metrics=baseline_metrics,
        diff_ref=diff_ref,
    )
    attempts.append(trial_attempt)
    final_outcome = outcome if trial_attempt.decision == "keep" else blocked_trial_outcome(outcome, trial_attempt)
    return _finalize_experiment_iteration(
        service,
        previous_record,
        running_run,
        step,
        auto_run_id=auto_run_id,
        options=options,
        counters=counters,
        budget=budget,
        outcome=final_outcome,
        experiment_stop_reason=stop_reason,
        attempts=attempts,
        baseline_metrics=baseline_metrics,
    )


def _ensure_baseline(
    service: TaskRunServiceInternals,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    experiment_options: TaskRunExperimentOptions,
    baseline_metrics: dict[str, float] | None,
) -> tuple[TaskRunExperimentAttempt | None, dict[str, float] | None]:
    if baseline_metrics is not None:
        return None, baseline_metrics
    return _run_benchmark_attempt(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        experiment_options=experiment_options,
    )


def _run_benchmark_attempt(
    service: TaskRunServiceInternals,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    experiment_options: TaskRunExperimentOptions,
) -> tuple[TaskRunExperimentAttempt, dict[str, float] | None]:
    command = run_host_command(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        phase="baseline",
        command=experiment_options.benchmark_command,
    )
    if not command.succeeded:
        reason = command.reason or "baseline benchmark command failed"
        return append_blocked_attempt(
            service,
            task_run,
            step,
            auto_run_id=auto_run_id,
            experiment_options=experiment_options,
            command=command_record(command),
            reason=reason,
            stop_reason="benchmark_failed",
            diff_ref={},
        ), None
    return _record_baseline_metrics(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        experiment_options=experiment_options,
        command=command_record(command),
        output=command.output,
    )


def _record_baseline_metrics(
    service: TaskRunServiceInternals,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    experiment_options: TaskRunExperimentOptions,
    command: Mapping[str, Any],
    output: str,
) -> tuple[TaskRunExperimentAttempt, dict[str, float] | None]:
    try:
        metrics = parse_metric_lines(output)
        require_primary_metric(metrics, experiment_options.primary_metric, phase="baseline")
    except MetricParseError as exc:
        return append_blocked_attempt(
            service,
            task_run,
            step,
            auto_run_id=auto_run_id,
            experiment_options=experiment_options,
            command=command,
            reason=str(exc),
            stop_reason="metric_parse_failed",
            diff_ref={},
        ), None
    attempt = append_experiment_record(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        decision="baseline",
        hypothesis="loop-level baseline",
        change={},
        command=command,
        metrics=metrics,
        result={
            "primaryMetric": experiment_options.primary_metric,
            "direction": experiment_options.metric_direction,
            "baselineValue": metrics[experiment_options.primary_metric],
            "reason": "baseline recorded",
        },
        diff_ref={},
    )
    return attempt, metrics


def _finalize_experiment_iteration(
    service: TaskRunServiceInternals,
    previous_record: TaskRunRecord,
    running_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    counters: AutoRunCounters,
    budget: AutoRunBudget,
    outcome: TaskRunStepOutcome,
    experiment_stop_reason: str | None,
    attempts: list[TaskRunExperimentAttempt],
    baseline_metrics: dict[str, float] | None,
) -> tuple[
    TaskRunRecord,
    AutoRunCounters,
    tuple[TaskRunAutoRunIteration, AutoRunStopDecision],
    list[TaskRunExperimentAttempt],
    dict[str, float] | None,
]:
    step_result = service._finalize_step(
        task_run=running_run,
        step=step,
        previous_status=previous_record.status,
        outcome=outcome,
        runtime_options=options.runtime_options,
        rebuild_projection=False,
    )
    if step_result.step is None:
        raise TaskRunServiceError("taskrun run finalized without a step record")
    record, counters, decision = _experiment_iteration_decision(
        service,
        step_result.task_run,
        step_result.step,
        options,
        counters,
        budget,
        experiment_stop_reason,
    )
    _append_iteration_finished_event(service, record, step_result.step, auto_run_id, options, counters, decision)
    record = _apply_next_task_status(service, record, decision)
    iteration = TaskRunAutoRunIteration(step_result.step, record.status, decision.stop_reason)
    return record, counters, (iteration, decision), attempts, baseline_metrics


def _experiment_iteration_decision(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    step: TaskStepRecord,
    options: TaskRunAutoRunOptions,
    counters: AutoRunCounters,
    budget: AutoRunBudget,
    experiment_stop_reason: str | None,
) -> tuple[TaskRunRecord, AutoRunCounters, AutoRunStopDecision]:
    step_denies = count_step_denies(
        service.repository.list_permission_decisions(record.id),
        step.id,
    )
    counters = advance_auto_run_counters(counters, step, step_denies)
    decision = evaluate_auto_run_stop(
        last_step=step,
        counters=counters,
        options=options,
        budget=budget,
        step_denies=step_denies,
    )
    if experiment_stop_reason == "user_cancelled":
        return record, counters, AutoRunStopDecision(True, "user_cancelled", "blocked", 130)
    if experiment_stop_reason is not None and step_denies == 0:
        return record, counters, AutoRunStopDecision(True, experiment_stop_reason, "blocked", 1)
    return record, counters, decision


def _append_iteration_finished_event(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    step: TaskStepRecord,
    auto_run_id: str,
    options: TaskRunAutoRunOptions,
    counters: AutoRunCounters,
    decision: AutoRunStopDecision,
) -> None:
    append_auto_run_event(
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


def _apply_next_task_status(
    service: TaskRunServiceInternals,
    record: TaskRunRecord,
    decision: AutoRunStopDecision,
) -> TaskRunRecord:
    if not decision.next_task_status or record.status == decision.next_task_status:
        return record
    return service.repository.update_task_run_status(
        record.id,
        status=decision.next_task_status,
        heartbeat_at=None,
        updated_at=service._now_iso(),
    )


__all__ = ["execute_experiment_auto_run_iteration"]
