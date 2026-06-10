from __future__ import annotations

from pathlib import Path

from ai_provider.auth_storage import AUTH_PATH_ENV, save_oauth_credentials
from ai_provider.credentials import resolve_provider_auth
from ai_provider.model_registry import get_model, parse_model_ref, resolve_model
from ai_provider.oauth import OAuthCredentials
from ai_provider.oauth_github_copilot import COPILOT_HEADERS
from ai_provider.providers.openai_completions import build_openai_completions_params
from ai_provider.providers.openai_responses import build_openai_responses_params
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import AssistantMessage, Context, Usage, UserMessage

_TOKEN = "tid=abc;exp=1;proxy-ep=proxy.business.githubcopilot.com;chat=1"


def _empty_usage() -> Usage:
    return Usage(input=0, output=0, cacheRead=0, cacheWrite=0, totalTokens=0)


# --- registry ----------------------------------------------------------------


def test_copilot_models_resolve_to_correct_api_family() -> None:
    completions = parse_model_ref("github-copilot/oauth/gpt-4o")
    assert completions.provider == "github-copilot"
    assert completions.auth_channel == "oauth"
    assert get_model("github-copilot", "gpt-4o").api == "openai-completions"

    responses = parse_model_ref("github-copilot/oauth/gpt-5.1")
    assert get_model("github-copilot", responses.model_id).api == "openai-responses"


# --- single-resolution base url ---------------------------------------------


def test_resolve_provider_auth_derives_base_url_from_token_proxy_ep() -> None:
    model = resolve_model("github-copilot/oauth/gpt-4o")

    auth = resolve_provider_auth(model, StreamOptions(api_key=_TOKEN))

    assert auth.api_key == _TOKEN
    assert auth.base_url == "https://api.business.githubcopilot.com"


def test_resolve_provider_auth_falls_back_to_individual_without_proxy_ep() -> None:
    model = resolve_model("github-copilot/oauth/gpt-5.1")

    auth = resolve_provider_auth(model, StreamOptions(api_key="opaque-token"))

    assert auth.base_url == "https://api.individual.githubcopilot.com"


def test_resolve_provider_auth_keeps_model_base_url_for_other_providers() -> None:
    model = resolve_model("openai/api/gpt-5.4")

    auth = resolve_provider_auth(model, StreamOptions(api_key="sk-test"))

    assert auth.base_url == model.base_url


def test_resolve_provider_auth_uses_enterprise_fallback_when_token_lacks_proxy_ep(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(AUTH_PATH_ENV, str(tmp_path / "auth.json"))
    # A stored enterprise credential whose token carries no proxy-ep: base url
    # must fall back to copilot-api.<enterprise>, not the individual host.
    save_oauth_credentials(
        "github-copilot",
        OAuthCredentials(
            access="opaque-token-without-proxy-ep",
            refresh="ghu_token",
            expires=9_999_999_999_999,
            extra={"enterpriseUrl": "company.ghe.com"},
        ),
        tmp_path / "auth.json",
    )
    model = resolve_model("github-copilot/oauth/gpt-4o")

    auth = resolve_provider_auth(model)

    assert auth.base_url == "https://copilot-api.company.ghe.com"


def test_resolve_provider_auth_refreshes_once_and_uses_new_token_base_url(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(AUTH_PATH_ENV, str(tmp_path / "auth.json"))
    save_oauth_credentials(
        "github-copilot",
        OAuthCredentials(access="stale", refresh="ghu_token", expires=1_000),
        tmp_path / "auth.json",
    )
    calls = {"n": 0}

    def fake_refresh(credentials: OAuthCredentials, *, now_ms):
        calls["n"] += 1
        return OAuthCredentials(access=_TOKEN, refresh="ghu_token", expires=10_000_000)

    monkeypatch.setattr(
        "ai_provider.auth_storage.refresh_github_copilot_credentials_sync",
        fake_refresh,
    )

    model = resolve_model("github-copilot/oauth/gpt-4o")
    auth = resolve_provider_auth(model)

    assert calls["n"] == 1  # single resolution, not double-refreshed
    assert auth.api_key == _TOKEN
    assert auth.base_url == "https://api.business.githubcopilot.com"


# --- dynamic headers ---------------------------------------------------------


def _user_context() -> Context:
    return Context(messages=[UserMessage(content="hi", timestamp=1)])


def _agent_last_context() -> Context:
    return Context(
        messages=[
            UserMessage(content="hi", timestamp=1),
            AssistantMessage(
                content=[],
                api="openai-completions",
                provider="github-copilot",
                model="gpt-4o",
                usage=_empty_usage(),
                stopReason="stop",
                timestamp=2,
            ),
        ]
    )


def test_completions_copilot_request_carries_static_and_dynamic_headers() -> None:
    model = resolve_model("github-copilot/oauth/gpt-4o")

    _payload, headers = build_openai_completions_params(model, _user_context(), StreamOptions())

    for key, value in COPILOT_HEADERS.items():
        assert headers[key] == value
    assert headers["X-Initiator"] == "user"
    assert headers["Openai-Intent"] == "conversation-edits"


def test_responses_copilot_request_marks_agent_initiator_on_assistant_turn() -> None:
    model = resolve_model("github-copilot/oauth/gpt-5.1")

    _payload, headers = build_openai_responses_params(model, _agent_last_context(), StreamOptions())

    assert headers["Copilot-Integration-Id"] == "vscode-chat"
    assert headers["X-Initiator"] == "agent"


def test_non_copilot_request_has_no_copilot_dynamic_headers() -> None:
    model = resolve_model("openai/api/gpt-4o-mini-chat-completions")

    _payload, headers = build_openai_completions_params(model, _user_context(), StreamOptions())

    assert "X-Initiator" not in headers
    assert "Openai-Intent" not in headers


# --- reasoning payload -------------------------------------------------------


def test_responses_copilot_skips_effort_none_reasoning() -> None:
    # pi-mono never sends reasoning.effort="none" to github-copilot; gpt-5.x
    # Copilot would otherwise be rejected on the default (reasoning_disabled) path.
    model = resolve_model("github-copilot/oauth/gpt-5.1")

    payload, _headers = build_openai_responses_params(
        model, _user_context(), StreamOptions(metadata={"reasoning_disabled": True})
    )

    assert "reasoning" not in payload


def test_responses_non_copilot_still_disables_reasoning_with_effort_none() -> None:
    model = resolve_model("openai/api/gpt-5.4")

    payload, _headers = build_openai_responses_params(
        model, _user_context(), StreamOptions(metadata={"reasoning_disabled": True})
    )

    assert payload["reasoning"] == {"effort": "none"}
