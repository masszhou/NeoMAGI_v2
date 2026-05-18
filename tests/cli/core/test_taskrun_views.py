"""D10: KEY_HISTORY two-tier model + derived event taxonomy."""

from __future__ import annotations

import pytest

from cli.core.taskrun_event_payloads import (
    DERIVED_EVENT_TYPES,
    DERIVED_RUNTIME_EVENT_TYPES,
    DERIVED_STEP_SUMMARY_EVENT_TYPES,
    DERIVED_TIER1_ONLY_EVENT_TYPES,
    DERIVED_TOOL_DETAIL_EVENT_TYPES,
    PAYLOAD_VERSIONS,
    TASK_STEP_EVIDENCE_MISSING,
    TASK_STEP_EVIDENCE_RECORDED,
    TASK_STEP_OUTCOME_SUPPORTED,
    TASK_STEP_OUTCOME_UNSUPPORTED,
    TASK_STEP_RESUME_CONTEXT_GENERATED,
    TASK_TOOL_OBSERVED,
    TASK_TOOL_POLICY_BLOCKED,
    TASK_TOOL_POLICY_RESOLVED,
    build_step_evidence_missing_payload,
    build_step_evidence_recorded_payload,
    build_tool_observed_payload,
    build_tool_policy_blocked_payload,
    build_tool_policy_resolved_payload,
    derived_payload,
)
from cli.core.taskrun_views import KEY_HISTORY_EVENT_TYPES


def test_derived_step_summary_events_join_key_history() -> None:
    assert DERIVED_STEP_SUMMARY_EVENT_TYPES <= KEY_HISTORY_EVENT_TYPES


def test_tool_detail_events_excluded_from_key_history() -> None:
    for event_type in DERIVED_TOOL_DETAIL_EVENT_TYPES:
        assert event_type not in KEY_HISTORY_EVENT_TYPES


def test_runtime_events_excluded_from_key_history() -> None:
    for event_type in DERIVED_RUNTIME_EVENT_TYPES:
        assert event_type not in KEY_HISTORY_EVENT_TYPES


def test_derived_taxonomy_partitions_correctly() -> None:
    assert DERIVED_STEP_SUMMARY_EVENT_TYPES.isdisjoint(DERIVED_TIER1_ONLY_EVENT_TYPES)
    assert (
        DERIVED_EVENT_TYPES
        == DERIVED_STEP_SUMMARY_EVENT_TYPES | DERIVED_TIER1_ONLY_EVENT_TYPES
    )


def test_payload_version_present_for_every_derived_event_type() -> None:
    assert set(PAYLOAD_VERSIONS) == DERIVED_EVENT_TYPES
    for version in PAYLOAD_VERSIONS.values():
        assert isinstance(version, int) and version >= 1


def test_derived_payload_stamps_version() -> None:
    payload = derived_payload(TASK_TOOL_OBSERVED, {"tool_call_id": "tc1"})
    assert payload["payload_version"] == PAYLOAD_VERSIONS[TASK_TOOL_OBSERVED]
    assert payload["tool_call_id"] == "tc1"


def test_derived_payload_rejects_unknown_event_type() -> None:
    with pytest.raises(KeyError):
        derived_payload("not_a_derived_event", {})


def test_tool_observed_payload_required_fields() -> None:
    payload = build_tool_observed_payload(
        tool_call_id="tc1",
        tool_name="bash",
        is_error=False,
        evidence_kind="test",
    )
    assert payload["payload_version"] == 1
    assert payload["tool_call_id"] == "tc1"
    assert payload["tool_name"] == "bash"
    assert payload["is_error"] is False
    assert payload["evidence_kind"] == "test"
    # optional fields omitted when not provided
    assert "tool_execution_id" not in payload
    assert "command_summary" not in payload
    assert "duration_ms" not in payload


def test_tool_observed_payload_optionals_included_when_set() -> None:
    payload = build_tool_observed_payload(
        tool_call_id="tc1",
        tool_name="bash",
        is_error=True,
        evidence_kind="generic",
        tool_execution_id="te1",
        command_summary="pytest tests/",
        duration_ms=1234,
    )
    assert payload["tool_execution_id"] == "te1"
    assert payload["command_summary"] == "pytest tests/"
    assert payload["duration_ms"] == 1234


def test_tool_policy_resolved_and_blocked_payloads() -> None:
    resolved = build_tool_policy_resolved_payload(
        tool_call_id="tc1",
        permission_profile_name="guarded",
        effect="allow",
    )
    assert resolved["payload_version"] == 1
    assert resolved["effect"] == "allow"
    # naming check (derived module exposes both names)
    assert TASK_TOOL_POLICY_RESOLVED == "task_tool_policy_resolved"

    blocked = build_tool_policy_blocked_payload(
        tool_call_id="tc1",
        permission_profile_name="guarded",
        effect="block",
        reason="denied by profile",
    )
    assert blocked["payload_version"] == 1
    assert blocked["effect"] == "block"
    assert blocked["reason"] == "denied by profile"
    assert TASK_TOOL_POLICY_BLOCKED == "task_tool_policy_blocked"


def test_step_evidence_payload_uses_evidence_kind_consistently() -> None:
    recorded = build_step_evidence_recorded_payload(
        evidence_kind="test",
        source_tool_call_ids=["tc1", "tc2"],
        claim_summary="ran pytest",
    )
    assert recorded["payload_version"] == 1
    assert recorded["evidence_kind"] == "test"
    assert recorded["source_tool_call_ids"] == ["tc1", "tc2"]
    assert recorded["claim_summary"] == "ran pytest"

    missing = build_step_evidence_missing_payload(evidence_kind="test")
    assert missing["evidence_kind"] == "test"
    assert missing["source_tool_call_ids"] == []
    assert "claim_summary" not in missing


def test_step_summary_constants_have_expected_names() -> None:
    assert TASK_STEP_EVIDENCE_RECORDED == "task_step_evidence_recorded"
    assert TASK_STEP_EVIDENCE_MISSING == "task_step_evidence_missing"
    assert TASK_STEP_OUTCOME_SUPPORTED == "task_step_outcome_supported"
    assert TASK_STEP_OUTCOME_UNSUPPORTED == "task_step_outcome_unsupported"
    assert TASK_STEP_RESUME_CONTEXT_GENERATED == "task_step_resume_context_generated"
