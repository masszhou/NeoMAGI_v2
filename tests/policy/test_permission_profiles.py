from __future__ import annotations

from pathlib import Path

import pytest

from policy.permission_profiles import (
    PermissionBudgetState,
    PermissionProfileError,
    PermissionProfileResolver,
    build_permission_profile_snapshot,
)
from policy.types import PolicyDecision, PolicyRequest


def test_builtin_guarded_snapshot_is_stable() -> None:
    snapshot = build_permission_profile_snapshot("guarded")

    assert snapshot["name"] == "guarded"
    assert snapshot["nonInteractive"] is True
    assert snapshot["sources"] == ["builtin"]
    assert snapshot["scope"]["paths"]["allow"] == ["$WORKSPACE/**"]
    assert snapshot["scope"]["network"]["mode"] == "deny"


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(PermissionProfileError, match="unknown permission profile"):
        build_permission_profile_snapshot("custom")


def test_network_allow_mode_is_rejected() -> None:
    with pytest.raises(PermissionProfileError, match="network"):
        build_permission_profile_snapshot(
            "full",
            {"network": {"mode": "allow"}},
            sources=["builtin", "project"],
            explicit_scope=True,
            explicit_scope_keys=["network"],
        )


def test_full_requires_explicit_scope() -> None:
    with pytest.raises(PermissionProfileError, match="requires explicit"):
        build_permission_profile_snapshot("full")


def test_full_snapshot_records_settings_sources() -> None:
    snapshot = build_permission_profile_snapshot(
        "full",
        {"scope": {"paths": {"allow": ["$WORKSPACE/**"]}}},
        sources=["builtin", "project"],
        explicit_scope=True,
        explicit_scope_keys=["paths"],
    )

    assert snapshot["name"] == "full"
    assert snapshot["sources"] == ["builtin", "project"]
    assert snapshot["explicitScope"] is True
    assert snapshot["explicitScopeKeys"] == ["paths"]


def test_resolver_allows_guarded_read_scope(tmp_path: Path) -> None:
    resolver = PermissionProfileResolver()
    request = PolicyRequest(
        toolName="read",
        args={"path": "a.txt"},
        cwd=str(tmp_path),
    )

    result = resolver.resolve(
        request,
        PolicyDecision.allow(resolved_paths={"path": str(tmp_path / "a.txt")}),
        build_permission_profile_snapshot("guarded"),
    )

    assert result.resolved_decision.effect == "allow"
    assert "permission:guarded:allow" in result.resolved_decision.audit_tags


def test_resolver_blocks_guarded_write_without_explicit_scope(tmp_path: Path) -> None:
    resolver = PermissionProfileResolver()
    request = PolicyRequest(
        toolName="write",
        args={"path": "a.txt"},
        cwd=str(tmp_path),
    )

    result = resolver.resolve(
        request,
        PolicyDecision.allow(resolved_paths={"path": str(tmp_path / "a.txt")}),
        build_permission_profile_snapshot("guarded"),
    )

    assert result.resolved_decision.effect == "block"
    assert "does not allow write path access" in result.resolved_decision.reason


def test_resolver_full_confirm_allows_explicit_scope(tmp_path: Path) -> None:
    resolver = PermissionProfileResolver()
    request = PolicyRequest(
        toolName="read",
        args={"path": "a.txt"},
        cwd=str(tmp_path),
    )
    profile = build_permission_profile_snapshot(
        "full",
        {"paths": {"allow": ["$WORKSPACE/**"]}},
        sources=["builtin", "project"],
        explicit_scope=True,
        explicit_scope_keys=["paths"],
    )

    result = resolver.resolve(request, PolicyDecision.confirm("needs approval"), profile)

    assert result.resolved_decision.effect == "allow"
    assert result.raw_decision.effect == "confirm"


def test_resolver_full_snapshot_without_explicit_scope_fails_closed(tmp_path: Path) -> None:
    resolver = PermissionProfileResolver()
    request = PolicyRequest(
        toolName="read",
        args={"path": "a.txt"},
        cwd=str(tmp_path),
    )
    profile = {
        "name": "full",
        "nonInteractive": True,
        "scope": {"paths": {"allow": ["$WORKSPACE/**"]}},
        "sources": ["builtin"],
        "explicitScope": False,
    }

    result = resolver.resolve(request, PolicyDecision.confirm("needs approval"), profile)

    assert result.resolved_decision.effect == "block"
    assert "requires explicit" in result.resolved_decision.reason


def test_resolver_interactive_headless_blocks_before_tool(tmp_path: Path) -> None:
    resolver = PermissionProfileResolver()
    request = PolicyRequest(
        toolName="read",
        args={"path": "a.txt"},
        cwd=str(tmp_path),
    )

    result = resolver.resolve(
        request,
        PolicyDecision.allow(),
        build_permission_profile_snapshot("interactive"),
    )

    assert result.resolved_decision.effect == "block"
    assert "cannot run headless" in result.resolved_decision.reason


def test_resolver_preserves_raw_block_reason(tmp_path: Path) -> None:
    resolver = PermissionProfileResolver()
    request = PolicyRequest(
        toolName="read",
        args={"path": "a.txt"},
        cwd=str(tmp_path),
    )

    result = resolver.resolve(
        request,
        PolicyDecision.block("base policy denied"),
        build_permission_profile_snapshot("guarded"),
    )

    assert result.resolved_decision.effect == "block"
    assert result.resolved_decision.reason == "base policy denied"


def test_resolver_reports_budget_exhaustion(tmp_path: Path) -> None:
    resolver = PermissionProfileResolver()
    request = PolicyRequest(
        toolName="write",
        args={"path": "a.txt"},
        cwd=str(tmp_path),
    )

    result = resolver.resolve(
        request,
        PolicyDecision.allow(),
        build_permission_profile_snapshot("guarded"),
        budget={"max_consecutive_denies": 2, "max_total_denies": 20},
        budget_state=PermissionBudgetState(consecutive_denies=1, total_denies=1),
    )

    assert result.resolved_decision.effect == "block"
    assert result.budget_exhausted is True
    assert "permission deny budget exhausted" in result.resolved_decision.reason
