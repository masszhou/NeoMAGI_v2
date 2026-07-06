"""P3-M6 research workflow persistence.

Truth is the TaskRun event stream (`task_research_*` events in Postgres);
records-side JSON snapshots under ``records/research/<task_run_id>/`` are a
redundant durable projection for audit/reproduction. State is reconstructed
by replaying events, so the workflow survives context compaction, process
restarts, and hand-edited snapshot files (snapshots never override events).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    RESEARCH_EVENT_TYPES,
)
from cli.core.research_workflow_graph import (
    ResearchWorkflowGraph,
    ResearchWorkflowGraphError,
    graph_from_dict,
    graph_to_dict,
)
from cli.core.taskrun_service import TaskRunService
from storage.taskrun_repository import TaskEventRecord


class ResearchWorkflowStoreError(ValueError):
    """Raised when workflow state is missing or an event payload is invalid."""


@dataclass(slots=True)
class ResearchWorkflowState:
    task_run_id: str
    records_root: Path
    graph: ResearchWorkflowGraph
    round_cap: int = DEFAULT_AUDIT_ROUND_CAP
    audits: list[dict[str, Any]] = field(default_factory=list)
    adjudications: list[dict[str, Any]] = field(default_factory=list)
    overrides: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    decision: dict[str, Any] | None = None

    def latest_audit(self) -> dict[str, Any] | None:
        return self.audits[-1] if self.audits else None

    def latest_adjudication_for_round(self, audit_round: int) -> dict[str, Any] | None:
        for record in reversed(self.adjudications):
            if record.get("round") == audit_round:
                return record
        return None

    def latest_proposal_for_node(self, node_id: str) -> dict[str, Any] | None:
        for record in reversed(self.proposals):
            if record.get("node_id") == node_id:
                return record
        return None


def research_records_root(workspace_root: Path, task_run_id: str) -> Path:
    return workspace_root / "records" / "research" / task_run_id


def load_workflow_state(
    service: TaskRunService, task_run_id: str, workspace_root: Path
) -> ResearchWorkflowState | None:
    """Replay `task_research_*` events into current workflow state."""

    events = [
        event
        for event in service.repository.list_events(task_run_id)
        if event.event_type in RESEARCH_EVENT_TYPES
    ]
    if not events:
        return None
    state: ResearchWorkflowState | None = None
    for event in events:
        state = _apply_event(state, event, task_run_id, workspace_root)
    return state


def require_workflow_state(
    service: TaskRunService, task_run_id: str, workspace_root: Path
) -> ResearchWorkflowState:
    state = load_workflow_state(service, task_run_id, workspace_root)
    if state is None:
        raise ResearchWorkflowStoreError(
            "no research workflow exists for this TaskRun; run "
            "`taskrun research init` first"
        )
    return state


def _apply_event(
    state: ResearchWorkflowState | None,
    event: TaskEventRecord,
    task_run_id: str,
    workspace_root: Path,
) -> ResearchWorkflowState:
    payload = dict(event.payload or {})
    if event.event_type == EVENT_RESEARCH_WORKFLOW_CREATED:
        return ResearchWorkflowState(
            task_run_id=task_run_id,
            records_root=research_records_root(workspace_root, task_run_id),
            graph=_graph_from_payload(payload),
            round_cap=int(payload.get("round_cap") or DEFAULT_AUDIT_ROUND_CAP),
        )
    if state is None:
        raise ResearchWorkflowStoreError(
            f"research event {event.event_type} precedes workflow creation"
        )
    if event.event_type in {
        EVENT_RESEARCH_GRAPH_APPLIED,
        EVENT_RESEARCH_NODE_TRANSITION,
        EVENT_RESEARCH_GATE_EVALUATED,
    }:
        state.graph = _graph_from_payload(payload)
    elif event.event_type == EVENT_RESEARCH_AUDIT_RECORDED:
        state.audits.append(payload)
    elif event.event_type == EVENT_RESEARCH_ADJUDICATION_RECORDED:
        state.adjudications.append(payload)
    elif event.event_type == EVENT_RESEARCH_OVERRIDE_RECORDED:
        state.overrides.append(payload)
    elif event.event_type == EVENT_RESEARCH_PROPOSAL_RECORDED:
        state.proposals.append(payload)
    elif event.event_type == EVENT_RESEARCH_DECISION_RECORDED:
        state.decision = payload
    return state


def _graph_from_payload(payload: Mapping[str, Any]) -> ResearchWorkflowGraph:
    raw = payload.get("graph")
    if not isinstance(raw, Mapping):
        raise ResearchWorkflowStoreError("research event payload missing graph")
    try:
        return graph_from_dict(raw)
    except ResearchWorkflowGraphError as exc:
        raise ResearchWorkflowStoreError(f"stored graph invalid: {exc}") from exc


def append_research_event(
    service: TaskRunService,
    state: ResearchWorkflowState,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    include_graph: bool = True,
) -> dict[str, Any]:
    """Append one research event carrying the full post-op graph, then
    refresh the records snapshot projection."""

    body = dict(payload)
    if include_graph:
        body["graph"] = graph_to_dict(state.graph)
    service.repository.append_event(
        task_run_id=state.task_run_id,
        event_type=event_type,
        payload=body,
        occurred_at=service._now_iso(),
    )
    write_snapshot(state)
    return body


def write_snapshot(state: ResearchWorkflowState) -> Path:
    state.records_root.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "task_run_id": state.task_run_id,
        "round_cap": state.round_cap,
        "graph": graph_to_dict(state.graph),
        "audit_rounds": len(state.audits),
        "adjudication_rounds": len(state.adjudications),
        "overrides": state.overrides,
        "proposals_recorded": len(state.proposals),
        "decision": state.decision,
    }
    target = state.records_root / "workflow_graph.json"
    target.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    return target


def write_records_json(state: ResearchWorkflowState, rel_path: str, data: Any) -> Path:
    target = (state.records_root / rel_path).resolve()
    root = state.records_root.resolve()
    if root != target and root not in target.parents:
        raise ResearchWorkflowStoreError(f"unsafe records path: {rel_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return target


__all__ = [
    "ResearchWorkflowState",
    "ResearchWorkflowStoreError",
    "append_research_event",
    "load_workflow_state",
    "require_workflow_state",
    "research_records_root",
    "write_records_json",
    "write_snapshot",
]
