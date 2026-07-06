from __future__ import annotations

import pytest

from cli.core.research_workflow_contract import (
    GATE_OUTCOME_FAIL,
    GATE_OUTCOME_PASS,
    NODE_STATUS_BLOCKED,
    NODE_STATUS_DONE,
    NODE_STATUS_PENDING,
    NODE_STATUS_READY,
)
from cli.core.research_workflow_graph import (
    ResearchWorkflowGraphError,
    apply_graph_ops,
    derived_status,
    graph_from_dict,
    graph_to_dict,
    ready_nodes,
    record_gate_outcome,
    transition_node,
)
from cli.core.research_workflow_service import default_research_graph


def _mini_graph_raw() -> dict:
    return {
        "nodes": [
            {"node_id": "a", "kind": "action"},
            {
                "node_id": "g",
                "kind": "gate",
                "gate_check": "audit_clear",
                "dependencies": ["a"],
            },
            {"node_id": "b", "kind": "action", "dependencies": ["g"]},
        ]
    }


def test_default_template_validates_and_only_first_node_is_ready() -> None:
    graph = default_research_graph()
    assert ready_nodes(graph) == ("read_materials",)
    assert derived_status(graph, "run_experiment_1") == NODE_STATUS_PENDING


def test_self_dependency_cycle_and_unknown_dep_rejected() -> None:
    with pytest.raises(ResearchWorkflowGraphError, match="self-dependency"):
        graph_from_dict(
            {"nodes": [{"node_id": "a", "kind": "action", "dependencies": ["a"]}]}
        )
    with pytest.raises(ResearchWorkflowGraphError, match="cycle"):
        graph_from_dict(
            {
                "nodes": [
                    {"node_id": "a", "kind": "action", "dependencies": ["b"]},
                    {"node_id": "b", "kind": "action", "dependencies": ["a"]},
                ]
            }
        )
    with pytest.raises(ResearchWorkflowGraphError, match="unknown dependency"):
        graph_from_dict(
            {"nodes": [{"node_id": "a", "kind": "action", "dependencies": ["x"]}]}
        )


def test_stored_ready_status_is_rejected_as_self_report() -> None:
    with pytest.raises(ResearchWorkflowGraphError, match="derived"):
        graph_from_dict(
            {"nodes": [{"node_id": "a", "kind": "action", "status": "ready"}]}
        )


def test_transitions_enforce_readiness_and_evidence() -> None:
    graph = graph_from_dict(_mini_graph_raw())
    # b depends on unpassed gate: not claimable.
    with pytest.raises(ResearchWorkflowGraphError, match="requires derived status"):
        transition_node(graph, "b", "claim", claimant="magipi")
    graph = transition_node(graph, "a", "start", claimant="magipi")
    with pytest.raises(ResearchWorkflowGraphError, match="evidence"):
        transition_node(graph, "a", "complete")
    graph = transition_node(graph, "a", "complete", evidence_refs=["ref1"])
    assert graph.node("a").status == NODE_STATUS_DONE
    # Gate not evaluated yet: b still pending, gate itself cannot complete.
    with pytest.raises(ResearchWorkflowGraphError, match="gate evaluation"):
        transition_node(graph, "g", "complete", evidence_refs=["x"])
    assert derived_status(graph, "b") == NODE_STATUS_PENDING


def test_gate_outcome_gates_dependents_and_supports_reeval() -> None:
    graph = graph_from_dict(_mini_graph_raw())
    with pytest.raises(ResearchWorkflowGraphError, match="dependencies"):
        record_gate_outcome(graph, "g", GATE_OUTCOME_PASS, {})
    graph = transition_node(graph, "a", "start", claimant="magipi")
    graph = transition_node(graph, "a", "complete", evidence_refs=["ref"])
    graph = record_gate_outcome(graph, "g", GATE_OUTCOME_FAIL, {"reason": "blockers"})
    assert derived_status(graph, "b") == NODE_STATUS_BLOCKED
    graph = record_gate_outcome(graph, "g", GATE_OUTCOME_PASS, {"reason": "clear"})
    assert derived_status(graph, "b") == NODE_STATUS_READY


def test_apply_ops_add_supersede_and_reject_cycles() -> None:
    graph = graph_from_dict(_mini_graph_raw())
    graph = apply_graph_ops(
        graph,
        [
            {"op": "add_node", "node": {"node_id": "c", "kind": "action"}},
            {"op": "add_dependency", "node_id": "c", "depends_on": "b"},
            {"op": "supersede_node", "node_id": "b", "reason": "early stop"},
        ],
    )
    assert graph.version == 2
    assert graph.node("b").status == "superseded"
    assert derived_status(graph, "c") == NODE_STATUS_BLOCKED
    with pytest.raises(ResearchWorkflowGraphError, match="cycle"):
        apply_graph_ops(
            graph,
            [
                {"op": "add_dependency", "node_id": "a", "depends_on": "c"},
                {"op": "add_dependency", "node_id": "c", "depends_on": "a"},
            ],
        )


def test_remove_dependency_requires_pending_dependent() -> None:
    graph = graph_from_dict(_mini_graph_raw())
    graph = transition_node(graph, "a", "start", claimant="magipi")
    with pytest.raises(ResearchWorkflowGraphError, match="pending"):
        apply_graph_ops(
            graph, [{"op": "remove_dependency", "node_id": "a", "depends_on": "g"}]
        )
    graph = apply_graph_ops(
        graph, [{"op": "remove_dependency", "node_id": "b", "depends_on": "g"}]
    )
    assert graph.node("b").dependencies == ()


def test_round_trip_serialization_preserves_semantics() -> None:
    graph = default_research_graph()
    graph = transition_node(graph, "read_materials", "start", claimant="magipi")
    graph = transition_node(
        graph, "read_materials", "complete", evidence_refs=["notes.md"]
    )
    reloaded = graph_from_dict(graph_to_dict(graph))
    assert graph_to_dict(reloaded)["ready_nodes"] == ["propose_experiment_1"]
    assert reloaded.node("read_materials").evidence_refs == ("notes.md",)
