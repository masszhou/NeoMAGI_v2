"""TaskRun experiment benchmark helpers.

This module is host-side glue for P2-M6. The DB ledger remains truth; host
commands still pass through the same shell policy and permission-profile
resolver used by governed bash tools.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Mapping

from cli.core.taskrun_experiment_revert import safe_revert_check
from cli.core.taskrun_host_audit import (
    elapsed_ms as _elapsed_ms,
    record_host_command_audit as _record_host_command_audit,
)
from policy.permission_profiles import PermissionProfileResolver
from policy.redaction import redacted_command_preview
from policy.sandbox import run_shell_command
from policy.shell_policy import DEFAULT_TIMEOUT_SECONDS, decide_shell_access
from policy.types import PolicyDecision, PolicyRequest
from storage.taskrun_repository import TaskExperimentRecord, TaskRunRecord, TaskStepRecord

MetricDirection = Literal["lower", "higher"]
ExperimentDecision = Literal["baseline", "keep", "revert", "blocked"]

_METRIC_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\u00b5]*$")
_RESERVED_METRIC_NAMES = {"__proto__", "constructor", "prototype"}


class MetricParseError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TaskRunExperimentOptions:
    benchmark_command: str
    primary_metric: str
    metric_direction: MetricDirection
    min_delta: float = 0.0
    revert_on_regression: bool = False


@dataclass(frozen=True, slots=True)
class TaskRunExperimentAttempt:
    experiment: TaskExperimentRecord
    metrics: dict[str, float]
    decision: str
    diff_ref: dict[str, object]


@dataclass(frozen=True, slots=True)
class HostCommandResult:
    phase: str
    command: str
    output: str
    exit_code: int | None
    cancelled: bool
    timed_out: bool
    policy_effect: str
    reason: str | None
    permission_decision_id: str | None
    duration_ms: int = 0
    started_at: str | None = None
    ended_at: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.policy_effect == "allow"
            and self.exit_code == 0
            and not self.cancelled
            and not self.timed_out
        )


@dataclass(frozen=True, slots=True)
class MetricComparison:
    decision: ExperimentDecision
    stop_reason: str | None
    result: dict[str, object]


@dataclass(frozen=True, slots=True)
class _HostPolicyResolution:
    decision: PolicyDecision
    permission_decision_id: str


def validate_experiment_options(options: TaskRunExperimentOptions) -> None:
    command = options.benchmark_command.strip()
    if not command:
        raise ValueError("--benchmark-command must not be empty")
    if not _valid_metric_name(options.primary_metric):
        raise ValueError(f"invalid --metric name: {options.primary_metric}")
    if options.metric_direction not in {"lower", "higher"}:
        raise ValueError("--metric-direction must be lower or higher")
    if not math.isfinite(options.min_delta) or options.min_delta < 0:
        raise ValueError("--min-delta must be a finite non-negative number")


def validate_experiment_permission_profile(
    options: TaskRunExperimentOptions,
    *,
    workspace_root: str,
    permission_profile: Mapping[str, Any],
    budget: Mapping[str, Any],
) -> None:
    failures: list[str] = []
    for label, phase, command in _required_host_commands(options):
        request = _host_policy_request(
            workspace_root=workspace_root,
            auto_run_id="preflight",
            task_run_id="preflight",
            step_id=None,
            phase=phase,
            command=command,
        )
        raw_decision = decide_shell_access(request)
        resolution = PermissionProfileResolver().resolve(
            request,
            raw_decision,
            permission_profile,
            ui_available=False,
            budget=budget,
        )
        if resolution.resolved_decision.effect == "allow":
            continue
        preview, _applied = redacted_command_preview(command)
        reason = resolution.resolved_decision.reason or "not allowed"
        failures.append(f"{label} `{preview}`: {reason}")
    if failures:
        joined = "; ".join(failures)
        raise ValueError(
            "experiment mode requires permission profile to allow required "
            f"host commands: {joined}"
        )


def parse_metric_lines(output: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for line_number, raw_line in enumerate(output.splitlines(), start=1):
        if not raw_line.startswith("METRIC "):
            continue
        payload = raw_line[len("METRIC ") :]
        if "=" not in payload:
            raise MetricParseError(
                f"malformed METRIC line {line_number}: expected name=value",
                code="malformed_metric_line",
            )
        name, raw_value = payload.split("=", 1)
        name = name.strip()
        raw_value = raw_value.strip()
        if not _valid_metric_name(name):
            raise MetricParseError(
                f"invalid metric name on line {line_number}: {name}",
                code="invalid_metric_name",
            )
        if name in metrics:
            raise MetricParseError(
                f"duplicate metric name on line {line_number}: {name}",
                code="duplicate_metric",
            )
        if not raw_value:
            raise MetricParseError(
                f"empty metric value on line {line_number}: {name}",
                code="empty_metric_value",
            )
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise MetricParseError(
                f"invalid metric value on line {line_number}: {name}",
                code="invalid_metric_value",
            ) from exc
        if not math.isfinite(value):
            raise MetricParseError(
                f"non-finite metric value on line {line_number}: {name}",
                code="non_finite_metric_value",
            )
        metrics[name] = value
    return metrics


def compare_primary_metric(
    *,
    baseline_metrics: Mapping[str, float],
    trial_metrics: Mapping[str, float],
    options: TaskRunExperimentOptions,
) -> MetricComparison:
    metric = options.primary_metric
    if metric not in baseline_metrics:
        raise MetricParseError(
            f"primary metric missing from baseline: {metric}",
            code="missing_primary_metric",
        )
    if metric not in trial_metrics:
        raise MetricParseError(
            f"primary metric missing from trial: {metric}",
            code="missing_primary_metric",
        )
    baseline = float(baseline_metrics[metric])
    trial = float(trial_metrics[metric])
    delta = trial - baseline
    if options.metric_direction == "lower":
        improved = trial < baseline - options.min_delta
        regressed = trial > baseline
    else:
        improved = trial > baseline + options.min_delta
        regressed = trial < baseline
    result = {
        "primaryMetric": metric,
        "direction": options.metric_direction,
        "baselineValue": baseline,
        "trialValue": trial,
        "delta": delta,
        "minDelta": options.min_delta,
        "improved": improved,
        "regressed": regressed,
    }
    if improved:
        result["reason"] = "primary metric improved"
        return MetricComparison("keep", None, result)
    if regressed:
        result["reason"] = "primary metric regressed"
        return MetricComparison("blocked", "experiment_blocked", result)
    result["reason"] = "primary metric did not improve"
    return MetricComparison("blocked", "experiment_blocked", result)


def experiment_started_payload(options: TaskRunExperimentOptions, workspace_root: str) -> dict[str, object]:
    preview, _applied = redacted_command_preview(options.benchmark_command)
    return {
        "enabled": True,
        "primaryMetric": options.primary_metric,
        "metricDirection": options.metric_direction,
        "minDelta": options.min_delta,
        "revertOnRegression": options.revert_on_regression,
        "benchmarkCommandPreview": preview,
        "safeRevertAvailable": _looks_like_git_workspace(workspace_root),
    }


def command_record(result: HostCommandResult) -> dict[str, object]:
    preview, _applied = redacted_command_preview(result.command)
    return {
        "phase": result.phase,
        "commandPreview": preview,
        "exitCode": result.exit_code,
        "cancelled": result.cancelled,
        "timedOut": result.timed_out,
        "policyEffect": result.policy_effect,
        "reason": result.reason,
        "permissionDecisionId": result.permission_decision_id,
        "durationMs": result.duration_ms,
        "outputSha256": _sha256(result.output),
        "outputPreview": _preview(result.output),
    }


def run_host_command(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    phase: Literal["baseline", "trial", "diff", "revert"],
    command: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> HostCommandResult:
    request = _host_policy_request(
        workspace_root=task_run.workspace_root,
        auto_run_id=auto_run_id,
        task_run_id=task_run.id,
        step_id=step.id,
        phase=phase,
        command=command,
        timeout=timeout,
    )
    started_at = service._now_iso()
    started_monotonic = time.monotonic()
    resolution = _resolve_host_policy(service, task_run, step, request)
    result = _host_command_result(
        resolution,
        phase=phase,
        command=command,
        workspace_root=task_run.workspace_root,
        timeout=timeout,
    )
    result = replace(
        result,
        started_at=started_at,
        ended_at=service._now_iso(),
        duration_ms=result.duration_ms or _elapsed_ms(started_monotonic),
    )
    _append_host_command_event(service, task_run, step, auto_run_id, result)
    _record_host_command_audit(service, auto_run_id, result)
    return result


def _resolve_host_policy(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    request: PolicyRequest,
) -> _HostPolicyResolution:
    raw_decision = decide_shell_access(request)
    resolution = PermissionProfileResolver().resolve(
        request,
        raw_decision,
        task_run.permission_profile,
        ui_available=False,
        budget=task_run.budget,
    )
    permission = service.repository.append_permission_decision(
        task_run_id=task_run.id,
        step_id=step.id,
        tool_execution_id=None,
        policy_request=request.model_dump(by_alias=True, exclude_none=True),
        raw_decision=resolution.raw_decision.model_dump(by_alias=True, exclude_none=True),
        resolved_decision=resolution.resolved_decision.model_dump(
            by_alias=True,
            exclude_none=True,
        ),
        profile_name=str(resolution.metadata.get("name") or task_run.permission_profile.get("name", "unknown")),
        occurred_at=service._now_iso(),
    )
    return _HostPolicyResolution(resolution.resolved_decision, permission.id)


def _host_command_result(
    resolution: _HostPolicyResolution,
    *,
    phase: str,
    command: str,
    workspace_root: str,
    timeout: float,
) -> HostCommandResult:
    decision = resolution.decision
    if decision.effect != "allow":
        return _blocked_host_command_result(resolution, phase=phase, command=command)
    return _execute_allowed_host_command(
        resolution,
        phase=phase,
        command=command,
        workspace_root=workspace_root,
        timeout=timeout,
    )


def _blocked_host_command_result(
    resolution: _HostPolicyResolution,
    *,
    phase: str,
    command: str,
) -> HostCommandResult:
    return HostCommandResult(
        phase=phase,
        command=command,
        output="",
        exit_code=None,
        cancelled=False,
        timed_out=False,
        policy_effect=resolution.decision.effect,
        reason=resolution.decision.reason,
        permission_decision_id=resolution.permission_decision_id,
    )


def _execute_allowed_host_command(
    resolution: _HostPolicyResolution,
    *,
    phase: str,
    command: str,
    workspace_root: str,
    timeout: float,
) -> HostCommandResult:
    effective_args = resolution.decision.normalized_args or {
        "command": command,
        "timeout": timeout,
    }
    cwd = Path(resolution.decision.resolved_paths.get("cwd", workspace_root))
    started_monotonic = time.monotonic()
    try:
        sandbox = asyncio.run(
            run_shell_command(
                str(effective_args["command"]),
                cwd=cwd,
                timeout=float(effective_args.get("timeout") or timeout),
            )
        )
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        return HostCommandResult(
            phase=phase,
            command=command,
            output="",
            exit_code=None,
            cancelled=False,
            timed_out=False,
            policy_effect=resolution.decision.effect,
            reason=f"host command failed: {exc}",
            permission_decision_id=resolution.permission_decision_id,
            duration_ms=_elapsed_ms(started_monotonic),
        )
    return HostCommandResult(
        phase=phase,
        command=command,
        output=sandbox.output,
        exit_code=sandbox.exit_code,
        cancelled=sandbox.cancelled,
        timed_out=sandbox.timed_out,
        policy_effect=resolution.decision.effect,
        reason=None,
        permission_decision_id=resolution.permission_decision_id,
        duration_ms=_elapsed_ms(started_monotonic),
    )


def _required_host_commands(
    options: TaskRunExperimentOptions,
) -> list[tuple[str, str, str]]:
    commands = [
        ("benchmark", "baseline", options.benchmark_command),
        ("git rev-parse", "diff", "git rev-parse --verify HEAD"),
        ("git status", "diff", "git status --porcelain=v1 --untracked-files=all"),
        ("git diff", "diff", "git diff --binary --no-ext-diff"),
        ("git diff numstat", "diff", "git diff --numstat --no-ext-diff"),
    ]
    if options.revert_on_regression:
        commands.append(
            (
                "git safe revert",
                "revert",
                "git diff --binary --no-ext-diff | git apply -R",
            )
        )
    return commands


def _host_policy_request(
    *,
    workspace_root: str,
    auto_run_id: str,
    task_run_id: str,
    step_id: str | None,
    phase: str,
    command: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> PolicyRequest:
    return PolicyRequest(
        runtimeSessionId=f"taskrun-host-{auto_run_id}",
        runId=auto_run_id,
        toolName="bash",
        args={"command": command, "timeout": timeout},
        cwd=workspace_root,
        actor="extension",
        source={
            "host": "task_run",
            "decision_subject": "host_command",
            "phase": phase,
            "task_run_id": task_run_id,
            "step_id": step_id,
            "auto_run_id": auto_run_id,
        },
    )


def capture_workspace_snapshot(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
) -> dict[str, object]:
    head = run_host_command(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        phase="diff",
        command="git rev-parse --verify HEAD",
    )
    status = run_host_command(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        phase="diff",
        command="git status --porcelain=v1 --untracked-files=all",
    )
    if not head.succeeded or not status.succeeded:
        return {
            "git_available": False,
            "git_head": None,
            "status": [],
            "safe_revert_supported": False,
            "unsafe_revert_reason": head.reason
            or status.reason
            or "git snapshot command failed",
        }
    return {
        "git_available": True,
        "git_head": head.output.strip(),
        "status": _status_lines(status.output),
        "safe_revert_supported": True,
        "unsafe_revert_reason": None,
    }


def capture_diff_ref(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    before_snapshot: Mapping[str, object],
) -> dict[str, object]:
    after_snapshot = capture_workspace_snapshot(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
    )
    before_status = [str(item) for item in before_snapshot.get("status", []) if str(item)]
    after_status = [str(item) for item in after_snapshot.get("status", []) if str(item)]
    diff = run_host_command(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        phase="diff",
        command="git diff --binary --no-ext-diff",
    )
    numstat = run_host_command(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        phase="diff",
        command="git diff --numstat --no-ext-diff",
    )
    safe, unsafe_reason = safe_revert_check(
        before_snapshot,
        after_snapshot,
        before_status,
        after_status,
        numstat.output if numstat.succeeded else "",
    )
    return {
        "git_head": after_snapshot.get("git_head") or before_snapshot.get("git_head"),
        "status_before": before_status,
        "status_after": after_status,
        "changed_paths": _changed_paths(after_status),
        "diff_sha256": _sha256(diff.output if diff.succeeded else ""),
        "diff_preview": _preview(diff.output if diff.succeeded else ""),
        "safe_revert_supported": safe,
        "unsafe_revert_reason": unsafe_reason,
    }


def run_safe_revert(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    diff_ref: Mapping[str, object],
) -> HostCommandResult:
    if not bool(diff_ref.get("safe_revert_supported")):
        return HostCommandResult(
            phase="revert",
            command="git diff --binary --no-ext-diff | git apply -R",
            output="",
            exit_code=None,
            cancelled=False,
            timed_out=False,
            policy_effect="block",
            reason=str(diff_ref.get("unsafe_revert_reason") or "unsafe revert"),
            permission_decision_id=None,
        )
    return run_host_command(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        phase="revert",
        command="git diff --binary --no-ext-diff | git apply -R",
    )


def append_experiment_record(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    decision: ExperimentDecision,
    hypothesis: str,
    change: Mapping[str, Any],
    command: Mapping[str, Any],
    metrics: Mapping[str, Any],
    result: Mapping[str, Any],
    diff_ref: Mapping[str, Any],
) -> TaskRunExperimentAttempt:
    created_at = service._now_iso()
    experiment = service.repository.append_experiment(
        task_run_id=task_run.id,
        step_id=step.id,
        hypothesis=hypothesis,
        change=change,
        command=command,
        metrics=metrics,
        result=result,
        decision=decision,
        diff_ref=diff_ref,
        created_at=created_at,
    )
    _append_experiment_event(
        service,
        task_run,
        step,
        auto_run_id=auto_run_id,
        experiment=experiment,
    )
    return TaskRunExperimentAttempt(
        experiment=experiment,
        metrics={key: float(value) for key, value in metrics.items()},
        decision=decision,
        diff_ref=dict(diff_ref),
    )


def _append_host_command_event(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    auto_run_id: str,
    result: HostCommandResult,
) -> None:
    service.repository.append_event(
        task_run_id=task_run.id,
        step_id=step.id,
        event_type="task_experiment_host_command_finished",
        payload={
            "auto_run_id": auto_run_id,
            "step_id": step.id,
            "phase": result.phase,
            "command": command_record(result),
        },
        occurred_at=service._now_iso(),
    )


def _append_experiment_event(
    service: Any,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    *,
    auto_run_id: str,
    experiment: TaskExperimentRecord,
) -> None:
    event_type = {
        "baseline": "task_experiment_baseline_recorded",
        "keep": "task_experiment_trial_recorded",
        "revert": "task_experiment_reverted",
        "blocked": "task_experiment_blocked",
    }[experiment.decision]
    result = experiment.result
    service.repository.append_event(
        task_run_id=task_run.id,
        step_id=step.id,
        event_type=event_type,
        payload={
            "auto_run_id": auto_run_id,
            "experiment_id": experiment.id,
            "step_id": step.id,
            "decision": experiment.decision,
            "primary_metric": result.get("primaryMetric"),
            "baseline_value": result.get("baselineValue"),
            "trial_value": result.get("trialValue"),
            "delta": result.get("delta"),
            "benchmark_command_preview": experiment.command.get("commandPreview"),
            "diff_ref": experiment.diff_ref,
            "reason": result.get("reason"),
        },
        occurred_at=experiment.created_at,
    )
    if experiment.decision in {"keep", "revert", "blocked"}:
        service.repository.append_event(
            task_run_id=task_run.id,
            step_id=step.id,
            event_type="task_experiment_decided",
            payload={
                "auto_run_id": auto_run_id,
                "experiment_id": experiment.id,
                "step_id": step.id,
                "decision": experiment.decision,
                "reason": result.get("reason"),
            },
            occurred_at=experiment.created_at,
        )


def _status_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip()]


def _changed_paths(status_lines: list[str]) -> list[str]:
    paths: list[str] = []
    for line in status_lines:
        if line.startswith("?? "):
            path = line[3:]
        elif len(line) >= 4:
            path = line[3:]
        else:
            path = line
        if path and path not in paths:
            paths.append(path)
    return paths


def _valid_metric_name(name: str) -> bool:
    return (
        bool(name)
        and name not in _RESERVED_METRIC_NAMES
        and bool(_METRIC_NAME_RE.fullmatch(name))
    )


def _looks_like_git_workspace(workspace_root: str) -> bool:
    git_path = Path(workspace_root) / ".git"
    return git_path.exists()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _preview(value: str, *, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit]


__all__ = [
    "ExperimentDecision",
    "HostCommandResult",
    "MetricComparison",
    "MetricDirection",
    "MetricParseError",
    "TaskRunExperimentAttempt",
    "TaskRunExperimentOptions",
    "append_experiment_record",
    "capture_diff_ref",
    "capture_workspace_snapshot",
    "command_record",
    "compare_primary_metric",
    "experiment_started_payload",
    "parse_metric_lines",
    "run_host_command",
    "run_safe_revert",
    "validate_experiment_permission_profile",
    "validate_experiment_options",
]
