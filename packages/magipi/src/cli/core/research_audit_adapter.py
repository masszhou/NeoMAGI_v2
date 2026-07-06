"""P3-M6 external read-only audit adapter.

Invokes an external auditor CLI (default: Claude Code in read-only plan mode),
captures the full transcript durably (prompt, stdout, stderr, exit code,
model, effort, elapsed seconds), and parses structured findings. The auditor
is evidence, never controller: findings feed the `magipi` adjudication step
and the `audit_clear` gate; they do not mutate workflow state themselves.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli.core.research_workflow_contract import (
    DEFAULT_AUDIT_TIMEOUT_SECONDS,
    FINDING_SEVERITIES,
)
from cli.core.research_workflow_store import (
    ResearchWorkflowState,
    ResearchWorkflowStoreError,
)

DEFAULT_AUDITOR_MODEL = "claude-opus-4-8"
DEFAULT_AUDITOR_EFFORT = "xhigh"
MAX_CONTEXT_REF_BYTES = 48_000
MAX_TRANSCRIPT_PARSE_BYTES = 2_000_000

_FINDINGS_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

AUDIT_PROMPT_CONTRACT = """\
You are an independent read-only auditor for an autonomous research workflow.
Do not modify any files. Review the plan below for correctness blockers.

Severity taxonomy:
- P0: blocks execution; would invalidate the experiment or damage truth.
- P1: blocks execution; material correctness/safety gap in the plan.
- P2: follow-up worth tracking; does not block execution.
- P3: note.

End your reply with exactly one fenced json block of this shape:
```json
{"findings": [{"finding_id": "F1", "severity": "P1", "title": "...",
               "detail": "...", "refs": ["..."]}]}
```
Use an empty findings list if the plan has no findings.
"""


@dataclass(frozen=True, slots=True)
class ResearchAuditOptions:
    plan_file: Path
    context_refs: tuple[Path, ...] = ()
    auditor_command: str | None = None
    model: str = DEFAULT_AUDITOR_MODEL
    effort: str = DEFAULT_AUDITOR_EFFORT
    timeout_seconds: int = DEFAULT_AUDIT_TIMEOUT_SECONDS
    objective: str = ""


@dataclass(frozen=True, slots=True)
class ResearchAuditResult:
    round: int
    exit_code: int
    elapsed_seconds: float
    transcript_dir: Path
    findings: tuple[dict[str, Any], ...]
    parse_errors: tuple[str, ...] = ()
    timed_out: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "auditor": "external_cli",
            "round": self.round,
            "exit_code": self.exit_code,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "transcript_ref": str(self.transcript_dir),
            "findings": [dict(f) for f in self.findings],
            "parse_errors": list(self.parse_errors),
            "timed_out": self.timed_out,
        }


def build_audit_command(options: ResearchAuditOptions) -> list[str]:
    if options.auditor_command:
        return shlex.split(options.auditor_command)
    return [
        "claude",
        "-p",
        "--permission-mode",
        "plan",
        "--model",
        options.model,
        "--effort",
        options.effort,
    ]


def build_audit_prompt(options: ResearchAuditOptions) -> str:
    sections = [AUDIT_PROMPT_CONTRACT]
    if options.objective:
        sections.append(f"## Task objective\n\n{options.objective}")
    sections.append("## Plan under audit\n\n" + _bounded_read(options.plan_file))
    for ref in options.context_refs:
        sections.append(f"## Context ref: {ref}\n\n" + _bounded_read(ref))
    return "\n\n".join(sections)


def _bounded_read(path: Path) -> str:
    if not path.is_file():
        raise ResearchWorkflowStoreError(f"audit input file missing: {path}")
    data = path.read_bytes()
    truncated = len(data) > MAX_CONTEXT_REF_BYTES
    text = data[:MAX_CONTEXT_REF_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[truncated at {MAX_CONTEXT_REF_BYTES} bytes]"
    return text


def run_research_audit(
    state: ResearchWorkflowState,
    options: ResearchAuditOptions,
    *,
    cwd: Path,
) -> ResearchAuditResult:
    audit_round = len(state.audits) + 1
    if audit_round > state.round_cap:
        raise ResearchWorkflowStoreError(
            f"audit round cap reached ({state.round_cap}); resolve blockers via "
            "remediation + re-review before the cap, or record a human override"
        )
    prompt = build_audit_prompt(options)
    command = build_audit_command(options)
    transcript_dir = state.records_root / "audits" / f"round_{audit_round:02d}"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    (transcript_dir / "prompt.md").write_text(prompt)

    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=options.timeout_seconds,
            cwd=cwd,
        )
        exit_code = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout = _expired_stream(exc.stdout)
        stderr = _expired_stream(exc.stderr)
    except FileNotFoundError as exc:
        raise ResearchWorkflowStoreError(
            f"auditor command not found: {command[0]} ({exc})"
        ) from exc
    elapsed = time.monotonic() - started

    (transcript_dir / "stdout.txt").write_text(stdout)
    (transcript_dir / "stderr.txt").write_text(stderr)
    findings, parse_errors = (
        ([], ["auditor_timed_out"])
        if timed_out
        else (
            parse_findings(stdout) if exit_code == 0 else ([], ["auditor_exit_nonzero"])
        )
    )
    meta = {
        "round": audit_round,
        "command": command,
        "model": options.model,
        "effort": options.effort,
        "permission_mode": "read_only_or_plan",
        "plan_file": str(options.plan_file),
        "context_refs": [str(ref) for ref in options.context_refs],
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed, 3),
        "timed_out": timed_out,
        "findings_count": len(findings),
        "parse_errors": parse_errors,
    }
    (transcript_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n"
    )
    return ResearchAuditResult(
        round=audit_round,
        exit_code=exit_code,
        elapsed_seconds=elapsed,
        transcript_dir=transcript_dir,
        findings=tuple(findings),
        parse_errors=tuple(parse_errors),
        timed_out=timed_out,
    )


def _expired_stream(raw: str | bytes | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def parse_findings(stdout: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract the last fenced ```json findings block from auditor output."""

    text = stdout[-MAX_TRANSCRIPT_PARSE_BYTES:]
    candidates = _FINDINGS_BLOCK_RE.findall(text)
    for raw in reversed(candidates):
        parsed = _try_findings_json(raw)
        if parsed is not None:
            return parsed
    parsed = _try_findings_json(text.strip())
    if parsed is not None:
        return parsed
    return [], ["findings_block_missing"]


def _try_findings_json(
    raw: str,
) -> tuple[list[dict[str, Any]], list[str]] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError, ValueError:
        return None
    if not isinstance(data, Mapping) or not isinstance(data.get("findings"), list):
        return None
    return normalize_findings(data["findings"])


def normalize_findings(
    raw_findings: Sequence[Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(raw_findings, start=1):
        if not isinstance(raw, Mapping):
            errors.append(f"finding_{index}_not_object")
            continue
        severity = str(raw.get("severity") or "").upper()
        if severity not in FINDING_SEVERITIES:
            errors.append(f"finding_{index}_invalid_severity")
            continue
        findings.append(
            {
                "finding_id": str(raw.get("finding_id") or f"F{index}"),
                "severity": severity,
                "title": str(raw.get("title") or "").strip(),
                "detail": str(raw.get("detail") or "").strip(),
                "refs": [str(ref) for ref in raw.get("refs") or []],
            }
        )
    ids = [f["finding_id"] for f in findings]
    if len(ids) != len(set(ids)):
        errors.append("duplicate_finding_ids")
    return findings, errors


__all__ = [
    "AUDIT_PROMPT_CONTRACT",
    "DEFAULT_AUDITOR_EFFORT",
    "DEFAULT_AUDITOR_MODEL",
    "ResearchAuditOptions",
    "ResearchAuditResult",
    "build_audit_command",
    "build_audit_prompt",
    "normalize_findings",
    "parse_findings",
    "run_research_audit",
]
