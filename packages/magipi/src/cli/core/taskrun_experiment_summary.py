"""TaskRun experiment summary projections."""

from __future__ import annotations

from cli.core.taskrun_parameter_golf_artifacts import (
    current_best_parameter_golf_artifact,
    parameter_golf_artifact_summary,
    parameter_golf_artifacts,
)
from storage.taskrun_repository import TaskExperimentRecord, TaskRunRecord


def current_best_experiment(
    experiments: list[TaskExperimentRecord],
) -> dict[str, object] | None:
    p3_best = current_best_parameter_golf_artifact(experiments)
    if p3_best is not None:
        return {
            "experiment_id": p3_best.attempt_id,
            "step_id": p3_best.step_id,
            "metric": "val_bpb",
            "value": p3_best.metric.get("value"),
            "direction": "minimize",
            "decision": p3_best.compat_decision,
            "diff_ref": dict(p3_best.diff_ref),
            "artifact": p3_best.to_dict(),
        }
    latest_baseline: TaskExperimentRecord | None = None
    latest_keep: TaskExperimentRecord | None = None
    for experiment in experiments:
        if experiment.decision == "baseline":
            latest_baseline = experiment
        elif experiment.decision == "keep":
            latest_keep = experiment
    best = latest_keep or latest_baseline
    if best is None:
        return None
    metric = experiment_primary_metric(best)
    return {
        "experiment_id": best.id,
        "step_id": best.step_id,
        "metric": metric,
        "value": best.metrics.get(metric) if metric else None,
        "direction": best.result.get("direction"),
        "decision": best.decision,
        "diff_ref": dict(best.diff_ref),
    }


def experiment_preview(experiment: TaskExperimentRecord) -> dict[str, object]:
    metric = experiment_primary_metric(experiment)
    return {
        "experiment_id": experiment.id,
        "decision": experiment.decision,
        "primary_metric": metric,
        "value": experiment.metrics.get(metric) if metric else None,
        "reason": experiment.result.get("reason"),
    }


def p3_artifact_summary(
    experiments: list[TaskExperimentRecord],
) -> dict[str, object] | None:
    return parameter_golf_artifact_summary(experiments)


def experiment_next_action(
    record: TaskRunRecord,
    experiment: TaskExperimentRecord | None,
) -> str | None:
    if experiment is None:
        return None
    prefix = f"`magipi taskrun run {record.id[:8]} --max-steps 1 ...`"
    reason = experiment.result.get("reason")
    detail = f" ({reason})" if reason else ""
    if experiment.decision == "keep":
        return f"Continue experiment mode with {prefix}, or inspect summary before closing."
    if experiment.decision == "revert":
        return f"Last trial was reverted{detail}; adjust the hypothesis, then continue with {prefix}."
    if experiment.decision == "blocked":
        return f"Resolve the experiment blocker{detail}, then continue with {prefix}."
    return None


def experiment_primary_metric(experiment: TaskExperimentRecord) -> str | None:
    metric = experiment.result.get("primaryMetric")
    if isinstance(metric, str) and metric:
        return metric
    for key in experiment.metrics:
        return str(key)
    return None


__all__ = [
    "current_best_experiment",
    "experiment_next_action",
    "experiment_preview",
    "experiment_primary_metric",
    "p3_artifact_summary",
    "parameter_golf_artifacts",
]
