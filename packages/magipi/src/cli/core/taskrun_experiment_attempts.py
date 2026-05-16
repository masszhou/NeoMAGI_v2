"""Experiment attempt record helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cli.core.taskrun_experiments import (
    MetricParseError,
    TaskRunExperimentAttempt,
    TaskRunExperimentOptions,
    append_experiment_record,
)
from cli.core.taskrun_step import TaskRunStepOutcome
from storage.taskrun_repository import TaskRunRecord, TaskStepRecord


def append_keep_attempt(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    auto_run_id: str,
    command: Mapping[str, Any],
    metrics: Mapping[str, Any],
    result: Mapping[str, Any],
    diff_ref: Mapping[str, Any],
) -> TaskRunExperimentAttempt:
    return append_experiment_record(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        decision="keep",
        hypothesis="agent trial",
        change={},
        command=command,
        metrics=metrics,
        result=result,
        diff_ref=diff_ref,
    )


def unsafe_revert_attempt(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    auto_run_id: str,
    experiment_options: TaskRunExperimentOptions,
    command: Mapping[str, Any],
    reason: str,
    diff_ref: Mapping[str, Any],
    metrics: Mapping[str, Any],
    result: Mapping[str, Any],
) -> tuple[TaskRunExperimentAttempt, str | None]:
    return blocked_trial_attempt(
        service,
        task_run,
        step,
        auto_run_id,
        experiment_options,
        command,
        reason,
        "unsafe_revert",
        diff_ref,
        metrics=metrics,
        result=result,
    )


def blocked_trial_attempt(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    auto_run_id: str,
    experiment_options: TaskRunExperimentOptions,
    command: Mapping[str, Any],
    reason: str,
    stop_reason: str,
    diff_ref: Mapping[str, Any],
    *,
    metrics: Mapping[str, Any] | None = None,
    result: Mapping[str, Any] | None = None,
) -> tuple[TaskRunExperimentAttempt, str | None]:
    return (
        append_blocked_attempt(
            service,
            task_run,
            step,
            auto_run_id=auto_run_id,
            experiment_options=experiment_options,
            command=command,
            reason=reason,
            stop_reason=stop_reason,
            diff_ref=diff_ref,
            metrics=metrics,
            result=result,
        ),
        stop_reason,
    )


def append_blocked_attempt(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    experiment_options: TaskRunExperimentOptions,
    command: Mapping[str, Any],
    reason: str,
    stop_reason: str,
    diff_ref: Mapping[str, Any],
    metrics: Mapping[str, Any] | None = None,
    result: Mapping[str, Any] | None = None,
) -> TaskRunExperimentAttempt:
    return append_experiment_record(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        decision="blocked",
        hypothesis="agent trial",
        change={},
        command=command,
        metrics=metrics or {},
        result={
            **dict(result or {}),
            "primaryMetric": experiment_options.primary_metric,
            "direction": experiment_options.metric_direction,
            "reason": reason,
            "stopReason": stop_reason,
        },
        diff_ref=diff_ref,
    )


def blocked_baseline_outcome(attempt: TaskRunExperimentAttempt) -> TaskRunStepOutcome:
    return TaskRunStepOutcome(
        status="blocked",
        block_reason=str(
            attempt.experiment.result.get("reason") or "baseline benchmark failed"
        ),
    )


def blocked_trial_outcome(
    outcome: TaskRunStepOutcome,
    attempt: TaskRunExperimentAttempt,
) -> TaskRunStepOutcome:
    return TaskRunStepOutcome(
        status="blocked",
        assistant_text=outcome.assistant_text,
        run_id=outcome.run_id,
        tool_count=outcome.tool_count,
        permission_decision_count=outcome.permission_decision_count,
        block_reason=str(
            attempt.experiment.result.get("reason") or "experiment blocked"
        ),
    )


def attempt_stop_reason(attempt: TaskRunExperimentAttempt) -> str:
    return str(attempt.experiment.result.get("stopReason") or "benchmark_failed")


def require_primary_metric(
    metrics: Mapping[str, float],
    primary_metric: str,
    *,
    phase: str,
) -> None:
    if primary_metric not in metrics:
        raise MetricParseError(
            f"primary metric missing from {phase}: {primary_metric}",
            code="missing_primary_metric",
        )


__all__ = [
    "append_blocked_attempt",
    "append_keep_attempt",
    "attempt_stop_reason",
    "blocked_baseline_outcome",
    "blocked_trial_attempt",
    "blocked_trial_outcome",
    "require_primary_metric",
    "unsafe_revert_attempt",
]
