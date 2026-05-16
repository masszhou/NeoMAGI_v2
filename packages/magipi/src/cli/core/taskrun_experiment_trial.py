"""Trial benchmark decision helpers for TaskRun experiment mode."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cli.core.taskrun_experiment_attempts import (
    append_keep_attempt,
    blocked_trial_attempt,
    unsafe_revert_attempt,
)
from cli.core.taskrun_experiments import (
    HostCommandResult,
    MetricComparison,
    MetricParseError,
    TaskRunExperimentAttempt,
    TaskRunExperimentOptions,
    append_experiment_record,
    capture_workspace_snapshot,
    command_record,
    compare_primary_metric,
    parse_metric_lines,
    run_host_command,
    run_safe_revert,
)
from cli.core.taskrun_service_internals import TaskRunServiceInternals
from storage.taskrun_repository import TaskRunRecord, TaskStepRecord


@dataclass(frozen=True, slots=True)
class _RegressionContext:
    service: TaskRunServiceInternals
    task_run: TaskRunRecord
    step: TaskStepRecord
    auto_run_id: str
    experiment_options: TaskRunExperimentOptions
    command: Mapping[str, Any]
    trial_metrics: dict[str, float]
    result: dict[str, object]
    diff_ref: dict[str, object]


def run_trial_attempt(
    service: TaskRunServiceInternals,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    experiment_options: TaskRunExperimentOptions,
    baseline_metrics: dict[str, float],
    diff_ref: dict[str, object],
) -> tuple[TaskRunExperimentAttempt, str | None]:
    command = run_host_command(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        phase="trial",
        command=experiment_options.benchmark_command,
    )
    if not command.succeeded:
        return blocked_trial_attempt(
            service,
            task_run,
            step,
            auto_run_id,
            experiment_options,
            command_record(command),
            command.reason or "trial benchmark command failed",
            "benchmark_failed",
            diff_ref,
        )
    return _compare_trial_metrics(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        experiment_options=experiment_options,
        baseline_metrics=baseline_metrics,
        diff_ref=diff_ref,
        command=command_record(command),
        output=command.output,
    )


def _compare_trial_metrics(
    service: TaskRunServiceInternals,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    experiment_options: TaskRunExperimentOptions,
    baseline_metrics: dict[str, float],
    diff_ref: dict[str, object],
    command: Mapping[str, Any],
    output: str,
) -> tuple[TaskRunExperimentAttempt, str | None]:
    try:
        trial_metrics, comparison = _trial_metric_comparison(
            baseline_metrics,
            experiment_options,
            output,
        )
    except MetricParseError as exc:
        return _metric_parse_block(
            service,
            task_run,
            step,
            auto_run_id,
            experiment_options,
            command,
            diff_ref,
            exc,
        )
    return _decide_trial_attempt(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        experiment_options=experiment_options,
        command=command,
        trial_metrics=trial_metrics,
        comparison=comparison,
        diff_ref=diff_ref,
    )


def _trial_metric_comparison(
    baseline_metrics: dict[str, float],
    experiment_options: TaskRunExperimentOptions,
    output: str,
) -> tuple[dict[str, float], MetricComparison]:
    trial_metrics = parse_metric_lines(output)
    comparison = compare_primary_metric(
        baseline_metrics=baseline_metrics,
        trial_metrics=trial_metrics,
        options=experiment_options,
    )
    return trial_metrics, comparison


def _metric_parse_block(
    service: TaskRunServiceInternals,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    auto_run_id: str,
    experiment_options: TaskRunExperimentOptions,
    command: Mapping[str, Any],
    diff_ref: dict[str, object],
    exc: MetricParseError,
) -> tuple[TaskRunExperimentAttempt, str | None]:
    return blocked_trial_attempt(
        service,
        task_run,
        step,
        auto_run_id,
        experiment_options,
        command,
        str(exc),
        "metric_parse_failed",
        diff_ref,
    )


def _decide_trial_attempt(
    service: TaskRunServiceInternals,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    experiment_options: TaskRunExperimentOptions,
    command: Mapping[str, Any],
    trial_metrics: dict[str, float],
    comparison: MetricComparison,
    diff_ref: dict[str, object],
) -> tuple[TaskRunExperimentAttempt, str | None]:
    if comparison.decision == "keep":
        return append_keep_attempt(
            service,
            task_run,
            step,
            auto_run_id,
            command,
            trial_metrics,
            comparison.result,
            diff_ref,
        ), None
    if bool(comparison.result.get("regressed")) and experiment_options.revert_on_regression:
        return _handle_regression_with_revert(
            _RegressionContext(
                service,
                task_run,
                step,
                auto_run_id,
                experiment_options,
                command,
                trial_metrics,
                comparison.result,
                diff_ref,
            )
        )
    return blocked_trial_attempt(
        service,
        task_run,
        step,
        auto_run_id,
        experiment_options,
        command,
        str(comparison.result.get("reason") or "experiment blocked"),
        "experiment_blocked",
        diff_ref,
        metrics=trial_metrics,
        result=comparison.result,
    )


def _handle_regression_with_revert(
    context: _RegressionContext,
) -> tuple[TaskRunExperimentAttempt, str | None]:
    if not bool(context.diff_ref.get("safe_revert_supported")):
        reason = str(context.diff_ref.get("unsafe_revert_reason") or "unsafe revert")
        return _unsafe_revert_from_regression(context, reason)
    revert = run_safe_revert(
        context.service,
        context.task_run,
        context.step,
        auto_run_id=context.auto_run_id,
        diff_ref=context.diff_ref,
    )
    if not revert.succeeded:
        return _unsafe_revert_from_regression(
            context,
            revert.reason or "safe revert command failed",
        )
    post_revert = capture_workspace_snapshot(
        context.service,
        context.task_run,
        context.step,
        auto_run_id=context.auto_run_id,
    )
    if not _revert_restored_clean_diff(post_revert):
        result = {**context.result, "revertCommand": command_record(revert)}
        return _unsafe_revert_from_regression(
            context,
            "safe revert did not restore a clean tracked diff",
            result=result,
        )
    return _append_revert_attempt(context, revert)


def _unsafe_revert_from_regression(
    context: _RegressionContext,
    reason: str,
    *,
    result: dict[str, object] | None = None,
) -> tuple[TaskRunExperimentAttempt, str | None]:
    return unsafe_revert_attempt(
        context.service,
        context.task_run,
        context.step,
        context.auto_run_id,
        context.experiment_options,
        context.command,
        reason,
        context.diff_ref,
        context.trial_metrics,
        result or context.result,
    )


def _revert_restored_clean_diff(post_revert: Mapping[str, object]) -> bool:
    return bool(post_revert.get("git_available")) and not post_revert.get("status")


def _append_revert_attempt(
    context: _RegressionContext,
    revert: HostCommandResult,
) -> tuple[TaskRunExperimentAttempt, str | None]:
    return (
        append_experiment_record(
            context.service,
            context.task_run,
            context.step,
            auto_run_id=context.auto_run_id,
            decision="revert",
            hypothesis="agent trial",
            change={},
            command=context.command,
            metrics=context.trial_metrics,
            result={
                **context.result,
                "reason": "primary metric regressed; trial reverted",
                "revertCommand": command_record(revert),
            },
            diff_ref=context.diff_ref,
        ),
        "experiment_reverted",
    )


__all__ = ["run_trial_attempt"]
