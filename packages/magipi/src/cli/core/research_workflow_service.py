"""P3-M6 research workflow orchestration helpers.

Ties the pure graph semantics, audit adapter, adjudication rules, and
decision gates to TaskRun truth. These helpers own the non-bypassable state
writes: every mutation appends a `task_research_*` event and refreshes the
records snapshot, and every gate outcome is computed here from durable
state — never accepted from model prose.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from cli.core.parameter_golf_contract import ANCHOR_NAME
from cli.core.research_adjudication import (
    audit_clear_check,
    remaining_blockers,
    validate_adjudication_entries,
    validate_override_request,
)
from cli.core.research_audit_adapter import (
    ResearchAuditOptions,
    run_research_audit,
)
from cli.core.research_decision import (
    evaluate_proposal,
    experiment_evidence_check,
    informed_proposal_valid_check,
    optimization_drive_satisfied,
    validate_decision_request,
)
from cli.core.research_workflow_contract import (
    DEFAULT_AUDIT_ROUND_CAP,
    EVENT_RESEARCH_ADJUDICATION_RECORDED,
    EVENT_RESEARCH_AUDIT_RECORDED,
    EVENT_RESEARCH_DECISION_RECORDED,
    EVENT_RESEARCH_GATE_EVALUATED,
    EVENT_RESEARCH_GRAPH_APPLIED,
    EVENT_RESEARCH_NODE_TRANSITION,
    EVENT_RESEARCH_OVERRIDE_RECORDED,
    EVENT_RESEARCH_PROPOSAL_RECORDED,
    EVENT_RESEARCH_WORKFLOW_CREATED,
    GATE_CHECK_AUDIT_CLEAR,
    GATE_CHECK_EXPERIMENT_EVIDENCE_RECORDED,
    GATE_CHECK_INFORMED_PROPOSAL_VALID,
    INFORMED_ITERATION_GATED_DECISIONS,
    NODE_KIND_ACTION,
    NODE_KIND_GATE,
    NODE_STATUS_CLAIMED,
    NODE_STATUS_DONE,
    NODE_STATUS_READY,
    NODE_STATUS_RUNNING,
)
from cli.core.research_workflow_graph import (
    ResearchWorkflowGraph,
    apply_graph_ops,
    derived_status,
    graph_from_dict,
    graph_to_dict,
    record_gate_outcome,
    transition_node,
)
from cli.core.research_workflow_store import (
    ResearchWorkflowState,
    ResearchWorkflowStoreError,
    append_research_event,
    load_workflow_state,
    research_records_root,
    write_records_json,
)
from cli.core.taskrun_parameter_golf_loop import (
    ParameterGolfLoopOptions,
    ParameterGolfLoopResult,
    run_parameter_golf_attempt_loop,
)
from cli.core.taskrun_service import TaskRunService
from storage.taskrun_repository import TaskExperimentRecord, TaskRunRecord

DEFAULT_CONDUCTOR = "magipi"

# Loop proposal fields forwarded to the parameter golf attempt loop.
_LOOP_PROPOSAL_FIELDS = (
    "hypothesis",
    "base_attempt_id",
    "expected_metric_direction",
    "change_summary",
    "run_command",
    "submission_files",
    "risk_flags",
    "stop_request",
)


def default_research_graph() -> ResearchWorkflowGraph:
    """Default bounded one-cycle research graph with one informed iteration."""

    def action(
        node_id: str, title: str, deps: Sequence[str], executor: str
    ) -> dict[str, Any]:
        return {
            "node_id": node_id,
            "kind": NODE_KIND_ACTION,
            "title": title,
            "dependencies": list(deps),
            "executor_policy": {"executor": executor},
        }

    def gate(
        node_id: str,
        title: str,
        deps: Sequence[str],
        check: str,
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "node_id": node_id,
            "kind": NODE_KIND_GATE,
            "title": title,
            "dependencies": list(deps),
            "gate_check": check,
            "gate_params": dict(params),
        }

    return graph_from_dict(
        {
            "version": 1,
            "nodes": [
                action(
                    "read_materials",
                    "Read runbook, skill, prior findings",
                    (),
                    DEFAULT_CONDUCTOR,
                ),
                action(
                    "propose_experiment_1",
                    "Propose bounded hypothesis 1",
                    ("read_materials",),
                    DEFAULT_CONDUCTOR,
                ),
                action(
                    "request_audit_1",
                    "Request independent read-only audit",
                    ("propose_experiment_1",),
                    "external_readonly_auditor",
                ),
                action(
                    "adjudicate_audit_1",
                    "Adjudicate audit findings",
                    ("request_audit_1",),
                    DEFAULT_CONDUCTOR,
                ),
                gate(
                    "audit_gate",
                    "No auditor P0/P1 blocker remains",
                    ("adjudicate_audit_1",),
                    GATE_CHECK_AUDIT_CLEAR,
                    {},
                ),
                action(
                    "run_experiment_1",
                    "Execute attempt 1 via TaskRun loop",
                    ("audit_gate",),
                    "taskrun_parameter_golf_loop",
                ),
                action(
                    "analyze_evidence_1",
                    "Analyze DB/records truth of attempt 1",
                    ("run_experiment_1",),
                    DEFAULT_CONDUCTOR,
                ),
                action(
                    "propose_experiment_2",
                    "Propose informed hypothesis 2",
                    ("analyze_evidence_1",),
                    DEFAULT_CONDUCTOR,
                ),
                gate(
                    "informed_iteration_gate",
                    "Later proposal structurally consumes prior evidence",
                    ("propose_experiment_2",),
                    GATE_CHECK_INFORMED_PROPOSAL_VALID,
                    {"proposal_node": "run_experiment_2"},
                ),
                action(
                    "run_experiment_2",
                    "Execute attempt 2 via TaskRun loop",
                    ("informed_iteration_gate",),
                    "taskrun_parameter_golf_loop",
                ),
                action(
                    "analyze_evidence_2",
                    "Analyze DB/records truth of attempt 2",
                    ("run_experiment_2",),
                    DEFAULT_CONDUCTOR,
                ),
                gate(
                    "experiment_evidence_gate",
                    "Attempt evidence recorded in DB",
                    ("analyze_evidence_2",),
                    GATE_CHECK_EXPERIMENT_EVIDENCE_RECORDED,
                    {"min_attempts": 2},
                ),
                action(
                    "write_findings",
                    "Write findings with direct references",
                    ("experiment_evidence_gate",),
                    DEFAULT_CONDUCTOR,
                ),
                action(
                    "final_decision",
                    "Record terminal decision",
                    ("write_findings",),
                    DEFAULT_CONDUCTOR,
                ),
            ],
        }
    )


def create_workflow(
    service: TaskRunService,
    task_run: TaskRunRecord,
    *,
    graph_raw: Mapping[str, Any] | None = None,
    round_cap: int = DEFAULT_AUDIT_ROUND_CAP,
) -> ResearchWorkflowState:
    workspace_root = Path(task_run.workspace_root)
    if load_workflow_state(service, task_run.id, workspace_root) is not None:
        raise ResearchWorkflowStoreError(
            "a research workflow already exists for this TaskRun"
        )
    if round_cap < 1:
        raise ResearchWorkflowStoreError("--round-cap must be >= 1")
    graph = graph_from_dict(graph_raw) if graph_raw else default_research_graph()
    state = ResearchWorkflowState(
        task_run_id=task_run.id,
        records_root=research_records_root(workspace_root, task_run.id),
        graph=graph,
        round_cap=round_cap,
    )
    append_research_event(
        service,
        state,
        EVENT_RESEARCH_WORKFLOW_CREATED,
        {"round_cap": round_cap, "anchor": ANCHOR_NAME},
    )
    return state


def apply_ops(
    service: TaskRunService,
    state: ResearchWorkflowState,
    ops: Sequence[Mapping[str, Any]],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    new_graph = apply_graph_ops(state.graph, ops)
    if dry_run:
        return {"dry_run": True, "graph": graph_to_dict(new_graph)}
    state.graph = new_graph
    return append_research_event(
        service,
        state,
        EVENT_RESEARCH_GRAPH_APPLIED,
        {"ops": [dict(op) for op in ops]},
    )


def record_transition(
    service: TaskRunService,
    state: ResearchWorkflowState,
    node_id: str,
    transition: str,
    *,
    claimant: str | None = None,
    evidence_refs: Sequence[str] = (),
    decision: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    state.graph = transition_node(
        state.graph,
        node_id,
        transition,
        claimant=claimant,
        evidence_refs=evidence_refs,
        decision=decision,
        reason=reason,
    )
    return append_research_event(
        service,
        state,
        EVENT_RESEARCH_NODE_TRANSITION,
        {
            "node_id": node_id,
            "transition": transition,
            "claimant": claimant,
            "evidence_refs": list(evidence_refs),
            "decision": decision,
            "reason": reason,
        },
    )


def ensure_node_running(
    service: TaskRunService,
    state: ResearchWorkflowState,
    node_id: str,
    *,
    claimant: str = DEFAULT_CONDUCTOR,
) -> None:
    status = derived_status(state.graph, node_id)
    if status == NODE_STATUS_RUNNING:
        return
    if status not in {NODE_STATUS_READY, NODE_STATUS_CLAIMED}:
        raise ResearchWorkflowStoreError(
            f"node {node_id!r} is {status!r}; it must be ready before work "
            "starts (dependencies and gates are enforced by code)"
        )
    record_transition(service, state, node_id, "start", claimant=claimant)


def evaluate_gate(
    service: TaskRunService,
    state: ResearchWorkflowState,
    node_id: str,
    experiments: Sequence[TaskExperimentRecord],
) -> dict[str, Any]:
    node = state.graph.node(node_id)
    if node.kind != NODE_KIND_GATE:
        raise ResearchWorkflowStoreError(f"node {node_id!r} is not a gate")
    if node.gate_check == GATE_CHECK_AUDIT_CLEAR:
        outcome, evidence = audit_clear_check(state)
    elif node.gate_check == GATE_CHECK_INFORMED_PROPOSAL_VALID:
        proposal_node = str(node.gate_params.get("proposal_node") or node_id)
        outcome, evidence = informed_proposal_valid_check(state, proposal_node)
    elif node.gate_check == GATE_CHECK_EXPERIMENT_EVIDENCE_RECORDED:
        min_attempts = int(node.gate_params.get("min_attempts") or 1)
        outcome, evidence = experiment_evidence_check(
            state, experiments, min_attempts=min_attempts
        )
    else:  # pragma: no cover - graph validation rejects unknown checks
        raise ResearchWorkflowStoreError(
            f"gate {node_id!r} has unregistered check {node.gate_check!r}"
        )
    state.graph = record_gate_outcome(state.graph, node_id, outcome, evidence)
    append_research_event(
        service,
        state,
        EVENT_RESEARCH_GATE_EVALUATED,
        {"node_id": node_id, "outcome": outcome, "evidence": evidence},
    )
    return {"node_id": node_id, "outcome": outcome, "evidence": evidence}


def request_audit(
    service: TaskRunService,
    state: ResearchWorkflowState,
    node_id: str,
    options: ResearchAuditOptions,
    *,
    cwd: Path,
) -> dict[str, Any]:
    node = state.graph.node(node_id)
    if node.kind != NODE_KIND_ACTION:
        raise ResearchWorkflowStoreError("audit must target an action node")
    status = derived_status(state.graph, node_id)
    if status not in {
        NODE_STATUS_READY,
        NODE_STATUS_CLAIMED,
        NODE_STATUS_RUNNING,
        NODE_STATUS_DONE,
    }:
        raise ResearchWorkflowStoreError(
            f"audit node {node_id!r} is {status!r}; its dependencies must be "
            "satisfied before requesting audit"
        )
    if status != NODE_STATUS_DONE:
        ensure_node_running(service, state, node_id)
    result = run_research_audit(state, options, cwd=cwd)
    payload = result.payload()
    payload["node_id"] = node_id
    payload["objective"] = options.objective
    payload["plan_file"] = str(options.plan_file)
    payload["model"] = options.model
    payload["effort"] = options.effort
    state.audits.append(payload)
    append_research_event(
        service,
        state,
        EVENT_RESEARCH_AUDIT_RECORDED,
        payload,
        include_graph=False,
    )
    usable = result.exit_code == 0 and not result.timed_out and not result.parse_errors
    if usable and derived_status(state.graph, node_id) == NODE_STATUS_RUNNING:
        record_transition(
            service,
            state,
            node_id,
            "complete",
            evidence_refs=[str(result.transcript_dir)],
        )
    return payload


def record_adjudication(
    service: TaskRunService,
    state: ResearchWorkflowState,
    node_id: str,
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = validate_adjudication_entries(state, entries)
    audit = state.latest_audit() or {}
    payload = {
        "node_id": node_id,
        "round": audit.get("round"),
        "entries": normalized,
        "audit_transcript_ref": audit.get("transcript_ref"),
    }
    adjudication_ref = write_records_json(
        state,
        f"adjudications/round_{int(audit.get('round') or 0):02d}.json",
        payload,
    )
    payload["adjudication_ref"] = str(adjudication_ref)
    state.adjudications.append(payload)
    append_research_event(
        service,
        state,
        EVENT_RESEARCH_ADJUDICATION_RECORDED,
        payload,
        include_graph=False,
    )
    payload["remaining_blockers"] = remaining_blockers(state)
    if derived_status(state.graph, node_id) in {
        NODE_STATUS_READY,
        NODE_STATUS_CLAIMED,
        NODE_STATUS_RUNNING,
    }:
        ensure_node_running(service, state, node_id)
        record_transition(
            service,
            state,
            node_id,
            "complete",
            evidence_refs=[str(adjudication_ref)],
        )
    return payload


def record_override(
    service: TaskRunService,
    state: ResearchWorkflowState,
    *,
    finding_id: str,
    approved_by: str,
    reason: str,
) -> dict[str, Any]:
    payload = validate_override_request(state, finding_id, approved_by, reason)
    state.overrides.append(payload)
    append_research_event(
        service,
        state,
        EVENT_RESEARCH_OVERRIDE_RECORDED,
        payload,
        include_graph=False,
    )
    return payload


def record_proposal(
    service: TaskRunService,
    state: ResearchWorkflowState,
    node_id: str,
    proposal_raw: Mapping[str, Any],
) -> dict[str, Any]:
    if not state.graph.has_node(node_id):
        raise ResearchWorkflowStoreError(f"unknown workflow node: {node_id}")
    experiments = service.repository.list_experiments(state.task_run_id)
    prior_ids = [record.id for record in experiments]
    record = evaluate_proposal(proposal_raw, prior_ids)
    record["node_id"] = node_id
    sequence = len(state.proposals) + 1
    proposal_ref = write_records_json(
        state, f"proposals/{sequence:02d}_{node_id}.json", record
    )
    record["proposal_ref"] = str(proposal_ref)
    state.proposals.append(record)
    append_research_event(
        service,
        state,
        EVENT_RESEARCH_PROPOSAL_RECORDED,
        record,
        include_graph=False,
    )
    return record


def run_experiment_node(
    service: TaskRunService,
    state: ResearchWorkflowState,
    node_id: str,
    *,
    cwd: Path,
    workspace: Path,
    timeout_seconds: int,
    seed: int,
    final_significance_runs: int = 0,
    permission_profile: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ParameterGolfLoopResult]:
    """Execute one attempt for a ready run node through the TaskRun loop.

    The loop owns experiment truth (task_experiments, records bundles, git
    lineage); this wrapper owns the graph discipline: the node must be ready
    (all gates passed), and completion carries the attempt evidence refs.
    """

    node = state.graph.node(node_id)
    if node.kind != NODE_KIND_ACTION:
        raise ResearchWorkflowStoreError("run-experiment must target an action node")
    proposal_record = state.latest_proposal_for_node(node_id)
    if proposal_record is None:
        raise ResearchWorkflowStoreError(
            f"no recorded proposal for node {node_id!r}; run "
            "`taskrun research propose` first"
        )
    ensure_node_running(service, state, node_id)
    loop_proposal = _loop_proposal_from_record(proposal_record)
    proposal_file = state.records_root / "loop_inputs" / f"{node_id}.json"
    proposal_file.parent.mkdir(parents=True, exist_ok=True)
    proposal_file.write_text(json.dumps([loop_proposal], indent=2) + "\n")
    options = ParameterGolfLoopOptions(
        anchor=ANCHOR_NAME,
        workspace=workspace,
        max_attempts=1,
        timeout_seconds=timeout_seconds,
        seed_start=seed,
        final_significance_runs=final_significance_runs,
        proposal_file=proposal_file,
    )
    result = run_parameter_golf_attempt_loop(
        service,
        state.task_run_id,
        cwd,
        options,
        permission_profile=permission_profile,
    )
    evidence_refs = [
        ref
        for iteration in result.iterations
        for ref in (
            f"task_experiments:{iteration.attempt_id}"
            if iteration.attempt_id
            else None,
            iteration.records_ref,
        )
        if ref
    ]
    attempted = [i for i in result.iterations if i.attempt_id]
    if attempted and evidence_refs:
        record_transition(
            service,
            state,
            node_id,
            "complete",
            evidence_refs=evidence_refs,
            decision=result.stop_reason,
        )
    else:
        reason = (
            ",".join(filter(None, (i.reason for i in result.iterations)))
            or result.stop_reason
        )
        record_transition(service, state, node_id, "fail", reason=reason)
    summary = {
        "node_id": node_id,
        "stop_reason": result.stop_reason,
        "attempts": [
            {
                "attempt_id": i.attempt_id,
                "verdict_status": i.verdict_status,
                "val_bpb": i.val_bpb,
                "artifact_size_bytes": i.artifact_size_bytes,
                "records_ref": i.records_ref,
                "best_delta": i.best_delta,
            }
            for i in result.iterations
        ],
        "evidence_refs": evidence_refs,
    }
    return summary, result


def _loop_proposal_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    proposal = dict(record.get("proposal") or {})
    proposal["hypothesis"] = record.get("hypothesis")
    loop_proposal = {
        key: proposal[key] for key in _LOOP_PROPOSAL_FIELDS if key in proposal
    }
    missing = [
        key
        for key in ("hypothesis", "run_command", "submission_files")
        if not loop_proposal.get(key)
    ]
    if missing:
        raise ResearchWorkflowStoreError(
            "proposal is missing loop execution fields: " + ",".join(missing)
        )
    return loop_proposal


def record_decision(
    service: TaskRunService,
    state: ResearchWorkflowState,
    *,
    decision: str,
    rationale: str,
    evidence_refs: Sequence[str],
    stop_policy_ref: str | None,
    findings_ref: str | None,
    final_node: str = "final_decision",
) -> dict[str, Any]:
    experiments = service.repository.list_experiments(state.task_run_id)
    payload = validate_decision_request(
        state,
        experiments,
        decision=decision,
        rationale=rationale,
        evidence_refs=evidence_refs,
        stop_policy_ref=stop_policy_ref,
    )
    if findings_ref:
        if not Path(findings_ref).is_file():
            raise ResearchWorkflowStoreError(
                f"--findings-ref does not exist: {findings_ref}"
            )
        payload["findings_ref"] = findings_ref
    # Gated decisions must walk the graph to the final node; fix_infra and
    # blocked are escape hatches allowed to terminate a partially-done graph.
    if state.graph.has_node(final_node):
        completable = derived_status(state.graph, final_node) in {
            NODE_STATUS_READY,
            NODE_STATUS_CLAIMED,
            NODE_STATUS_RUNNING,
        }
        if decision in INFORMED_ITERATION_GATED_DECISIONS and not completable:
            raise ResearchWorkflowStoreError(
                f"decision {decision!r} requires node {final_node!r} to be "
                "ready; finish the workflow graph first"
            )
        if completable:
            ensure_node_running(service, state, final_node)
            record_transition(
                service,
                state,
                final_node,
                "complete",
                evidence_refs=evidence_refs,
                decision=decision,
            )
    decision_ref = write_records_json(state, "decision.json", payload)
    payload["decision_ref"] = str(decision_ref)
    state.decision = payload
    append_research_event(
        service,
        state,
        EVENT_RESEARCH_DECISION_RECORDED,
        payload,
        include_graph=False,
    )
    return payload


def workflow_status(
    state: ResearchWorkflowState,
    experiments: Sequence[TaskExperimentRecord],
) -> dict[str, Any]:
    drive_ok, drive_evidence = optimization_drive_satisfied(state, experiments)
    latest_audit = state.latest_audit() or {}
    return {
        "task_run_id": state.task_run_id,
        "records_root": str(state.records_root),
        "round_cap": state.round_cap,
        "graph": graph_to_dict(state.graph),
        "audit": {
            "rounds": len(state.audits),
            "latest_round": latest_audit.get("round"),
            "latest_transcript_ref": latest_audit.get("transcript_ref"),
            "remaining_blockers": remaining_blockers(state),
        },
        "adjudications": len(state.adjudications),
        "overrides": state.overrides,
        "proposals": [
            {
                "node_id": record.get("node_id"),
                "proposal_ref": record.get("proposal_ref"),
                "informed_iteration_valid": record.get("informed_iteration_valid"),
            }
            for record in state.proposals
        ],
        "optimization_drive": {"satisfied": drive_ok, **drive_evidence},
        "decision": state.decision,
        "done": _terminal_summary(state.graph),
    }


def _terminal_summary(graph: ResearchWorkflowGraph) -> dict[str, Any]:
    done = [node.node_id for node in graph.nodes if node.status == NODE_STATUS_DONE]
    return {"done_nodes": done, "total_nodes": len(graph.nodes)}


__all__ = [
    "DEFAULT_CONDUCTOR",
    "apply_ops",
    "create_workflow",
    "default_research_graph",
    "ensure_node_running",
    "evaluate_gate",
    "record_adjudication",
    "record_decision",
    "record_override",
    "record_proposal",
    "record_transition",
    "request_audit",
    "run_experiment_node",
    "workflow_status",
]
