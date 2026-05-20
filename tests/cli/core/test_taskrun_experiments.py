from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import cli.core.taskrun_experiment_loop as taskrun_experiment_loop
import cli.core.taskrun_experiment_trial as taskrun_experiment_trial
from cli.core.taskrun_autorun import TaskRunAutoRunOptions
from cli.core.taskrun_experiments import (
    HostCommandResult,
    MetricParseError,
    TaskRunExperimentOptions,
    parse_metric_lines,
    run_host_command,
)
from cli.core.taskrun_service import (
    TaskRunRuntimeOptions,
    TaskRunServiceError,
    TaskRunStepContext,
    TaskRunStepOutcome,
)
from policy.audit import InMemoryAuditSink
from policy.permission_profiles import build_permission_profile_snapshot
from test_taskrun_service import _FakeTaskRunRepository, _seed_record, _service


class _Runner:
    def __init__(self, outcome: TaskRunStepOutcome) -> None:
        self.outcome = outcome
        self.contexts: list[TaskRunStepContext] = []

    def run(self, context: TaskRunStepContext) -> TaskRunStepOutcome:
        self.contexts.append(context)
        context.heartbeat()
        return self.outcome


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("METRIC latency_ms=120\n", {"latency_ms": 120.0}),
        ("noise\nMETRIC score=0.25\nMETRIC aux.value=2\n", {"score": 0.25, "aux.value": 2.0}),
        ("METRIC micro\u00b5=1\n", {"micro\u00b5": 1.0}),
    ],
)
def test_metric_parser_accepts_strict_metric_lines(
    output: str,
    expected: dict[str, float],
) -> None:
    assert parse_metric_lines(output) == expected


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ("METRIC latency_ms=NaN\n", "non_finite_metric_value"),
        ("METRIC latency_ms=inf\n", "non_finite_metric_value"),
        ("METRIC latency_ms=120ms\n", "invalid_metric_value"),
        ("METRIC __proto__=1\n", "invalid_metric_name"),
        ("METRIC constructor=1\n", "invalid_metric_name"),
        ("METRIC latency_ms=1\nMETRIC latency_ms=2\n", "duplicate_metric"),
        ("METRIC missing_value=\n", "empty_metric_value"),
    ],
)
def test_metric_parser_rejects_unsafe_or_ambiguous_lines(
    output: str,
    code: str,
) -> None:
    with pytest.raises(MetricParseError) as exc:
        parse_metric_lines(output)

    assert exc.value.code == code


def test_host_command_records_extension_permission_source(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    profile = build_permission_profile_snapshot(
        "full",
        {
            "paths": {"allow": ["$WORKSPACE/**"]},
            "commands": {"allow": ["printf"]},
        },
        sources=["builtin", "project"],
        explicit_scope=True,
        explicit_scope_keys=["commands", "paths"],
    )
    record = _seed_record(repo, tmp_path, permission_profile=profile)
    service = _service(repo)
    _summary, running, step = service._start_step(record, TaskRunRuntimeOptions())

    result = run_host_command(
        service,
        running,
        step,
        auto_run_id="019e2200-0000-7000-8000-000000000999",
        phase="baseline",
        command="printf 'METRIC latency_ms=1\\n'",
    )

    assert result.succeeded is True
    assert result.duration_ms >= 0
    decision = repo.permission_decisions[0]
    assert decision.tool_execution_id is None
    assert decision.step_id == step.id
    assert decision.policy_request["actor"] == "extension"
    assert decision.policy_request["source"]["host"] == "task_run"
    assert decision.policy_request["source"]["decision_subject"] == "host_command"
    assert decision.policy_request["source"]["phase"] == "baseline"
    assert decision.resolved_decision["effect"] == "allow"


def test_host_command_records_audit_sink_with_redacted_preview(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    sink = InMemoryAuditSink()
    profile = build_permission_profile_snapshot(
        "full",
        {
            "paths": {"allow": ["$WORKSPACE/**"]},
            "commands": {"allow": ["printf"]},
        },
        sources=["builtin", "project"],
        explicit_scope=True,
        explicit_scope_keys=["commands", "paths"],
    )
    record = _seed_record(repo, tmp_path, permission_profile=profile)
    service = _service(repo)
    service.host_command_audit_sink = sink
    _summary, running, step = service._start_step(record, TaskRunRuntimeOptions())
    secret = "sk-" + ("A" * 40)

    result = run_host_command(
        service,
        running,
        step,
        auto_run_id="019e2200-0000-7000-8000-000000000999",
        phase="baseline",
        command=f"printf '{secret}\\n'",
    )

    assert result.duration_ms >= 0
    assert len(sink.records) == 1
    record = sink.records[0]
    assert record.tool_name == "host_command"
    assert record.args["phase"] == "baseline"
    assert secret not in record.args["commandPreview"]
    assert record.duration_ms == result.duration_ms


def test_experiment_auto_run_records_fresh_baseline_and_keep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path, permission_profile=_experiment_profile())
    service = _service(repo)
    _stub_diff(monkeypatch, safe=True)
    _stub_benchmarks(
        monkeypatch,
        {
            "baseline": ["METRIC latency_ms=120\n"],
            "trial": ["METRIC latency_ms=110\n"],
        },
    )

    result = service.run(
        record.id,
        tmp_path,
        options=TaskRunAutoRunOptions(
            1,
            TaskRunRuntimeOptions(),
            TaskRunExperimentOptions(
                benchmark_command="python bench.py",
                primary_metric="latency_ms",
                metric_direction="lower",
            ),
        ),
        runner=_Runner(TaskRunStepOutcome(status="done", assistant_text="changed code")),
    )

    assert result.stop_reason == "max_steps_reached"
    assert result.task_run.status == "pending"
    assert [experiment.decision for experiment in repo.experiments] == ["baseline", "keep"]
    assert result.task_run.summary["current_best"]["value"] == 110.0
    started = [
        event
        for event in repo.events
        if event.event_type == "task_run_auto_run_started"
    ][0]
    assert started.payload["experiment"]["enabled"] is True
    assert started.payload["experiment"]["primaryMetric"] == "latency_ms"
    baseline_event = [
        event
        for event in repo.events
        if event.event_type == "task_experiment_baseline_recorded"
    ][0]
    assert baseline_event.payload["trial_value"] is None
    assert baseline_event.payload["delta"] is None
    assert baseline_event.payload["diff_ref"] == {}


def test_experiment_baseline_metric_parse_failure_blocks_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path, permission_profile=_experiment_profile())
    service = _service(repo)
    _stub_benchmarks(monkeypatch, {"baseline": ["no metric\n"]})

    result = service.run(
        record.id,
        tmp_path,
        options=TaskRunAutoRunOptions(
            1,
            TaskRunRuntimeOptions(),
            TaskRunExperimentOptions(
                benchmark_command="python bench.py",
                primary_metric="latency_ms",
                metric_direction="lower",
            ),
        ),
        runner=_Runner(TaskRunStepOutcome(status="done", assistant_text="unused")),
    )

    assert result.exit_code == 1
    assert result.stop_reason == "metric_parse_failed"
    assert result.task_run.status == "blocked"
    assert repo.experiments[0].decision == "blocked"
    assert repo.experiments[0].result["stopReason"] == "metric_parse_failed"
    assert repo.list_steps(record.id)[0].status == "blocked"


def test_experiment_run_fails_fast_when_profile_denies_git(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    profile = build_permission_profile_snapshot(
        "full",
        {
            "paths": {"allow": ["$WORKSPACE/**"]},
            "commands": {"allow": ["python"]},
        },
        sources=["builtin", "project"],
        explicit_scope=True,
        explicit_scope_keys=["commands", "paths"],
    )
    record = _seed_record(repo, tmp_path, permission_profile=profile)
    service = _service(repo)

    with pytest.raises(TaskRunServiceError, match="git rev-parse"):
        service.run(
            record.id,
            tmp_path,
            options=TaskRunAutoRunOptions(
                1,
                TaskRunRuntimeOptions(),
                TaskRunExperimentOptions(
                    benchmark_command="python bench.py",
                    primary_metric="latency_ms",
                    metric_direction="lower",
                ),
            ),
            runner=_Runner(TaskRunStepOutcome(status="done")),
        )

    assert repo.list_steps(record.id) == []
    assert repo.permission_decisions == []
    assert repo.experiments == []
    assert all(
        event.event_type != "task_run_auto_run_started" for event in repo.events
    )


def test_experiment_preflight_failure_does_not_update_run_profile(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    original_profile = _experiment_profile()
    denied_profile = build_permission_profile_snapshot(
        "full",
        {
            "paths": {"allow": ["$WORKSPACE/**"]},
            "commands": {"allow": ["python"]},
        },
        sources=["builtin", "project"],
        explicit_scope=True,
        explicit_scope_keys=["commands", "paths"],
    )
    record = _seed_record(repo, tmp_path, permission_profile=original_profile)
    service = _service(repo)

    with pytest.raises(TaskRunServiceError, match="git rev-parse"):
        service.run(
            record.id,
            tmp_path,
            permission_profile=denied_profile,
            options=TaskRunAutoRunOptions(
                1,
                TaskRunRuntimeOptions(),
                TaskRunExperimentOptions(
                    benchmark_command="python bench.py",
                    primary_metric="latency_ms",
                    metric_direction="lower",
                ),
            ),
            runner=_Runner(TaskRunStepOutcome(status="done")),
        )

    assert repo.runs[record.id].permission_profile == original_profile
    assert repo.list_steps(record.id) == []
    assert repo.permission_decisions == []
    assert repo.experiments == []
    assert all(
        event.event_type != "task_run_permission_profile_updated"
        for event in repo.events
    )
    assert all(
        event.event_type != "task_run_auto_run_started" for event in repo.events
    )


def test_experiment_regression_with_safe_revert_records_revert_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeTaskRunRepository()
    record = _seed_record(repo, tmp_path, permission_profile=_experiment_profile())
    service = _service(repo)
    _stub_diff(monkeypatch, safe=True)
    _stub_benchmarks(
        monkeypatch,
        {
            "baseline": ["METRIC latency_ms=100\n"],
            "trial": ["METRIC latency_ms=125\n"],
            "revert": [""],
        },
    )

    result = service.run(
        record.id,
        tmp_path,
        options=TaskRunAutoRunOptions(
            1,
            TaskRunRuntimeOptions(),
            TaskRunExperimentOptions(
                benchmark_command="python bench.py",
                primary_metric="latency_ms",
                metric_direction="lower",
                revert_on_regression=True,
            ),
        ),
        runner=_Runner(TaskRunStepOutcome(status="done", assistant_text="changed code")),
    )

    assert result.exit_code == 1
    assert result.stop_reason == "experiment_reverted"
    assert result.task_run.status == "blocked"
    assert [experiment.decision for experiment in repo.experiments] == ["baseline", "revert"]
    assert repo.experiments[-1].result["revertCommand"]["policyEffect"] == "allow"


def _experiment_profile() -> dict[str, Any]:
    return build_permission_profile_snapshot(
        "full",
        {
            "paths": {"allow": ["$WORKSPACE/**"]},
            "commands": {"allow": ["python", "git"]},
            "git": {"allowReset": False},
        },
        sources=["builtin", "project"],
        explicit_scope=True,
        explicit_scope_keys=["commands", "paths"],
    )


def _stub_diff(monkeypatch: pytest.MonkeyPatch, *, safe: bool) -> None:
    def snapshot(*_args, **_kwargs):
        return {
            "git_available": True,
            "git_head": "abc123",
            "status": [],
            "safe_revert_supported": True,
            "unsafe_revert_reason": None,
        }

    monkeypatch.setattr(taskrun_experiment_loop, "capture_workspace_snapshot", snapshot)
    monkeypatch.setattr(taskrun_experiment_trial, "capture_workspace_snapshot", snapshot)
    monkeypatch.setattr(
        taskrun_experiment_loop,
        "capture_diff_ref",
        lambda *_args, **_kwargs: {
            "git_head": "abc123",
            "status_before": [],
            "status_after": [" M file.py"],
            "changed_paths": ["file.py"],
            "diff_sha256": "sha",
            "diff_preview": "diff --git a/file.py b/file.py",
            "safe_revert_supported": safe,
            "unsafe_revert_reason": None if safe else "untracked files",
        },
    )


def _stub_benchmarks(
    monkeypatch: pytest.MonkeyPatch,
    outputs: dict[str, list[str]],
) -> None:
    def fake_run_host_command(
        _service,
        _task_run,
        _step,
        *,
        auto_run_id: str,
        phase: str,
        command: str,
        timeout: float = 120,
    ) -> HostCommandResult:
        del auto_run_id, timeout
        output = outputs[phase].pop(0)
        return HostCommandResult(
            phase=phase,
            command=command,
            output=output,
            exit_code=0,
            cancelled=False,
            timed_out=False,
            policy_effect="allow",
            reason=None,
            permission_decision_id=None,
        )

    monkeypatch.setattr(taskrun_experiment_loop, "run_host_command", fake_run_host_command)
    monkeypatch.setattr(taskrun_experiment_trial, "run_host_command", fake_run_host_command)
    if "revert" in outputs:
        monkeypatch.setattr(
            taskrun_experiment_trial,
            "run_safe_revert",
            lambda _service, _task_run, _step, *, auto_run_id, diff_ref: HostCommandResult(
                phase="revert",
                command="git diff --binary --no-ext-diff | git apply -R",
                output=outputs["revert"].pop(0),
                exit_code=0,
                cancelled=False,
                timed_out=False,
                policy_effect="allow",
                reason=None,
                permission_decision_id=None,
            ),
        )
