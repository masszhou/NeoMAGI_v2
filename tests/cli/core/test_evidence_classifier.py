"""D12 evidence classifier + verification inference unit tests."""

from __future__ import annotations

import pytest

from cli.core.evidence_classifier import (
    EVIDENCE_BUILD,
    EVIDENCE_FILE_WRITE,
    EVIDENCE_GENERIC,
    EVIDENCE_LINT,
    EVIDENCE_READ,
    EVIDENCE_TEST,
    VERIFICATION_ABANDONED,
    VERIFICATION_ERROR,
    VERIFICATION_INCONSISTENT,
    VERIFICATION_MISSING_EVIDENCE,
    VERIFICATION_SUPPORTED,
    EvidenceObservation,
    classify_tool_evidence,
    detect_claims,
    infer_verification_state,
)


@pytest.mark.parametrize(
    "tool_name, args, expected",
    [
        ("pytest", {}, EVIDENCE_TEST),
        ("jest", {}, EVIDENCE_TEST),
        ("vitest", {}, EVIDENCE_TEST),
        ("mocha", {}, EVIDENCE_TEST),
        ("cargo_test", {}, EVIDENCE_TEST),
        ("go_test", {}, EVIDENCE_TEST),
        ("write", {"path": "a.txt"}, EVIDENCE_FILE_WRITE),
        ("edit", {"path": "a.txt"}, EVIDENCE_FILE_WRITE),
        ("patch", {}, EVIDENCE_FILE_WRITE),
        ("str_replace_based_edit", {}, EVIDENCE_FILE_WRITE),
        ("ruff", {}, EVIDENCE_LINT),
        ("eslint", {}, EVIDENCE_LINT),
        ("pylint", {}, EVIDENCE_LINT),
        ("mypy", {}, EVIDENCE_LINT),
        ("tsc", {}, EVIDENCE_LINT),
        ("read", {}, EVIDENCE_READ),
        ("ls", {}, EVIDENCE_READ),
        ("glob", {}, EVIDENCE_READ),
        ("grep", {}, EVIDENCE_READ),
        ("find", {}, EVIDENCE_READ),
        ("unknown_tool", {}, EVIDENCE_GENERIC),
    ],
)
def test_classify_tool_evidence_dispatches_by_tool_name(
    tool_name: str, args: dict, expected: str
) -> None:
    assert classify_tool_evidence(tool_name, args, is_error=False) == expected


@pytest.mark.parametrize(
    "command, expected",
    [
        ("pytest tests/", EVIDENCE_TEST),
        ("npm test --run", EVIDENCE_TEST),
        ("yarn test", EVIDENCE_TEST),
        ("cargo test", EVIDENCE_TEST),
        ("go test ./...", EVIDENCE_TEST),
        ("jest --watch", EVIDENCE_TEST),
        ("vitest run", EVIDENCE_TEST),
        ("tox -e py314", EVIDENCE_TEST),
        ("ruff check src/", EVIDENCE_LINT),
        ("eslint .", EVIDENCE_LINT),
        ("npm run build", EVIDENCE_BUILD),
        ("cargo build", EVIDENCE_BUILD),
        ("make", EVIDENCE_BUILD),
        ("tsc -p tsconfig.json", EVIDENCE_BUILD),
        ("ls -la", EVIDENCE_GENERIC),  # bash + no matching cmd
        ("", EVIDENCE_GENERIC),
    ],
)
def test_classify_bash_command_evidence(command: str, expected: str) -> None:
    assert classify_tool_evidence("bash", {"command": command}, is_error=False) == expected


def test_classify_is_error_is_ignored_by_classifier() -> None:
    # The classifier does not change kind based on is_error — verification
    # stage decides whether a failed pytest counts as "inconsistent".
    assert classify_tool_evidence("pytest", {}, is_error=True) == EVIDENCE_TEST
    assert classify_tool_evidence("pytest", {}, is_error=False) == EVIDENCE_TEST


def test_detect_claims_recognizes_english_and_chinese_phrases() -> None:
    text_en = "Ran the suite; tests passed cleanly."
    text_cn = "已修改 src/foo.py 并通过测试。"
    assert detect_claims(text_en)[EVIDENCE_TEST] is True
    assert detect_claims(text_en)[EVIDENCE_FILE_WRITE] is False
    cn = detect_claims(text_cn)
    assert cn[EVIDENCE_TEST] is True
    assert cn[EVIDENCE_FILE_WRITE] is True


def test_detect_claims_does_not_match_unrelated_text() -> None:
    text = "Here is a friendly intro that does not claim anything."
    flags = detect_claims(text)
    assert flags[EVIDENCE_TEST] is False
    assert flags[EVIDENCE_FILE_WRITE] is False


def _obs(kind: str, *, is_error: bool = False, call_id: str = "c1", tool: str = "bash") -> EvidenceObservation:
    return EvidenceObservation(
        tool_call_id=call_id,
        tool_name=tool,
        is_error=is_error,
        evidence_kind=kind,
    )


def test_infer_verification_supported_when_evidence_matches_claim() -> None:
    result = infer_verification_state(
        assistant_text="Ran pytest, all tests passed.",
        observations=[_obs(EVIDENCE_TEST)],
        error_message=None,
        assistant_stop_reason="end_turn",
    )
    assert result.state == VERIFICATION_SUPPORTED
    assert result.reason is None


def test_infer_verification_missing_evidence_blocks_claim_without_run() -> None:
    result = infer_verification_state(
        assistant_text="All tests passed, ready to ship.",
        observations=[_obs(EVIDENCE_READ)],  # only read, no test
        error_message=None,
        assistant_stop_reason="end_turn",
    )
    assert result.state == VERIFICATION_MISSING_EVIDENCE
    assert "test" in (result.reason or "")
    assert EVIDENCE_TEST in result.missing_kinds


def test_infer_verification_inconsistent_when_evidence_is_error() -> None:
    result = infer_verification_state(
        assistant_text="All tests passed, ready to ship.",
        observations=[_obs(EVIDENCE_TEST, is_error=True)],
        error_message=None,
        assistant_stop_reason="end_turn",
    )
    assert result.state == VERIFICATION_INCONSISTENT
    assert EVIDENCE_TEST in result.inconsistent_kinds


def test_infer_verification_abandoned_when_last_turn_stopped_on_tool_call() -> None:
    result = infer_verification_state(
        assistant_text="",
        observations=[],
        error_message=None,
        assistant_stop_reason="tool_call",
    )
    assert result.state == VERIFICATION_ABANDONED


def test_infer_verification_error_when_terminal_error_message() -> None:
    result = infer_verification_state(
        assistant_text="ignored",
        observations=[],
        error_message="provider hung up",
        assistant_stop_reason="error",
    )
    assert result.state == VERIFICATION_ERROR


def test_infer_verification_supported_with_no_claim() -> None:
    # No claim => no evidence required.
    result = infer_verification_state(
        assistant_text="Here is an explanation without claims.",
        observations=[_obs(EVIDENCE_READ)],
        error_message=None,
        assistant_stop_reason="end_turn",
    )
    assert result.state == VERIFICATION_SUPPORTED
