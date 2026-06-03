from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import cli.core.taskrun_parameter_golf_loop as loop
from cli.core.parameter_golf_contract import (
    ANCHOR_NAME,
    LOOP_STOP_ACTOR_PROPOSAL_INVALID,
    LOOP_STOP_CONSECUTIVE_INVALID_ATTEMPTS,
    LOOP_STOP_CONSECUTIVE_NO_IMPROVEMENT,
    LOOP_STOP_BUDGET_MISMATCH,
    LOOP_STOP_ARTIFACT_CAP_VIOLATION,
    LOOP_STOP_MAX_ATTEMPTS_REACHED,
    LOOP_STOP_VALIDATION_TOUCH_DETECTED,
    VERDICT_ACCEPTED,
    VERDICT_ERROR,
    VERDICT_REJECTED,
)
from cli.core.taskrun_parameter_golf_attempt import (
    ParameterGolfAttemptOptions,
    ParameterGolfAttemptResult,
    ParameterGolfHarnessResult,
)
from cli.core.taskrun_parameter_golf_loop import (
    ParameterGolfLoopOptions,
    build_actor_context,
    final_significance_from_samples,
    parse_actor_proposal,
    validate_actor_proposal,
    validate_loop_options,
)
from cli.core.taskrun_parameter_golf_trajectory import p3_trajectory_summary
from cli.core.taskrun_service import TaskRunService
from storage.taskrun_repository import TaskExperimentRecord, TaskRunRecord
from test_taskrun_service import _FakeTaskRunRepository, _seed_record, _service


TASK_RUN_ID = "019e2200-0000-7000-8000-000000000001"
STEP_ID = "019e2200-0000-7000-8000-000000000002"


def test_loop_options_require_bounded_attempts_and_actor_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--proposal-file or --actor-command"):
        validate_loop_options(
            ParameterGolfLoopOptions(
                anchor=ANCHOR_NAME,
                workspace=tmp_path,
                max_attempts=1,
            )
        )

    with pytest.raises(ValueError, match="--max-attempts must be >= 1"):
        validate_loop_options(
            ParameterGolfLoopOptions(
                anchor=ANCHOR_NAME,
                workspace=tmp_path,
                max_attempts=0,
                actor_command="printf '{}'",
            )
        )


def test_actor_context_is_bounded_to_trajectory_and_anchor_rules() -> None:
    trajectory = p3_trajectory_summary([_experiment("000010")], task_run_id=TASK_RUN_ID)

    context = build_actor_context(
        task_run=_task_run(),
        trajectory=trajectory,
        iteration=2,
    )

    assert context["anchor"] == ANCHOR_NAME
    assert context["rules"]["metric_direction"] == "lower val_bpb"
    assert context["rules"]["required_submission_file"] == "train_gpt.py"
    assert context["trajectory"]["current_best"]["attempt_id"].endswith("000010")


def test_actor_proposal_accepts_minimal_valid_shape() -> None:
    trajectory = p3_trajectory_summary([_experiment("000010")], task_run_id=TASK_RUN_ID)
    proposal = parse_actor_proposal(
        json.dumps(
            {
                "hypothesis": "reduce validation bpb by simplifying cache use",
                "base_attempt_id": "019e2200-0000-7000-8000-000000000010",
                "expected_metric_direction": "lower val_bpb",
                "change_summary": "change train_gpt.py only",
                "run_command": "MAX_WALLCLOCK_SECONDS=480 VOCAB_SIZE=1024 python train_gpt.py",
                "submission_files": ["train_gpt.py"],
                "risk_flags": [],
            }
        )
    )

    assert validate_actor_proposal(proposal, trajectory=trajectory) == []


def test_actor_proposal_rejects_cross_run_parent_missing_submission_and_budget_change() -> (
    None
):
    trajectory = p3_trajectory_summary([_experiment("000010")], task_run_id=TASK_RUN_ID)
    proposal = parse_actor_proposal(
        {
            "hypothesis": "try bigger vocab",
            "base_attempt_id": "019e2200-0000-7000-8000-000000009999",
            "expected_metric_direction": "lower val_bpb",
            "change_summary": "change budget",
            "run_command": "MAX_WALLCLOCK_SECONDS=900 VOCAB_SIZE=2048 python train_gpt.py",
            "submission_files": ["notes.md"],
        }
    )

    reasons = validate_actor_proposal(proposal, trajectory=trajectory)

    assert "base_attempt_not_in_task_run" in reasons
    assert "missing_submission_train_gpt" in reasons
    assert LOOP_STOP_BUDGET_MISMATCH in reasons


def test_actor_proposal_reports_validation_touch_separately() -> None:
    proposal = parse_actor_proposal(
        {
            "hypothesis": "touch validation loader",
            "expected_metric_direction": "lower val_bpb",
            "change_summary": "bad validation change",
            "run_command": "MAX_WALLCLOCK_SECONDS=480 VOCAB_SIZE=1024 python train_gpt.py",
            "submission_files": ["train_gpt.py"],
            "risk_flags": ["validation_touch"],
        }
    )

    reasons = validate_actor_proposal(proposal, trajectory=p3_trajectory_summary([]))

    assert LOOP_STOP_VALIDATION_TOUCH_DETECTED in reasons
    assert LOOP_STOP_BUDGET_MISMATCH not in reasons


def test_loop_runs_root_and_child_attempts_from_db_trajectory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    service = _service(repo)
    _write_train_file(tmp_path)
    proposal_file = _proposal_file(
        tmp_path,
        [
            _proposal(base_attempt_id=None),
            _proposal(base_attempt_id=None),
        ],
    )
    fake = _FakeAttemptProducer(
        [
            _AttemptSpec(VERDICT_ACCEPTED, 1.55),
            _AttemptSpec(VERDICT_ACCEPTED, 1.54),
        ]
    )
    monkeypatch.setattr(loop, "run_single_parameter_golf_attempt", fake)

    result = loop.run_parameter_golf_attempt_loop(
        service,
        record.id,
        tmp_path,
        ParameterGolfLoopOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            max_attempts=2,
            proposal_file=proposal_file,
        ),
    )

    assert result.stop_reason == LOOP_STOP_MAX_ATTEMPTS_REACHED
    assert len(result.iterations) == 2
    assert fake.parents == [None, result.iterations[0].attempt_id]
    assert result.iterations[1].best_delta == pytest.approx(-0.01)
    assert result.trajectory["tree"]["attempt_count"] == 2
    assert repo.runs[record.id].summary["p3_trajectory"]["tree"]["attempt_count"] == 2
    assert [event.event_type for event in repo.events].count(
        "task_parameter_golf_loop_iteration_completed"
    ) == 2


def test_loop_records_command_seed_for_same_seed_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    service = _service(repo)
    _write_train_file(tmp_path)
    proposal_file = _proposal_file(
        tmp_path,
        [
            _proposal(run_command="SEED=42 MAX_WALLCLOCK_SECONDS=480 VOCAB_SIZE=1024 python train_gpt.py"),
            _proposal(run_command="SEED=42 MAX_WALLCLOCK_SECONDS=480 VOCAB_SIZE=1024 python train_gpt.py"),
        ],
    )
    fake = _FakeAttemptProducer(
        [
            _AttemptSpec(VERDICT_ACCEPTED, 1.596),
            _AttemptSpec(VERDICT_ACCEPTED, 1.590),
        ]
    )
    monkeypatch.setattr(loop, "run_single_parameter_golf_attempt", fake)

    loop.run_parameter_golf_attempt_loop(
        service,
        record.id,
        tmp_path,
        ParameterGolfLoopOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            max_attempts=2,
            proposal_file=proposal_file,
        ),
    )

    assert fake.seeds == [42, 42]


def test_loop_stops_after_consecutive_invalid_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    service = _service(repo)
    _write_train_file(tmp_path)
    proposal_file = _proposal_file(tmp_path, [_proposal(), _proposal()])
    monkeypatch.setattr(
        loop,
        "run_single_parameter_golf_attempt",
        _FakeAttemptProducer(
            [
                _AttemptSpec(VERDICT_ERROR, None),
                _AttemptSpec(VERDICT_ERROR, None),
            ]
        ),
    )

    result = loop.run_parameter_golf_attempt_loop(
        service,
        record.id,
        tmp_path,
        ParameterGolfLoopOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            max_attempts=5,
            invalid_attempt_patience=2,
            proposal_file=proposal_file,
        ),
    )

    assert result.stop_reason == LOOP_STOP_CONSECUTIVE_INVALID_ATTEMPTS
    assert result.exit_code == 1
    assert len(result.iterations) == 2


def test_loop_stops_after_consecutive_no_improvement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    service = _service(repo)
    _write_train_file(tmp_path)
    proposal_file = _proposal_file(tmp_path, [_proposal(), _proposal()])
    monkeypatch.setattr(
        loop,
        "run_single_parameter_golf_attempt",
        _FakeAttemptProducer(
            [
                _AttemptSpec(VERDICT_ACCEPTED, 1.55),
                _AttemptSpec(VERDICT_REJECTED, 1.70),
            ]
        ),
    )

    result = loop.run_parameter_golf_attempt_loop(
        service,
        record.id,
        tmp_path,
        ParameterGolfLoopOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            max_attempts=5,
            no_improvement_patience=1,
            proposal_file=proposal_file,
        ),
    )

    assert result.stop_reason == LOOP_STOP_CONSECUTIVE_NO_IMPROVEMENT
    assert result.iterations[0].stop_candidate is None
    assert result.iterations[1].stop_candidate == LOOP_STOP_CONSECUTIVE_NO_IMPROVEMENT


def test_loop_stops_on_budget_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    service = _service(repo)
    _write_train_file(tmp_path)
    proposal_file = _proposal_file(tmp_path, [_proposal()])
    monkeypatch.setattr(
        loop,
        "run_single_parameter_golf_attempt",
        _FakeAttemptProducer(
            [_AttemptSpec(VERDICT_REJECTED, 1.55, reasons=["budget_mismatch:vocab_size"])]
        ),
    )

    result = loop.run_parameter_golf_attempt_loop(
        service,
        record.id,
        tmp_path,
        ParameterGolfLoopOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            max_attempts=3,
            proposal_file=proposal_file,
        ),
    )

    assert result.stop_reason == LOOP_STOP_BUDGET_MISMATCH
    assert result.exit_code == 1


def test_loop_stops_on_artifact_cap_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    service = _service(repo)
    _write_train_file(tmp_path)
    proposal_file = _proposal_file(tmp_path, [_proposal()])
    monkeypatch.setattr(
        loop,
        "run_single_parameter_golf_attempt",
        _FakeAttemptProducer(
            [_AttemptSpec(VERDICT_REJECTED, 1.55, artifact_size=16_000_001)]
        ),
    )

    result = loop.run_parameter_golf_attempt_loop(
        service,
        record.id,
        tmp_path,
        ParameterGolfLoopOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            max_attempts=3,
            proposal_file=proposal_file,
        ),
    )

    assert result.stop_reason == LOOP_STOP_ARTIFACT_CAP_VIOLATION
    assert result.exit_code == 1


def test_loop_stops_after_invalid_proposal_patience(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    service = _service(repo)
    proposal_file = _proposal_file(
        tmp_path,
        [
            {"hypothesis": "", "submission_files": []},
            {"hypothesis": "", "submission_files": []},
        ],
    )

    result = loop.run_parameter_golf_attempt_loop(
        service,
        record.id,
        tmp_path,
        ParameterGolfLoopOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            max_attempts=3,
            invalid_attempt_patience=2,
            proposal_file=proposal_file,
        ),
    )

    assert result.stop_reason == LOOP_STOP_ACTOR_PROPOSAL_INVALID
    assert len(result.iterations) == 2
    assert all(not iteration.proposal_valid for iteration in result.iterations)


def test_final_significance_fails_closed_for_too_few_samples() -> None:
    payload = final_significance_from_samples([1.55, 1.54])

    assert payload["final"] is False
    assert payload["reason"] == "insufficient_candidate_samples"
    assert payload["sample_size"] == 2
    assert "p_value" in payload
    assert "welch_t" in payload


def test_final_significance_accepts_lower_bpb_significant_samples() -> None:
    payload = final_significance_from_samples([1.54, 1.541, 1.539])

    assert payload["final"] is True
    assert payload["p_value"] < 0.01
    assert payload["welch_t"] < 0
    assert payload["sample_size"] == 3


def test_loop_final_significance_is_explicitly_deferred_without_repeated_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    service = _service(repo)
    _write_train_file(tmp_path)
    proposal_file = _proposal_file(
        tmp_path,
        [_proposal(stop_request="final_success")],
    )
    monkeypatch.setattr(
        loop,
        "run_single_parameter_golf_attempt",
        _FakeAttemptProducer([_AttemptSpec(VERDICT_ACCEPTED, 1.54)]),
    )

    result = loop.run_parameter_golf_attempt_loop(
        service,
        record.id,
        tmp_path,
        ParameterGolfLoopOptions(
            anchor=ANCHOR_NAME,
            workspace=tmp_path,
            max_attempts=1,
            final_significance_runs=3,
            proposal_file=proposal_file,
        ),
    )

    assert result.stop_reason == LOOP_STOP_MAX_ATTEMPTS_REACHED
    assert result.final_significance["final"] is False
    assert result.final_significance["reason"] == "insufficient_candidate_samples"
    experiment = repo.list_experiments(record.id)[0]
    assert experiment.result["significance"]["sample_size"] == 0


def _task_run() -> TaskRunRecord:
    return TaskRunRecord(
        id=TASK_RUN_ID,
        workspace_root="/tmp/work",
        agent_session_id="019e2200-0000-7000-8000-000000000099",
        goal="parameter golf",
        status="pending",
        permission_profile={},
        budget={},
        stop_conditions={},
        summary={},
        created_at="2026-05-30T00:00:00+00:00",
        updated_at="2026-05-30T00:00:00+00:00",
    )


def _experiment(suffix: str) -> TaskExperimentRecord:
    attempt_id = f"019e2200-0000-7000-8000-000000{suffix}"
    return TaskExperimentRecord(
        id=attempt_id,
        task_run_id=TASK_RUN_ID,
        step_id=STEP_ID,
        hypothesis="h",
        change={"anchor": ANCHOR_NAME},
        command={},
        metrics={"val_bpb": 1.55, "artifact_size_bytes": 10},
        result={
            "verdict": {"status": "accepted", "reasons": []},
            "harness": {
                "status": "valid",
                "budget_comparable": True,
                "required_files_ok": True,
            },
            "artifact": {"content_ref": f"records/{attempt_id}"},
            "significance": {"final": False, "reason": "single_run_only"},
        },
        decision="keep",
        diff_ref={"records_ref": f"records/{attempt_id}"},
        created_at="2026-05-30T00:00:00+00:00",
    )


def _write_train_file(tmp_path: Path) -> None:
    (tmp_path / "train_gpt.py").write_text("print('train')\n", encoding="utf-8")


def _proposal(
    *,
    base_attempt_id: str | None = None,
    stop_request: str | None = None,
    run_command: str = "MAX_WALLCLOCK_SECONDS=480 VOCAB_SIZE=1024 python train_gpt.py",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "hypothesis": "try a smaller cache",
        "base_attempt_id": base_attempt_id,
        "expected_metric_direction": "lower val_bpb",
        "change_summary": "edit train_gpt.py only",
        "run_command": run_command,
        "submission_files": ["train_gpt.py"],
        "risk_flags": [],
    }
    if stop_request is not None:
        payload["stop_request"] = stop_request
    return payload


def _proposal_file(tmp_path: Path, proposals: list[dict[str, object]]) -> Path:
    path = tmp_path / "proposals.json"
    path.write_text(json.dumps(proposals), encoding="utf-8")
    return path


class _AttemptSpec:
    def __init__(
        self,
        verdict: str,
        val_bpb: float | None,
        *,
        artifact_size: int = 10,
        reasons: list[str] | None = None,
    ) -> None:
        self.verdict = verdict
        self.val_bpb = val_bpb
        self.artifact_size = artifact_size
        self.reasons = reasons or [verdict]


class _FakeAttemptProducer:
    def __init__(self, specs: list[_AttemptSpec]) -> None:
        self.specs = specs
        self.parents: list[str | None] = []
        self.seeds: list[int] = []

    def __call__(
        self,
        service: TaskRunService,
        id_or_prefix: str | None,
        cwd: Path,
        options: ParameterGolfAttemptOptions,
        *,
        permission_profile: dict[str, Any] | None = None,
    ) -> ParameterGolfAttemptResult:
        del permission_profile
        index = len(self.parents)
        spec = self.specs[index]
        self.parents.append(options.parent_experiment_id)
        self.seeds.append(options.seed)
        task_run = service.repository.get_task_run(id_or_prefix or "") or _task_run()
        attempt_id = f"019e2200-0000-7000-8000-0000000001{index:02d}"
        metrics = (
            {"val_bpb": spec.val_bpb, "artifact_size_bytes": spec.artifact_size}
            if spec.val_bpb is not None
            else {}
        )
        harness = ParameterGolfHarnessResult(
            status="error" if spec.verdict == VERDICT_ERROR else "valid",
            metrics=metrics,
            budget_comparable=spec.verdict != VERDICT_ERROR,
            required_files_ok=spec.verdict != VERDICT_ERROR,
            verdict={"status": spec.verdict, "reasons": spec.reasons},
            reasons=spec.reasons,
            details={},
        )
        experiment = service.repository.append_experiment(
            task_run_id=task_run.id,
            step_id=STEP_ID,
            experiment_id=attempt_id,
            hypothesis=options.hypothesis_file.read_text(encoding="utf-8"),
            change={"anchor": ANCHOR_NAME},
            command={"command": options.command},
            metrics=metrics,
            result={
                "verdict": harness.verdict,
                "harness": harness.to_dict(),
                "artifact": {"content_ref": f"records/{attempt_id}"},
                "significance": {"final": False, "reason": "single_run_only"},
            },
            decision="keep" if spec.verdict == VERDICT_ACCEPTED else "blocked",
            diff_ref={
                "records_ref": f"records/{attempt_id}",
                "parent_experiment_id": options.parent_experiment_id,
            },
            created_at=f"2026-05-30T00:0{index}:00+00:00",
        )
        task_result = service.summary(task_run.id, cwd)
        return ParameterGolfAttemptResult(
            task_result=task_result,
            experiment=experiment,
            records_ref=f"records/{attempt_id}",
            harness=harness,
        )
