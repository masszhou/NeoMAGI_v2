"""``magipi taskrun research`` subcommands — P3-M6 autonomous research workflow.

Model-facing governed surface over the research workflow helpers. Every
mutation appends `task_research_*` TaskRun events and refreshes the records
snapshot; readiness, gates, audit blockers, informed-iteration evidence, and
terminal decisions are enforced in code, not by prompt compliance.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli.core.research_audit_adapter import (
    DEFAULT_AUDITOR_EFFORT,
    DEFAULT_AUDITOR_MODEL,
    ResearchAuditOptions,
)
from cli.core.research_background import (
    background_job_status,
    build_worker_argv,
    spawn_background_job,
)
from cli.core.research_workflow_contract import (
    DEFAULT_AUDIT_ROUND_CAP,
    DEFAULT_AUDIT_TIMEOUT_SECONDS,
)
from cli.core.research_workflow_service import (
    DEFAULT_CONDUCTOR,
    apply_ops,
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
    require_workflow_state,
)
from cli.core.taskrun_service import TaskRunService
from storage.taskrun_repository import TaskRunRecord


@dataclass(frozen=True, slots=True)
class ResearchCommandResult:
    payload: dict[str, Any]
    exit_code: int = 0


def add_research_command(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    research = sub.add_parser(
        "research",
        help="P3-M6 autonomous research workflow (graph, audit, decision).",
    )
    rsub = research.add_subparsers(
        dest="research_cmd", required=True, metavar="RESEARCH_SUBCOMMAND"
    )
    _add_init(rsub)
    _add_views(rsub)
    _add_graph_apply(rsub)
    _add_transitions(rsub)
    _add_gate_eval(rsub)
    _add_audit(rsub)
    _add_adjudicate(rsub)
    _add_override(rsub)
    _add_propose(rsub)
    _add_run_experiment(rsub)
    _add_decide(rsub)
    _add_job_status(rsub)


def _task_id_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "id", nargs="?", default=None, help="TaskRun id or unique prefix."
    )


def _node_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--node", required=True, help="Workflow node id.")


def _add_init(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    init = sub.add_parser("init", help="Create the research workflow graph.")
    _task_id_arg(init)
    init.add_argument(
        "--round-cap",
        type=int,
        default=DEFAULT_AUDIT_ROUND_CAP,
        help="Maximum external audit rounds.",
    )
    init.add_argument(
        "--graph-file",
        type=Path,
        default=None,
        help="Custom graph JSON; defaults to the bounded research template.",
    )


def _add_views(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    status = sub.add_parser("status", help="Show workflow state and gates.")
    _task_id_arg(status)
    ready = sub.add_parser("ready", help="List nodes with derived status ready.")
    _task_id_arg(ready)


def _add_graph_apply(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    apply_cmd = sub.add_parser(
        "apply", help="Validate and apply graph ops (add nodes/edges, supersede)."
    )
    _task_id_arg(apply_cmd)
    apply_cmd.add_argument("--file", type=Path, required=True, help="Ops JSON file.")
    apply_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview without persisting.",
    )


def _add_transitions(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    claim = sub.add_parser("claim", help="Claim a ready action node.")
    start = sub.add_parser("start-node", help="Start a ready/claimed action node.")
    complete = sub.add_parser(
        "complete", help="Complete a running node with evidence refs."
    )
    fail = sub.add_parser("fail-node", help="Mark a running node failed.")
    for parser in (claim, start, complete, fail):
        _task_id_arg(parser)
        _node_arg(parser)
    for parser in (claim, start):
        parser.add_argument("--claimant", default=DEFAULT_CONDUCTOR)
    complete.add_argument(
        "--evidence-ref",
        action="append",
        default=[],
        help="Durable evidence ref (repeatable).",
    )
    complete.add_argument("--decision", default=None)
    fail.add_argument("--reason", required=True)


def _add_gate_eval(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    gate = sub.add_parser(
        "gate-eval", help="Compute a gate outcome from durable state."
    )
    _task_id_arg(gate)
    _node_arg(gate)


def _add_audit(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    audit = sub.add_parser(
        "audit-request",
        help="Run the external read-only auditor and record the transcript.",
    )
    _task_id_arg(audit)
    _node_arg(audit)
    audit.add_argument("--plan-file", type=Path, required=True)
    audit.add_argument(
        "--context-ref",
        action="append",
        type=Path,
        default=[],
        help="Extra file included in the audit prompt (repeatable).",
    )
    audit.add_argument("--objective", default="")
    audit.add_argument(
        "--auditor-command",
        default=None,
        help="Override auditor CLI (default: claude -p read-only plan mode).",
    )
    audit.add_argument("--model", default=DEFAULT_AUDITOR_MODEL)
    audit.add_argument("--effort", default=DEFAULT_AUDITOR_EFFORT)
    audit.add_argument(
        "--timeout-seconds", type=int, default=DEFAULT_AUDIT_TIMEOUT_SECONDS
    )
    audit.add_argument(
        "--background",
        action="store_true",
        help="Run the audit as a detached worker; poll with job-status.",
    )


def _add_adjudicate(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    adjudicate = sub.add_parser(
        "adjudicate",
        help="Record magipi adjudication covering every latest-round finding.",
    )
    _task_id_arg(adjudicate)
    _node_arg(adjudicate)
    adjudicate.add_argument(
        "--file",
        type=Path,
        required=True,
        help='JSON: {"entries": [{finding_id, decision, rationale, action_ref}]}',
    )


def _add_override(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    override = sub.add_parser(
        "override",
        help="Record explicit human override for a rebutted P0/P1 finding.",
    )
    _task_id_arg(override)
    override.add_argument("--finding-id", required=True)
    override.add_argument(
        "--approved-by",
        required=True,
        help="Human approver identity; magipi must not invoke this itself.",
    )
    override.add_argument("--reason", required=True)


def _add_propose(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    propose = sub.add_parser(
        "propose",
        help="Record a proposal for a run node (informed evidence enforced).",
    )
    _task_id_arg(propose)
    _node_arg(propose)
    propose.add_argument("--file", type=Path, required=True, help="Proposal JSON.")


def _add_run_experiment(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    run = sub.add_parser(
        "run-experiment",
        help="Execute one attempt for a ready run node via the TaskRun loop.",
    )
    _task_id_arg(run)
    _node_arg(run)
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--timeout-seconds", type=int, default=900)
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--final-significance-runs", type=int, default=0)
    run.add_argument(
        "--background",
        action="store_true",
        help="Run the attempt as a detached worker; poll with job-status.",
    )


def _add_job_status(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    job = sub.add_parser("job-status", help="Check a detached background research job.")
    _task_id_arg(job)
    job.add_argument("--job", required=True, help="Job id from --background.")


def _add_decide(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    decide = sub.add_parser(
        "decide", help="Record the terminal evidence-backed decision."
    )
    _task_id_arg(decide)
    decide.add_argument("--decision", required=True)
    decide.add_argument("--rationale", required=True)
    decide.add_argument(
        "--evidence-ref", action="append", default=[], help="Repeatable."
    )
    decide.add_argument("--stop-policy-ref", default=None)
    decide.add_argument("--findings-ref", default=None)


def execute_research_command(
    args: argparse.Namespace, service: TaskRunService, cwd: Path
) -> ResearchCommandResult:
    workspace_root = str(Path(cwd).resolve())
    task_run: TaskRunRecord = service._select_task_run(workspace_root, args.id)
    if args.research_cmd == "init":
        return _run_init(args, service, task_run)
    state = require_workflow_state(service, task_run.id, Path(task_run.workspace_root))
    handler = _HANDLERS[args.research_cmd]
    return handler(args, service, state, cwd)


def _run_init(
    args: argparse.Namespace, service: TaskRunService, task_run: TaskRunRecord
) -> ResearchCommandResult:
    graph_raw = _load_json(args.graph_file) if args.graph_file else None
    state = create_workflow(
        service, task_run, graph_raw=graph_raw, round_cap=args.round_cap
    )
    experiments = service.repository.list_experiments(state.task_run_id)
    return ResearchCommandResult(payload=workflow_status(state, experiments))


def _run_status(args, service, state, cwd) -> ResearchCommandResult:
    experiments = service.repository.list_experiments(state.task_run_id)
    return ResearchCommandResult(payload=workflow_status(state, experiments))


def _run_ready(args, service, state, cwd) -> ResearchCommandResult:
    graph = workflow_status(
        state, service.repository.list_experiments(state.task_run_id)
    )["graph"]
    return ResearchCommandResult(
        payload={
            "ready_nodes": graph["ready_nodes"],
            "nodes": [
                {
                    "node_id": node["node_id"],
                    "kind": node["kind"],
                    "derived_status": node["derived_status"],
                    "blocking_reason": node["blocking_reason"],
                }
                for node in graph["nodes"]
            ],
        }
    )


def _run_apply(args, service, state, cwd) -> ResearchCommandResult:
    ops_doc = _load_json(args.file)
    ops = ops_doc.get("ops") if isinstance(ops_doc, dict) else ops_doc
    if not isinstance(ops, list):
        raise ResearchWorkflowStoreError(
            'ops file must be a JSON list or {"ops": [...]}'
        )
    payload = apply_ops(service, state, ops, dry_run=args.dry_run)
    return ResearchCommandResult(payload=payload)


def _run_claim(args, service, state, cwd) -> ResearchCommandResult:
    payload = record_transition(
        service, state, args.node, "claim", claimant=args.claimant
    )
    return ResearchCommandResult(payload=payload)


def _run_start_node(args, service, state, cwd) -> ResearchCommandResult:
    payload = record_transition(
        service, state, args.node, "start", claimant=args.claimant
    )
    return ResearchCommandResult(payload=payload)


def _run_complete(args, service, state, cwd) -> ResearchCommandResult:
    payload = record_transition(
        service,
        state,
        args.node,
        "complete",
        evidence_refs=args.evidence_ref,
        decision=args.decision,
    )
    return ResearchCommandResult(payload=payload)


def _run_fail_node(args, service, state, cwd) -> ResearchCommandResult:
    payload = record_transition(service, state, args.node, "fail", reason=args.reason)
    return ResearchCommandResult(payload=payload)


def _run_gate_eval(args, service, state, cwd) -> ResearchCommandResult:
    experiments = service.repository.list_experiments(state.task_run_id)
    return ResearchCommandResult(
        payload=evaluate_gate(service, state, args.node, experiments)
    )


def _run_audit_request(args, service, state, cwd) -> ResearchCommandResult:
    if args.background:
        return _spawn_background(args, state, cwd, kind="audit-request")
    options = ResearchAuditOptions(
        plan_file=args.plan_file,
        context_refs=tuple(args.context_ref),
        auditor_command=args.auditor_command,
        model=args.model,
        effort=args.effort,
        timeout_seconds=args.timeout_seconds,
        objective=args.objective,
    )
    payload = request_audit(service, state, args.node, options, cwd=cwd)
    return ResearchCommandResult(payload=payload)


def _run_adjudicate(args, service, state, cwd) -> ResearchCommandResult:
    doc = _load_json(args.file)
    entries = doc.get("entries") if isinstance(doc, dict) else doc
    if not isinstance(entries, list):
        raise ResearchWorkflowStoreError(
            'adjudication file must be a JSON list or {"entries": [...]}'
        )
    payload = record_adjudication(service, state, args.node, entries)
    return ResearchCommandResult(payload=payload)


def _run_override(args, service, state, cwd) -> ResearchCommandResult:
    payload = record_override(
        service,
        state,
        finding_id=args.finding_id,
        approved_by=args.approved_by,
        reason=args.reason,
    )
    return ResearchCommandResult(payload=payload)


def _run_propose(args, service, state, cwd) -> ResearchCommandResult:
    proposal_raw = _load_json(args.file)
    if not isinstance(proposal_raw, dict):
        raise ResearchWorkflowStoreError("proposal file must be a JSON object")
    payload = record_proposal(service, state, args.node, proposal_raw)
    return ResearchCommandResult(payload=payload)


def _run_run_experiment(args, service, state, cwd) -> ResearchCommandResult:
    if args.background:
        return _spawn_background(args, state, cwd, kind="run-experiment")
    summary, result = run_experiment_node(
        service,
        state,
        args.node,
        cwd=cwd,
        workspace=args.workspace,
        timeout_seconds=args.timeout_seconds,
        seed=args.seed,
        final_significance_runs=args.final_significance_runs,
    )
    return ResearchCommandResult(payload=summary, exit_code=result.exit_code)


def _run_decide(args, service, state, cwd) -> ResearchCommandResult:
    payload = record_decision(
        service,
        state,
        decision=args.decision,
        rationale=args.rationale,
        evidence_refs=args.evidence_ref,
        stop_policy_ref=args.stop_policy_ref,
        findings_ref=args.findings_ref,
    )
    return ResearchCommandResult(payload=payload)


def _spawn_background(args, state, cwd, *, kind: str) -> ResearchCommandResult:
    payload = spawn_background_job(
        state,
        kind=kind,
        node_id=args.node,
        worker_argv=build_worker_argv(),
        cwd=cwd,
    )
    return ResearchCommandResult(payload=payload)


def _run_job_status(args, service, state, cwd) -> ResearchCommandResult:
    return ResearchCommandResult(payload=background_job_status(state, args.job))


_HANDLERS = {
    "status": _run_status,
    "ready": _run_ready,
    "apply": _run_apply,
    "claim": _run_claim,
    "start-node": _run_start_node,
    "complete": _run_complete,
    "fail-node": _run_fail_node,
    "gate-eval": _run_gate_eval,
    "audit-request": _run_audit_request,
    "adjudicate": _run_adjudicate,
    "override": _run_override,
    "propose": _run_propose,
    "run-experiment": _run_run_experiment,
    "decide": _run_decide,
    "job-status": _run_job_status,
}


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise ResearchWorkflowStoreError(f"file not found: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ResearchWorkflowStoreError(f"invalid JSON in {path}: {exc}") from exc


def print_research_result(result: ResearchCommandResult) -> None:
    print(json.dumps(result.payload, indent=2, sort_keys=True, default=str))


__all__ = [
    "ResearchCommandResult",
    "add_research_command",
    "execute_research_command",
    "print_research_result",
]
