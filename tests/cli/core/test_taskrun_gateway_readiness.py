from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from cli.core.taskrun_autorun import TaskRunAutoRunOptions
from cli.core.taskrun_event_payloads import (
    TASK_TOOL_OBSERVED,
    derived_payload,
)
from cli.core.taskrun_host_contract import (
    CALLER_PROVENANCE_PAYLOAD_KEY,
    DERIVED_EVENT_TYPES,
    DERIVED_STEP_SUMMARY_EVENT_TYPES,
    DERIVED_TIER1_ONLY_EVENT_TYPES,
    PAYLOAD_VERSIONS,
    TASKRUN_OPERATION_MANIFEST,
    TASKRUN_OPERATION_MANIFEST_BY_NAME,
    TaskRunHostContext,
    event_snapshots,
    operation_snapshot,
)
from cli.core.taskrun_service import (
    TaskRunRuntimeOptions,
    TaskRunService,
    TaskRunServiceError,
    TaskRunStepOutcome,
)
from cli.core.taskrun_views import KEY_HISTORY_EVENT_TYPES
from storage.taskrun_repository import TaskEventRecord
from test_taskrun_service import (
    _FakeRunner,
    _FakeTaskRunRepository,
    _guarded_profile,
    _seed_record,
    _service,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_operation_manifest_matches_current_service_surface() -> None:
    implemented = {
        "start",
        "status",
        "summary",
        "list",
        "history",
        "next",
        "events",
        "step",
        "run",
        "close",
    }
    covered_by = {"set_permission_profile", "resume"}
    deferred_to_p3 = {"cancel", "archive", "cleanup"}

    manifest = TASKRUN_OPERATION_MANIFEST_BY_NAME
    assert set(manifest) == implemented | covered_by | deferred_to_p3 | {
        "compaction_in_headless_step"
    }
    assert {name for name, item in manifest.items() if item.status == "implemented"} == implemented
    assert {name for name, item in manifest.items() if item.status == "covered_by"} == covered_by
    assert {
        name for name, item in manifest.items() if item.status == "deferred_to_p3"
    } == deferred_to_p3
    assert manifest["compaction_in_headless_step"].status == "deferred_to_p2_followup"
    assert "D14 emitter" in manifest["compaction_in_headless_step"].evidence_or_reason

    for operation in implemented:
        assert hasattr(TaskRunService, operation), operation
    for operation in deferred_to_p3:
        assert not hasattr(TaskRunService, operation), operation


def test_manifest_covered_operations_reference_current_cli_paths() -> None:
    manifest = TASKRUN_OPERATION_MANIFEST_BY_NAME
    assert "run --permission" in manifest["set_permission_profile"].evidence_or_reason
    assert "step/run <id>" in manifest["resume"].evidence_or_reason
    for item in TASKRUN_OPERATION_MANIFEST:
        assert item.evidence_or_reason
        assert "HTTP" not in item.evidence_or_reason
        assert "WebSocket" not in item.evidence_or_reason
        assert "channel" not in item.operation.lower()


def test_event_taxonomy_closed_set() -> None:
    assert DERIVED_EVENT_TYPES == {
        "task_tool_observed",
        "task_tool_policy_resolved",
        "task_tool_policy_blocked",
        "task_runtime_compaction_observed",
        "task_runtime_auto_retry_observed",
        "task_step_evidence_recorded",
        "task_step_evidence_missing",
        "task_step_blocker_detected",
        "task_step_outcome_supported",
        "task_step_outcome_unsupported",
        "task_step_resume_context_generated",
    }


def test_key_history_does_not_include_tool_detail() -> None:
    assert DERIVED_STEP_SUMMARY_EVENT_TYPES <= KEY_HISTORY_EVENT_TYPES
    assert DERIVED_TIER1_ONLY_EVENT_TYPES.isdisjoint(KEY_HISTORY_EVENT_TYPES)


def test_payload_version_per_event_type() -> None:
    assert set(PAYLOAD_VERSIONS) == DERIVED_EVENT_TYPES
    assert all(isinstance(version, int) and version >= 1 for version in PAYLOAD_VERSIONS.values())
    stamped = derived_payload(TASK_TOOL_OBSERVED, {"tool_call_id": "tc1"})
    assert stamped["payload_version"] == PAYLOAD_VERSIONS[TASK_TOOL_OBSERVED]
    assert "schema_version" not in stamped


def test_operation_snapshot_includes_verification_block(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    service.start(
        "Verify the repo",
        tmp_path,
        permission_profile=_guarded_profile(),
    )
    runner = _FakeRunner(
        TaskRunStepOutcome(
            status="done",
            assistant_text="Verified with tests.",
            verification_state="supported",
            verification_reason="pytest passed",
        )
    )

    result = service.step(
        None,
        tmp_path,
        runtime_options=TaskRunRuntimeOptions(model_ref="faux/local/faux-1"),
        runner=runner,
    )
    snapshot = operation_snapshot("step", result)

    assert snapshot.task_run_id == result.task_run.id
    assert snapshot.status == "pending"
    assert snapshot.step_id == result.step.id
    assert snapshot.step_verification_state == {
        "state": "supported",
        "reason": "pytest passed",
    }
    assert "missing_kinds" not in snapshot.step_verification_state
    assert "inconsistent_kinds" not in snapshot.step_verification_state
    json.dumps(snapshot.to_dict(), sort_keys=True)


def test_auto_run_snapshot_includes_iterations_and_stop_reason(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start(
        "Run once",
        tmp_path,
        permission_profile=_guarded_profile(),
    )

    result = service.run(
        started.task_run.id,
        tmp_path,
        options=TaskRunAutoRunOptions(
            max_steps=1,
            runtime_options=TaskRunRuntimeOptions(model_ref="faux/local/faux-1"),
        ),
        runner=_FakeRunner(TaskRunStepOutcome(status="done", assistant_text="done")),
    )
    snapshot = operation_snapshot("run", result)

    assert snapshot.stop_reason == "max_steps_reached"
    assert snapshot.iteration_count == 1
    assert snapshot.experiment_attempt_count == 0
    assert snapshot.event_count == len(result.events)
    json.dumps(snapshot.to_dict(), sort_keys=True)


def test_event_snapshots_do_not_depend_on_projection_file(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start("Inspect events", tmp_path)
    started.projection.events_path.unlink()

    result = service.events(started.task_run.id, tmp_path)
    snapshots = event_snapshots(result)

    assert started.projection.events_path.is_file() is False
    assert [snapshot.event_type for snapshot in snapshots] == [
        event.event_type for event in result.events
    ]
    json.dumps([snapshot.to_dict() for snapshot in snapshots], sort_keys=True)


def test_event_cursor_uses_composite_ordering_and_limit(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path)
    same_time = "2026-05-13T00:00:00+00:00"
    first = repo.append_event(
        task_run_id=record.id,
        event_type="first",
        payload={},
        occurred_at=same_time,
        event_id="019e2200-0000-7000-8000-000000000001",
    )
    third = repo.append_event(
        task_run_id=record.id,
        event_type="third",
        payload={},
        occurred_at=same_time,
        event_id="019e2200-0000-7000-8000-000000000003",
    )
    second = repo.append_event(
        task_run_id=record.id,
        event_type="second",
        payload={},
        occurred_at=same_time,
        event_id="019e2200-0000-7000-8000-000000000002",
    )

    assert [event.id for event in repo.list_events(record.id)] == [
        first.id,
        second.id,
        third.id,
    ]
    result = _service(repo).events(
        record.id,
        tmp_path,
        after_event_id=first.id,
        limit=1,
    )

    assert [event.id for event in result.events] == [second.id]


def test_event_cursor_fails_closed_for_unknown_or_wrong_taskrun(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    first = _seed_record(repo, tmp_path, task_id="019e2200-0000-7000-8000-000000000111")
    second = _seed_record(repo, tmp_path, task_id="019e2200-0000-7000-8000-000000000112")
    other_event = repo.append_event(
        task_run_id=second.id,
        event_type="other",
        payload={},
        event_id="019e2200-0000-7000-8000-000000000999",
    )

    with pytest.raises(KeyError):
        repo.list_events(
            first.id,
            after_event_id="019e2200-0000-7000-8000-000000000998",
        )
    with pytest.raises(ValueError, match="does not belong"):
        repo.list_events(first.id, after_event_id=other_event.id)


def test_caller_provenance_is_operation_event_metadata(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)

    result = service.start(
        "Track provenance",
        tmp_path,
        host_context={
            "source": "test",
            "request_id": "req-1",
            "actor": "local-test",
        },
    )
    started_event = repo.list_events(result.task_run.id)[0]

    assert started_event.payload[CALLER_PROVENANCE_PAYLOAD_KEY] == {
        "source": "test",
        "request_id": "req-1",
        "actor": "local-test",
    }
    with pytest.raises(ValueError, match="unknown TaskRun host context source"):
        TaskRunHostContext(source="remote")
    with pytest.raises(ValueError, match="unknown TaskRun host context field"):
        TaskRunHostContext.from_mapping({"source": "test", "principal": "p1"})


def test_invalid_provenance_fails_before_taskrun_write(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()

    with pytest.raises(ValueError, match="unknown TaskRun host context field"):
        _service(repo).start(
            "Should not be created",
            tmp_path,
            host_context={"source": "test", "principal": "p1"},
        )

    assert repo.runs == {}
    assert repo.events == []


def test_provenance_does_not_bypass_permission_profile(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    started = service.start(
        "Interactive should still block headless step",
        tmp_path,
        host_context={"source": "host", "actor": "debug-admin"},
    )

    with pytest.raises(TaskRunServiceError, match="interactive permission profile"):
        service.step(
            started.task_run.id,
            tmp_path,
            runtime_options=TaskRunRuntimeOptions(model_ref="faux/local/faux-1"),
            runner=_FakeRunner(TaskRunStepOutcome(status="done")),
            host_context={"source": "host", "actor": "debug-admin"},
        )


def test_provenance_is_not_added_to_derived_payloads() -> None:
    for event_type in DERIVED_EVENT_TYPES:
        payload = derived_payload(event_type, {})
        assert CALLER_PROVENANCE_PAYLOAD_KEY not in payload


def test_taskrun_schema_does_not_add_provenance_columns() -> None:
    source = (REPO_ROOT / "packages/magipi/src/storage/schema.py").read_text(
        encoding="utf-8"
    )
    for table in ("task_runs", "task_steps"):
        table_sql = _create_table_sql(source, table)
        for column in ("caller_provenance", "request_id", "actor"):
            assert column not in table_sql


def test_taskrun_core_import_boundary() -> None:
    violations: list[str] = []
    for path in _taskrun_core_paths():
        for imported in _imported_modules(path):
            if _is_denied_import(imported):
                violations.append(f"{path.relative_to(REPO_ROOT)} imports {imported}")

    assert violations == []


def test_snapshot_helper_does_not_import_live_runtime_state() -> None:
    path = REPO_ROOT / "packages/magipi/src/cli/core/taskrun_host_contract.py"
    imported = set(_imported_modules(path))

    assert "cli.core.taskrun_agent_session" not in imported
    assert "cli.core.taskrun_policy_hook" not in imported
    assert "cli.tools.policy_resolution_store" not in imported


def test_cli_adapter_does_not_use_snapshot_as_render_path() -> None:
    source = (REPO_ROOT / "packages/magipi/src/cli/taskrun_commands.py").read_text(
        encoding="utf-8"
    )

    assert "operation_snapshot" not in source
    assert "TaskRunOperationSnapshot" not in source
    assert "gateway" not in source.lower()
    assert "channel" not in source.lower()


def test_d14_headless_trigger_remains_deferred_but_emitter_exists() -> None:
    runner_source = (
        REPO_ROOT / "packages/magipi/src/cli/core/taskrun_runner.py"
    ).read_text(encoding="utf-8")
    emitter_source = (
        REPO_ROOT / "packages/magipi/src/cli/core/compaction_event_emitter.py"
    ).read_text(encoding="utf-8")

    assert "TaskRunCompactionEventEmitter" not in runner_source
    assert "recover_assistant_response" not in runner_source
    assert "TaskRunCompactionEventEmitter" in emitter_source


def test_actor_and_source_are_not_used_as_access_control_branches() -> None:
    pattern = re.compile(r"\b(actor|source)\s*(==|!=| in | not in )")
    violations: list[str] = []
    for path in _taskrun_core_paths():
        if path.name == "taskrun_host_contract.py":
            continue
        source = path.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            violations.append(f"{path.relative_to(REPO_ROOT)}:{match.group(0)}")

    assert violations == []


def test_event_payload_carries_payload_version_per_event_type() -> None:
    event = TaskEventRecord(
        id="019e2200-0000-7000-8000-000000000010",
        task_run_id="019e2200-0000-7000-8000-000000000011",
        step_id=None,
        event_type=TASK_TOOL_OBSERVED,
        payload=derived_payload(TASK_TOOL_OBSERVED, {"tool_call_id": "tc1"}),
        occurred_at="2026-05-13T00:00:00+00:00",
    )

    snapshot = event_snapshots(type("Result", (), {"events": [event]})())[0]

    assert snapshot.payload["payload_version"] == PAYLOAD_VERSIONS[TASK_TOOL_OBSERVED]


def _taskrun_core_paths() -> list[Path]:
    paths: list[Path] = []
    patterns = [
        "packages/magipi/src/cli/core/taskrun*.py",
        "packages/magipi/src/cli/core/evidence_classifier.py",
        "packages/magipi/src/cli/core/compaction_event_emitter.py",
        "packages/magipi/src/cli/tools/policy_resolution_store.py",
        "packages/magipi/src/storage/taskrun*.py",
    ]
    for pattern in patterns:
        paths.extend(REPO_ROOT.glob(pattern))
    return sorted(set(paths))


def _create_table_sql(source: str, table: str) -> str:
    pattern = re.compile(
        rf"CREATE TABLE IF NOT EXISTS \{{schema\}}\.\s*{table}\("
        r"(?P<body>.*?)\n    \)",
        re.DOTALL,
    )
    match = pattern.search(source)
    assert match is not None
    return match.group("body")


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def _is_denied_import(module: str) -> bool:
    denied_exact = {
        "argparse",
        "sys",
        "fastapi",
        "starlette",
        "websocket",
        "websockets",
        "cli.taskrun_commands",
        "cli.interactive.runtime",
        "cli.interactive.commands",
    }
    denied_prefixes = (
        "gateway",
        "channels",
        "telegram",
        "slack",
        "webchat",
        "cli.tui",
        "cli.interactive.tui_",
        "cli.interactive.render",
        "cli.interactive.controller",
    )
    lowered = module.lower()
    return module in denied_exact or lowered.startswith(denied_prefixes)
