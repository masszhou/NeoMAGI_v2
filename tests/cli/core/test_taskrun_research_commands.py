from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from cli.taskrun_commands import _build_parser
from cli.taskrun_research_commands import (
    ResearchCommandResult,
    execute_research_command,
    print_research_result,
)
from test_taskrun_service import _FakeTaskRunRepository, _seed_record, _service


def _parse(argv: list[str]) -> argparse.Namespace:
    return _build_parser("magipi").parse_args(argv)


def test_parser_wires_research_subcommands() -> None:
    args = _parse(["research", "init", "--round-cap", "2"])
    assert args.cmd == "research"
    assert args.research_cmd == "init"
    assert args.round_cap == 2
    args = _parse(["research", "decide", "--decision", "fix_infra", "--rationale", "r"])
    assert args.research_cmd == "decide"
    assert args.evidence_ref == []


def test_init_status_ready_flow_through_cli_layer(tmp_path: Path, capsys) -> None:
    repo = _FakeTaskRunRepository()
    _seed_record(repo, tmp_path)
    service = _service(repo)

    result = execute_research_command(_parse(["research", "init"]), service, tmp_path)
    assert isinstance(result, ResearchCommandResult)
    assert result.exit_code == 0
    assert result.payload["graph"]["ready_nodes"] == ["read_materials"]

    status = execute_research_command(_parse(["research", "status"]), service, tmp_path)
    assert status.payload["decision"] is None
    ready = execute_research_command(_parse(["research", "ready"]), service, tmp_path)
    assert ready.payload["ready_nodes"] == ["read_materials"]

    print_research_result(ready)
    printed = json.loads(capsys.readouterr().out)
    assert printed["ready_nodes"] == ["read_materials"]


def test_research_commands_require_workflow_before_use(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    _seed_record(repo, tmp_path)
    service = _service(repo)
    with pytest.raises(ValueError, match="research init"):
        execute_research_command(_parse(["research", "status"]), service, tmp_path)


def test_transition_commands_flow_and_reject_gate_bypass(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    _seed_record(repo, tmp_path)
    service = _service(repo)
    execute_research_command(_parse(["research", "init"]), service, tmp_path)
    execute_research_command(
        _parse(["research", "start-node", "--node", "read_materials"]),
        service,
        tmp_path,
    )
    done = execute_research_command(
        _parse(
            [
                "research",
                "complete",
                "--node",
                "read_materials",
                "--evidence-ref",
                "notes.md",
            ]
        ),
        service,
        tmp_path,
    )
    assert done.payload["node_id"] == "read_materials"
    with pytest.raises(ValueError, match="requires derived status"):
        execute_research_command(
            _parse(["research", "claim", "--node", "run_experiment_1"]),
            service,
            tmp_path,
        )
