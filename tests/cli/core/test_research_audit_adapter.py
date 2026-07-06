from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.core.research_audit_adapter import (
    ResearchAuditOptions,
    build_audit_command,
    parse_findings,
    run_research_audit,
)
from cli.core.research_workflow_service import create_workflow
from cli.core.research_workflow_store import ResearchWorkflowStoreError
from test_taskrun_service import _FakeTaskRunRepository, _seed_record, _service


def _state(tmp_path: Path):
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    service = _service(repo)
    return service, create_workflow(service, record)


def _stub_auditor(tmp_path: Path, findings: list[dict]) -> str:
    payload = json.dumps({"findings": findings})
    script = tmp_path / "stub_auditor.py"
    script.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('audit narrative')\n"
        f"print('```json\\n{payload}\\n```')\n"
    )
    return f"python3 {script}"


def test_parse_findings_takes_last_valid_block_and_normalizes() -> None:
    stdout = (
        'prose\n```json\n{"findings": []}\n```\nmore\n'
        '```json\n{"findings": [{"severity": "p1", "title": "t"}]}\n```\n'
    )
    findings, errors = parse_findings(stdout)
    assert errors == []
    assert findings == [
        {"finding_id": "F1", "severity": "P1", "title": "t", "detail": "", "refs": []}
    ]


def test_parse_findings_reports_missing_block_and_bad_severity() -> None:
    assert parse_findings("no json here") == ([], ["findings_block_missing"])
    findings, errors = parse_findings(
        '```json\n{"findings": [{"severity": "critical"}]}\n```'
    )
    assert findings == []
    assert errors == ["finding_1_invalid_severity"]


def test_default_command_is_readonly_claude_and_override_wins() -> None:
    options = ResearchAuditOptions(plan_file=Path("plan.md"))
    assert build_audit_command(options)[:4] == [
        "claude",
        "-p",
        "--permission-mode",
        "plan",
    ]
    override = ResearchAuditOptions(
        plan_file=Path("plan.md"), auditor_command="echo hi"
    )
    assert build_audit_command(override) == ["echo", "hi"]


def test_run_audit_captures_transcript_and_findings(tmp_path: Path) -> None:
    service, state = _state(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("hypothesis: change one knob")
    options = ResearchAuditOptions(
        plan_file=plan,
        auditor_command=_stub_auditor(
            tmp_path, [{"finding_id": "F1", "severity": "P2", "title": "note"}]
        ),
        timeout_seconds=30,
    )
    result = run_research_audit(state, options, cwd=tmp_path)
    assert result.exit_code == 0
    assert result.parse_errors == ()
    assert [f["finding_id"] for f in result.findings] == ["F1"]
    assert (result.transcript_dir / "prompt.md").is_file()
    assert (result.transcript_dir / "stdout.txt").is_file()
    meta = json.loads((result.transcript_dir / "meta.json").read_text())
    assert meta["round"] == 1
    assert meta["findings_count"] == 1


def test_run_audit_times_out_and_records_unusable_round(tmp_path: Path) -> None:
    service, state = _state(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    options = ResearchAuditOptions(
        plan_file=plan, auditor_command="sleep 5", timeout_seconds=1
    )
    result = run_research_audit(state, options, cwd=tmp_path)
    assert result.timed_out is True
    assert result.findings == ()
    assert "auditor_timed_out" in result.parse_errors


def test_round_cap_is_enforced(tmp_path: Path) -> None:
    service, state = _state(tmp_path)
    state.round_cap = 1
    state.audits.append({"round": 1})
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    with pytest.raises(ResearchWorkflowStoreError, match="round cap"):
        run_research_audit(
            state,
            ResearchAuditOptions(plan_file=plan, auditor_command="true"),
            cwd=tmp_path,
        )
