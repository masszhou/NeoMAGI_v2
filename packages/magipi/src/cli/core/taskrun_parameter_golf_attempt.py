"""P3 Mini Parameter Golf single-attempt closed-loop helpers."""

from __future__ import annotations

import json
import math
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from cli.core.taskrun_experiments import (
    HostCommandResult,
    capture_diff_ref,
    capture_workspace_snapshot,
    command_record,
    run_host_command,
)
from cli.core.parameter_golf_contract import (
    ANCHOR_NAME,
    BASELINE_MEAN_VAL_BPB,
    BASELINE_N,
    BASELINE_SAMPLE_STD_VAL_BPB,
    DECISION_BLOCKED,
    DECISION_KEEP,
    DEFAULT_REFERENCE_BUDGET,
    DEFAULT_TIMEOUT_SECONDS,
    METRIC_SOURCE,
    REQUIRED_BUNDLE_DIRS,
    REQUIRED_BUNDLE_FILES,
    SIGNIFICANCE_REASON_SINGLE_RUN_ONLY,
    SUBMISSION_ARTIFACT_CAP_BYTES,
    VERDICT_ACCEPTED,
    VERDICT_ERROR,
    VERDICT_REJECTED,
)
from cli.core.taskrun_parameter_golf_artifacts import is_parameter_golf_artifact_record
from cli.core.taskrun_service import TaskRunResult, TaskRunService
from cli.core.taskrun_step import TaskRunRuntimeOptions, TaskRunStepOutcome
from policy.redaction import redacted_command_preview
from policy.shell_policy import MAX_TIMEOUT_SECONDS
from storage.ids import new_db_uuid
from storage.taskrun_repository import (
    TaskExperimentRecord,
    TaskRunRecord,
    TaskStepRecord,
)

_FINAL_EXACT_RE = re.compile(
    r"\bfinal_int8_zlib_roundtrip_exact\b.*?\bval_bpb:(?P<value>\S+)"
)
_TOKENIZER_RE = re.compile(r"\bval_bpb:enabled\b.*?\btokenizer_path=(?P<path>\S+)")
_TRAIN_LOADER_RE = re.compile(
    r"\btrain_loader:dataset:(?P<dataset>\S+)\s+train_shards:(?P<shards>\d+)"
)
_VAL_LOADER_RE = re.compile(r"\bval_loader:shards\s+pattern=(?P<pattern>\S+)")
_ENV_BUDGET_KEYS = {
    "DATA_PATH": "data_path",
    "TOKENIZER_PATH": "tokenizer_path",
    "VOCAB_SIZE": "vocab_size",
    "MAX_WALLCLOCK_SECONDS": "max_wallclock_seconds",
}


class ParameterGolfHarnessError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ParameterGolfAttemptOptions:
    anchor: str
    workspace: Path
    hypothesis_file: Path
    command: str
    seed: int
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    submission_files: tuple[Path, ...] = ()
    parent_experiment_id: str | None = None


@dataclass(frozen=True, slots=True)
class ParameterGolfHarnessResult:
    status: Literal["valid", "invalid", "error"]
    metrics: dict[str, Any]
    budget_comparable: bool
    required_files_ok: bool
    verdict: dict[str, Any]
    reasons: list[str]
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "metrics": dict(self.metrics),
            "budget_comparable": self.budget_comparable,
            "required_files_ok": self.required_files_ok,
            "verdict": dict(self.verdict),
            "reasons": list(self.reasons),
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class ParameterGolfAttemptResult:
    task_result: TaskRunResult
    experiment: TaskExperimentRecord
    records_ref: str
    harness: ParameterGolfHarnessResult

    @property
    def exit_code(self) -> int:
        return self.task_result.exit_code


def validate_attempt_options(options: ParameterGolfAttemptOptions) -> None:
    if options.anchor != ANCHOR_NAME:
        raise ValueError(f"--anchor must be {ANCHOR_NAME}")
    if not options.workspace.exists() or not options.workspace.is_dir():
        raise ValueError(
            f"--workspace must be an existing directory: {options.workspace}"
        )
    if not options.hypothesis_file.exists() or not options.hypothesis_file.is_file():
        raise ValueError(
            f"--hypothesis-file must be an existing file: {options.hypothesis_file}"
        )
    if not options.command.strip():
        raise ValueError("--command must not be empty")
    if options.timeout_seconds < 500:
        raise ValueError("--timeout-seconds must be >= 500 for the Tier 2 480s budget")
    if options.timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise ValueError(f"--timeout-seconds must be <= {MAX_TIMEOUT_SECONDS}")
    if not options.submission_files:
        raise ValueError("--submission-file is required at least once")
    source_paths = _resolve_submission_files(
        options.workspace, options.submission_files
    )
    names = {path.name for path in source_paths}
    if "train_gpt.py" not in names:
        raise ValueError("--submission-file must include train_gpt.py")
    if len(names) != len(source_paths):
        raise ValueError("--submission-file basenames must be unique")
    train_gpt = next(path for path in source_paths if path.name == "train_gpt.py")
    if not train_gpt.exists() or not train_gpt.is_file():
        raise ValueError(
            f"--submission-file train_gpt.py must exist before run: {train_gpt}"
        )


def _resolve_submission_files(
    workspace: Path, paths: Sequence[Path]
) -> tuple[Path, ...]:
    workspace = workspace.resolve()
    return tuple(path if path.is_absolute() else workspace / path for path in paths)


def parse_final_exact_val_bpb(log_text: str) -> tuple[float, str]:
    matches = [
        (match.group("value"), match.group(0))
        for match in _FINAL_EXACT_RE.finditer(log_text)
    ]
    if not matches:
        raise ParameterGolfHarnessError(
            "missing final exact val_bpb line",
            code="missing_final_exact_val_bpb",
        )
    raw_value, raw_line = matches[-1]
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ParameterGolfHarnessError(
            f"invalid final exact val_bpb value: {raw_value}",
            code="invalid_final_exact_val_bpb",
        ) from exc
    if not math.isfinite(value):
        raise ParameterGolfHarnessError(
            f"non-finite final exact val_bpb value: {raw_value}",
            code="non_finite_final_exact_val_bpb",
        )
    return value, raw_line


def submission_artifact_size(submission_dir: Path) -> tuple[int, list[dict[str, Any]]]:
    if not submission_dir.exists() or not submission_dir.is_dir():
        raise ParameterGolfHarnessError(
            "missing submission directory",
            code="missing_submission_dir",
        )
    total = 0
    files: list[dict[str, Any]] = []
    for path in sorted(submission_dir.rglob("*")):
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        files.append(
            {"path": path.relative_to(submission_dir.parent).as_posix(), "bytes": size}
        )
    return total, files


def run_parameter_golf_harness(
    records_dir: Path,
    *,
    baseline_mean: float = BASELINE_MEAN_VAL_BPB,
    expected_budget: Mapping[str, Any] = DEFAULT_REFERENCE_BUDGET,
) -> ParameterGolfHarnessResult:
    reasons: list[str] = []
    details: dict[str, Any] = {
        "baseline": {
            "mean_val_bpb": baseline_mean,
            "sample_std_val_bpb": BASELINE_SAMPLE_STD_VAL_BPB,
            "n": BASELINE_N,
        }
    }
    manifest_path = records_dir / "manifest.json"
    manifest: dict[str, Any]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _error_harness_result(
            "manifest_unreadable",
            f"manifest is missing or not parseable: {exc}",
            details,
        )

    try:
        log_text = (records_dir / "train_log.txt").read_text(
            encoding="utf-8",
            errors="replace",
        )
        actual_budget = _derive_actual_budget(manifest, log_text, reasons)
        details["actual_budget"] = actual_budget
        required_files_ok = _required_bundle_paths_ok(records_dir, manifest, reasons)
        budget_comparable = _budget_comparable(
            manifest,
            actual_budget,
            expected_budget,
            reasons,
        )
        val_bpb, raw_metric_line = parse_final_exact_val_bpb(log_text)
        artifact_size, files = submission_artifact_size(records_dir / "submission")
    except OSError as exc:
        details["error_code"] = "train_log_unreadable"
        return _error_harness_result("train_log_unreadable", str(exc), details)
    except ParameterGolfHarnessError as exc:
        details["error_code"] = exc.code
        return _error_harness_result(exc.code, str(exc), details)

    metrics = {
        "val_bpb": val_bpb,
        "metric_source": METRIC_SOURCE,
        "artifact_size_bytes": artifact_size,
    }
    details["metric_source_line"] = raw_metric_line
    details["artifact_files"] = files

    if artifact_size > SUBMISSION_ARTIFACT_CAP_BYTES:
        reasons.append("submission_artifact_over_cap")
    if not files:
        reasons.append("submission_artifact_missing")
    if not any(file["path"] == "submission/train_gpt.py" for file in files):
        reasons.append("submission_train_gpt_missing")
    for path in manifest.get("artifact", {}).get("required_submission_files", []):
        if not (records_dir / str(path)).is_file():
            reasons.append(f"missing_submission_file:{path}")

    invalid = (
        not required_files_ok
        or not budget_comparable
        or artifact_size > SUBMISSION_ARTIFACT_CAP_BYTES
        or not files
        or "submission_train_gpt_missing" in reasons
        or any(reason.startswith("missing_submission_file:") for reason in reasons)
    )
    if invalid:
        verdict = {"status": VERDICT_REJECTED, "reasons": _dedupe(reasons)}
        return ParameterGolfHarnessResult(
            status="invalid",
            metrics=metrics,
            budget_comparable=budget_comparable,
            required_files_ok=required_files_ok,
            verdict=verdict,
            reasons=_dedupe(reasons),
            details=details,
        )

    if val_bpb < baseline_mean:
        verdict_reasons = [
            "single_run_valid_evidence",
            "improved_over_baseline_mean",
            "not_final_significance_verdict",
        ]
        verdict_status = VERDICT_ACCEPTED
    else:
        verdict_reasons = ["single_run_valid_evidence", "not_better_than_baseline_mean"]
        verdict_status = VERDICT_REJECTED
    return ParameterGolfHarnessResult(
        status="valid",
        metrics=metrics,
        budget_comparable=True,
        required_files_ok=True,
        verdict={"status": verdict_status, "reasons": verdict_reasons},
        reasons=verdict_reasons,
        details=details,
    )


def write_attempt_bundle(
    *,
    records_root: Path,
    attempt_id: str,
    task_run_id: str,
    parent_experiment_id: str | None,
    hypothesis: str,
    command: str,
    seed: int,
    timeout_seconds: int,
    train_log: str,
    submission_files: Sequence[Path],
    command_result: HostCommandResult | None,
) -> Path:
    records_root = records_root.resolve()
    records_dir = _safe_records_dir(records_root, attempt_id)
    records_dir.mkdir(parents=True, exist_ok=False)
    submission_dir = records_dir / "submission"
    submission_dir.mkdir()
    for source in submission_files:
        target = submission_dir / source.name
        if source.exists() and source.is_file():
            shutil.copy2(source, target)
    artifact_size, artifact_files = submission_artifact_size(submission_dir)
    manifest = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "task_run_id": task_run_id,
        "parent_experiment_id": parent_experiment_id,
        "upstream_commit": None,
        "budget": {
            key: value
            for key, value in DEFAULT_REFERENCE_BUDGET.items()
            if key != "metric_source"
        },
        "run": {
            "seed": seed,
            "command": command,
            "timeout_seconds": timeout_seconds,
            "execution_path": "host_command",
            "train_seconds": None,
            "eval_seconds": None,
            "exit_code": command_result.exit_code if command_result else None,
            "timed_out": command_result.timed_out if command_result else False,
        },
        "metrics": {
            "val_bpb": None,
            "metric_source": METRIC_SOURCE,
            "artifact_size_bytes": artifact_size,
        },
        "artifact": {
            "required_files": REQUIRED_BUNDLE_FILES,
            "required_dirs": REQUIRED_BUNDLE_DIRS,
            "content_ref": f"records/{attempt_id}",
            "submission_ref": f"records/{attempt_id}/submission",
            "files": artifact_files,
            "required_submission_files": [
                f"submission/{source.name}" for source in submission_files
            ],
        },
        "verdict": {"status": "pending", "reasons": []},
    }
    (records_dir / "train_log.txt").write_text(train_log, encoding="utf-8")
    (records_dir / "submission.json").write_text(
        json.dumps(
            {
                "attempt_id": attempt_id,
                "anchor": ANCHOR_NAME,
                "seed": seed,
                "metric_source": METRIC_SOURCE,
                "artifact_size_bytes": artifact_size,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (records_dir / "README.md").write_text(
        _readme_text(
            attempt_id=attempt_id,
            hypothesis=hypothesis,
            command=command,
            seed=seed,
            timeout_seconds=timeout_seconds,
        ),
        encoding="utf-8",
    )
    (records_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (records_dir / "eval_result.json").write_text("{}\n", encoding="utf-8")
    return records_dir


def finalize_attempt_bundle(
    records_dir: Path, harness: ParameterGolfHarnessResult
) -> None:
    manifest_path = records_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["metrics"].update(harness.metrics)
    manifest["verdict"] = harness.verdict
    if "artifact_files" in harness.details:
        manifest["artifact"]["files"] = harness.details["artifact_files"]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (records_dir / "eval_result.json").write_text(
        json.dumps(harness.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_single_parameter_golf_attempt(
    service: TaskRunService,
    id_or_prefix: str | None,
    cwd: Path,
    options: ParameterGolfAttemptOptions,
    *,
    permission_profile: Mapping[str, Any] | None = None,
) -> ParameterGolfAttemptResult:
    validate_attempt_options(options)
    submission_files = _resolve_submission_files(
        options.workspace, options.submission_files
    )
    workspace_root = str(Path(cwd).resolve())
    service.recover_stale_running(workspace_root)
    record = service._select_task_run_for_step(workspace_root, id_or_prefix)
    if permission_profile is not None:
        record = service.repository.update_task_run_permission_profile(
            record.id,
            permission_profile,
            updated_at=service._now_iso(),
        )
    service._validate_step_ready(record, workspace_root, explicit=bool(id_or_prefix))
    _validate_parent_experiment_id(service, record, options.parent_experiment_id)
    attempt_id = new_db_uuid()
    hypothesis = options.hypothesis_file.read_text(encoding="utf-8").strip()
    runtime_options = TaskRunRuntimeOptions()
    _summary, running_run, step = service._start_step(
        record,
        runtime_options,
        host_context={
            "source": "host",
            "request_id": attempt_id,
            "actor": f"p3:{options.anchor}",
        },
    )
    before_snapshot = capture_workspace_snapshot(
        service,
        running_run,
        step,
        auto_run_id=attempt_id,
    )
    command = _workspace_command(options.workspace, options.command)
    command_result = run_host_command(
        service,
        running_run,
        step,
        auto_run_id=attempt_id,
        phase="trial",
        command=command,
        timeout=options.timeout_seconds,
    )
    records_dir = write_attempt_bundle(
        records_root=options.workspace / "records",
        attempt_id=attempt_id,
        task_run_id=running_run.id,
        parent_experiment_id=options.parent_experiment_id,
        hypothesis=hypothesis,
        command=options.command,
        seed=options.seed,
        timeout_seconds=options.timeout_seconds,
        train_log=command_result.output,
        submission_files=submission_files,
        command_result=command_result,
    )
    if not command_result.succeeded:
        harness = _command_failure_harness(command_result)
    else:
        harness = run_parameter_golf_harness(records_dir)
    finalize_attempt_bundle(records_dir, harness)
    diff_ref = capture_diff_ref(
        service,
        running_run,
        step,
        auto_run_id=attempt_id,
        before_snapshot=before_snapshot,
    )
    diff_ref.update(
        {
            "records_ref": f"records/{attempt_id}",
            "parent_experiment_id": options.parent_experiment_id,
            "workspace_dirty": bool(diff_ref.get("status_after")),
        }
    )
    decision = _compat_decision(harness.verdict["status"])
    preview, _applied = redacted_command_preview(options.command)
    experiment = service.repository.append_experiment(
        task_run_id=running_run.id,
        step_id=step.id,
        experiment_id=attempt_id,
        hypothesis=hypothesis,
        change={
            "anchor": options.anchor,
            "workspace": str(options.workspace),
            "diff_summary": diff_ref,
        },
        command={
            "command": options.command,
            "commandPreview": preview,
            "executionPath": "host_command",
            "timeoutSeconds": options.timeout_seconds,
            "seed": options.seed,
        },
        metrics=harness.metrics,
        result={
            "verdict": harness.verdict,
            "harness": harness.to_dict(),
            "artifact": {
                "content_ref": f"records/{attempt_id}",
                "required_files": REQUIRED_BUNDLE_FILES,
                "required_dirs": REQUIRED_BUNDLE_DIRS,
            },
            "significance": {
                "final": False,
                "reason": SIGNIFICANCE_REASON_SINGLE_RUN_ONLY,
            },
            "reason": ", ".join(harness.verdict.get("reasons", [])),
        },
        decision=decision,
        diff_ref=diff_ref,
        created_at=service._now_iso(),
    )
    _append_attempt_events(
        service, running_run, step, experiment, harness, command_result
    )
    outcome_status = _step_status_for_verdict(harness.verdict["status"])
    task_result = service._finalize_step(
        task_run=running_run,
        step=step,
        previous_status=record.status,
        outcome=TaskRunStepOutcome(
            status=outcome_status,
            assistant_text=f"P3 single attempt {attempt_id}: {harness.verdict['status']}",
            error_message=None
            if outcome_status != "failed"
            else harness.verdict["status"],
            block_reason=None if outcome_status != "blocked" else "experiment_blocked",
            next_action="Review the attempt bundle and decide the next candidate.",
        ),
        runtime_options=runtime_options,
    )
    return ParameterGolfAttemptResult(
        task_result=task_result,
        experiment=experiment,
        records_ref=f"records/{attempt_id}",
        harness=harness,
    )


def _required_bundle_paths_ok(
    records_dir: Path, manifest: Mapping[str, Any], reasons: list[str]
) -> bool:
    ok = True
    required_files = manifest.get("artifact", {}).get(
        "required_files", REQUIRED_BUNDLE_FILES
    )
    required_dirs = manifest.get("artifact", {}).get(
        "required_dirs", REQUIRED_BUNDLE_DIRS
    )
    for name in required_files:
        if not (records_dir / str(name)).is_file():
            ok = False
            reasons.append(f"missing_required_file:{name}")
    for name in required_dirs:
        if not (records_dir / str(name)).is_dir():
            ok = False
            reasons.append(f"missing_required_dir:{name}")
    return ok


def _derive_actual_budget(
    manifest: Mapping[str, Any],
    log_text: str,
    reasons: list[str],
) -> dict[str, Any]:
    command = str(manifest.get("run", {}).get("command") or "")
    command_budget = _command_budget(command, reasons)
    log_budget = _log_budget(log_text, reasons)
    actual: dict[str, Any] = {
        "tier": manifest.get("budget", {}).get("tier"),
        "metric_source": manifest.get("metrics", {}).get("metric_source"),
    }
    actual.update(command_budget)
    actual.update(log_budget)
    _check_budget_source_conflicts(command_budget, log_budget, reasons)
    return actual


def _command_budget(command: str, reasons: list[str]) -> dict[str, Any]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        reasons.append(f"budget_command_parse_error:{exc}")
        return {}
    raw: dict[str, str] = {}
    for token in tokens:
        if "=" not in token or token.startswith("-"):
            continue
        key, value = token.split("=", 1)
        if key in _ENV_BUDGET_KEYS:
            raw[_ENV_BUDGET_KEYS[key]] = value
    budget: dict[str, Any] = {}
    for key, value in raw.items():
        if key in {"vocab_size", "max_wallclock_seconds"}:
            parsed = _parse_positive_int(value)
            if parsed is None:
                reasons.append(f"budget_invalid:{key}")
                continue
            budget[key] = parsed
        else:
            budget[key] = _normalize_budget_value(key, value)
    return budget


def _log_budget(log_text: str, reasons: list[str]) -> dict[str, Any]:
    budget: dict[str, Any] = {}
    tokenizer = _TOKENIZER_RE.search(log_text)
    if tokenizer:
        budget["tokenizer_path"] = _normalize_budget_value(
            "tokenizer_path",
            tokenizer.group("path"),
        )
    train_loader = _TRAIN_LOADER_RE.search(log_text)
    if train_loader:
        budget["train_shards"] = int(train_loader.group("shards"))
    val_loader = _VAL_LOADER_RE.search(log_text)
    if val_loader:
        data_path = _data_path_from_val_pattern(val_loader.group("pattern"))
        if data_path is not None:
            budget["data_path"] = _normalize_budget_value("data_path", data_path)
        else:
            reasons.append("budget_log_unparseable:data_path")
    if not tokenizer:
        reasons.append("budget_log_missing:tokenizer_path")
    if not train_loader:
        reasons.append("budget_log_missing:train_shards")
    if not val_loader:
        reasons.append("budget_log_missing:data_path")
    return budget


def _check_budget_source_conflicts(
    command_budget: Mapping[str, Any],
    log_budget: Mapping[str, Any],
    reasons: list[str],
) -> None:
    for key in sorted(set(command_budget) & set(log_budget)):
        if command_budget[key] != log_budget[key]:
            reasons.append(f"budget_source_conflict:{key}")


def _budget_comparable(
    manifest: Mapping[str, Any],
    actual_budget: Mapping[str, Any],
    expected_budget: Mapping[str, Any],
    reasons: list[str],
) -> bool:
    manifest_budget = manifest.get("budget")
    if not isinstance(manifest_budget, Mapping):
        reasons.append("budget_missing")
        return False
    ok = True
    for key, expected in expected_budget.items():
        actual = actual_budget.get(key)
        if actual is None:
            ok = False
            reasons.append(f"budget_actual_missing:{key}")
            continue
        if actual != expected:
            ok = False
            reasons.append(f"budget_mismatch:{key}")
        if key == "metric_source":
            declared = manifest.get("metrics", {}).get("metric_source")
        else:
            declared = manifest_budget.get(key)
        if declared != expected:
            ok = False
            reasons.append(f"manifest_budget_mismatch:{key}")
    return ok


def _parse_positive_int(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _normalize_budget_value(key: str, value: object) -> object:
    if key == "data_path":
        text = str(value)
        return text if text.endswith("/") else f"{text}/"
    return str(value)


def _data_path_from_val_pattern(pattern: str) -> str | None:
    marker = "/fineweb_val_"
    if marker not in pattern:
        return None
    return pattern.split(marker, 1)[0] + "/"


def _error_harness_result(
    code: str,
    reason: str,
    details: Mapping[str, Any],
) -> ParameterGolfHarnessResult:
    return ParameterGolfHarnessResult(
        status=VERDICT_ERROR,
        metrics={},
        budget_comparable=False,
        required_files_ok=False,
        verdict={"status": VERDICT_ERROR, "reasons": [code]},
        reasons=[reason],
        details=dict(details),
    )


def _command_failure_harness(
    command_result: HostCommandResult,
) -> ParameterGolfHarnessResult:
    if command_result.timed_out:
        code = "run_timed_out"
    elif command_result.policy_effect != "allow":
        code = "run_not_allowed"
    else:
        code = "run_failed"
    return ParameterGolfHarnessResult(
        status=VERDICT_ERROR,
        metrics={},
        budget_comparable=False,
        required_files_ok=False,
        verdict={"status": VERDICT_ERROR, "reasons": [code]},
        reasons=[code],
        details={"command": command_record(command_result)},
    )


def _compat_decision(verdict_status: str) -> str:
    return DECISION_KEEP if verdict_status == VERDICT_ACCEPTED else DECISION_BLOCKED


def _step_status_for_verdict(verdict_status: str) -> str:
    if verdict_status == VERDICT_ACCEPTED:
        return "done"
    if verdict_status == VERDICT_REJECTED:
        return "blocked"
    return "failed"


def _validate_parent_experiment_id(
    service: TaskRunService,
    record: TaskRunRecord,
    parent_experiment_id: str | None,
) -> None:
    if parent_experiment_id is None:
        return
    experiments = service.repository.list_experiments(record.id)
    parent = next(
        (
            experiment
            for experiment in experiments
            if experiment.id == parent_experiment_id
        ),
        None,
    )
    if parent is None:
        raise ValueError(
            "--parent-experiment-id must reference a Parameter Golf attempt in the same TaskRun"
        )
    if not is_parameter_golf_artifact_record(parent):
        raise ValueError(
            "--parent-experiment-id must reference a P3 Parameter Golf attempt"
        )


def _append_attempt_events(
    service: TaskRunService,
    task_run: TaskRunRecord,
    step: TaskStepRecord,
    experiment: TaskExperimentRecord,
    harness: ParameterGolfHarnessResult,
    command_result: HostCommandResult,
) -> None:
    service.repository.append_event(
        task_run_id=task_run.id,
        step_id=step.id,
        event_type="task_experiment_metric_recorded",
        payload={
            "experiment_id": experiment.id,
            "attempt_id": experiment.id,
            "val_bpb": harness.metrics.get("val_bpb"),
            "artifact_size_bytes": harness.metrics.get("artifact_size_bytes"),
            "records_ref": experiment.diff_ref.get("records_ref"),
            "command": command_record(command_result),
        },
        occurred_at=experiment.created_at,
    )
    service.repository.append_event(
        task_run_id=task_run.id,
        step_id=step.id,
        event_type="task_experiment_decided",
        payload={
            "experiment_id": experiment.id,
            "decision": experiment.decision,
            "verdict_status": harness.verdict["status"],
            "reason": ", ".join(harness.verdict.get("reasons", [])),
        },
        occurred_at=experiment.created_at,
    )


def _safe_records_dir(records_root: Path, attempt_id: str) -> Path:
    target = (records_root / attempt_id).resolve()
    root = records_root.resolve()
    if target != root / attempt_id or root not in target.parents:
        raise ValueError(f"unsafe records path for attempt: {attempt_id}")
    return target


def _workspace_command(workspace: Path, command: str) -> str:
    return f"cd {shlex.quote(str(workspace.resolve()))} && {command}"


def _readme_text(
    *,
    attempt_id: str,
    hypothesis: str,
    command: str,
    seed: int,
    timeout_seconds: int,
) -> str:
    return (
        f"# Parameter Golf Attempt {attempt_id}\n\n"
        f"## Hypothesis\n\n{hypothesis}\n\n"
        f"## Run\n\n"
        f"- seed: {seed}\n"
        f"- timeout_seconds: {timeout_seconds}\n"
        f"- command: `{command}`\n\n"
        "## Evidence\n\n"
        "- `manifest.json` is the machine entrypoint.\n"
        "- `eval_result.json` contains the deterministic harness result.\n"
        "- `submission/` is the capped upstream artifact payload.\n"
    )


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
    "ANCHOR_NAME",
    "ParameterGolfAttemptOptions",
    "ParameterGolfAttemptResult",
    "ParameterGolfHarnessError",
    "ParameterGolfHarnessResult",
    "parse_final_exact_val_bpb",
    "run_parameter_golf_harness",
    "run_single_parameter_golf_attempt",
    "submission_artifact_size",
    "validate_attempt_options",
    "write_attempt_bundle",
]
