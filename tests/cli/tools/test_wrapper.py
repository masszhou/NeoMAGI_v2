"""D11 wrapper consume-or-legacy + finalize skip-on-pre-resolved behaviour."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from cli.tools.policy_resolution_store import PolicyResolutionStore
from cli.tools.profiles import create_read_only_tools
from cli.tools.wrapper import (
    TaskRunPermissionContext,
)
from policy.audit import InMemoryAuditSink
from policy.permission_profiles import (
    PermissionBudgetState,
    build_permission_profile_snapshot,
)
from policy.types import PolicyDecision


def _read_runtime_tools(
    *,
    cwd: Path,
    taskrun_permission_context: TaskRunPermissionContext,
    audit_sink: InMemoryAuditSink,
):
    return create_read_only_tools(
        cwd,
        runtime_session_id="rt-1",
        run_id="run-1",
        audit_sink=audit_sink,
        taskrun_permission_context=taskrun_permission_context,
    )


def _read_tool(tools):
    return next(tool for tool in tools if tool.name == "read")


def _ctx(
    *,
    task_run_id: str = "019e2300-0000-7000-8000-000000000099",
    step_id: str = "019e2300-0000-7000-8000-000000000098",
    profile_name: str = "guarded",
    recorded: list[dict[str, Any]] | None = None,
    store: PolicyResolutionStore | None = None,
) -> TaskRunPermissionContext:
    def record(**kwargs: Any) -> None:
        if recorded is not None:
            recorded.append(dict(kwargs))

    return TaskRunPermissionContext(
        task_run_id=task_run_id,
        step_id=step_id,
        permission_profile=build_permission_profile_snapshot(profile_name),
        budget=None,
        budget_state=PermissionBudgetState(),
        record_permission_decision=record,
        policy_resolution_store=store,
    )


def test_wrapper_consumes_pre_resolved_policy_and_skips_legacy_recorder(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hi", encoding="utf-8")

    store = PolicyResolutionStore()
    resolved_paths = {"path": str(target)}
    store.put(
        "call_pre",
        raw=PolicyDecision.allow(resolved_paths=resolved_paths),
        resolved=PolicyDecision.allow(
            resolved_paths=resolved_paths,
            audit_tags=["pre_resolved:test"],
        ),
        permission_profile={"name": "guarded"},
    )

    recorded: list[dict[str, Any]] = []
    audit = InMemoryAuditSink()
    context = _ctx(recorded=recorded, store=store)
    tools = _read_runtime_tools(cwd=tmp_path, taskrun_permission_context=context, audit_sink=audit)
    read_tool = _read_tool(tools)

    result = asyncio.run(read_tool.execute("call_pre", {"path": "a.txt"}, None, None))
    assert result.is_error is False
    # Hook pre-resolved → wrapper skipped legacy recorder.
    assert recorded == []
    # Store is empty after consume.
    assert store.has_pending() is False
    # Audit still ran with the pre-resolved decision tags.
    assert audit.records, "audit should record the call"
    audit_record = audit.records[-1]
    assert audit_record.tool_name == "read"
    assert "pre_resolved:test" in audit_record.policy_decision.audit_tags


def test_wrapper_falls_back_to_legacy_recorder_when_store_miss(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hi", encoding="utf-8")

    store = PolicyResolutionStore()
    recorded: list[dict[str, Any]] = []
    audit = InMemoryAuditSink()
    context = _ctx(recorded=recorded, store=store)
    tools = _read_runtime_tools(cwd=tmp_path, taskrun_permission_context=context, audit_sink=audit)
    read_tool = _read_tool(tools)

    result = asyncio.run(read_tool.execute("call_legacy", {"path": "a.txt"}, None, None))
    assert result.is_error is False
    # Store miss → wrapper ran legacy resolver and called recorder.
    assert len(recorded) == 1
    assert recorded[0]["resolved_decision"]["effect"] in {"allow", "block"}


def test_non_taskrun_caller_still_works_without_store(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hi", encoding="utf-8")
    audit = InMemoryAuditSink()
    tools = create_read_only_tools(
        tmp_path,
        runtime_session_id="rt-2",
        run_id="run-2",
        audit_sink=audit,
        # No taskrun_permission_context → wrapper goes legacy without recorder.
    )
    read_tool = _read_tool(tools)
    result = asyncio.run(read_tool.execute("call_outside", {"path": "a.txt"}, None, None))
    assert result.is_error is False
    assert audit.records, "audit fired even in non-TaskRun mode"
