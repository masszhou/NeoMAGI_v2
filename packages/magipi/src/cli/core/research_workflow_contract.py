"""P3-M6 autonomous research workflow contract constants.

Shared vocabulary for the typed workflow state graph, audit/adjudication
gates, informed-iteration proposal evidence, and the terminal decision enum.
Semantics are owned by ADR-0027 and
`dev_docs/user_tests/p3_m6_magipi_autonomous_research_acceptance_runbook.md`.
"""

from __future__ import annotations

NODE_KIND_ACTION = "action"
NODE_KIND_GATE = "gate"
NODE_KINDS = frozenset({NODE_KIND_ACTION, NODE_KIND_GATE})

# Stored (base) node statuses. `ready` / `blocked` are never stored: they are
# derived from dependency and gate truth on every load, so a model-written
# "ready" flag cannot become truth (ADR-0027).
NODE_STATUS_PENDING = "pending"
NODE_STATUS_CLAIMED = "claimed"
NODE_STATUS_RUNNING = "running"
NODE_STATUS_DONE = "done"
NODE_STATUS_FAILED = "failed"
NODE_STATUS_CANCELLED = "cancelled"
NODE_STATUS_SUPERSEDED = "superseded"
STORED_NODE_STATUSES = frozenset(
    {
        NODE_STATUS_PENDING,
        NODE_STATUS_CLAIMED,
        NODE_STATUS_RUNNING,
        NODE_STATUS_DONE,
        NODE_STATUS_FAILED,
        NODE_STATUS_CANCELLED,
        NODE_STATUS_SUPERSEDED,
    }
)

# Derived-only statuses.
NODE_STATUS_READY = "ready"
NODE_STATUS_BLOCKED = "blocked"

GATE_OUTCOME_PASS = "pass"
GATE_OUTCOME_FAIL = "fail"
GATE_OUTCOMES = frozenset({GATE_OUTCOME_PASS, GATE_OUTCOME_FAIL})

# Registered gate checks. Gate outcomes are computed by code from durable
# workflow/DB state; the model cannot write an outcome directly.
GATE_CHECK_AUDIT_CLEAR = "audit_clear"
GATE_CHECK_INFORMED_PROPOSAL_VALID = "informed_proposal_valid"
GATE_CHECK_EXPERIMENT_EVIDENCE_RECORDED = "experiment_evidence_recorded"
GATE_CHECKS = frozenset(
    {
        GATE_CHECK_AUDIT_CLEAR,
        GATE_CHECK_INFORMED_PROPOSAL_VALID,
        GATE_CHECK_EXPERIMENT_EVIDENCE_RECORDED,
    }
)

FINDING_SEVERITY_P0 = "P0"
FINDING_SEVERITY_P1 = "P1"
FINDING_SEVERITY_P2 = "P2"
FINDING_SEVERITY_P3 = "P3"
FINDING_SEVERITIES = frozenset(
    {
        FINDING_SEVERITY_P0,
        FINDING_SEVERITY_P1,
        FINDING_SEVERITY_P2,
        FINDING_SEVERITY_P3,
    }
)
BLOCKING_SEVERITIES = frozenset({FINDING_SEVERITY_P0, FINDING_SEVERITY_P1})

ADJUDICATION_ACCEPT = "accept"
ADJUDICATION_REJECT = "reject"
ADJUDICATION_MODIFY = "modify"
ADJUDICATION_DEFER = "defer"
ADJUDICATION_DECISIONS = frozenset(
    {
        ADJUDICATION_ACCEPT,
        ADJUDICATION_REJECT,
        ADJUDICATION_MODIFY,
        ADJUDICATION_DEFER,
    }
)

DEFAULT_AUDIT_ROUND_CAP = 3
DEFAULT_AUDIT_TIMEOUT_SECONDS = 1800

RESEARCH_DECISION_CONTINUE = "continue"
RESEARCH_DECISION_STOP_NEGATIVE = "stop_negative"
RESEARCH_DECISION_FIX_INFRA = "fix_infra"
RESEARCH_DECISION_BLOCKED = "blocked"
RESEARCH_DECISION_SUCCESS = "success"
RESEARCH_DECISIONS = frozenset(
    {
        RESEARCH_DECISION_CONTINUE,
        RESEARCH_DECISION_STOP_NEGATIVE,
        RESEARCH_DECISION_FIX_INFRA,
        RESEARCH_DECISION_BLOCKED,
        RESEARCH_DECISION_SUCCESS,
    }
)
# Decisions that require the optimization-drive gate (>=2 attempts plus a
# structurally informed later proposal). `fix_infra` / `blocked` are the
# metric-invalid escape hatch and are exempt.
INFORMED_ITERATION_GATED_DECISIONS = frozenset(
    {
        RESEARCH_DECISION_CONTINUE,
        RESEARCH_DECISION_STOP_NEGATIVE,
        RESEARCH_DECISION_SUCCESS,
    }
)

# Required structured fields for an informed-iteration proposal
# (runbook "Informed Iteration Protocol").
INFORMED_ITERATION_FIELDS = (
    "prior_attempt_ref",
    "observed_signal",
    "failure_attribution",
    "next_hypothesis",
    "expected_effect",
    "changed_from_prior",
    "stop_policy_ref",
)

EVENT_RESEARCH_WORKFLOW_CREATED = "task_research_workflow_created"
EVENT_RESEARCH_GRAPH_APPLIED = "task_research_graph_applied"
EVENT_RESEARCH_NODE_TRANSITION = "task_research_node_transition"
EVENT_RESEARCH_GATE_EVALUATED = "task_research_gate_evaluated"
EVENT_RESEARCH_AUDIT_RECORDED = "task_research_audit_recorded"
EVENT_RESEARCH_ADJUDICATION_RECORDED = "task_research_adjudication_recorded"
EVENT_RESEARCH_OVERRIDE_RECORDED = "task_research_override_recorded"
EVENT_RESEARCH_PROPOSAL_RECORDED = "task_research_proposal_recorded"
EVENT_RESEARCH_DECISION_RECORDED = "task_research_decision_recorded"
RESEARCH_EVENT_TYPES = frozenset(
    {
        EVENT_RESEARCH_WORKFLOW_CREATED,
        EVENT_RESEARCH_GRAPH_APPLIED,
        EVENT_RESEARCH_NODE_TRANSITION,
        EVENT_RESEARCH_GATE_EVALUATED,
        EVENT_RESEARCH_AUDIT_RECORDED,
        EVENT_RESEARCH_ADJUDICATION_RECORDED,
        EVENT_RESEARCH_OVERRIDE_RECORDED,
        EVENT_RESEARCH_PROPOSAL_RECORDED,
        EVENT_RESEARCH_DECISION_RECORDED,
    }
)

__all__ = [
    "ADJUDICATION_ACCEPT",
    "ADJUDICATION_DECISIONS",
    "ADJUDICATION_DEFER",
    "ADJUDICATION_MODIFY",
    "ADJUDICATION_REJECT",
    "BLOCKING_SEVERITIES",
    "DEFAULT_AUDIT_ROUND_CAP",
    "DEFAULT_AUDIT_TIMEOUT_SECONDS",
    "EVENT_RESEARCH_ADJUDICATION_RECORDED",
    "EVENT_RESEARCH_AUDIT_RECORDED",
    "EVENT_RESEARCH_DECISION_RECORDED",
    "EVENT_RESEARCH_GATE_EVALUATED",
    "EVENT_RESEARCH_GRAPH_APPLIED",
    "EVENT_RESEARCH_NODE_TRANSITION",
    "EVENT_RESEARCH_OVERRIDE_RECORDED",
    "EVENT_RESEARCH_PROPOSAL_RECORDED",
    "EVENT_RESEARCH_WORKFLOW_CREATED",
    "FINDING_SEVERITIES",
    "FINDING_SEVERITY_P0",
    "FINDING_SEVERITY_P1",
    "FINDING_SEVERITY_P2",
    "FINDING_SEVERITY_P3",
    "GATE_CHECKS",
    "GATE_CHECK_AUDIT_CLEAR",
    "GATE_CHECK_EXPERIMENT_EVIDENCE_RECORDED",
    "GATE_CHECK_INFORMED_PROPOSAL_VALID",
    "GATE_OUTCOMES",
    "GATE_OUTCOME_FAIL",
    "GATE_OUTCOME_PASS",
    "INFORMED_ITERATION_FIELDS",
    "INFORMED_ITERATION_GATED_DECISIONS",
    "NODE_KINDS",
    "NODE_KIND_ACTION",
    "NODE_KIND_GATE",
    "NODE_STATUS_BLOCKED",
    "NODE_STATUS_CANCELLED",
    "NODE_STATUS_CLAIMED",
    "NODE_STATUS_DONE",
    "NODE_STATUS_FAILED",
    "NODE_STATUS_PENDING",
    "NODE_STATUS_READY",
    "NODE_STATUS_RUNNING",
    "NODE_STATUS_SUPERSEDED",
    "RESEARCH_DECISIONS",
    "RESEARCH_DECISION_BLOCKED",
    "RESEARCH_DECISION_CONTINUE",
    "RESEARCH_DECISION_FIX_INFRA",
    "RESEARCH_DECISION_STOP_NEGATIVE",
    "RESEARCH_DECISION_SUCCESS",
    "RESEARCH_EVENT_TYPES",
    "STORED_NODE_STATUSES",
]
