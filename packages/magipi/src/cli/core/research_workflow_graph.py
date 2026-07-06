"""P3-M6 typed directed research workflow state graph.

Pure graph semantics: validation, derived readiness, legal transitions, and
apply operations. Persistence lives in :mod:`research_workflow_store`; gate
outcome computation lives in :mod:`research_workflow_service`.

Dependency direction is **dependent -> prerequisite** (ADR-0027). `ready` and
`blocked` are derived on every load from stored statuses and gate outcomes;
they are never stored, so an agent-written readiness flag cannot become truth.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from cli.core.research_workflow_contract import (
    GATE_CHECKS,
    GATE_OUTCOME_FAIL,
    GATE_OUTCOME_PASS,
    GATE_OUTCOMES,
    NODE_KIND_GATE,
    NODE_KINDS,
    NODE_STATUS_BLOCKED,
    NODE_STATUS_CANCELLED,
    NODE_STATUS_CLAIMED,
    NODE_STATUS_DONE,
    NODE_STATUS_FAILED,
    NODE_STATUS_PENDING,
    NODE_STATUS_READY,
    NODE_STATUS_RUNNING,
    NODE_STATUS_SUPERSEDED,
    STORED_NODE_STATUSES,
)


class ResearchWorkflowGraphError(ValueError):
    """Raised when a graph document, apply op, or transition is invalid."""


@dataclass(frozen=True, slots=True)
class ResearchNode:
    node_id: str
    kind: str
    title: str = ""
    status: str = NODE_STATUS_PENDING
    dependencies: tuple[str, ...] = ()
    gate_check: str | None = None
    gate_params: Mapping[str, Any] = field(default_factory=dict)
    gate_outcome: str | None = None
    gate_evidence: Mapping[str, Any] = field(default_factory=dict)
    executor_policy: Mapping[str, Any] = field(default_factory=dict)
    tool_policy: Mapping[str, Any] = field(default_factory=dict)
    claimant: str | None = None
    required_inputs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    decision: str | None = None
    blocking_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchWorkflowGraph:
    nodes: tuple[ResearchNode, ...]
    version: int = 1

    def node(self, node_id: str) -> ResearchNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise ResearchWorkflowGraphError(f"unknown workflow node: {node_id}")

    def has_node(self, node_id: str) -> bool:
        return any(node.node_id == node_id for node in self.nodes)

    def with_node(self, updated: ResearchNode) -> ResearchWorkflowGraph:
        nodes = tuple(
            updated if node.node_id == updated.node_id else node for node in self.nodes
        )
        return replace(self, nodes=nodes)


def validate_graph(graph: ResearchWorkflowGraph) -> None:
    seen: set[str] = set()
    for node in graph.nodes:
        if not node.node_id or not node.node_id.strip():
            raise ResearchWorkflowGraphError("node_id must be non-empty")
        if node.node_id in seen:
            raise ResearchWorkflowGraphError(f"duplicate node_id: {node.node_id}")
        seen.add(node.node_id)
        _validate_node_shape(node)
    for node in graph.nodes:
        for dep in node.dependencies:
            if dep == node.node_id:
                raise ResearchWorkflowGraphError(
                    f"self-dependency rejected: {node.node_id}"
                )
            if dep not in seen:
                raise ResearchWorkflowGraphError(
                    f"unknown dependency {dep!r} on node {node.node_id!r}"
                )
    _reject_cycles(graph)


def _validate_node_shape(node: ResearchNode) -> None:
    if node.kind not in NODE_KINDS:
        raise ResearchWorkflowGraphError(
            f"node {node.node_id!r}: kind must be one of {sorted(NODE_KINDS)}"
        )
    if node.status not in STORED_NODE_STATUSES:
        raise ResearchWorkflowGraphError(
            f"node {node.node_id!r}: stored status {node.status!r} invalid; "
            "ready/blocked are derived and cannot be stored"
        )
    if node.kind == NODE_KIND_GATE:
        if node.gate_check not in GATE_CHECKS:
            raise ResearchWorkflowGraphError(
                f"gate node {node.node_id!r}: gate_check must be one of "
                f"{sorted(GATE_CHECKS)}"
            )
        if node.gate_outcome is not None and node.gate_outcome not in GATE_OUTCOMES:
            raise ResearchWorkflowGraphError(
                f"gate node {node.node_id!r}: gate_outcome {node.gate_outcome!r} "
                "invalid"
            )
    elif node.gate_check is not None or node.gate_outcome is not None:
        raise ResearchWorkflowGraphError(
            f"action node {node.node_id!r} cannot carry gate fields"
        )


def _reject_cycles(graph: ResearchWorkflowGraph) -> None:
    colors: dict[str, int] = {}

    def visit(node_id: str, stack: tuple[str, ...]) -> None:
        state = colors.get(node_id, 0)
        if state == 1:
            cycle = " -> ".join((*stack, node_id))
            raise ResearchWorkflowGraphError(f"dependency cycle rejected: {cycle}")
        if state == 2:
            return
        colors[node_id] = 1
        for dep in graph.node(node_id).dependencies:
            visit(dep, (*stack, node_id))
        colors[node_id] = 2

    for node in graph.nodes:
        visit(node.node_id, ())


def dependency_satisfied(node: ResearchNode) -> bool:
    """A prerequisite satisfies its dependents when it is done (actions) or
    has a passing code-computed outcome (gates)."""

    if node.kind == NODE_KIND_GATE:
        return (
            node.status == NODE_STATUS_DONE and node.gate_outcome == GATE_OUTCOME_PASS
        )
    return node.status == NODE_STATUS_DONE


def dependency_blocking(node: ResearchNode) -> bool:
    if node.kind == NODE_KIND_GATE and node.gate_outcome == GATE_OUTCOME_FAIL:
        return True
    return node.status in {
        NODE_STATUS_FAILED,
        NODE_STATUS_CANCELLED,
        NODE_STATUS_SUPERSEDED,
    }


def derived_status(graph: ResearchWorkflowGraph, node_id: str) -> str:
    """Effective status: stored status plus derived ready/blocked for
    pending nodes. Never trusts a stored ready/blocked value."""

    node = graph.node(node_id)
    if node.status != NODE_STATUS_PENDING:
        return node.status
    deps = [graph.node(dep) for dep in node.dependencies]
    if any(dependency_blocking(dep) for dep in deps):
        return NODE_STATUS_BLOCKED
    if all(dependency_satisfied(dep) for dep in deps):
        return NODE_STATUS_READY
    return NODE_STATUS_PENDING


def blocking_reason(graph: ResearchWorkflowGraph, node_id: str) -> str | None:
    node = graph.node(node_id)
    if node.status != NODE_STATUS_PENDING:
        return None
    reasons = [
        f"{dep}:{_dep_state(graph.node(dep))}"
        for dep in node.dependencies
        if dependency_blocking(graph.node(dep))
    ]
    return ",".join(reasons) if reasons else None


def _dep_state(node: ResearchNode) -> str:
    if node.kind == NODE_KIND_GATE and node.gate_outcome == GATE_OUTCOME_FAIL:
        return "gate_failed"
    return node.status


def ready_nodes(graph: ResearchWorkflowGraph) -> tuple[str, ...]:
    return tuple(
        node.node_id
        for node in graph.nodes
        if derived_status(graph, node.node_id) == NODE_STATUS_READY
    )


_TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    # transition -> (allowed derived source statuses, target stored status)
    "claim": (frozenset({NODE_STATUS_READY}), NODE_STATUS_CLAIMED),
    "start": (
        frozenset({NODE_STATUS_READY, NODE_STATUS_CLAIMED}),
        NODE_STATUS_RUNNING,
    ),
    "complete": (frozenset({NODE_STATUS_RUNNING}), NODE_STATUS_DONE),
    "fail": (frozenset({NODE_STATUS_RUNNING}), NODE_STATUS_FAILED),
    "cancel": (
        frozenset(
            {
                NODE_STATUS_PENDING,
                NODE_STATUS_READY,
                NODE_STATUS_CLAIMED,
                NODE_STATUS_RUNNING,
                NODE_STATUS_BLOCKED,
            }
        ),
        NODE_STATUS_CANCELLED,
    ),
    "supersede": (
        frozenset(
            {
                NODE_STATUS_PENDING,
                NODE_STATUS_READY,
                NODE_STATUS_CLAIMED,
                NODE_STATUS_RUNNING,
                NODE_STATUS_BLOCKED,
                NODE_STATUS_FAILED,
            }
        ),
        NODE_STATUS_SUPERSEDED,
    ),
}


def transition_node(
    graph: ResearchWorkflowGraph,
    node_id: str,
    transition: str,
    *,
    claimant: str | None = None,
    evidence_refs: Sequence[str] = (),
    decision: str | None = None,
    reason: str | None = None,
) -> ResearchWorkflowGraph:
    """Apply a legal node transition and return the updated graph.

    Enforced here, not by prompt compliance:
    - transitions start from the *derived* status, so a node whose gates or
      dependencies are unsatisfied cannot be claimed/started;
    - `complete` requires evidence refs or a structured decision;
    - gate nodes cannot be completed through this path (only gate evaluation
      moves them, see the store/service layer).
    """

    node = graph.node(node_id)
    try:
        allowed, target = _TRANSITIONS[transition]
    except KeyError:
        raise ResearchWorkflowGraphError(f"unknown transition: {transition}") from None
    if node.kind == NODE_KIND_GATE and transition in {
        "claim",
        "start",
        "complete",
        "fail",
    }:
        raise ResearchWorkflowGraphError(
            f"gate node {node_id!r} state moves only through gate evaluation"
        )
    current = derived_status(graph, node_id)
    if current not in allowed:
        raise ResearchWorkflowGraphError(
            f"transition {transition!r} on node {node_id!r} requires derived "
            f"status in {sorted(allowed)}; current is {current!r}"
        )
    if transition == "complete" and not evidence_refs and decision is None:
        raise ResearchWorkflowGraphError(
            f"node {node_id!r} cannot become done without evidence refs or a "
            "structured decision"
        )
    if transition in {"fail", "supersede"} and not reason:
        raise ResearchWorkflowGraphError(
            f"transition {transition!r} on node {node_id!r} requires a reason"
        )
    updated = replace(
        node,
        status=target,
        claimant=claimant if claimant is not None else node.claimant,
        evidence_refs=(*node.evidence_refs, *tuple(evidence_refs)),
        decision=decision if decision is not None else node.decision,
        blocking_reason=reason if transition in {"fail", "supersede"} else None,
    )
    return graph.with_node(updated)


def record_gate_outcome(
    graph: ResearchWorkflowGraph,
    node_id: str,
    outcome: str,
    evidence: Mapping[str, Any],
) -> ResearchWorkflowGraph:
    """Record a code-computed gate outcome. Pass marks the gate done; fail
    keeps it pending so a later re-evaluation can flip it after remediation."""

    node = graph.node(node_id)
    if node.kind != NODE_KIND_GATE:
        raise ResearchWorkflowGraphError(f"node {node_id!r} is not a gate")
    if outcome not in GATE_OUTCOMES:
        raise ResearchWorkflowGraphError(f"invalid gate outcome: {outcome!r}")
    deps = [graph.node(dep) for dep in node.dependencies]
    unsatisfied = [dep.node_id for dep in deps if not dependency_satisfied(dep)]
    if unsatisfied:
        raise ResearchWorkflowGraphError(
            f"gate {node_id!r} cannot be evaluated before dependencies are "
            f"satisfied: {','.join(unsatisfied)}"
        )
    updated = replace(
        node,
        status=NODE_STATUS_DONE
        if outcome == GATE_OUTCOME_PASS
        else NODE_STATUS_PENDING,
        gate_outcome=outcome,
        gate_evidence=dict(evidence),
    )
    return graph.with_node(updated)


# --------------------------------------------------------------------------- #
# Apply boundary: declarative ops validated against the resulting graph.       #
# --------------------------------------------------------------------------- #


def apply_graph_ops(
    graph: ResearchWorkflowGraph,
    ops: Iterable[Mapping[str, Any]],
) -> ResearchWorkflowGraph:
    """Apply add/remove ops and validate the resulting graph.

    Supported ops:
    - ``{"op": "add_node", "node": {...}}`` (new nodes start pending)
    - ``{"op": "add_dependency", "node_id": ..., "depends_on": ...}``
    - ``{"op": "remove_dependency", "node_id": ..., "depends_on": ...}``
      (only while the dependent is still pending)
    - ``{"op": "supersede_node", "node_id": ..., "reason": ...}``
    """

    result = graph
    for op in ops:
        result = _apply_single_op(result, op)
    result = replace(result, version=graph.version + 1)
    validate_graph(result)
    return result


def _apply_single_op(
    graph: ResearchWorkflowGraph, op: Mapping[str, Any]
) -> ResearchWorkflowGraph:
    kind = op.get("op")
    if kind == "add_node":
        node = node_from_dict(op.get("node") or {})
        if graph.has_node(node.node_id):
            raise ResearchWorkflowGraphError(
                f"add_node: node already exists: {node.node_id}"
            )
        if node.status != NODE_STATUS_PENDING:
            raise ResearchWorkflowGraphError(
                f"add_node: new node {node.node_id!r} must start pending"
            )
        return replace(graph, nodes=(*graph.nodes, node))
    if kind in {"add_dependency", "remove_dependency"}:
        return _apply_dependency_op(graph, op, add=kind == "add_dependency")
    if kind == "supersede_node":
        node_id = str(op.get("node_id") or "")
        reason = str(op.get("reason") or "")
        return transition_node(graph, node_id, "supersede", reason=reason)
    raise ResearchWorkflowGraphError(f"unknown graph op: {kind!r}")


def _apply_dependency_op(
    graph: ResearchWorkflowGraph, op: Mapping[str, Any], *, add: bool
) -> ResearchWorkflowGraph:
    node = graph.node(str(op.get("node_id") or ""))
    dep = str(op.get("depends_on") or "")
    if node.status != NODE_STATUS_PENDING:
        raise ResearchWorkflowGraphError(
            f"dependency edits require pending dependent; {node.node_id!r} is "
            f"{node.status!r}"
        )
    if add:
        if dep in node.dependencies:
            return graph
        updated = replace(node, dependencies=(*node.dependencies, dep))
    else:
        if dep not in node.dependencies:
            raise ResearchWorkflowGraphError(
                f"remove_dependency: {node.node_id!r} does not depend on {dep!r}"
            )
        updated = replace(
            node,
            dependencies=tuple(d for d in node.dependencies if d != dep),
        )
    return graph.with_node(updated)


# --------------------------------------------------------------------------- #
# Serialization                                                                #
# --------------------------------------------------------------------------- #


def node_from_dict(raw: Mapping[str, Any]) -> ResearchNode:
    return ResearchNode(
        node_id=str(raw.get("node_id") or ""),
        kind=str(raw.get("kind") or ""),
        title=str(raw.get("title") or ""),
        status=str(raw.get("status") or NODE_STATUS_PENDING),
        dependencies=tuple(str(dep) for dep in raw.get("dependencies") or ()),
        gate_check=_str_or_none(raw.get("gate_check")),
        gate_params=dict(raw.get("gate_params") or {}),
        gate_outcome=_str_or_none(raw.get("gate_outcome")),
        gate_evidence=dict(raw.get("gate_evidence") or {}),
        executor_policy=dict(raw.get("executor_policy") or {}),
        tool_policy=dict(raw.get("tool_policy") or {}),
        claimant=_str_or_none(raw.get("claimant")),
        required_inputs=tuple(str(x) for x in raw.get("required_inputs") or ()),
        evidence_refs=tuple(str(x) for x in raw.get("evidence_refs") or ()),
        decision=_str_or_none(raw.get("decision")),
        blocking_reason=_str_or_none(raw.get("blocking_reason")),
    )


def graph_from_dict(raw: Mapping[str, Any]) -> ResearchWorkflowGraph:
    graph = ResearchWorkflowGraph(
        nodes=tuple(node_from_dict(item) for item in raw.get("nodes") or ()),
        version=int(raw.get("version") or 1),
    )
    validate_graph(graph)
    return graph


def node_to_dict(graph: ResearchWorkflowGraph, node: ResearchNode) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "kind": node.kind,
        "title": node.title,
        "status": node.status,
        "derived_status": derived_status(graph, node.node_id),
        "dependencies": list(node.dependencies),
        "gate_check": node.gate_check,
        "gate_params": dict(node.gate_params),
        "gate_outcome": node.gate_outcome,
        "gate_evidence": dict(node.gate_evidence),
        "executor_policy": dict(node.executor_policy),
        "tool_policy": dict(node.tool_policy),
        "claimant": node.claimant,
        "required_inputs": list(node.required_inputs),
        "evidence_refs": list(node.evidence_refs),
        "decision": node.decision,
        "blocking_reason": blocking_reason(graph, node.node_id) or node.blocking_reason,
    }


def graph_to_dict(graph: ResearchWorkflowGraph) -> dict[str, Any]:
    return {
        "version": graph.version,
        "dependency_direction": "dependent->prerequisite",
        "nodes": [node_to_dict(graph, node) for node in graph.nodes],
        "ready_nodes": list(ready_nodes(graph)),
    }


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


__all__ = [
    "ResearchNode",
    "ResearchWorkflowGraph",
    "ResearchWorkflowGraphError",
    "apply_graph_ops",
    "blocking_reason",
    "dependency_blocking",
    "dependency_satisfied",
    "derived_status",
    "graph_from_dict",
    "graph_to_dict",
    "node_from_dict",
    "node_to_dict",
    "ready_nodes",
    "record_gate_outcome",
    "transition_node",
]
