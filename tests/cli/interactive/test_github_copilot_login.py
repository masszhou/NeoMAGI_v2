from __future__ import annotations

import threading
from pathlib import Path

import pytest

import cli.slash_commands.auth as auth_commands
from ai_provider.auth_storage import AUTH_PATH_ENV, load_auth_storage
from ai_provider.oauth import OAuthCredentials
from ai_provider.oauth_github_copilot import GitHubCopilotDeviceCode


def _device() -> GitHubCopilotDeviceCode:
    return GitHubCopilotDeviceCode(
        device_code="dev-1",
        user_code="WXYZ-1234",
        verification_uri="https://github.com/login/device",
        interval=1,
        expires_in=900,
    )


def test_parse_enterprise_normalizes_and_rejects_bad_input() -> None:
    assert auth_commands._parse_github_copilot_enterprise([]) is None
    assert auth_commands._parse_github_copilot_enterprise(["  "]) is None
    assert auth_commands._parse_github_copilot_enterprise(["company.ghe.com"]) == "company.ghe.com"
    assert (
        auth_commands._parse_github_copilot_enterprise(["https://company.ghe.com/x"])
        == "company.ghe.com"
    )
    with pytest.raises(ValueError, match="Enterprise"):
        auth_commands._parse_github_copilot_enterprise(["::bad::"])


def test_start_login_shows_user_code_and_spawns_for_default_domain(monkeypatch) -> None:
    started_domains: list[str] = []
    monkeypatch.setattr(
        auth_commands,
        "start_github_copilot_device_flow",
        lambda domain: started_domains.append(domain) or _device(),
    )
    done = threading.Event()
    recorded: dict[str, object] = {}

    def fake_complete(domain, enterprise_domain, device, notify):
        recorded["domain"] = domain
        recorded["enterprise"] = enterprise_domain
        auth_commands._github_copilot_login_active = False
        done.set()

    monkeypatch.setattr(auth_commands, "_complete_github_copilot_login", fake_complete)

    try:
        message = auth_commands._start_github_copilot_login([])
        assert "WXYZ-1234" in message
        assert "https://github.com/login/device" in message
        assert started_domains == ["github.com"]
        assert done.wait(timeout=2.0)
        assert recorded == {"domain": "github.com", "enterprise": None}
    finally:
        auth_commands._github_copilot_login_active = False


def test_start_login_uses_enterprise_domain(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_commands, "start_github_copilot_device_flow", lambda domain: _device()
    )
    done = threading.Event()
    recorded: dict[str, object] = {}

    def fake_complete(domain, enterprise_domain, device, notify):
        recorded["domain"] = domain
        recorded["enterprise"] = enterprise_domain
        auth_commands._github_copilot_login_active = False
        done.set()

    monkeypatch.setattr(auth_commands, "_complete_github_copilot_login", fake_complete)

    try:
        auth_commands._start_github_copilot_login(["company.ghe.com"])
        assert done.wait(timeout=2.0)
        assert recorded == {"domain": "company.ghe.com", "enterprise": "company.ghe.com"}
    finally:
        auth_commands._github_copilot_login_active = False


def test_complete_login_polls_exchanges_and_saves(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(AUTH_PATH_ENV, str(tmp_path / "auth.json"))
    monkeypatch.setattr(
        auth_commands,
        "poll_github_copilot_access_token",
        lambda domain, device_code, interval, expires_in: "ghu_token",
    )
    monkeypatch.setattr(
        auth_commands,
        "exchange_github_copilot_token",
        lambda github_token, enterprise_domain: OAuthCredentials(
            access="copilot-token",
            refresh=github_token,
            expires=700_000,
            extra={"enterpriseUrl": enterprise_domain} if enterprise_domain else {},
        ),
    )
    notes: list[tuple[str, str]] = []

    auth_commands._complete_github_copilot_login(
        "company.ghe.com",
        "company.ghe.com",
        _device(),
        notify=lambda message, level: notes.append((message, level)),
    )

    stored = load_auth_storage(tmp_path / "auth.json")["github-copilot"]
    assert stored["access"] == "copilot-token"
    assert stored["refresh"] == "ghu_token"
    assert stored["enterpriseUrl"] == "company.ghe.com"
    assert notes and notes[-1][1] == "info"
    assert auth_commands._github_copilot_login_active is False


def test_complete_login_reports_error_without_leaking_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(AUTH_PATH_ENV, str(tmp_path / "auth.json"))

    def boom(domain, device_code, interval, expires_in):
        raise RuntimeError("device flow timed out")

    monkeypatch.setattr(auth_commands, "poll_github_copilot_access_token", boom)
    notes: list[tuple[str, str]] = []

    auth_commands._complete_github_copilot_login(
        "github.com",
        None,
        _device(),
        notify=lambda message, level: notes.append((message, level)),
    )

    assert notes and notes[-1][1] == "error"
    assert load_auth_storage(tmp_path / "auth.json") == {}
