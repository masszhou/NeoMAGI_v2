"""P3-M6 informed-iteration proposal evidence and terminal decision gating.

The workflow graph provides procedural drive; this module owns the minimal
optimization drive: a later proposal must structurally consume prior attempt
evidence, and terminal decisions must satisfy the matching evidence gate
(roadmap §P3-M6 优化驱动验收).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from cli.core.research_workflow_contract import (
    GATE_OUTCOME_FAIL,
    GATE_OUTCOME_PASS,
    INFORMED_ITERATION_FIELDS,
    INFORMED_ITERATION_GATED_DECISIONS,
    RESEARCH_DECISION_STOP_NEGATIVE,
    RESEARCH_DECISIONS,
)
from cli.core.research_workflow_store import (
    ResearchWorkflowState,
    ResearchWorkflowStoreError,
)
from storage.taskrun_repository import TaskExperimentRecord


def validate_informed_iteration_evidence(
    proposal: Mapping[str, Any],
    prior_experiment_ids: Sequence[str],
) -> list[str]:
    """Return structural errors for the informed-iteration evidence block."""

    errors: list[str] = []
    evidence = proposal.get("informed_iteration")
    if not isinstance(evidence, Mapping):
        return ["informed_iteration_block_missing"]
    for field_name in INFORMED_ITERATION_FIELDS:
        value = str(evidence.get(field_name) or "").strip()
        if not value:
            errors.append(f"informed_iteration.{field_name}_missing")
    prior_ref = str(evidence.get("prior_attempt_ref") or "").strip()
    if prior_ref and prior_ref not in set(prior_experiment_ids):
        errors.append("informed_iteration.prior_attempt_ref_unknown")
    return errors


def proposal_requires_informed_evidence(
    prior_experiment_ids: Sequence[str],
) -> bool:
    return len(prior_experiment_ids) > 0


def evaluate_proposal(
    proposal: Mapping[str, Any],
    prior_experiment_ids: Sequence[str],
) -> dict[str, Any]:
    """Normalize a proposal record with its informed-iteration validity."""

    hypothesis = str(proposal.get("hypothesis") or "").strip()
    if not hypothesis:
        raise ResearchWorkflowStoreError("proposal requires a hypothesis")
    required = proposal_requires_informed_evidence(prior_experiment_ids)
    errors = (
        validate_informed_iteration_evidence(proposal, prior_experiment_ids)
        if required
        else []
    )
    if required and errors:
        raise ResearchWorkflowStoreError(
            "proposal after a prior attempt must carry structured "
            "informed-iteration evidence; errors: " + ",".join(errors)
        )
    return {
        "hypothesis": hypothesis,
        "informed_required": required,
        "informed_iteration_valid": required and not errors,
        "informed_iteration": dict(proposal.get("informed_iteration") or {}),
        "proposal": {
            key: value for key, value in proposal.items() if key != "informed_iteration"
        },
    }


def informed_proposal_valid_check(
    state: ResearchWorkflowState,
    node_id: str,
) -> tuple[str, dict[str, Any]]:
    """Code-computed `informed_proposal_valid` gate outcome for a run node.

    The gate's ``gate_params.proposal_node`` names the run node whose latest
    recorded proposal must carry valid informed-iteration evidence.
    """

    record = state.latest_proposal_for_node(node_id)
    evidence: dict[str, Any] = {"proposal_node": node_id}
    if record is None:
        evidence["reason"] = "proposal_missing"
        return GATE_OUTCOME_FAIL, evidence
    evidence["proposal_ref"] = record.get("proposal_ref")
    if not record.get("informed_iteration_valid"):
        evidence["reason"] = "informed_iteration_evidence_invalid"
        return GATE_OUTCOME_FAIL, evidence
    evidence["reason"] = "informed_iteration_evidence_valid"
    evidence["prior_attempt_ref"] = (record.get("informed_iteration") or {}).get(
        "prior_attempt_ref"
    )
    return GATE_OUTCOME_PASS, evidence


def experiment_evidence_check(
    state: ResearchWorkflowState,
    experiments: Sequence[TaskExperimentRecord],
    *,
    min_attempts: int,
) -> tuple[str, dict[str, Any]]:
    """Code-computed `experiment_evidence_recorded` gate outcome."""

    attempt_ids = [record.id for record in experiments]
    evidence: dict[str, Any] = {
        "attempts_recorded": len(attempt_ids),
        "min_attempts": min_attempts,
        "attempt_ids": attempt_ids[-10:],
    }
    if len(attempt_ids) < min_attempts:
        evidence["reason"] = "insufficient_attempts"
        return GATE_OUTCOME_FAIL, evidence
    evidence["reason"] = "attempts_recorded"
    return GATE_OUTCOME_PASS, evidence


def optimization_drive_satisfied(
    state: ResearchWorkflowState,
    experiments: Sequence[TaskExperimentRecord],
) -> tuple[bool, dict[str, Any]]:
    informed = [
        record for record in state.proposals if record.get("informed_iteration_valid")
    ]
    evidence = {
        "attempts_recorded": len(experiments),
        "informed_proposals": len(informed),
    }
    return (len(experiments) >= 2 and len(informed) >= 1), evidence


def validate_decision_request(
    state: ResearchWorkflowState,
    experiments: Sequence[TaskExperimentRecord],
    *,
    decision: str,
    rationale: str,
    evidence_refs: Sequence[str],
    stop_policy_ref: str | None,
) -> dict[str, Any]:
    """Enforce the terminal decision protocol; returns the decision payload."""

    if decision not in RESEARCH_DECISIONS:
        raise ResearchWorkflowStoreError(
            f"decision must be one of {sorted(RESEARCH_DECISIONS)}"
        )
    if state.decision is not None:
        raise ResearchWorkflowStoreError(
            "a terminal decision is already recorded for this workflow"
        )
    if not rationale.strip():
        raise ResearchWorkflowStoreError("decision requires --rationale")
    if not evidence_refs:
        raise ResearchWorkflowStoreError(
            "decision requires at least one --evidence-ref (DB/records/transcript)"
        )
    drive_ok, drive_evidence = optimization_drive_satisfied(state, experiments)
    if decision in INFORMED_ITERATION_GATED_DECISIONS and not drive_ok:
        raise ResearchWorkflowStoreError(
            "optimization-drive gate not satisfied for decision "
            f"{decision!r}: need >=2 attempts and >=1 structurally informed "
            f"proposal; observed {drive_evidence}"
        )
    if (
        decision == RESEARCH_DECISION_STOP_NEGATIVE
        and not (stop_policy_ref or "").strip()
    ):
        raise ResearchWorkflowStoreError(
            "stop_negative requires --stop-policy-ref naming the satisfied stop policy"
        )
    return {
        "decision": decision,
        "rationale": rationale.strip(),
        "evidence_refs": [str(ref) for ref in evidence_refs],
        "stop_policy_ref": (stop_policy_ref or "").strip() or None,
        "optimization_drive": drive_evidence,
    }


__all__ = [
    "evaluate_proposal",
    "experiment_evidence_check",
    "informed_proposal_valid_check",
    "optimization_drive_satisfied",
    "proposal_requires_informed_evidence",
    "validate_decision_request",
    "validate_informed_iteration_evidence",
]
