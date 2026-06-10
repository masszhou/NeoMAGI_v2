from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from ai_provider.oauth import (
    OAuthCredentials,
    OAuthError,
    OAuthLoginCallbacks,
    OAuthPrompt,
    get_oauth_provider,
    list_oauth_providers,
    reset_oauth_providers_for_tests,
)
from ai_provider.oauth_github_copilot import (
    COPILOT_HEADERS,
    GITHUB_COPILOT_CLIENT_ID,
    GITHUB_COPILOT_INDIVIDUAL_BASE_URL,
    GITHUB_COPILOT_PROVIDER_ID,
    GitHubCopilotOAuthProvider,
    exchange_github_copilot_token,
    get_github_copilot_urls,
    github_copilot_base_url,
    normalize_domain,
    poll_github_copilot_access_token,
    refresh_github_copilot_credentials_sync,
    start_github_copilot_device_flow,
)

_TOKEN = "tid=abc;exp=123;proxy-ep=proxy.individual.githubcopilot.com;chat=1"


def _make_http(routes: dict[str, Any]):
    """Routed fake of the oauth http_json helper.

    ``routes`` maps a URL fragment to either a static dict (returned every call)
    or a list (popped per call). Records every call for assertions.
    """

    calls: list[SimpleNamespace] = []
    state = {key: list(value) if isinstance(value, list) else value for key, value in routes.items()}

    def http(
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
        error_label: str = "request",
    ) -> Mapping[str, Any]:
        calls.append(
            SimpleNamespace(
                url=url,
                method=method,
                headers=dict(headers or {}),
                form=dict(form or {}),
                error_label=error_label,
            )
        )
        for fragment, value in state.items():
            if fragment in url:
                return value.pop(0) if isinstance(value, list) else value
        raise AssertionError(f"no route for {url}")

    http.calls = calls  # type: ignore[attr-defined]
    return http


def _constant_clock(value: int = 0):
    return lambda: value


def _advancing_clock(start: int = 0, step: int = 1000):
    state = {"t": start}

    def clock() -> int:
        current = state["t"]
        state["t"] += step
        return current

    return clock


# --- pure helpers ------------------------------------------------------------


def test_normalize_domain_accepts_host_and_url_and_rejects_blank() -> None:
    assert normalize_domain("company.ghe.com") == "company.ghe.com"
    assert normalize_domain("https://company.ghe.com/path") == "company.ghe.com"
    assert normalize_domain("  ") is None


def test_github_copilot_base_url_prefers_proxy_ep_then_falls_back() -> None:
    assert github_copilot_base_url(_TOKEN) == "https://api.individual.githubcopilot.com"
    assert github_copilot_base_url(None, "company.ghe.com") == "https://copilot-api.company.ghe.com"
    assert github_copilot_base_url(None) == GITHUB_COPILOT_INDIVIDUAL_BASE_URL


def test_get_github_copilot_urls_uses_enterprise_host() -> None:
    urls = get_github_copilot_urls("company.ghe.com")
    assert urls["device_code"] == "https://company.ghe.com/login/device/code"
    assert urls["copilot_token"] == "https://api.company.ghe.com/copilot_internal/v2/token"


# --- device flow -------------------------------------------------------------


def test_start_device_flow_parses_response_and_sends_client_id() -> None:
    http = _make_http(
        {
            "/login/device/code": {
                "device_code": "dev-1",
                "user_code": "WXYZ-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
                "expires_in": 900,
            }
        }
    )

    device = start_github_copilot_device_flow("github.com", http_json=http)

    assert device.user_code == "WXYZ-1234"
    assert device.device_code == "dev-1"
    assert http.calls[0].form["client_id"] == GITHUB_COPILOT_CLIENT_ID
    assert http.calls[0].form["scope"] == "read:user"


def test_poll_returns_token_after_authorization_pending() -> None:
    http = _make_http(
        {
            "/login/oauth/access_token": [
                {"error": "authorization_pending"},
                {"access_token": "ghu_token"},
            ]
        }
    )

    token = poll_github_copilot_access_token(
        "github.com",
        "dev-1",
        interval=1,
        expires_in=900,
        http_json=http,
        sleep=lambda _s: None,
        now_ms=_constant_clock(),
    )

    assert token == "ghu_token"
    assert len(http.calls) == 2


def test_poll_handles_slow_down_then_succeeds() -> None:
    http = _make_http(
        {
            "/login/oauth/access_token": [
                {"error": "slow_down", "interval": 7},
                {"access_token": "ghu_token"},
            ]
        }
    )

    token = poll_github_copilot_access_token(
        "github.com",
        "dev-1",
        interval=1,
        expires_in=900,
        http_json=http,
        sleep=lambda _s: None,
        now_ms=_constant_clock(),
    )

    assert token == "ghu_token"


def test_poll_raises_on_timeout() -> None:
    http = _make_http({"/login/oauth/access_token": {"error": "authorization_pending"}})

    with pytest.raises(OAuthError, match="timed out"):
        poll_github_copilot_access_token(
            "github.com",
            "dev-1",
            interval=1,
            expires_in=0,
            http_json=http,
            sleep=lambda _s: None,
            now_ms=_advancing_clock(start=0, step=1000),
        )


def test_poll_raises_on_terminal_error() -> None:
    http = _make_http(
        {"/login/oauth/access_token": {"error": "access_denied", "error_description": "user said no"}}
    )

    with pytest.raises(OAuthError, match="access_denied: user said no"):
        poll_github_copilot_access_token(
            "github.com",
            "dev-1",
            interval=1,
            expires_in=900,
            http_json=http,
            sleep=lambda _s: None,
            now_ms=_constant_clock(),
        )


# --- token exchange + refresh ------------------------------------------------


def test_exchange_builds_bearer_get_and_applies_expiry_skew() -> None:
    http = _make_http(
        {"/copilot_internal/v2/token": {"token": _TOKEN, "expires_at": 1000}}
    )

    credentials = exchange_github_copilot_token(
        "ghu_token", http_json=http, now_ms=_constant_clock(0)
    )

    call = http.calls[0]
    assert call.method == "GET"
    assert call.headers["Authorization"] == "Bearer ghu_token"
    for key, value in COPILOT_HEADERS.items():
        assert call.headers[key] == value
    assert credentials.access == _TOKEN
    assert credentials.refresh == "ghu_token"
    # expires_at(1000s) -> 1_000_000 ms minus the 5-minute (300_000 ms) skew.
    assert credentials.expires == 700_000
    assert "enterpriseUrl" not in credentials.extra


def test_exchange_records_enterprise_domain_and_round_trips() -> None:
    http = _make_http(
        {"/copilot_internal/v2/token": {"token": _TOKEN, "expires_at": 1000}}
    )

    credentials = exchange_github_copilot_token(
        "ghu_token", "company.ghe.com", http_json=http, now_ms=_constant_clock(0)
    )

    assert http.calls[0].url == "https://api.company.ghe.com/copilot_internal/v2/token"
    assert credentials.extra["enterpriseUrl"] == "company.ghe.com"
    restored = OAuthCredentials.from_mapping(credentials.to_mapping())
    assert restored.extra["enterpriseUrl"] == "company.ghe.com"


def test_refresh_preserves_enterprise_domain_and_hits_enterprise_endpoint() -> None:
    http = _make_http(
        {"/copilot_internal/v2/token": {"token": _TOKEN, "expires_at": 2000}}
    )
    stored = OAuthCredentials(
        access="expired",
        refresh="ghu_token",
        expires=0,
        extra={"enterpriseUrl": "company.ghe.com"},
    )

    refreshed = refresh_github_copilot_credentials_sync(
        stored, http_json=http, now_ms=_constant_clock(0)
    )

    assert http.calls[0].url == "https://api.company.ghe.com/copilot_internal/v2/token"
    assert http.calls[0].headers["Authorization"] == "Bearer ghu_token"
    assert refreshed.extra["enterpriseUrl"] == "company.ghe.com"
    assert refreshed.access == _TOKEN


# --- provider registry + end-to-end login -----------------------------------


def test_builtin_registry_contains_github_copilot() -> None:
    reset_oauth_providers_for_tests()
    ids = [provider.id for provider in list_oauth_providers()]
    assert GITHUB_COPILOT_PROVIDER_ID in ids
    provider = get_oauth_provider(GITHUB_COPILOT_PROVIDER_ID)
    assert provider.name == "GitHub Copilot"
    assert provider.uses_callback_server is False


def test_provider_login_runs_device_flow_end_to_end() -> None:
    async def run() -> None:
        http = _make_http(
            {
                "/login/device/code": {
                    "device_code": "dev-1",
                    "user_code": "WXYZ-1234",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 1,
                    "expires_in": 900,
                },
                "/login/oauth/access_token": {"access_token": "ghu_token"},
                "/copilot_internal/v2/token": {"token": _TOKEN, "expires_at": 1000},
            }
        )
        provider = GitHubCopilotOAuthProvider(
            http_json=http, sleep=lambda _s: None, now_ms=_constant_clock(0)
        )
        auth_infos: list[Any] = []

        async def on_prompt(_prompt: OAuthPrompt) -> str:
            return ""  # individual account, no enterprise domain

        credentials = await provider.login(
            OAuthLoginCallbacks(
                on_auth=lambda info: auth_infos.append(info),
                on_prompt=on_prompt,
            )
        )

        assert credentials.access == _TOKEN
        assert credentials.refresh == "ghu_token"
        assert auth_infos[0].url == "https://github.com/login/device"
        assert "WXYZ-1234" in (auth_infos[0].instructions or "")

    asyncio.run(run())


def test_provider_login_rejects_invalid_enterprise_domain() -> None:
    async def run() -> None:
        provider = GitHubCopilotOAuthProvider(http_json=_make_http({}))

        async def on_prompt(_prompt: OAuthPrompt) -> str:
            return "::not a domain::"

        with pytest.raises(OAuthError, match="Enterprise"):
            await provider.login(
                OAuthLoginCallbacks(on_auth=lambda _info: None, on_prompt=on_prompt)
            )

    asyncio.run(run())
