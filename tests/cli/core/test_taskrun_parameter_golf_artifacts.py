from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from cli.core.taskrun_experiment_summary import (
    current_best_experiment,
    p3_artifact_summary,
)
from cli.core.taskrun_parameter_golf_artifacts import (
    current_best_parameter_golf_artifact,
    is_parameter_golf_artifact_record,
    parameter_golf_artifacts,
    project_parameter_golf_artifact,
    verify_parameter_golf_records,
)
from storage.taskrun_repository import TaskExperimentRecord, TaskRunRecord


TASK_RUN_ID = "019e2200-0000-7000-8000-000000000001"
STEP_ID = "019e2200-0000-7000-8000-000000000002"


def test_projects_valid_accepted_m1_payload_as_best_candidate() -> None:
    artifact = project_parameter_golf_artifact(_experiment("0006", val_bpb=1.55))

    assert artifact.metric == {
        "name": "val_bpb",
        "value": 1.55,
        "direction": "minimize",
    }
    assert artifact.artifact["records_ref"] == f"records/{artifact.attempt_id}"
    assert artifact.verdict["status"] == "accepted"
    assert artifact.significance == {"final": False, "reason": "single_run_only"}
    assert artifact.eligibility == {"best_candidate": True, "reasons": []}


def test_rejected_and_error_artifacts_are_listed_but_not_best() -> None:
    rejected = _experiment(
        "0006", val_bpb=1.7, verdict_status="rejected", decision="blocked"
    )
    error = _experiment(
        "0007",
        metrics={},
        verdict_status="error",
        decision="blocked",
        harness_status="error",
    )

    artifacts = parameter_golf_artifacts([rejected, error])

    assert [artifact.verdict["status"] for artifact in artifacts] == [
        "rejected",
        "error",
    ]
    assert current_best_parameter_golf_artifact([rejected, error]) is None
    assert "verdict_not_accepted" in artifacts[0].eligibility["reasons"]
    assert "missing_val_bpb" in artifacts[1].eligibility["reasons"]
    assert "missing_artifact_size_bytes" in artifacts[1].eligibility["reasons"]


def test_current_best_is_lowest_valid_metric_not_latest_keep() -> None:
    older_better = _experiment(
        "0006", val_bpb=1.54, created_at="2026-05-30T00:00:00+00:00"
    )
    newer_worse = _experiment(
        "0007", val_bpb=1.56, created_at="2026-05-30T00:01:00+00:00"
    )

    best = current_best_parameter_golf_artifact([older_better, newer_worse])
    summary_best = current_best_experiment([older_better, newer_worse])

    assert best is not None
    assert best.attempt_id == older_better.id
    assert summary_best is not None
    assert summary_best["experiment_id"] == older_better.id
    assert summary_best["metric"] == "val_bpb"
    assert summary_best["direction"] == "minimize"


def test_guard_rail_payloads_fail_closed() -> None:
    over_cap = _experiment("0006", artifact_size_bytes=16_000_001)
    budget_mismatch = _experiment("0007", harness={"budget_comparable": False})
    missing_ref = replace(
        _experiment("0008"),
        diff_ref={},
        result={
            **_experiment("0008").result,
            "artifact": {"required_files": [], "required_dirs": []},
        },
    )

    artifacts = parameter_golf_artifacts([over_cap, budget_mismatch, missing_ref])

    assert "artifact_over_cap" in artifacts[0].eligibility["reasons"]
    assert "budget_not_comparable" in artifacts[1].eligibility["reasons"]
    assert "missing_records_ref" in artifacts[2].eligibility["reasons"]
    assert (
        current_best_parameter_golf_artifact([over_cap, budget_mismatch, missing_ref])
        is None
    )


def test_tie_breaker_uses_created_at_then_attempt_id() -> None:
    later = _experiment("0008", val_bpb=1.55, created_at="2026-05-30T00:01:00+00:00")
    earlier_high_id = _experiment(
        "0007", val_bpb=1.55, created_at="2026-05-30T00:00:00+00:00"
    )
    earlier_low_id = _experiment(
        "0006", val_bpb=1.55, created_at="2026-05-30T00:00:00+00:00"
    )

    best = current_best_parameter_golf_artifact(
        [later, earlier_high_id, earlier_low_id]
    )

    assert best is not None
    assert best.attempt_id == earlier_low_id.id


def test_p3_artifact_summary_counts_statuses_and_best() -> None:
    accepted = _experiment("0006", val_bpb=1.55)
    rejected = _experiment(
        "0007", val_bpb=1.7, verdict_status="rejected", decision="blocked"
    )
    error = _experiment("0008", metrics={}, verdict_status="error", decision="blocked")

    summary = p3_artifact_summary([accepted, rejected, error])

    assert summary is not None
    assert summary["count"] == 3
    assert summary["accepted_count"] == 1
    assert summary["rejected_count"] == 1
    assert summary["error_count"] == 1
    assert summary["current_best"]["attempt_id"] == accepted.id
    assert summary["baseline"]["mean_val_bpb"] == 1.599788296


def test_p2_val_bpb_metric_without_artifact_payload_is_not_p3_artifact() -> None:
    experiment = TaskExperimentRecord(
        id="019e2200-0000-7000-8000-000000000006",
        task_run_id=TASK_RUN_ID,
        step_id=STEP_ID,
        hypothesis="generic lm benchmark",
        change={},
        command={},
        metrics={"val_bpb": 1.55},
        result={"primaryMetric": "val_bpb", "direction": "lower"},
        decision="keep",
        diff_ref={},
        created_at="2026-05-30T00:00:00+00:00",
    )

    assert is_parameter_golf_artifact_record(experiment) is False
    assert parameter_golf_artifacts([experiment]) == []


def test_records_consistency_check_matches_m1_manifest(tmp_path: Path) -> None:
    experiment = _experiment("0006", val_bpb=1.55)
    artifact = project_parameter_golf_artifact(experiment)
    records_dir = tmp_path / "records" / experiment.id
    records_dir.mkdir(parents=True)
    (records_dir / "manifest.json").write_text(
        json.dumps(
            {
                "attempt_id": experiment.id,
                "metrics": {"val_bpb": 1.55},
                "artifact": {"content_ref": f"records/{experiment.id}"},
                "verdict": artifact.verdict,
            }
        ),
        encoding="utf-8",
    )
    (records_dir / "eval_result.json").write_text(
        json.dumps({"status": "valid", "verdict": artifact.verdict}),
        encoding="utf-8",
    )

    check = verify_parameter_golf_records(_task_run(tmp_path), artifact)

    assert check.ok is True
    assert check.reasons == []


def test_records_consistency_check_reports_manifest_drift(tmp_path: Path) -> None:
    experiment = _experiment("0006", val_bpb=1.55)
    artifact = project_parameter_golf_artifact(experiment)
    records_dir = tmp_path / "records" / experiment.id
    records_dir.mkdir(parents=True)
    (records_dir / "manifest.json").write_text(
        json.dumps(
            {
                "attempt_id": experiment.id,
                "metrics": {"val_bpb": 1.7},
                "artifact": {"content_ref": f"records/{experiment.id}"},
                "verdict": {"status": "rejected", "reasons": []},
            }
        ),
        encoding="utf-8",
    )
    (records_dir / "eval_result.json").write_text(
        json.dumps({"status": "valid", "verdict": artifact.verdict}),
        encoding="utf-8",
    )

    check = verify_parameter_golf_records(_task_run(tmp_path), artifact)

    assert check.ok is False
    assert "records_metric_mismatch" in check.reasons
    assert "records_verdict_mismatch" in check.reasons


def test_records_consistency_check_reports_missing_manifest(tmp_path: Path) -> None:
    artifact = project_parameter_golf_artifact(_experiment("0006"))

    check = verify_parameter_golf_records(_task_run(tmp_path), artifact)

    assert check.ok is False
    assert "records_manifest_missing" in check.reasons


def _experiment(
    suffix: str,
    *,
    val_bpb: float = 1.55,
    artifact_size_bytes: int = 1234,
    metrics: dict[str, object] | None = None,
    verdict_status: str = "accepted",
    decision: str = "keep",
    harness_status: str = "valid",
    harness: dict[str, object] | None = None,
    created_at: str = "2026-05-30T00:00:00+00:00",
) -> TaskExperimentRecord:
    experiment_id = f"019e2200-0000-7000-8000-00000000{suffix}"
    full_harness = {
        "status": harness_status,
        "budget_comparable": True,
        "required_files_ok": True,
    }
    if harness:
        full_harness.update(harness)
    if metrics is None:
        metrics = {"val_bpb": val_bpb, "artifact_size_bytes": artifact_size_bytes}
    return TaskExperimentRecord(
        id=experiment_id,
        task_run_id=TASK_RUN_ID,
        step_id=STEP_ID,
        hypothesis="try lower bpb",
        change={"anchor": "parameter-golf-mini"},
        command={"commandPreview": "python train_gpt.py"},
        metrics=metrics,
        result={
            "verdict": {"status": verdict_status, "reasons": ["reason"]},
            "harness": full_harness,
            "artifact": {
                "content_ref": f"records/{experiment_id}",
                "required_files": ["README.md", "manifest.json"],
                "required_dirs": ["submission"],
            },
            "significance": {"final": False, "reason": "single_run_only"},
        },
        decision=decision,
        diff_ref={"records_ref": f"records/{experiment_id}"},
        created_at=created_at,
    )


def _task_run(tmp_path: Path) -> TaskRunRecord:
    return TaskRunRecord(
        id=TASK_RUN_ID,
        workspace_root=str(tmp_path),
        agent_session_id="019e2200-0000-7000-8000-000000000009",
        goal="p3",
        status="pending",
        permission_profile={},
        budget={},
        stop_conditions={},
        summary={},
        created_at="2026-05-30T00:00:00+00:00",
        updated_at="2026-05-30T00:00:00+00:00",
    )
