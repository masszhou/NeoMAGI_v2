from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from ai_provider.oauth import (
    OPENAI_CODEX_AUTHORIZE_URL,
    OPENAI_CODEX_CLIENT_ID,
    OPENAI_CODEX_JWT_CLAIM_PATH,
    OPENAI_CODEX_REDIRECT_URI,
    OPENAI_CODEX_TOKEN_URL,
    OPENAI_OAUTH_PROVIDER_ID,
    OAuthCredentials,
    OAuthLoginCallbacks,
    OAuthPrompt,
    OpenAIOAuthProvider,
    get_oauth_api_key,
    get_oauth_provider,
    list_oauth_providers,
    parse_authorization_input,
    register_oauth_provider,
    reset_oauth_providers_for_tests,
)


class FakeCallbackServer:
    def __init__(self, code: str | None = None) -> None:
        self.code = code
        self.closed = False
        self.cancelled = False

    def cancel_wait(self) -> None:
        self.cancelled = True

    async def wait_for_code(self) -> str | None:
        return self.code

    def close(self) -> None:
        self.closed = True


def _jwt_with_account(account_id: str) -> str:
    header = {"alg": "none", "typ": "JWT"}
    payload = {
        OPENAI_CODEX_JWT_CLAIM_PATH: {
            "chatgpt_account_id": account_id,
        }
    }
    return f"{_b64_json(header)}.{_b64_json(payload)}.signature"


def _b64_json(data: Mapping[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_builtin_oauth_registry_is_openai_only() -> None:
    reset_oauth_providers_for_tests()

    assert [provider.id for provider in list_oauth_providers()] == [
        OPENAI_OAUTH_PROVIDER_ID
    ]
    assert get_oauth_provider("openai").name == "OpenAI (Codex OAuth)"
    with pytest.raises(KeyError):
        get_oauth_provider("anthropic")


def test_parse_authorization_input_accepts_url_query_and_raw_code() -> None:
    assert parse_authorization_input("code-1").code == "code-1"
    assert parse_authorization_input("code-2#state-2").state == "state-2"

    parsed = parse_authorization_input(
        "http://localhost:1455/auth/callback?code=code-3&state=state-3"
    )
    assert parsed.code == "code-3"
    assert parsed.state == "state-3"

    parsed = parse_authorization_input("code=code-4&state=state-4")
    assert parsed.code == "code-4"
    assert parsed.state == "state-4"


def test_openai_login_uses_pkce_manual_fallback_and_exchanges_code() -> None:
    async def run() -> None:
        states: list[str] = []
        auth_urls: list[str] = []
        token_posts: list[tuple[str, Mapping[str, str]]] = []
        fake_server = FakeCallbackServer()

        async def fake_token_post(
            url: str,
            data: Mapping[str, str],
        ) -> Mapping[str, Any]:
            token_posts.append((url, dict(data)))
            return {
                "access_token": _jwt_with_account("acct-123"),
                "refresh_token": "refresh-1",
                "expires_in": 3600,
            }

        provider = OpenAIOAuthProvider(
            token_post=fake_token_post,
            callback_server_factory=lambda state: states.append(state) or fake_server,
            now_ms=lambda: 10_000,
        )

        async def on_prompt(prompt: OAuthPrompt) -> str:
            assert "authorization code" in prompt.message
            return (
                "http://localhost:1455/auth/callback"
                f"?code=manual-code&state={states[0]}"
            )

        credentials = await provider.login(
            OAuthLoginCallbacks(
                on_auth=lambda info: auth_urls.append(info.url),
                on_prompt=on_prompt,
            )
        )

        assert fake_server.closed is True
        assert credentials.access == _jwt_with_account("acct-123")
        assert credentials.refresh == "refresh-1"
        assert credentials.expires == 3_610_000
        assert credentials.account_id == "acct-123"

        parsed_auth = urlparse(auth_urls[0])
        auth_params = parse_qs(parsed_auth.query)
        assert f"{parsed_auth.scheme}://{parsed_auth.netloc}{parsed_auth.path}" == (
            OPENAI_CODEX_AUTHORIZE_URL
        )
        assert auth_params["client_id"] == [OPENAI_CODEX_CLIENT_ID]
        assert auth_params["redirect_uri"] == [OPENAI_CODEX_REDIRECT_URI]
        assert auth_params["code_challenge_method"] == ["S256"]
        assert auth_params["state"] == [states[0]]
        assert auth_params["codex_cli_simplified_flow"] == ["true"]
        assert auth_params["originator"] == ["pi"]
        assert auth_params["code_challenge"][0]

        assert token_posts == [
            (
                OPENAI_CODEX_TOKEN_URL,
                {
                    "grant_type": "authorization_code",
                    "client_id": OPENAI_CODEX_CLIENT_ID,
                    "code": "manual-code",
                    "code_verifier": token_posts[0][1]["code_verifier"],
                    "redirect_uri": OPENAI_CODEX_REDIRECT_URI,
                },
            )
        ]
        assert token_posts[0][1]["code_verifier"]

    asyncio.run(run())


def test_openai_refresh_posts_refresh_grant() -> None:
    async def run() -> None:
        token_posts: list[tuple[str, Mapping[str, str]]] = []

        async def fake_token_post(
            url: str,
            data: Mapping[str, str],
        ) -> Mapping[str, Any]:
            token_posts.append((url, dict(data)))
            return {
                "access_token": _jwt_with_account("acct-refreshed"),
                "refresh_token": "refresh-new",
                "expires_in": 120,
            }

        provider = OpenAIOAuthProvider(token_post=fake_token_post, now_ms=lambda: 1_000)
        credentials = await provider.refresh_token(
            OAuthCredentials(access="old", refresh="refresh-old", expires=0)
        )

        assert credentials.access == _jwt_with_account("acct-refreshed")
        assert credentials.refresh == "refresh-new"
        assert credentials.expires == 121_000
        assert credentials.account_id == "acct-refreshed"
        assert token_posts == [
            (
                OPENAI_CODEX_TOKEN_URL,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": "refresh-old",
                    "client_id": OPENAI_CODEX_CLIENT_ID,
                },
            )
        ]

    asyncio.run(run())


def test_get_oauth_api_key_refreshes_expired_credentials() -> None:
    async def run() -> None:
        reset_oauth_providers_for_tests()
        refresh_calls = 0

        async def fake_token_post(
            url: str,
            data: Mapping[str, str],
        ) -> Mapping[str, Any]:
            nonlocal refresh_calls
            refresh_calls += 1
            assert data["grant_type"] == "refresh_token"
            return {
                "access_token": _jwt_with_account("acct-fresh"),
                "refresh_token": "refresh-fresh",
                "expires_in": 60,
            }

        register_oauth_provider(
            OpenAIOAuthProvider(token_post=fake_token_post, now_ms=lambda: 1_000)
        )

        result = await get_oauth_api_key(
            "openai",
            {
                "openai": OAuthCredentials(
                    access="expired",
                    refresh="refresh-old",
                    expires=999,
                )
            },
            now_ms=lambda: 1_000,
        )

        assert result is not None
        assert result.api_key == _jwt_with_account("acct-fresh")
        assert result.new_credentials.refresh == "refresh-fresh"
        assert refresh_calls == 1

    asyncio.run(run())


def test_get_oauth_api_key_uses_fresh_credentials_without_refresh() -> None:
    async def run() -> None:
        reset_oauth_providers_for_tests()
        credentials = OAuthCredentials(
            access=_jwt_with_account("acct-current"),
            refresh="refresh-current",
            expires=120_000,
            account_id="acct-current",
        )

        result = await get_oauth_api_key(
            "openai",
            {"openai": credentials.to_mapping()},
            now_ms=lambda: 1_000,
        )

        assert result is not None
        assert result.api_key == credentials.access
        assert result.new_credentials == credentials

    asyncio.run(run())
