"""P3 Mini Parameter Golf autonomous multi-attempt loop."""

from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from cli.core.parameter_golf_contract import (
    ANCHOR_NAME,
    BASELINE_MEAN_VAL_BPB,
    BASELINE_N,
    BASELINE_SAMPLE_STD_VAL_BPB,
    FINAL_SIGNIFICANCE_P_THRESHOLD,
    GENERIC_STOP_RUNNER_ERROR,
    LOOP_STOP_ACTOR_PROPOSAL_INVALID,
    LOOP_STOP_ARTIFACT_CAP_VIOLATION,
    LOOP_STOP_BUDGET_MISMATCH,
    LOOP_STOP_CONSECUTIVE_INVALID_ATTEMPTS,
    LOOP_STOP_CONSECUTIVE_NO_IMPROVEMENT,
    LOOP_STOP_FINAL_SUCCESS,
    LOOP_STOP_MAX_ATTEMPTS_REACHED,
    LOOP_STOP_VALIDATION_TOUCH_DETECTED,
    MAX_LOOP_ATTEMPTS,
    MIN_FINAL_SIGNIFICANCE_RUNS,
    PARAMETER_GOLF_GENERIC_STOP_REASONS,
    PARAMETER_GOLF_LOOP_STOP_REASONS,
    SUBMISSION_ARTIFACT_CAP_BYTES,
    VERDICT_ACCEPTED,
    VERDICT_ERROR,
)
from cli.core.taskrun_parameter_golf_attempt import (
    DEFAULT_TIMEOUT_SECONDS,
    ParameterGolfAttemptOptions,
    run_single_parameter_golf_attempt,
)
from cli.core.taskrun_parameter_golf_trajectory import p3_trajectory_summary
from cli.core.taskrun_service import TaskRunService
from storage.taskrun_repository import TaskExperimentRecord, TaskRunRecord


@dataclass(frozen=True, slots=True)
class ParameterGolfLoopOptions:
    anchor: str
    workspace: Path
    max_attempts: int
    no_improvement_patience: int = 3
    invalid_attempt_patience: int = 2
    final_significance_runs: int = 0
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    seed_start: int = 42
    proposal_file: Path | None = None
    actor_command: str | None = None


@dataclass(frozen=True, slots=True)
class ParameterGolfActorProposal:
    hypothesis: str
    base_attempt_id: str | None
    expected_metric_direction: str
    change_summary: str
    run_command: str
    submission_files: tuple[Path, ...]
    risk_flags: tuple[str, ...] = ()
    stop_request: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "hypothesis": self.hypothesis,
            "base_attempt_id": self.base_attempt_id,
            "expected_metric_direction": self.expected_metric_direction,
            "change_summary": self.change_summary,
            "run_command": self.run_command,
            "submission_files": [path.as_posix() for path in self.submission_files],
            "risk_flags": list(self.risk_flags),
            "stop_request": self.stop_request,
        }


@dataclass(frozen=True, slots=True)
class ParameterGolfLoopIterationResult:
    index: int
    attempt_id: str | None
    parent_experiment_id: str | None
    verdict_status: str | None
    val_bpb: float | None
    artifact_size_bytes: int | None
    records_ref: str | None
    best_delta: float | None
    proposal_valid: bool
    stop_candidate: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ParameterGolfLoopResult:
    task_run: TaskRunRecord
    iterations: tuple[ParameterGolfLoopIterationResult, ...]
    stop_reason: str
    anchor_stop_detail: str | None
    trajectory: dict[str, object]
    final_significance: dict[str, object] | None
    exit_code: int


def validate_loop_options(options: ParameterGolfLoopOptions) -> None:
    if options.anchor != ANCHOR_NAME:
        raise ValueError(f"--anchor must be {ANCHOR_NAME}")
    if not options.workspace.exists() or not options.workspace.is_dir():
        raise ValueError(
            f"--workspace must be an existing directory: {options.workspace}"
        )
    if options.max_attempts < 1:
        raise ValueError("--max-attempts must be >= 1")
    if options.max_attempts > MAX_LOOP_ATTEMPTS:
        raise ValueError(f"--max-attempts must be <= {MAX_LOOP_ATTEMPTS}")
    if options.no_improvement_patience < 1:
        raise ValueError("--no-improvement-patience must be >= 1")
    if options.invalid_attempt_patience < 1:
        raise ValueError("--invalid-attempt-patience must be >= 1")
    if options.final_significance_runs not in {0} and (
        options.final_significance_runs < MIN_FINAL_SIGNIFICANCE_RUNS
    ):
        raise ValueError("--final-significance-runs must be 0 or >= 3")
    if options.timeout_seconds < 500:
        raise ValueError("--timeout-seconds must be >= 500 for the Tier 2 480s budget")
    if options.proposal_file is None and not (options.actor_command or "").strip():
        raise ValueError("--proposal-file or --actor-command is required")


def build_actor_context(
    *,
    task_run: TaskRunRecord,
    trajectory: Mapping[str, object],
    iteration: int,
) -> dict[str, object]:
    return {
        "anchor": ANCHOR_NAME,
        "task_run_id": task_run.id,
        "iteration": iteration,
        "rules": {
            "metric_direction": "lower val_bpb",
            "artifact_cap_bytes": SUBMISSION_ARTIFACT_CAP_BYTES,
            "required_submission_file": "train_gpt.py",
            "fixed_budget": {
                "tier": "tier2_a6000",
                "max_wallclock_seconds": 480,
                "train_shards": 1,
                "vocab_size": 1024,
            },
            "forbidden": [
                "validation set changes",
                "budget/tier changes",
                "runtime branch/session fork",
            ],
        },
        "trajectory": dict(trajectory),
    }


def parse_actor_proposal(
    payload: str | Mapping[str, Any],
) -> ParameterGolfActorProposal:
    raw = json.loads(payload) if isinstance(payload, str) else dict(payload)
    submission_files = raw.get("submission_files")
    if not isinstance(submission_files, Sequence) or isinstance(submission_files, str):
        submission_files = []
    risk_flags = raw.get("risk_flags")
    if not isinstance(risk_flags, Sequence) or isinstance(risk_flags, str):
        risk_flags = []
    base_attempt_id = raw.get("base_attempt_id")
    return ParameterGolfActorProposal(
        hypothesis=str(raw.get("hypothesis") or "").strip(),
        base_attempt_id=base_attempt_id
        if isinstance(base_attempt_id, str) and base_attempt_id
        else None,
        expected_metric_direction=str(
            raw.get("expected_metric_direction") or ""
        ).strip(),
        change_summary=str(raw.get("change_summary") or "").strip(),
        run_command=str(raw.get("run_command") or "").strip(),
        submission_files=tuple(Path(str(path)) for path in submission_files),
        risk_flags=tuple(str(flag) for flag in risk_flags),
        stop_request=str(raw["stop_request"]).strip()
        if raw.get("stop_request")
        else None,
    )


def validate_actor_proposal(
    proposal: ParameterGolfActorProposal,
    *,
    trajectory: Mapping[str, object],
) -> list[str]:
    reasons: list[str] = []
    if not proposal.hypothesis:
        reasons.append("missing_hypothesis")
    if proposal.expected_metric_direction != "lower val_bpb":
        reasons.append("expected_metric_direction_must_be_lower_val_bpb")
    if not proposal.change_summary:
        reasons.append("missing_change_summary")
    if not proposal.run_command:
        reasons.append("missing_run_command")
    names = {path.name for path in proposal.submission_files}
    if "train_gpt.py" not in names:
        reasons.append("missing_submission_train_gpt")
    if (
        proposal.base_attempt_id
        and proposal.base_attempt_id not in _trajectory_attempt_ids(trajectory)
    ):
        reasons.append("base_attempt_not_in_task_run")
    command = proposal.run_command
    if (
        "MAX_WALLCLOCK_SECONDS=" in command
        and "MAX_WALLCLOCK_SECONDS=480" not in command
    ):
        reasons.append(LOOP_STOP_BUDGET_MISMATCH)
    if "VOCAB_SIZE=" in command and "VOCAB_SIZE=1024" not in command:
        reasons.append(LOOP_STOP_BUDGET_MISMATCH)
    lowered_flags = {flag.lower() for flag in proposal.risk_flags}
    if {"budget_mismatch", "tier_change"} & lowered_flags:
        reasons.append(LOOP_STOP_BUDGET_MISMATCH)
    if "validation_touch" in lowered_flags:
        reasons.append(LOOP_STOP_VALIDATION_TOUCH_DETECTED)
    return _dedupe(reasons)


def run_parameter_golf_attempt_loop(
    service: TaskRunService,
    id_or_prefix: str | None,
    cwd: Path,
    options: ParameterGolfLoopOptions,
    *,
    permission_profile: Mapping[str, Any] | None = None,
) -> ParameterGolfLoopResult:
    validate_loop_options(options)
    workspace_root = str(Path(cwd).resolve())
    service.recover_stale_running(workspace_root)
    task_run = service._select_task_run(workspace_root, id_or_prefix)
    proposals = (
        _load_proposal_file(options.proposal_file) if options.proposal_file else []
    )
    _append_loop_event(
        service,
        task_run.id,
        "task_parameter_golf_loop_started",
        {"options": _options_payload(options)},
    )

    iterations: list[ParameterGolfLoopIterationResult] = []
    invalid_count = 0
    no_improvement_count = 0
    # Wallclock/GPU budget accounting is deferred; keep the event field explicit.
    stop_reason: str | None = None
    anchor_stop_detail: str | None = None
    final_significance: dict[str, object] | None = None

    for index in range(1, options.max_attempts + 1):
        task_run = service.repository.get_task_run(task_run.id) or task_run
        experiments = service.repository.list_experiments(task_run.id)
        trajectory = p3_trajectory_summary(experiments, task_run_id=task_run.id)
        context = build_actor_context(
            task_run=task_run, trajectory=trajectory, iteration=index
        )
        proposal = _proposal_for_iteration(
            proposals,
            index=index,
            options=options,
            workspace=options.workspace,
            context=context,
        )
        proposal = _proposal_with_default_base(proposal, trajectory)
        validation = validate_actor_proposal(proposal, trajectory=trajectory)
        if validation:
            invalid_count += 1
            stop_candidate = (
                LOOP_STOP_ACTOR_PROPOSAL_INVALID
                if invalid_count >= options.invalid_attempt_patience
                else None
            )
            iteration = ParameterGolfLoopIterationResult(
                index=index,
                attempt_id=None,
                parent_experiment_id=proposal.base_attempt_id,
                verdict_status=None,
                val_bpb=None,
                artifact_size_bytes=None,
                records_ref=None,
                best_delta=None,
                proposal_valid=False,
                stop_candidate=stop_candidate,
                reason=",".join(validation),
            )
            iterations.append(iteration)
            _append_loop_event(
                service,
                task_run.id,
                "task_parameter_golf_loop_iteration_completed",
                _iteration_payload(iteration),
            )
            if stop_candidate is not None:
                stop_reason = stop_candidate
                break
            continue

        before_best = _best_val(trajectory)
        hypothesis_file = _write_loop_hypothesis(options.workspace, index, proposal)
        attempt = run_single_parameter_golf_attempt(
            service,
            task_run.id,
            cwd,
            ParameterGolfAttemptOptions(
                anchor=options.anchor,
                workspace=options.workspace,
                hypothesis_file=hypothesis_file,
                command=proposal.run_command,
                seed=options.seed_start + index - 1,
                timeout_seconds=options.timeout_seconds,
                submission_files=proposal.submission_files,
                parent_experiment_id=proposal.base_attempt_id,
            ),
            permission_profile=permission_profile if index == 1 else None,
        )
        task_run = attempt.task_result.task_run
        after_experiments = service.repository.list_experiments(task_run.id)
        trajectory = p3_trajectory_summary(after_experiments, task_run_id=task_run.id)
        after_best = _best_val(trajectory)
        best_delta = _delta(before_best, after_best)
        verdict_status = str(attempt.harness.verdict.get("status") or "")
        val_bpb = _float_or_none(attempt.harness.metrics.get("val_bpb"))
        artifact_size = _int_or_none(attempt.harness.metrics.get("artifact_size_bytes"))
        if verdict_status in {VERDICT_ERROR}:
            invalid_count += 1
        else:
            invalid_count = 0
        if best_delta is not None and best_delta < 0:
            no_improvement_count = 0
        else:
            no_improvement_count += 1
        stop_candidate = _attempt_stop_candidate(
            verdict_status=verdict_status,
            artifact_size=artifact_size,
            harness_reasons=attempt.harness.reasons,
            no_improvement_count=no_improvement_count,
            invalid_count=invalid_count,
            options=options,
        )
        if stop_candidate is None and proposal.stop_request == LOOP_STOP_FINAL_SUCCESS:
            final_significance = final_significance_from_samples(
                _candidate_samples(attempt.experiment, options.final_significance_runs)
            )
            _write_final_significance(service, attempt.experiment, final_significance)
            if final_significance.get("final") is True:
                stop_candidate = LOOP_STOP_FINAL_SUCCESS
        iteration = ParameterGolfLoopIterationResult(
            index=index,
            attempt_id=attempt.experiment.id,
            parent_experiment_id=proposal.base_attempt_id,
            verdict_status=verdict_status,
            val_bpb=val_bpb,
            artifact_size_bytes=artifact_size,
            records_ref=attempt.records_ref,
            best_delta=best_delta,
            proposal_valid=True,
            stop_candidate=stop_candidate,
        )
        iterations.append(iteration)
        _append_loop_event(
            service,
            task_run.id,
            "task_parameter_golf_loop_iteration_completed",
            _iteration_payload(iteration),
        )
        if stop_candidate is not None:
            stop_reason = stop_candidate
            break

    if stop_reason is None:
        stop_reason = LOOP_STOP_MAX_ATTEMPTS_REACHED
    if (
        stop_reason
        not in PARAMETER_GOLF_LOOP_STOP_REASONS | PARAMETER_GOLF_GENERIC_STOP_REASONS
    ):
        stop_reason = GENERIC_STOP_RUNNER_ERROR
    task_run = service.repository.get_task_run(task_run.id) or task_run
    final_trajectory = p3_trajectory_summary(
        service.repository.list_experiments(task_run.id),
        task_run_id=task_run.id,
    )
    _append_loop_event(
        service,
        task_run.id,
        "task_parameter_golf_loop_stopped",
        {
            "stop_reason": stop_reason,
            "anchor_stop_detail": anchor_stop_detail,
            "attempts_run": len([item for item in iterations if item.attempt_id]),
            "final_significance": final_significance,
        },
    )
    # Force summary projection from DB truth after loop stop.
    service.summary(task_run.id, cwd)
    task_run = service.repository.get_task_run(task_run.id) or task_run
    return ParameterGolfLoopResult(
        task_run=task_run,
        iterations=tuple(iterations),
        stop_reason=stop_reason,
        anchor_stop_detail=anchor_stop_detail,
        trajectory=final_trajectory,
        final_significance=final_significance,
        exit_code=0
        if stop_reason
        in {
            LOOP_STOP_FINAL_SUCCESS,
            LOOP_STOP_MAX_ATTEMPTS_REACHED,
            LOOP_STOP_CONSECUTIVE_NO_IMPROVEMENT,
        }
        else 1,
    )


def final_significance_from_samples(samples: Sequence[float]) -> dict[str, object]:
    clean = [float(value) for value in samples if math.isfinite(float(value))]
    payload: dict[str, object] = {
        "final": False,
        "sample_size": len(clean),
        "n_runs": len(clean),
        "threshold": FINAL_SIGNIFICANCE_P_THRESHOLD,
        "baseline_mean": BASELINE_MEAN_VAL_BPB,
        "baseline_std": BASELINE_SAMPLE_STD_VAL_BPB,
        "baseline_n": BASELINE_N,
    }
    if len(clean) < MIN_FINAL_SIGNIFICANCE_RUNS:
        payload.update(
            {
                "reason": "insufficient_candidate_samples",
                "p_value": None,
                "welch_t": None,
            }
        )
        return payload
    mean = statistics.fmean(clean)
    std = statistics.stdev(clean) if len(clean) > 1 else 0.0
    denom = math.sqrt(
        (std * std / len(clean)) + (BASELINE_SAMPLE_STD_VAL_BPB**2 / BASELINE_N)
    )
    if denom == 0:
        payload.update(
            {
                "reason": "zero_variance",
                "p_value": None,
                "welch_t": None,
                "mean": mean,
                "std": std,
            }
        )
        return payload
    welch_t = (mean - BASELINE_MEAN_VAL_BPB) / denom
    # Normal approximation is conservative enough for the MVP gate and avoids a
    # new SciPy dependency in CLI core.
    p_value = math.erfc(abs(welch_t) / math.sqrt(2.0))
    payload.update({"mean": mean, "std": std, "welch_t": welch_t, "p_value": p_value})
    if mean < BASELINE_MEAN_VAL_BPB and p_value < FINAL_SIGNIFICANCE_P_THRESHOLD:
        payload.update({"final": True, "reason": LOOP_STOP_FINAL_SUCCESS})
    else:
        payload["reason"] = "not_significant"
    return payload


def _load_proposal_file(path: Path | None) -> list[ParameterGolfActorProposal]:
    if path is None:
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        raw_items = json.loads(text)
    else:
        raw_items = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [parse_actor_proposal(item) for item in raw_items]


def _proposal_for_iteration(
    proposals: Sequence[ParameterGolfActorProposal],
    *,
    index: int,
    options: ParameterGolfLoopOptions,
    workspace: Path,
    context: Mapping[str, object],
) -> ParameterGolfActorProposal:
    if index <= len(proposals):
        return proposals[index - 1]
    if not options.actor_command:
        return ParameterGolfActorProposal("", None, "", "", "", ())
    context_path = workspace / "records" / "_loop_context.json"
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(
        json.dumps(context, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    env = dict(os.environ)
    env["MAGIPI_P3_CONTEXT_FILE"] = str(context_path)
    result = subprocess.run(
        options.actor_command,
        cwd=workspace,
        env=env,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        return ParameterGolfActorProposal(
            "",
            None,
            "",
            "",
            "",
            (),
            risk_flags=(f"actor_command_failed:{result.returncode}",),
        )
    return parse_actor_proposal(result.stdout)


def _proposal_with_default_base(
    proposal: ParameterGolfActorProposal,
    trajectory: Mapping[str, object],
) -> ParameterGolfActorProposal:
    if proposal.base_attempt_id is not None:
        return proposal
    next_action = trajectory.get("next_action")
    if not isinstance(next_action, Mapping):
        return proposal
    base_attempt_id = next_action.get("base_attempt_id")
    if not isinstance(base_attempt_id, str) or not base_attempt_id:
        return proposal
    return replace(proposal, base_attempt_id=base_attempt_id)


def _attempt_stop_candidate(
    *,
    verdict_status: str,
    artifact_size: int | None,
    harness_reasons: Sequence[str],
    no_improvement_count: int,
    invalid_count: int,
    options: ParameterGolfLoopOptions,
) -> str | None:
    if artifact_size is not None and artifact_size > SUBMISSION_ARTIFACT_CAP_BYTES:
        return LOOP_STOP_ARTIFACT_CAP_VIOLATION
    if any("budget_mismatch" in reason for reason in harness_reasons):
        return LOOP_STOP_BUDGET_MISMATCH
    if invalid_count >= options.invalid_attempt_patience:
        return LOOP_STOP_CONSECUTIVE_INVALID_ATTEMPTS
    if (
        no_improvement_count >= options.no_improvement_patience
        and verdict_status != VERDICT_ACCEPTED
    ):
        return LOOP_STOP_CONSECUTIVE_NO_IMPROVEMENT
    return None


def _write_loop_hypothesis(
    workspace: Path,
    index: int,
    proposal: ParameterGolfActorProposal,
) -> Path:
    target = (
        workspace / "records" / "_loop_inputs" / f"attempt_{index:03d}_hypothesis.md"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"{proposal.hypothesis}\n\n## Change Summary\n\n{proposal.change_summary}\n",
        encoding="utf-8",
    )
    return target


def _candidate_samples(
    experiment: TaskExperimentRecord,
    repeat_count: int,
) -> list[float]:
    value = _float_or_none(experiment.metrics.get("val_bpb"))
    if value is None or repeat_count < MIN_FINAL_SIGNIFICANCE_RUNS:
        return []
    repeated = experiment.result.get("significance", {}).get("candidate_runs")
    if isinstance(repeated, Sequence) and not isinstance(repeated, str):
        return [
            _float_or_none(item)
            for item in repeated
            if _float_or_none(item) is not None
        ]
    return []


def _write_final_significance(
    service: TaskRunService,
    experiment: TaskExperimentRecord,
    significance: Mapping[str, object],
) -> TaskExperimentRecord:
    result = dict(experiment.result)
    result["significance"] = dict(significance)
    return service.repository.update_experiment_result(experiment.id, result)


def _trajectory_attempt_ids(trajectory: Mapping[str, object]) -> set[str]:
    tree = trajectory.get("tree")
    if not isinstance(tree, Mapping):
        return set()
    nodes = tree.get("nodes")
    if not isinstance(nodes, Sequence):
        return set()
    return {
        str(node.get("attempt_id"))
        for node in nodes
        if isinstance(node, Mapping) and node.get("attempt_id")
    }


def _best_val(trajectory: Mapping[str, object]) -> float | None:
    best = trajectory.get("current_best")
    if not isinstance(best, Mapping):
        return None
    metric = best.get("metric")
    if not isinstance(metric, Mapping):
        return None
    return _float_or_none(metric.get("value"))


def _delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return after - before


def _append_loop_event(
    service: TaskRunService,
    task_run_id: str,
    event_type: str,
    payload: Mapping[str, object],
) -> None:
    service.repository.append_event(
        task_run_id=task_run_id,
        event_type=event_type,
        payload=dict(payload),
        occurred_at=service._now_iso(),
    )


def _options_payload(options: ParameterGolfLoopOptions) -> dict[str, object]:
    return {
        "anchor": options.anchor,
        "workspace": str(options.workspace),
        "max_attempts": options.max_attempts,
        "no_improvement_patience": options.no_improvement_patience,
        "invalid_attempt_patience": options.invalid_attempt_patience,
        "final_significance_runs": options.final_significance_runs,
        "timeout_seconds": options.timeout_seconds,
        "proposal_file": str(options.proposal_file) if options.proposal_file else None,
        "actor_command": bool(options.actor_command),
    }


def _iteration_payload(
    iteration: ParameterGolfLoopIterationResult,
) -> dict[str, object]:
    return {
        "iteration": iteration.index,
        "attempt_id": iteration.attempt_id,
        "parent_experiment_id": iteration.parent_experiment_id,
        "verdict_status": iteration.verdict_status,
        "val_bpb": iteration.val_bpb,
        "artifact_size_bytes": iteration.artifact_size_bytes,
        "records_ref": iteration.records_ref,
        "best_delta": iteration.best_delta,
        "proposal_valid": iteration.proposal_valid,
        "stop_candidate": iteration.stop_candidate,
        "reason": iteration.reason,
    }


def _float_or_none(value: object) -> float | None:
    if isinstance(value, int | float):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    return None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


__all__ = [
    "ParameterGolfActorProposal",
    "ParameterGolfLoopIterationResult",
    "ParameterGolfLoopOptions",
    "ParameterGolfLoopResult",
    "build_actor_context",
    "final_significance_from_samples",
    "parse_actor_proposal",
    "run_parameter_golf_attempt_loop",
    "validate_actor_proposal",
    "validate_loop_options",
]
