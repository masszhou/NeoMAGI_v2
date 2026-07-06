"""P3-M6 magipi adjudication of external audit findings.

`magipi` must independently adjudicate every audit finding before execution.
An auditor-assigned P0/P1 blocks execution until a later re-review no longer
reports it, or a human explicitly approves an override after a `magipi`
rebuttal — `magipi` cannot clear a blocker by self-downgrading (ADR-0027).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from cli.core.research_workflow_contract import (
    ADJUDICATION_DECISIONS,
    BLOCKING_SEVERITIES,
    GATE_OUTCOME_FAIL,
    GATE_OUTCOME_PASS,
)
from cli.core.research_workflow_store import (
    ResearchWorkflowState,
    ResearchWorkflowStoreError,
)


def validate_adjudication_entries(
    state: ResearchWorkflowState,
    entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate that entries cover exactly the latest audit round's findings."""

    audit = state.latest_audit()
    if audit is None:
        raise ResearchWorkflowStoreError("no audit round to adjudicate")
    findings = {str(f.get("finding_id")): f for f in audit.get("findings") or []}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(entries, start=1):
        finding_id = str(raw.get("finding_id") or "")
        decision = str(raw.get("decision") or "")
        rationale = str(raw.get("rationale") or "").strip()
        if finding_id not in findings:
            raise ResearchWorkflowStoreError(
                f"adjudication entry {index}: unknown finding_id {finding_id!r}"
            )
        if finding_id in seen:
            raise ResearchWorkflowStoreError(
                f"adjudication entry {index}: duplicate finding_id {finding_id!r}"
            )
        if decision not in ADJUDICATION_DECISIONS:
            raise ResearchWorkflowStoreError(
                f"adjudication entry {index}: decision must be one of "
                f"{sorted(ADJUDICATION_DECISIONS)}"
            )
        if not rationale:
            raise ResearchWorkflowStoreError(
                f"adjudication entry {index}: rationale is required"
            )
        seen.add(finding_id)
        normalized.append(
            {
                "finding_id": finding_id,
                "severity": findings[finding_id].get("severity"),
                "decision": decision,
                "rationale": rationale,
                "action_ref": str(raw.get("action_ref") or ""),
            }
        )
    missing = sorted(set(findings) - seen)
    if missing:
        raise ResearchWorkflowStoreError(
            f"adjudication must cover every finding; missing: {','.join(missing)}"
        )
    return normalized


def overridden_finding_ids(state: ResearchWorkflowState) -> set[str]:
    return {
        str(record.get("finding_id"))
        for record in state.overrides
        if record.get("approved_by")
    }


def remaining_blockers(state: ResearchWorkflowState) -> list[dict[str, Any]]:
    """P0/P1 findings from the *latest* audit round without a human override.

    A re-audit that no longer reports a finding clears it naturally, because
    only the latest round is consulted.
    """

    audit = state.latest_audit()
    if audit is None:
        return []
    overridden = overridden_finding_ids(state)
    return [
        dict(finding)
        for finding in audit.get("findings") or []
        if str(finding.get("severity")) in BLOCKING_SEVERITIES
        and str(finding.get("finding_id")) not in overridden
    ]


def validate_override_request(
    state: ResearchWorkflowState,
    finding_id: str,
    approved_by: str,
    reason: str,
) -> dict[str, Any]:
    """Human override requires an existing magipi rebuttal (an adjudication
    entry for that finding) — the human approves, the model must first argue."""

    if not approved_by.strip():
        raise ResearchWorkflowStoreError("override requires --approved-by")
    if not reason.strip():
        raise ResearchWorkflowStoreError("override requires --reason")
    audit = state.latest_audit()
    if audit is None:
        raise ResearchWorkflowStoreError("no audit round exists to override")
    finding = next(
        (
            f
            for f in audit.get("findings") or []
            if str(f.get("finding_id")) == finding_id
        ),
        None,
    )
    if finding is None:
        raise ResearchWorkflowStoreError(
            f"finding {finding_id!r} not present in latest audit round"
        )
    adjudication = state.latest_adjudication_for_round(int(audit.get("round") or 0))
    entries = (adjudication or {}).get("entries") or []
    rebuttal = next(
        (e for e in entries if str(e.get("finding_id")) == finding_id), None
    )
    if rebuttal is None:
        raise ResearchWorkflowStoreError(
            f"override for {finding_id!r} requires a prior magipi adjudication "
            "entry (rebuttal) for that finding"
        )
    return {
        "finding_id": finding_id,
        "severity": finding.get("severity"),
        "round": audit.get("round"),
        "approved_by": approved_by.strip(),
        "reason": reason.strip(),
        "rebuttal_decision": rebuttal.get("decision"),
    }


def audit_clear_check(state: ResearchWorkflowState) -> tuple[str, dict[str, Any]]:
    """Code-computed `audit_clear` gate outcome."""

    audit = state.latest_audit()
    evidence: dict[str, Any] = {
        "audit_rounds": len(state.audits),
        "round_cap": state.round_cap,
    }
    if audit is None:
        evidence["reason"] = "no_audit_round"
        return GATE_OUTCOME_FAIL, evidence
    evidence["latest_round"] = audit.get("round")
    evidence["transcript_ref"] = audit.get("transcript_ref")
    if audit.get("timed_out") or audit.get("exit_code") != 0:
        evidence["reason"] = "latest_audit_not_usable"
        return GATE_OUTCOME_FAIL, evidence
    if audit.get("parse_errors"):
        evidence["reason"] = "findings_parse_errors"
        evidence["parse_errors"] = audit.get("parse_errors")
        return GATE_OUTCOME_FAIL, evidence
    adjudication = state.latest_adjudication_for_round(int(audit.get("round") or 0))
    if adjudication is None:
        evidence["reason"] = "adjudication_missing"
        return GATE_OUTCOME_FAIL, evidence
    evidence["adjudication_entries"] = len(adjudication.get("entries") or [])
    blockers = remaining_blockers(state)
    if blockers:
        evidence["reason"] = "auditor_p0_p1_unresolved"
        evidence["blockers"] = [f.get("finding_id") for f in blockers]
        return GATE_OUTCOME_FAIL, evidence
    evidence["reason"] = "no_blocking_findings"
    evidence["overrides"] = sorted(overridden_finding_ids(state))
    return GATE_OUTCOME_PASS, evidence


__all__ = [
    "audit_clear_check",
    "overridden_finding_ids",
    "remaining_blockers",
    "validate_adjudication_entries",
    "validate_override_request",
]
