"""P3-M6 research workflow integration tests over the fake TaskRun repository.

Covers the code-enforced discipline end to end: graph readiness, audit +
adjudication + P0/P1 blocker gate, human override, informed-iteration
enforcement, governed experiment execution, terminal decision gating, and
event-replay reconstruction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cli.core.research_workflow_service as workflow_service
from cli.core.research_audit_adapter import ResearchAuditOptions
from cli.core.research_workflow_service import (
    create_workflow,
    evaluate_gate,
    record_adjudication,
    record_decision,
    record_override,
    record_proposal,
    record_transition,
    request_audit,
    run_experiment_node,
    workflow_status,
)
from cli.core.research_workflow_store import (
    ResearchWorkflowStoreError,
    load_workflow_state,
)
from cli.core.taskrun_parameter_golf_loop import (
    ParameterGolfLoopIterationResult,
    ParameterGolfLoopResult,
)
from storage.taskrun_repository import TaskExperimentRecord
from test_taskrun_service import _FakeTaskRunRepository, _seed_record, _service

STEP_ID = "019e2200-0000-7000-8000-00000000aaaa"


def _setup(tmp_path: Path):
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    service = _service(repo)
    state = create_workflow(service, record)
    return repo, service, state


def _stub_auditor(tmp_path: Path, findings: list[dict], name: str = "stub") -> str:
    payload = json.dumps({"findings": findings})
    script = tmp_path / f"{name}_auditor.py"
    script.write_text(
        f"import sys\nsys.stdin.read()\nprint('```json\\n{payload}\\n```')\n"
    )
    return f"python3 {script}"


def _plan(tmp_path: Path) -> Path:
    plan = tmp_path / "plan.md"
    plan.write_text("bounded hypothesis plan")
    return plan


def _complete(service, state, node: str, evidence: str) -> None:
    record_transition(service, state, node, "start", claimant="magipi")
    record_transition(service, state, node, "complete", evidence_refs=[evidence])


def _proposal_raw(informed: dict | None = None) -> dict:
    raw = {
        "hypothesis": "lower val_bpb via TIED_EMBED_LR=0.035",
        "expected_metric_direction": "lower val_bpb",
        "change_summary": "change train_gpt.py only",
        "run_command": "torchrun train_gpt.py",
        "submission_files": ["train_gpt.py"],
    }
    if informed is not None:
        raw["informed_iteration"] = informed
    return raw


def _informed_block(prior_ref: str) -> dict:
    return {
        "prior_attempt_ref": prior_ref,
        "observed_signal": "attempt1 val_bpb 1.5948",
        "failure_attribution": "single sample below significance gate",
        "next_hypothesis": "narrow LR to 0.035",
        "expected_effect": "delta >= 0.005",
        "changed_from_prior": "TIED_EMBED_LR 0.04 -> 0.035",
        "stop_policy_ref": "runbook lower_bound 0.005",
    }


def _install_fake_loop(monkeypatch, repo, attempt_id: str, val_bpb: float) -> None:
    def fake_loop(service, task_run_id, cwd, options, *, permission_profile=None):
        record = TaskExperimentRecord(
            id=attempt_id,
            task_run_id=task_run_id,
            step_id=STEP_ID,
            hypothesis="h",
            change={},
            command={},
            metrics={"val_bpb": val_bpb},
            result={},
            decision="keep",
            diff_ref={"records_ref": f"records/{attempt_id}"},
            created_at="2026-06-01T00:00:00+00:00",
        )
        repo.experiments.append(record)
        iteration = ParameterGolfLoopIterationResult(
            index=1,
            attempt_id=attempt_id,
            parent_experiment_id=None,
            verdict_status="accepted",
            val_bpb=val_bpb,
            artifact_size_bytes=100,
            records_ref=f"records/{attempt_id}",
            best_delta=None,
            proposal_valid=True,
        )
        return ParameterGolfLoopResult(
            task_run=repo.get_task_run(task_run_id),
            iterations=(iteration,),
            stop_reason="max_attempts_reached",
            anchor_stop_detail=None,
            trajectory={},
            final_significance=None,
            exit_code=0,
        )

    monkeypatch.setattr(workflow_service, "run_parameter_golf_attempt_loop", fake_loop)


def _advance_to_audit_gate(tmp_path, service, state, findings: list[dict]) -> None:
    _complete(service, state, "read_materials", "notes.md")
    _complete(service, state, "propose_experiment_1", "plan.md")
    record_proposal(service, state, "run_experiment_1", _proposal_raw())
    request_audit(
        service,
        state,
        "request_audit_1",
        ResearchAuditOptions(
            plan_file=_plan(tmp_path),
            auditor_command=_stub_auditor(tmp_path, findings),
            timeout_seconds=30,
        ),
        cwd=tmp_path,
    )
    entries = [
        {
            "finding_id": f["finding_id"],
            "decision": "reject",
            "rationale": "auditor misread the frozen budget",
            "action_ref": "runbook",
        }
        for f in findings
    ]
    record_adjudication(service, state, "adjudicate_audit_1", entries)


def test_workflow_cannot_run_experiment_before_audit_gate(tmp_path) -> None:
    repo, service, state = _setup(tmp_path)
    _complete(service, state, "read_materials", "notes.md")
    _complete(service, state, "propose_experiment_1", "plan.md")
    record_proposal(service, state, "run_experiment_1", _proposal_raw())
    with pytest.raises(ResearchWorkflowStoreError, match="must be ready"):
        run_experiment_node(
            service,
            state,
            "run_experiment_1",
            cwd=tmp_path,
            workspace=tmp_path,
            timeout_seconds=600,
            seed=42,
        )


def test_p0_blocker_blocks_until_override_after_rebuttal(tmp_path) -> None:
    repo, service, state = _setup(tmp_path)
    finding = {"finding_id": "F1", "severity": "P0", "title": "touches val set"}
    _advance_to_audit_gate(tmp_path, service, state, [finding])
    outcome = evaluate_gate(service, state, "audit_gate", [])
    assert outcome["outcome"] == "fail"
    assert outcome["evidence"]["reason"] == "auditor_p0_p1_unresolved"
    with pytest.raises(ResearchWorkflowStoreError, match="must be ready"):
        run_experiment_node(
            service,
            state,
            "run_experiment_1",
            cwd=tmp_path,
            workspace=tmp_path,
            timeout_seconds=600,
            seed=42,
        )
    with pytest.raises(ResearchWorkflowStoreError, match="approved-by"):
        record_override(service, state, finding_id="F1", approved_by=" ", reason="x")
    record_override(
        service,
        state,
        finding_id="F1",
        approved_by="zhiliang",
        reason="frozen budget audit is wrong; accepted risk",
    )
    assert evaluate_gate(service, state, "audit_gate", [])["outcome"] == "pass"


def test_reaudit_round_clears_blocker_without_override(tmp_path) -> None:
    repo, service, state = _setup(tmp_path)
    finding = {"finding_id": "F1", "severity": "P1", "title": "seed unpinned"}
    _advance_to_audit_gate(tmp_path, service, state, [finding])
    assert evaluate_gate(service, state, "audit_gate", [])["outcome"] == "fail"
    request_audit(
        service,
        state,
        "request_audit_1",
        ResearchAuditOptions(
            plan_file=_plan(tmp_path),
            auditor_command=_stub_auditor(tmp_path, [], name="clean"),
            timeout_seconds=30,
        ),
        cwd=tmp_path,
    )
    record_adjudication(service, state, "adjudicate_audit_1", [])
    assert evaluate_gate(service, state, "audit_gate", [])["outcome"] == "pass"


def test_adjudication_must_cover_every_finding(tmp_path) -> None:
    repo, service, state = _setup(tmp_path)
    _complete(service, state, "read_materials", "notes.md")
    _complete(service, state, "propose_experiment_1", "plan.md")
    request_audit(
        service,
        state,
        "request_audit_1",
        ResearchAuditOptions(
            plan_file=_plan(tmp_path),
            auditor_command=_stub_auditor(
                tmp_path,
                [{"finding_id": "F1", "severity": "P2", "title": "note"}],
            ),
            timeout_seconds=30,
        ),
        cwd=tmp_path,
    )
    with pytest.raises(ResearchWorkflowStoreError, match="missing: F1"):
        record_adjudication(service, state, "adjudicate_audit_1", [])


def test_full_happy_path_with_informed_iteration_and_decision(
    tmp_path, monkeypatch
) -> None:
    repo, service, state = _setup(tmp_path)
    _advance_to_audit_gate(tmp_path, service, state, [])
    assert evaluate_gate(service, state, "audit_gate", [])["outcome"] == "pass"

    _install_fake_loop(monkeypatch, repo, "e1" + "0" * 30, 1.5948)
    summary, result = run_experiment_node(
        service,
        state,
        "run_experiment_1",
        cwd=tmp_path,
        workspace=tmp_path,
        timeout_seconds=600,
        seed=42,
    )
    attempt_1 = summary["attempts"][0]["attempt_id"]
    assert state.graph.node("run_experiment_1").status == "done"

    _complete(service, state, "analyze_evidence_1", "trajectory")
    with pytest.raises(ResearchWorkflowStoreError, match="informed-iteration"):
        record_proposal(service, state, "run_experiment_2", _proposal_raw())
    with pytest.raises(ResearchWorkflowStoreError, match="prior_attempt_ref"):
        record_proposal(
            service,
            state,
            "run_experiment_2",
            _proposal_raw(_informed_block("not-a-real-id")),
        )
    record_proposal(
        service,
        state,
        "run_experiment_2",
        _proposal_raw(_informed_block(attempt_1)),
    )
    _complete(service, state, "propose_experiment_2", "proposal2")
    gate = evaluate_gate(service, state, "informed_iteration_gate", [])
    assert gate["outcome"] == "pass"

    _install_fake_loop(monkeypatch, repo, "e2" + "0" * 30, 1.5920)
    run_experiment_node(
        service,
        state,
        "run_experiment_2",
        cwd=tmp_path,
        workspace=tmp_path,
        timeout_seconds=600,
        seed=43,
    )
    _complete(service, state, "analyze_evidence_2", "trajectory2")
    gate = evaluate_gate(service, state, "experiment_evidence_gate", repo.experiments)
    assert gate["outcome"] == "pass"

    findings_file = tmp_path / "findings.md"
    findings_file.write_text("# findings\nrefs...")
    _complete(service, state, "write_findings", str(findings_file))
    with pytest.raises(ResearchWorkflowStoreError, match="stop-policy-ref"):
        record_decision(
            service,
            state,
            decision="stop_negative",
            rationale="below gate",
            evidence_refs=[f"task_experiments:{attempt_1}"],
            stop_policy_ref=None,
            findings_ref=str(findings_file),
        )
    payload = record_decision(
        service,
        state,
        decision="stop_negative",
        rationale="below gate",
        evidence_refs=[f"task_experiments:{attempt_1}"],
        stop_policy_ref="runbook lower_bound 0.005",
        findings_ref=str(findings_file),
    )
    assert payload["optimization_drive"]["informed_proposals"] == 1
    with pytest.raises(ResearchWorkflowStoreError, match="already recorded"):
        record_decision(
            service,
            state,
            decision="continue",
            rationale="again",
            evidence_refs=["x"],
            stop_policy_ref=None,
            findings_ref=None,
        )


def test_decision_gate_requires_two_attempts_and_informed_proposal(
    tmp_path,
) -> None:
    repo, service, state = _setup(tmp_path)
    with pytest.raises(ResearchWorkflowStoreError, match="optimization-drive"):
        record_decision(
            service,
            state,
            decision="continue",
            rationale="r",
            evidence_refs=["x"],
            stop_policy_ref=None,
            findings_ref=None,
        )
    # fix_infra is the metric-invalid escape hatch: allowed without attempts.
    payload = record_decision(
        service,
        state,
        decision="fix_infra",
        rationale="records verifier broken",
        evidence_refs=["records/research"],
        stop_policy_ref=None,
        findings_ref=None,
    )
    assert payload["decision"] == "fix_infra"


def test_state_reconstructs_from_events_and_snapshot_written(
    tmp_path, monkeypatch
) -> None:
    repo, service, state = _setup(tmp_path)
    _advance_to_audit_gate(tmp_path, service, state, [])
    evaluate_gate(service, state, "audit_gate", [])
    _install_fake_loop(monkeypatch, repo, "e1" + "0" * 30, 1.5948)
    run_experiment_node(
        service,
        state,
        "run_experiment_1",
        cwd=tmp_path,
        workspace=tmp_path,
        timeout_seconds=600,
        seed=42,
    )
    reloaded = load_workflow_state(service, state.task_run_id, tmp_path)
    assert reloaded is not None
    assert (
        workflow_status(reloaded, repo.experiments)["graph"]
        == (workflow_status(state, repo.experiments)["graph"])
    )
    assert len(reloaded.audits) == 1
    assert len(reloaded.adjudications) == 1
    snapshot = state.records_root / "workflow_graph.json"
    assert snapshot.is_file()
    assert json.loads(snapshot.read_text())["task_run_id"] == state.task_run_id


def test_background_job_spawn_and_status(tmp_path) -> None:
    import sys

    from cli.core.research_background import (
        background_job_status,
        build_worker_argv,
        spawn_background_job,
    )

    repo, service, state = _setup(tmp_path)
    argv = build_worker_argv(
        ["magipi", "taskrun", "research", "audit-request", "--background"]
    )
    assert argv[:3] == [sys.executable, "-m", "cli"]
    assert "--background" not in argv

    payload = spawn_background_job(
        state,
        kind="audit-request",
        node_id="request_audit_1",
        worker_argv=[sys.executable, "-c", "print('worker done')"],
        cwd=tmp_path,
    )
    job_id = payload["job_id"]
    import time

    for _ in range(100):
        status = background_job_status(state, job_id)
        if not status["running"]:
            break
        time.sleep(0.1)
    assert status["running"] is False
    assert "worker done" in status["log_tail"]
    with pytest.raises(ResearchWorkflowStoreError, match="unknown research job"):
        background_job_status(state, "nope")


def test_second_workflow_init_is_rejected(tmp_path) -> None:
    repo, service, state = _setup(tmp_path)
    record = repo.get_task_run(state.task_run_id)
    with pytest.raises(ResearchWorkflowStoreError, match="already exists"):
        create_workflow(service, record)
