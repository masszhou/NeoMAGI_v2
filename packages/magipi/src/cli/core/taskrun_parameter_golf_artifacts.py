"""P3 Parameter Golf artifact read-model projections."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli.core.parameter_golf_contract import (
    BASELINE_MEAN_VAL_BPB,
    BASELINE_N,
    BASELINE_SAMPLE_STD_VAL_BPB,
    ELIGIBILITY_FINAL_SIGNIFICANCE_PAYLOAD_UNEXPECTED,
    REQUIRED_BUNDLE_DIRS,
    REQUIRED_BUNDLE_FILES,
    SUBMISSION_ARTIFACT_CAP_BYTES,
    VERDICT_ACCEPTED,
    VERDICT_ERROR,
    VERDICT_REJECTED,
)
from storage.taskrun_repository import TaskExperimentRecord, TaskRunRecord

ARTIFACT_METRIC_NAME = "val_bpb"
ARTIFACT_METRIC_DIRECTION = "minimize"


@dataclass(frozen=True, slots=True)
class ParameterGolfArtifact:
    attempt_id: str
    task_run_id: str
    step_id: str
    created_at: str
    hypothesis: str
    metric: dict[str, object]
    artifact: dict[str, object]
    verdict: dict[str, object]
    compat_decision: str
    significance: dict[str, object]
    eligibility: dict[str, object]
    diff_ref: dict[str, object]

    @property
    def val_bpb(self) -> float | None:
        value = self.metric.get("value")
        return float(value) if isinstance(value, int | float) else None

    @property
    def best_candidate(self) -> bool:
        return bool(self.eligibility.get("best_candidate"))

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "task_run_id": self.task_run_id,
            "step_id": self.step_id,
            "created_at": self.created_at,
            "hypothesis": self.hypothesis,
            "metric": dict(self.metric),
            "artifact": dict(self.artifact),
            "verdict": dict(self.verdict),
            "compat_decision": self.compat_decision,
            "significance": dict(self.significance),
            "eligibility": dict(self.eligibility),
            "diff_ref": dict(self.diff_ref),
        }


@dataclass(frozen=True, slots=True)
class RecordsConsistencyCheck:
    attempt_id: str
    ok: bool
    reasons: list[str]
    manifest_path: str | None = None
    eval_result_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "ok": self.ok,
            "reasons": list(self.reasons),
            "manifest_path": self.manifest_path,
            "eval_result_path": self.eval_result_path,
        }


def is_parameter_golf_artifact_record(experiment: TaskExperimentRecord) -> bool:
    result = experiment.result
    return isinstance(result.get("verdict"), Mapping) or isinstance(
        result.get("artifact"), Mapping
    )


def project_parameter_golf_artifact(
    experiment: TaskExperimentRecord,
) -> ParameterGolfArtifact:
    result = experiment.result
    artifact_payload = _mapping(result.get("artifact"))
    verdict = _mapping(result.get("verdict"))
    harness = _mapping(result.get("harness"))
    significance = _mapping(result.get("significance"))
    records_ref = _string(experiment.diff_ref.get("records_ref"))
    content_ref = _string(artifact_payload.get("content_ref"))
    val_bpb, val_reason = _finite_float(experiment.metrics.get(ARTIFACT_METRIC_NAME))
    artifact_size, artifact_size_reason = _int_value(
        experiment.metrics.get("artifact_size_bytes")
    )

    reasons: list[str] = []
    verdict_status = _string(verdict.get("status"))
    if verdict_status != VERDICT_ACCEPTED:
        reasons.append("verdict_not_accepted")
    if val_reason:
        reasons.append(val_reason)
    if artifact_size_reason:
        reasons.append(artifact_size_reason)
    elif artifact_size is not None and artifact_size > SUBMISSION_ARTIFACT_CAP_BYTES:
        reasons.append("artifact_over_cap")
    if harness.get("status") != "valid":
        reasons.append("harness_not_valid")
    if harness.get("budget_comparable") is not True:
        reasons.append("budget_not_comparable")
    if harness.get("required_files_ok") is not True:
        reasons.append("required_files_not_ok")
    if not (records_ref or content_ref):
        reasons.append("missing_records_ref")
    if significance.get("final") is True and not _has_statistical_fields(significance):
        reasons.append(ELIGIBILITY_FINAL_SIGNIFICANCE_PAYLOAD_UNEXPECTED)

    return ParameterGolfArtifact(
        attempt_id=experiment.id,
        task_run_id=experiment.task_run_id,
        step_id=experiment.step_id,
        created_at=experiment.created_at,
        hypothesis=experiment.hypothesis,
        metric={
            "name": ARTIFACT_METRIC_NAME,
            "value": val_bpb,
            "direction": ARTIFACT_METRIC_DIRECTION,
        },
        artifact={
            "size_bytes": artifact_size,
            "cap_bytes": SUBMISSION_ARTIFACT_CAP_BYTES,
            "content_ref": content_ref,
            "records_ref": records_ref,
            "required_files": _string_list(
                artifact_payload.get("required_files"), REQUIRED_BUNDLE_FILES
            ),
            "required_dirs": _string_list(
                artifact_payload.get("required_dirs"), REQUIRED_BUNDLE_DIRS
            ),
        },
        verdict={
            "status": verdict_status,
            "reasons": _string_list(verdict.get("reasons"), []),
        },
        compat_decision=experiment.decision,
        significance={
            "final": bool(significance.get("final")),
            "reason": _string(significance.get("reason")),
        },
        eligibility={"best_candidate": not reasons, "reasons": reasons},
        diff_ref=dict(experiment.diff_ref),
    )


def parameter_golf_artifacts(
    experiments: list[TaskExperimentRecord],
) -> list[ParameterGolfArtifact]:
    return [
        project_parameter_golf_artifact(experiment)
        for experiment in experiments
        if is_parameter_golf_artifact_record(experiment)
    ]


def current_best_parameter_golf_artifact(
    experiments: list[TaskExperimentRecord],
) -> ParameterGolfArtifact | None:
    candidates = [
        artifact
        for artifact in parameter_golf_artifacts(experiments)
        if artifact.best_candidate
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda artifact: (
            artifact.val_bpb if artifact.val_bpb is not None else math.inf,
            artifact.created_at,
            artifact.attempt_id,
        ),
    )


def parameter_golf_artifact_summary(
    experiments: list[TaskExperimentRecord],
) -> dict[str, object] | None:
    artifacts = parameter_golf_artifacts(experiments)
    if not artifacts:
        return None
    best = current_best_parameter_golf_artifact(experiments)
    return {
        "current_best": best.to_dict() if best is not None else None,
        "count": len(artifacts),
        "accepted_count": _count_status(artifacts, VERDICT_ACCEPTED),
        "rejected_count": _count_status(artifacts, VERDICT_REJECTED),
        "error_count": _count_status(artifacts, VERDICT_ERROR),
        "baseline": {
            "metric": ARTIFACT_METRIC_NAME,
            "direction": ARTIFACT_METRIC_DIRECTION,
            "mean_val_bpb": BASELINE_MEAN_VAL_BPB,
            "sample_std_val_bpb": BASELINE_SAMPLE_STD_VAL_BPB,
            "n": BASELINE_N,
        },
    }


def verify_parameter_golf_records(
    task_run: TaskRunRecord,
    artifact: ParameterGolfArtifact,
) -> RecordsConsistencyCheck:
    records_ref = _string(artifact.artifact.get("records_ref")) or _string(
        artifact.artifact.get("content_ref")
    )
    if not records_ref:
        return RecordsConsistencyCheck(
            artifact.attempt_id,
            False,
            ["missing_records_ref"],
        )
    records_dir = _records_dir(Path(task_run.workspace_root), records_ref)
    if records_dir is None:
        return RecordsConsistencyCheck(
            artifact.attempt_id,
            False,
            ["records_ref_outside_workspace"],
        )
    manifest_path = records_dir / "manifest.json"
    eval_result_path = records_dir / "eval_result.json"
    reasons: list[str] = []
    manifest = _load_json(
        manifest_path, reasons, missing_code="records_manifest_missing"
    )
    eval_result = _load_json(
        eval_result_path,
        reasons,
        missing_code="records_eval_result_missing",
    )
    if isinstance(manifest, Mapping):
        _check_manifest(artifact, manifest, reasons)
    if isinstance(eval_result, Mapping):
        _check_eval_result(artifact, eval_result, reasons)
    return RecordsConsistencyCheck(
        attempt_id=artifact.attempt_id,
        ok=not reasons,
        reasons=reasons,
        manifest_path=str(manifest_path),
        eval_result_path=str(eval_result_path),
    )


def _check_manifest(
    artifact: ParameterGolfArtifact,
    manifest: Mapping[str, Any],
    reasons: list[str],
) -> None:
    if manifest.get("attempt_id") != artifact.attempt_id:
        reasons.append("records_attempt_id_mismatch")
    manifest_metric = _mapping(manifest.get("metrics")).get(ARTIFACT_METRIC_NAME)
    if manifest_metric != artifact.metric.get("value"):
        reasons.append("records_metric_mismatch")
    manifest_content_ref = _string(
        _mapping(manifest.get("artifact")).get("content_ref")
    )
    refs = {
        _string(artifact.artifact.get("records_ref")),
        _string(artifact.artifact.get("content_ref")),
    }
    if manifest_content_ref and manifest_content_ref not in refs:
        reasons.append("records_content_ref_mismatch")
    if _mapping(manifest.get("verdict")) != artifact.verdict:
        reasons.append("records_verdict_mismatch")


def _check_eval_result(
    artifact: ParameterGolfArtifact,
    eval_result: Mapping[str, Any],
    reasons: list[str],
) -> None:
    if _mapping(eval_result.get("verdict")) != artifact.verdict:
        reasons.append("records_eval_verdict_mismatch")
    status = _string(eval_result.get("status"))
    if status and status != "valid" and artifact.best_candidate:
        reasons.append("records_eval_status_mismatch")


def _load_json(
    path: Path,
    reasons: list[str],
    *,
    missing_code: str,
) -> object | None:
    if not path.is_file():
        reasons.append(missing_code)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        reasons.append(missing_code.replace("_missing", "_invalid"))
        return None


def _records_dir(workspace_root: Path, records_ref: str) -> Path | None:
    if Path(records_ref).is_absolute():
        return None
    root = workspace_root.resolve()
    target = (root / records_ref).resolve()
    if target == root or root not in target.parents:
        return None
    return target


def _count_status(artifacts: list[ParameterGolfArtifact], status: str) -> int:
    return sum(1 for artifact in artifacts if artifact.verdict.get("status") == status)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_list(value: object, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    return [item for item in value if isinstance(item, str)]


def _finite_float(value: object) -> tuple[float | None, str | None]:
    if value is None:
        return None, "missing_val_bpb"
    try:
        parsed = float(value)
    except TypeError, ValueError:
        return None, "invalid_val_bpb"
    if not math.isfinite(parsed):
        return None, "invalid_val_bpb"
    return parsed, None


def _int_value(value: object) -> tuple[int | None, str | None]:
    if value is None:
        return None, "missing_artifact_size_bytes"
    if isinstance(value, bool):
        return None, "missing_artifact_size_bytes"
    try:
        parsed = int(value)
    except TypeError, ValueError:
        return None, "missing_artifact_size_bytes"
    return parsed, None


def _has_statistical_fields(significance: Mapping[str, Any]) -> bool:
    return any(key in significance for key in ("p_value", "welch_t", "sample_size"))


__all__ = [
    "ARTIFACT_METRIC_DIRECTION",
    "ARTIFACT_METRIC_NAME",
    "ParameterGolfArtifact",
    "RecordsConsistencyCheck",
    "current_best_parameter_golf_artifact",
    "is_parameter_golf_artifact_record",
    "parameter_golf_artifact_summary",
    "parameter_golf_artifacts",
    "project_parameter_golf_artifact",
    "verify_parameter_golf_records",
]
