"""P3 Parameter Golf attempt tree and trajectory read models."""

from __future__ import annotations

from dataclasses import dataclass, field

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
    VERDICT_ACCEPTED,
    VERDICT_ERROR,
    VERDICT_REJECTED,
)
from cli.core.taskrun_parameter_golf_artifacts import (
    ParameterGolfArtifact,
    current_best_parameter_golf_artifact,
    is_parameter_golf_artifact_record,
    project_parameter_golf_artifact,
)
from storage.taskrun_repository import TaskExperimentRecord


@dataclass(slots=True)
class ParameterGolfAttemptTreeNode:
    attempt_id: str
    task_run_id: str
    step_id: str
    parent_experiment_id: str | None
    children: list[str]
    depth: int
    path: list[str]
    created_at: str
    hypothesis: str
    metric: dict[str, object]
    artifact: dict[str, object]
    verdict: dict[str, object]
    significance: dict[str, object]
    lineage: dict[str, object]
    diagnostics: list[str] = field(default_factory=list)
    projection: ParameterGolfArtifact | None = field(default=None, repr=False)

    @property
    def val_bpb(self) -> float | None:
        value = self.metric.get("value")
        return float(value) if isinstance(value, int | float) else None

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "task_run_id": self.task_run_id,
            "step_id": self.step_id,
            "parent_experiment_id": self.parent_experiment_id,
            "children": list(self.children),
            "depth": self.depth,
            "path": list(self.path),
            "created_at": self.created_at,
            "hypothesis": self.hypothesis,
            "metric": dict(self.metric),
            "artifact": dict(self.artifact),
            "verdict": dict(self.verdict),
            "significance": dict(self.significance),
            "lineage": dict(self.lineage),
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class ParameterGolfAttemptTree:
    nodes: list[ParameterGolfAttemptTreeNode]
    root_attempt_ids: list[str]
    diagnostics: list[str]

    @property
    def attempt_count(self) -> int:
        return len(self.nodes)

    def node_by_id(self) -> dict[str, ParameterGolfAttemptTreeNode]:
        return {node.attempt_id: node for node in self.nodes}

    def to_dict(self) -> dict[str, object]:
        return {
            "root_attempt_ids": list(self.root_attempt_ids),
            "attempt_count": self.attempt_count,
            "diagnostics": list(self.diagnostics),
            "nodes": [node.to_dict() for node in self.nodes],
        }


def project_parameter_golf_attempt_tree(
    experiments: list[TaskExperimentRecord],
    *,
    task_run_id: str | None = None,
) -> ParameterGolfAttemptTree:
    target_task_run_id = task_run_id or _first_p3_task_run_id(experiments)
    tree_diagnostics: list[str] = []
    nodes_by_id: dict[str, ParameterGolfAttemptTreeNode] = {}
    all_p3_task_run_ids_by_attempt: dict[str, str] = {}

    for experiment in experiments:
        if not is_parameter_golf_artifact_record(experiment):
            _append_once(tree_diagnostics, TREE_NON_PARAMETER_GOLF_RECORD_SKIPPED)
            continue
        all_p3_task_run_ids_by_attempt.setdefault(experiment.id, experiment.task_run_id)
        if (
            target_task_run_id is not None
            and experiment.task_run_id != target_task_run_id
        ):
            continue
        if experiment.id in nodes_by_id:
            _append_once(tree_diagnostics, TREE_DUPLICATE_ATTEMPT_ID_UNEXPECTED)
            continue
        artifact = project_parameter_golf_artifact(experiment)
        parent_id = _string(experiment.diff_ref.get("parent_experiment_id"))
        nodes_by_id[experiment.id] = ParameterGolfAttemptTreeNode(
            attempt_id=artifact.attempt_id,
            task_run_id=artifact.task_run_id,
            step_id=artifact.step_id,
            parent_experiment_id=parent_id,
            children=[],
            depth=0,
            path=[],
            created_at=artifact.created_at,
            hypothesis=artifact.hypothesis,
            metric=dict(artifact.metric),
            artifact=dict(artifact.artifact),
            verdict=dict(artifact.verdict),
            significance=dict(artifact.significance),
            lineage={
                "records_ref": artifact.artifact.get("records_ref")
                or artifact.artifact.get("content_ref"),
                "commit_sha": experiment.diff_ref.get("commit_sha")
                or experiment.diff_ref.get("git_head"),
                "branch": experiment.diff_ref.get("branch"),
                "parent_commit": experiment.diff_ref.get("parent_commit"),
            },
            diagnostics=[],
            projection=artifact,
        )

    parent_edges: dict[str, str | None] = {}
    for node in nodes_by_id.values():
        parent_id = node.parent_experiment_id
        if parent_id is None:
            parent_edges[node.attempt_id] = None
        elif parent_id == node.attempt_id:
            _append_once(node.diagnostics, LINEAGE_PARENT_SELF_REFERENCE)
            parent_edges[node.attempt_id] = None
        elif parent_id in nodes_by_id:
            parent_edges[node.attempt_id] = parent_id
        elif parent_id in all_p3_task_run_ids_by_attempt:
            _append_once(node.diagnostics, LINEAGE_PARENT_NOT_IN_TASK_RUN)
            parent_edges[node.attempt_id] = None
        else:
            _append_once(node.diagnostics, LINEAGE_MISSING_PARENT)
            parent_edges[node.attempt_id] = None

    _break_cycles(nodes_by_id, parent_edges)

    for node in nodes_by_id.values():
        node.children.clear()
    for attempt_id, parent_id in parent_edges.items():
        if parent_id is not None and parent_id in nodes_by_id:
            nodes_by_id[parent_id].children.append(attempt_id)
    for node in nodes_by_id.values():
        node.children.sort(key=lambda child_id: _node_sort_key(nodes_by_id[child_id]))

    roots = sorted(
        [
            attempt_id
            for attempt_id, parent_id in parent_edges.items()
            if parent_id is None
        ],
        key=lambda attempt_id: _node_sort_key(nodes_by_id[attempt_id]),
    )
    _assign_paths(nodes_by_id, roots)
    ordered_nodes = sorted(
        nodes_by_id.values(),
        key=lambda node: (
            node.path or [node.attempt_id],
            node.created_at,
            node.attempt_id,
        ),
    )
    return ParameterGolfAttemptTree(
        nodes=ordered_nodes,
        root_attempt_ids=roots,
        diagnostics=tree_diagnostics,
    )


def p3_trajectory_summary(
    experiments: list[TaskExperimentRecord],
    *,
    task_run_id: str | None = None,
) -> dict[str, object]:
    target_task_run_id = task_run_id or _first_p3_task_run_id(experiments)
    tree = project_parameter_golf_attempt_tree(
        experiments, task_run_id=target_task_run_id
    )
    artifacts = [
        node.projection
        for node in sorted(tree.nodes, key=_node_sort_key)
        if node.projection is not None
    ]
    best = current_best_parameter_golf_artifact(
        [
            experiment
            for experiment in experiments
            if (
                target_task_run_id is None
                or experiment.task_run_id == target_task_run_id
            )
        ]
    )
    last_artifact = artifacts[-1] if artifacts else None
    return {
        "current_best": best.to_dict() if best is not None else None,
        "last_attempt": _last_attempt_summary(last_artifact),
        "next_action": _next_action_candidate(tree, best, last_artifact),
        "tree": tree.to_dict(),
    }


def _next_action_candidate(
    tree: ParameterGolfAttemptTree,
    best: ParameterGolfArtifact | None,
    last: ParameterGolfArtifact | None,
) -> dict[str, object]:
    nodes = tree.node_by_id()
    valid_base_ids = {
        node.attempt_id
        for node in tree.nodes
        if LINEAGE_PARENT_CYCLE not in node.diagnostics
        and LINEAGE_PARENT_SELF_REFERENCE not in node.diagnostics
    }
    current_best_id = best.attempt_id if best is not None else None
    if last is None:
        return {
            "kind": NEXT_ACTION_PROPOSE_NEXT,
            "base_attempt_id": None,
            "reason": "no_attempts",
        }
    status = _string(last.verdict.get("status"))
    if status == VERDICT_ERROR or (
        (not last.best_candidate) and status != VERDICT_REJECTED
    ):
        base_id = _valid_base(
            _string(last.diff_ref.get("parent_experiment_id")),
            current_best_id,
            valid_base_ids,
        )
        return {
            "kind": NEXT_ACTION_RETRY_INVALID,
            "base_attempt_id": base_id,
            "reason": f"last_attempt_{status or 'invalid'}",
        }
    if status == VERDICT_REJECTED and current_best_id is not None:
        return {
            "kind": NEXT_ACTION_CONTINUE_FROM_BEST,
            "base_attempt_id": _valid_base(current_best_id, None, valid_base_ids),
            "reason": "last_attempt_rejected",
        }
    if status == VERDICT_ACCEPTED and current_best_id == last.attempt_id:
        return {
            "kind": NEXT_ACTION_PROPOSE_NEXT,
            "base_attempt_id": _valid_base(current_best_id, None, valid_base_ids),
            "reason": "last_attempt_is_current_best",
        }
    base_id = _valid_base(current_best_id, last.attempt_id, valid_base_ids)
    if base_id is None and last.attempt_id in nodes:
        base_id = _valid_base(last.attempt_id, None, valid_base_ids)
    return {
        "kind": NEXT_ACTION_PROPOSE_NEXT,
        "base_attempt_id": base_id,
        "reason": "continue_experiment",
    }


def _last_attempt_summary(
    artifact: ParameterGolfArtifact | None,
) -> dict[str, object] | None:
    if artifact is None:
        return None
    return {
        "attempt_id": artifact.attempt_id,
        "parent_experiment_id": _string(artifact.diff_ref.get("parent_experiment_id")),
        "hypothesis": artifact.hypothesis,
        "verdict_status": artifact.verdict.get("status"),
        "val_bpb": artifact.metric.get("value"),
        "records_ref": artifact.artifact.get("records_ref")
        or artifact.artifact.get("content_ref"),
        "created_at": artifact.created_at,
    }


def _break_cycles(
    nodes_by_id: dict[str, ParameterGolfAttemptTreeNode],
    parent_edges: dict[str, str | None],
) -> None:
    seen_cycles: set[frozenset[str]] = set()
    for attempt_id in sorted(
        nodes_by_id, key=lambda key: _node_sort_key(nodes_by_id[key])
    ):
        path: list[str] = []
        position: dict[str, int] = {}
        cursor: str | None = attempt_id
        while cursor is not None and cursor in nodes_by_id:
            if cursor in position:
                cycle = path[position[cursor] :]
                cycle_key = frozenset(cycle)
                if cycle_key in seen_cycles:
                    break
                seen_cycles.add(cycle_key)
                for cycle_id in cycle:
                    _append_once(
                        nodes_by_id[cycle_id].diagnostics, LINEAGE_PARENT_CYCLE
                    )
                root_id = min(cycle, key=lambda key: _node_sort_key(nodes_by_id[key]))
                parent_edges[root_id] = None
                break
            position[cursor] = len(path)
            path.append(cursor)
            cursor = parent_edges.get(cursor)


def _assign_paths(
    nodes_by_id: dict[str, ParameterGolfAttemptTreeNode],
    roots: list[str],
) -> None:
    visited: set[str] = set()

    def visit(attempt_id: str, path: list[str]) -> None:
        if attempt_id in visited:
            return
        visited.add(attempt_id)
        node = nodes_by_id[attempt_id]
        node.path = [*path, attempt_id]
        node.depth = len(path)
        for child_id in node.children:
            visit(child_id, node.path)

    for root_id in roots:
        visit(root_id, [])
    for attempt_id in sorted(
        nodes_by_id, key=lambda key: _node_sort_key(nodes_by_id[key])
    ):
        if attempt_id not in visited:
            roots.append(attempt_id)
            visit(attempt_id, [])


def _first_p3_task_run_id(experiments: list[TaskExperimentRecord]) -> str | None:
    for experiment in experiments:
        if is_parameter_golf_artifact_record(experiment):
            return experiment.task_run_id
    return None


def _valid_base(
    primary: str | None,
    fallback: str | None,
    valid_base_ids: set[str],
) -> str | None:
    if primary in valid_base_ids:
        return primary
    if fallback in valid_base_ids:
        return fallback
    return None


def _node_sort_key(node: ParameterGolfAttemptTreeNode | ParameterGolfArtifact):
    return (node.created_at, node.attempt_id)


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = [
    "ParameterGolfAttemptTree",
    "ParameterGolfAttemptTreeNode",
    "p3_trajectory_summary",
    "project_parameter_golf_attempt_tree",
]
