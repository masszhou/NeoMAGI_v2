"""D12 evidence classifier and claim-vs-evidence verification logic.

Pure helpers, no I/O. The classifier maps each observed tool execution
into a coarse ``evidence_kind`` (one of ``test`` / ``file_write`` /
``lint`` / ``build`` / ``read`` / ``generic``) so the step finalize step
can decide whether the assistant's final claim is supported by what
actually ran.

D12 stays close to structural claim-vs-evidence consistency on purpose:
no subjective quality scoring. The result feeds
``task_steps.output.verification_state`` and may demote
``task_steps.status`` to ``blocked`` / ``failed`` per the amendment.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final


EVIDENCE_TEST: Final[str] = "test"
EVIDENCE_FILE_WRITE: Final[str] = "file_write"
EVIDENCE_LINT: Final[str] = "lint"
EVIDENCE_BUILD: Final[str] = "build"
EVIDENCE_READ: Final[str] = "read"
EVIDENCE_GENERIC: Final[str] = "generic"

EVIDENCE_KINDS: Final[frozenset[str]] = frozenset(
    {EVIDENCE_TEST, EVIDENCE_FILE_WRITE, EVIDENCE_LINT, EVIDENCE_BUILD, EVIDENCE_READ, EVIDENCE_GENERIC}
)

# Verification states (D12 amendment).
VERIFICATION_SUPPORTED: Final[str] = "supported"
VERIFICATION_MISSING_EVIDENCE: Final[str] = "missing_evidence"
VERIFICATION_INCONSISTENT: Final[str] = "inconsistent"
VERIFICATION_ABANDONED: Final[str] = "abandoned"
VERIFICATION_ERROR: Final[str] = "error"

VERIFICATION_BLOCKING_STATES: Final[frozenset[str]] = frozenset(
    {VERIFICATION_MISSING_EVIDENCE, VERIFICATION_INCONSISTENT, VERIFICATION_ABANDONED}
)

_TEST_TOOLS = frozenset({"pytest", "jest", "vitest", "mocha", "cargo_test", "go_test"})
_FILE_WRITE_TOOLS = frozenset({"write", "edit", "patch", "str_replace_based_edit"})
_LINT_TOOLS = frozenset({"ruff", "eslint", "pylint", "mypy", "tsc"})
_READ_TOOLS = frozenset({"read", "ls", "glob", "grep", "find"})

_TEST_CMD_RE = re.compile(
    r"\b(pytest|npm\s+test|yarn\s+test|cargo\s+test|go\s+test|jest|vitest|tox)\b"
)
_LINT_CMD_RE = re.compile(r"\b(ruff|eslint|pylint|mypy|tsc(?!\s+-p))\b")
_BUILD_CMD_RE = re.compile(
    r"\b((npm\s+run\s+)?build|cargo\s+build|make|tsc\s+-p)\b"
)

# Claim regexes — keep conservative (W5 spec). The English alternatives are
# kept lowercase; we run match against ``assistant_text.lower()``. Chinese
# phrases are kept verbatim so the bilingual model output paths line up.
_CLAIM_TEST_RE = re.compile(
    r"(已测试|跑了测试|跑过测试|通过测试|测试通过|tests?\s+(?:passed|passing|run|ran|complete|completed)|"
    r"verified\s+via\s+tests?|all\s+tests?\s+pass)"
)
_CLAIM_FILE_WRITE_RE = re.compile(
    r"(已修改|已写入|已写到|创建了文件|"
    r"(?:wrote|created|updated|edited|modified|patched)\s+(?:the\s+)?file)"
)


@dataclass(frozen=True, slots=True)
class EvidenceObservation:
    tool_call_id: str
    tool_name: str
    is_error: bool
    evidence_kind: str
    command_summary: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    state: str
    reason: str | None
    claims: dict[str, bool]
    missing_kinds: list[str]
    inconsistent_kinds: list[str]


def _bash_command_text(args: Mapping[str, Any] | None) -> str:
    if not isinstance(args, Mapping):
        return ""
    command = args.get("command")
    return command if isinstance(command, str) else ""


def classify_tool_evidence(
    tool_name: str,
    args: Mapping[str, Any] | None,
    is_error: bool,
) -> str:
    """Return the ``evidence_kind`` for an observed tool execution.

    Pure function; no side effects. ``is_error`` is accepted only to keep
    the call site uniform — the classifier itself does not change result
    based on it (an erroring ``pytest`` still counts as ``test`` evidence,
    just one that failed; the verification stage decides whether that
    supports or contradicts the claim).
    """

    _ = is_error  # documented; influences D12 finalize, not the kind.
    if tool_name in _TEST_TOOLS:
        return EVIDENCE_TEST
    if tool_name in _FILE_WRITE_TOOLS:
        return EVIDENCE_FILE_WRITE
    if tool_name in _LINT_TOOLS:
        return EVIDENCE_LINT
    if tool_name in _READ_TOOLS:
        return EVIDENCE_READ
    if tool_name == "bash":
        command = _bash_command_text(args)
        if _TEST_CMD_RE.search(command):
            return EVIDENCE_TEST
        if _LINT_CMD_RE.search(command):
            return EVIDENCE_LINT
        if _BUILD_CMD_RE.search(command):
            return EVIDENCE_BUILD
    return EVIDENCE_GENERIC


def summarize_command(args: Mapping[str, Any] | None, *, limit: int = 200) -> str | None:
    """Render a short, debug-grade summary of the tool's command."""

    if not isinstance(args, Mapping):
        return None
    text = _bash_command_text(args)
    if not text:
        # Fall back to the first string-valued argument so non-bash tools
        # also get a hint in the payload.
        for value in args.values():
            if isinstance(value, str) and value.strip():
                text = value
                break
    if not text:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _matches_claim(text: str, pattern: re.Pattern[str]) -> bool:
    if not text:
        return False
    return bool(pattern.search(text))


def detect_claims(assistant_text: str) -> dict[str, bool]:
    """Return per-evidence-kind claim flags inferred from assistant text."""

    if not assistant_text:
        return {}
    lowered = assistant_text.lower()
    return {
        EVIDENCE_TEST: _matches_claim(lowered, _CLAIM_TEST_RE) or bool(_CLAIM_TEST_RE.search(assistant_text)),
        EVIDENCE_FILE_WRITE: _matches_claim(lowered, _CLAIM_FILE_WRITE_RE) or bool(_CLAIM_FILE_WRITE_RE.search(assistant_text)),
    }


def infer_verification_state(
    *,
    assistant_text: str,
    observations: Sequence[EvidenceObservation],
    error_message: str | None,
    assistant_stop_reason: str | None,
) -> VerificationResult:
    """Compute the step's claim-vs-evidence verification state.

    Decision order (D12 amendment + W5 spec):

    1. Terminal assistant error → ``error``.
    2. Last assistant message stopped on ``tool_call`` without
       completing the turn → ``abandoned``.
    3. For every claim flag (test / file_write) the assistant raised:
       if there is no ``observations`` row with the same ``evidence_kind``
       at all → ``missing_evidence``;
       if all matching rows have ``is_error=true`` → ``inconsistent``;
       else the claim is satisfied.
    4. If all claims pass and no other negative signal → ``supported``.
    """

    if error_message:
        return VerificationResult(
            state=VERIFICATION_ERROR,
            reason=error_message,
            claims={},
            missing_kinds=[],
            inconsistent_kinds=[],
        )
    if assistant_stop_reason == "tool_call":
        return VerificationResult(
            state=VERIFICATION_ABANDONED,
            reason="assistant ended turn awaiting tool result",
            claims={},
            missing_kinds=[],
            inconsistent_kinds=[],
        )

    claims = detect_claims(assistant_text)
    missing: list[str] = []
    inconsistent: list[str] = []
    for kind, claimed in claims.items():
        if not claimed:
            continue
        relevant = [obs for obs in observations if obs.evidence_kind == kind]
        if not relevant:
            missing.append(kind)
            continue
        if all(obs.is_error for obs in relevant):
            inconsistent.append(kind)

    if missing:
        return VerificationResult(
            state=VERIFICATION_MISSING_EVIDENCE,
            reason=f"claim mentions {missing[0]} but no successful evidence found",
            claims=claims,
            missing_kinds=missing,
            inconsistent_kinds=inconsistent,
        )
    if inconsistent:
        return VerificationResult(
            state=VERIFICATION_INCONSISTENT,
            reason=f"claim mentions {inconsistent[0]} but all matching tool runs reported errors",
            claims=claims,
            missing_kinds=missing,
            inconsistent_kinds=inconsistent,
        )
    return VerificationResult(
        state=VERIFICATION_SUPPORTED,
        reason=None,
        claims=claims,
        missing_kinds=[],
        inconsistent_kinds=[],
    )


__all__ = [
    "EVIDENCE_BUILD",
    "EVIDENCE_FILE_WRITE",
    "EVIDENCE_GENERIC",
    "EVIDENCE_KINDS",
    "EVIDENCE_LINT",
    "EVIDENCE_READ",
    "EVIDENCE_TEST",
    "EvidenceObservation",
    "VERIFICATION_ABANDONED",
    "VERIFICATION_BLOCKING_STATES",
    "VERIFICATION_ERROR",
    "VERIFICATION_INCONSISTENT",
    "VERIFICATION_MISSING_EVIDENCE",
    "VERIFICATION_SUPPORTED",
    "VerificationResult",
    "classify_tool_evidence",
    "detect_claims",
    "infer_verification_state",
    "summarize_command",
]
