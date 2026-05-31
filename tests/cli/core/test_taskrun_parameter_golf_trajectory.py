from __future__ import annotations

from dataclasses import replace

import cli.core.parameter_golf_contract as contract
import cli.core.taskrun_parameter_golf_artifacts as artifacts
import cli.core.taskrun_parameter_golf_attempt as attempt
from cli.core.parameter_golf_contract import (
    LINEAGE_MISSING_PARENT,
    LINEAGE_PARENT_CYCLE,
    LINEAGE_PARENT_NOT_IN_TASK_RUN,
    LINEAGE_PARENT_SELF_REFERENCE,
    NEXT_ACTION_CONTINUE_FROM_BEST,
    NEXT_ACTION_PROPOSE_NEXT,
    NEXT_ACTION_RETRY_INVALID,
    TREE_DUPLICATE_ATTEMPT_ID_UNEXPECTED,
    TREE_NON_PARAMETER_GOLF_RECORD_SKIPPED,
)
from cli.core.taskrun_parameter_golf_trajectory import (
    p3_trajectory_summary,
    project_parameter_golf_attempt_tree,
)
from storage.taskrun_repository import TaskExperimentRecord
from test_taskrun_service import _FakeTaskRunRepository, _seed_record, _service


TASK_RUN_ID = "019e2200-0000-7000-8000-000000000001"
OTHER_TASK_RUN_ID = "019e2200-0000-7000-8000-000000000099"
STEP_ID = "019e2200-0000-7000-8000-000000000002"


def test_attempt_tree_rebuilds_chain_and_branch() -> None:
    root = _experiment("000010")
    child = _experiment("000011", parent=root.id)
    fork = _experiment("000012", parent=root.id, val_bpb=1.54)

    tree = project_parameter_golf_attempt_tree(
        [root, child, fork], task_run_id=TASK_RUN_ID
    )
    nodes = tree.node_by_id()

    assert tree.root_attempt_ids == [root.id]
    assert nodes[root.id].children == [child.id, fork.id]
    assert nodes[child.id].depth == 1
    assert nodes[fork.id].path == [root.id, fork.id]
    assert tree.diagnostics == []


def test_rejected_attempt_stays_in_tree_but_not_current_best() -> None:
    accepted = _experiment("000010", val_bpb=1.54)
    rejected = _experiment(
        "000011",
        parent=accepted.id,
        val_bpb=1.70,
        verdict_status="rejected",
        decision="blocked",
        created_at="2026-05-30T00:01:00+00:00",
    )

    summary = p3_trajectory_summary([accepted, rejected], task_run_id=TASK_RUN_ID)

    assert summary["current_best"]["attempt_id"] == accepted.id
    assert summary["last_attempt"]["attempt_id"] == rejected.id
    assert summary["next_action"]["kind"] == NEXT_ACTION_CONTINUE_FROM_BEST
    assert summary["next_action"]["base_attempt_id"] == accepted.id
    assert summary["tree"]["attempt_count"] == 2


def test_error_attempt_retries_parent_then_best_then_null() -> None:
    accepted = _experiment("000010", val_bpb=1.54)
    error = _experiment(
        "000011",
        parent=accepted.id,
        metrics={},
        verdict_status="error",
        decision="blocked",
        harness_status="error",
        created_at="2026-05-30T00:01:00+00:00",
    )

    summary = p3_trajectory_summary([accepted, error], task_run_id=TASK_RUN_ID)

    assert summary["current_best"]["attempt_id"] == accepted.id
    assert summary["last_attempt"]["attempt_id"] == error.id
    assert summary["next_action"]["kind"] == NEXT_ACTION_RETRY_INVALID
    assert summary["next_action"]["base_attempt_id"] == accepted.id


def test_accepted_new_best_proposes_next_from_best() -> None:
    older = _experiment("000010", val_bpb=1.56)
    newer = _experiment(
        "000011",
        parent=older.id,
        val_bpb=1.54,
        created_at="2026-05-30T00:01:00+00:00",
    )

    summary = p3_trajectory_summary([older, newer], task_run_id=TASK_RUN_ID)

    assert summary["current_best"]["attempt_id"] == newer.id
    assert summary["next_action"]["kind"] == NEXT_ACTION_PROPOSE_NEXT
    assert summary["next_action"]["base_attempt_id"] == newer.id


def test_no_attempts_returns_deterministic_null_trajectory() -> None:
    summary = p3_trajectory_summary([], task_run_id=TASK_RUN_ID)

    assert summary["current_best"] is None
    assert summary["last_attempt"] is None
    assert summary["next_action"] == {
        "kind": NEXT_ACTION_PROPOSE_NEXT,
        "base_attempt_id": None,
        "reason": "no_attempts",
    }
    assert summary["tree"]["attempt_count"] == 0


def test_default_taskrun_target_keeps_best_and_tree_in_same_run() -> None:
    first_run = _experiment("000010", val_bpb=1.55)
    other_run_better = replace(
        _experiment("000020", val_bpb=1.40),
        task_run_id=OTHER_TASK_RUN_ID,
    )

    summary = p3_trajectory_summary([first_run, other_run_better])

    assert summary["current_best"]["attempt_id"] == first_run.id
    assert summary["tree"]["attempt_count"] == 1
    assert summary["tree"]["nodes"][0]["task_run_id"] == TASK_RUN_ID


def test_invalid_parent_diagnostics_are_node_scoped() -> None:
    missing = _experiment(
        "000010",
        parent="019e2200-0000-7000-8000-000000009999",
    )
    other_parent = replace(
        _experiment("000020"),
        task_run_id=OTHER_TASK_RUN_ID,
    )
    cross_run = _experiment("000011", parent=other_parent.id)
    self_ref = _experiment("000012")
    self_ref = replace(
        self_ref,
        diff_ref={
            **self_ref.diff_ref,
            "parent_experiment_id": self_ref.id,
        },
    )

    tree = project_parameter_golf_attempt_tree(
        [missing, other_parent, cross_run, self_ref],
        task_run_id=TASK_RUN_ID,
    )
    nodes = tree.node_by_id()

    assert LINEAGE_MISSING_PARENT in nodes[missing.id].diagnostics
    assert LINEAGE_PARENT_NOT_IN_TASK_RUN in nodes[cross_run.id].diagnostics
    assert LINEAGE_PARENT_SELF_REFERENCE in nodes[self_ref.id].diagnostics
    assert set(tree.root_attempt_ids) == {missing.id, cross_run.id, self_ref.id}


def test_three_node_cycle_diagnostics_break_at_earliest_node() -> None:
    first = _experiment("000010", created_at="2026-05-30T00:00:00+00:00")
    second = _experiment(
        "000011",
        parent=first.id,
        created_at="2026-05-30T00:01:00+00:00",
    )
    third = _experiment(
        "000012",
        parent=second.id,
        created_at="2026-05-30T00:02:00+00:00",
    )
    first = replace(
        first, diff_ref={**first.diff_ref, "parent_experiment_id": third.id}
    )

    tree = project_parameter_golf_attempt_tree(
        [first, second, third], task_run_id=TASK_RUN_ID
    )
    nodes = tree.node_by_id()

    assert tree.root_attempt_ids == [first.id]
    assert LINEAGE_PARENT_CYCLE in nodes[first.id].diagnostics
    assert LINEAGE_PARENT_CYCLE in nodes[second.id].diagnostics
    assert LINEAGE_PARENT_CYCLE in nodes[third.id].diagnostics


def test_tree_level_diagnostics_report_duplicates_and_non_p3_skips() -> None:
    first = _experiment("000010")
    duplicate = replace(
        _experiment("000011", created_at="2026-05-30T00:01:00+00:00"),
        id=first.id,
    )
    non_p3 = TaskExperimentRecord(
        id="019e2200-0000-7000-8000-000000000090",
        task_run_id=TASK_RUN_ID,
        step_id=STEP_ID,
        hypothesis="generic experiment",
        change={},
        command={},
        metrics={"latency_ms": 100.0},
        result={"primaryMetric": "latency_ms", "direction": "lower"},
        decision="keep",
        diff_ref={},
        created_at="2026-05-30T00:02:00+00:00",
    )

    tree = project_parameter_golf_attempt_tree(
        [first, duplicate, non_p3], task_run_id=TASK_RUN_ID
    )

    assert tree.attempt_count == 1
    assert TREE_DUPLICATE_ATTEMPT_ID_UNEXPECTED in tree.diagnostics
    assert TREE_NON_PARAMETER_GOLF_RECORD_SKIPPED in tree.diagnostics


def test_p3_modules_reuse_contract_constants() -> None:
    assert attempt.VERDICT_ACCEPTED is contract.VERDICT_ACCEPTED
    assert attempt.VERDICT_REJECTED is contract.VERDICT_REJECTED
    assert attempt.VERDICT_ERROR is contract.VERDICT_ERROR
    assert attempt.DECISION_KEEP is contract.DECISION_KEEP
    assert attempt.DECISION_BLOCKED is contract.DECISION_BLOCKED
    assert (
        attempt.SIGNIFICANCE_REASON_SINGLE_RUN_ONLY
        is contract.SIGNIFICANCE_REASON_SINGLE_RUN_ONLY
    )
    assert artifacts.VERDICT_ACCEPTED is contract.VERDICT_ACCEPTED
    assert artifacts.VERDICT_REJECTED is contract.VERDICT_REJECTED
    assert artifacts.VERDICT_ERROR is contract.VERDICT_ERROR


def test_taskrun_summary_rebuild_includes_p3_trajectory(tmp_path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    root = _experiment("000010", val_bpb=1.54)
    rejected = _experiment(
        "000011",
        parent=root.id,
        val_bpb=1.70,
        verdict_status="rejected",
        decision="blocked",
        created_at="2026-05-30T00:01:00+00:00",
    )
    repo.experiments.extend(
        [
            replace(root, task_run_id=record.id),
            replace(rejected, task_run_id=record.id),
        ]
    )

    result = _service(repo).summary(record.id, tmp_path)

    trajectory = result.summary["p3_trajectory"]
    assert trajectory["current_best"]["attempt_id"] == root.id
    assert trajectory["last_attempt"]["attempt_id"] == rejected.id
    assert trajectory["tree"]["attempt_count"] == 2


def _experiment(
    suffix: str,
    *,
    parent: str | None = None,
    val_bpb: float = 1.55,
    artifact_size_bytes: int = 1234,
    metrics: dict[str, object] | None = None,
    verdict_status: str = "accepted",
    decision: str = "keep",
    harness_status: str = "valid",
    created_at: str = "2026-05-30T00:00:00+00:00",
) -> TaskExperimentRecord:
    experiment_id = f"019e2200-0000-7000-8000-000000{suffix}"
    if metrics is None:
        metrics = {"val_bpb": val_bpb, "artifact_size_bytes": artifact_size_bytes}
    return TaskExperimentRecord(
        id=experiment_id,
        task_run_id=TASK_RUN_ID,
        step_id=STEP_ID,
        hypothesis=f"try {suffix}",
        change={"anchor": "parameter-golf-mini"},
        command={"commandPreview": "python train_gpt.py"},
        metrics=metrics,
        result={
            "verdict": {"status": verdict_status, "reasons": ["reason"]},
            "harness": {
                "status": harness_status,
                "budget_comparable": True,
                "required_files_ok": True,
            },
            "artifact": {
                "content_ref": f"records/{experiment_id}",
                "required_files": ["README.md", "manifest.json"],
                "required_dirs": ["submission"],
            },
            "significance": {"final": False, "reason": "single_run_only"},
        },
        decision=decision,
        diff_ref={
            "records_ref": f"records/{experiment_id}",
            "parent_experiment_id": parent,
        },
        created_at=created_at,
    )
