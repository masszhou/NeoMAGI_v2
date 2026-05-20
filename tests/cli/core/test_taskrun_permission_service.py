from __future__ import annotations

from pathlib import Path

from policy.permission_profiles import build_permission_profile_snapshot
from test_taskrun_service import _FakeTaskRunRepository, _service


def test_start_persists_explicit_permission_profile_snapshot(tmp_path: Path) -> None:
    repo = _FakeTaskRunRepository()
    service = _service(repo)
    profile = build_permission_profile_snapshot(
        "guarded",
        {"paths": {"allow": ["$WORKSPACE/**"]}},
        sources=["builtin", "project"],
        explicit_scope=True,
        explicit_scope_keys=["paths"],
    )

    result = service.start("Analyze this repo", tmp_path, permission_profile=profile)

    assert result.task_run.permission_profile["name"] == "guarded"
    assert result.task_run.permission_profile["sources"] == ["builtin", "project"]
    assert result.summary["permission_profile"]["name"] == "guarded"
