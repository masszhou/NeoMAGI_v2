from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ai_provider.api_registry import get_api, stream, stream_simple
from ai_provider.auth_storage import AUTH_PATH_ENV, save_oauth_credentials
from ai_provider.model_registry import get_model
from ai_provider.oauth import OAuthCredentials
from ai_provider.providers.openai_codex_responses import build_openai_codex_responses_params
from ai_provider.runtime_types import SimpleStreamOptions, StreamOptions
from ai_provider.types import Context, Tool, UserMessage

JWT_CLAIM_PATH = "https://api.openai.com/auth"


def _jwt_with_account(account_id: str) -> str:
    header = {"alg": "none", "typ": "JWT"}
    payload = {JWT_CLAIM_PATH: {"chatgpt_account_id": account_id}}
    return f"{_b64_json(header)}.{_b64_json(payload)}.signature"


def _b64_json(data: Mapping[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _context() -> Context:
    return Context(
        systemPrompt="Use terse answers.",
        messages=[UserMessage(content="Say exactly CODEX_OK", timestamp=1)],
        tools=[
            Tool(
                name="read",
                description="Read a file",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            )
        ],
    )


def test_codex_api_family_is_registered() -> None:
    assert get_api("openai-codex-responses").api_name == "openai-codex-responses"
    model = get_model("openai-codex", "gpt-5.3-codex")
    assert model.api == "openai-codex-responses"
    assert model.base_url == "https://chatgpt.com/backend-api"


def test_codex_payload_and_headers_consume_oauth_token() -> None:
    token = _jwt_with_account("acct-123")
    model = get_model("openai-codex", "gpt-5.3-codex")
    payload, headers = build_openai_codex_responses_params(
        model,
        _context(),
        StreamOptions(
            api_key=token,
            cache_retention="short",
            session_id="codex-session-1",
            metadata={"reasoning_effort": "low"},
        ),
    )

    assert payload["model"] == "gpt-5.3-codex"
    assert payload["instructions"] == "Use terse answers."
    assert payload["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "Say exactly CODEX_OK"}],
        }
    ]
    assert payload["prompt_cache_key"] == "codex-session-1"
    assert payload["reasoning"] == {"effort": "low", "summary": "auto"}
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["tool_choice"] == "auto"
    assert "Authorization" not in str(payload)
    assert headers["Authorization"] == f"Bearer {token}"
    assert headers["chatgpt-account-id"] == "acct-123"
    assert headers["originator"] == "pi"
    assert headers["OpenAI-Beta"] == "responses=experimental"
    assert headers["session_id"] == "codex-session-1"
    assert headers["x-client-request-id"] == "codex-session-1"


def test_codex_stream_parses_sse_like_response_events() -> None:
    async def run() -> None:
        token = _jwt_with_account("acct-123")
        captured: dict[str, object] = {}

        def fake_client(payload: object, headers: dict[str, str]) -> list[dict[str, object]]:
            captured["payload"] = payload
            captured["headers"] = headers
            return [
                {"type": "response.created", "response": {"id": "resp_codex_1"}},
                {"type": "response.output_item.added", "item": {"type": "message", "id": "msg_1"}},
                {"type": "response.output_text.delta", "delta": "CODEX_OK"},
                {
                    "type": "response.done",
                    "response": {
                        "id": "resp_codex_1",
                        "status": "completed",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 3,
                            "total_tokens": 13,
                            "input_tokens_details": {"cached_tokens": 4},
                        },
                    },
                },
            ]

        model = get_model("openai-codex", "gpt-5.3-codex")
        result_stream = stream(
            model,
            _context(),
            StreamOptions(api_key=token, client=fake_client, cache_retention="none"),
        )
        events = [event.type async for event in result_stream]
        result = await result_stream.result()

        assert events == ["start", "text_start", "text_delta", "text_end", "done"]
        assert result.response_id == "resp_codex_1"
        assert result.stop_reason == "stop"
        assert result.content[0].text == "CODEX_OK"
        assert result.usage.input == 6
        assert result.usage.cache_read == 4
        assert captured["headers"]["chatgpt-account-id"] == "acct-123"

    asyncio.run(run())


def test_codex_stream_requires_top_level_instructions() -> None:
    async def run() -> None:
        token = _jwt_with_account("acct-123")

        def fake_client(payload: object, headers: dict[str, str]) -> list[dict[str, object]]:
            raise AssertionError("request should fail before transport")

        model = get_model("openai-codex", "gpt-5.3-codex")
        result = await stream(
            model,
            Context(messages=[UserMessage(content="Say exactly CODEX_OK", timestamp=1)]),
            StreamOptions(api_key=token, client=fake_client, cache_retention="none"),
        ).result()

        assert result.stop_reason == "error"
        assert result.error_message is not None
        assert "requires Context.systemPrompt" in result.error_message

    asyncio.run(run())


def test_codex_stream_uses_stored_oauth_token(monkeypatch, tmp_path: Path) -> None:
    async def run() -> None:
        token = _jwt_with_account("acct-stored")
        auth_path = tmp_path / "auth.json"
        save_oauth_credentials(
            "openai-codex",
            OAuthCredentials(
                access=token,
                refresh="refresh-stored",
                expires=4_000_000_000_000,
                account_id="acct-stored",
            ),
            auth_path,
        )
        monkeypatch.setenv(AUTH_PATH_ENV, str(auth_path))
        monkeypatch.delenv("OPENAI_CODEX_OAUTH_TOKEN", raising=False)
        captured: dict[str, object] = {}

        def fake_client(payload: object, headers: dict[str, str]) -> list[dict[str, object]]:
            captured["headers"] = headers
            return [{"type": "response.completed", "response": {"id": "resp_stored"}}]

        model = get_model("openai-codex", "gpt-5.3-codex")
        result = await stream(
            model,
            _context(),
            StreamOptions(client=fake_client, cache_retention="none"),
        ).result()

        assert result.response_id == "resp_stored"
        assert captured["headers"]["Authorization"] == f"Bearer {token}"
        assert captured["headers"]["chatgpt-account-id"] == "acct-stored"

    asyncio.run(run())


def test_codex_stream_simple_sets_reasoning_payload() -> None:
    async def run() -> None:
        token = _jwt_with_account("acct-123")
        captured: dict[str, object] = {}

        def fake_client(payload: object, headers: dict[str, str]) -> list[dict[str, object]]:
            captured["payload"] = payload
            return [{"type": "response.completed", "response": {"id": "resp_1"}}]

        model = get_model("openai-codex", "gpt-5.3-codex")
        await stream_simple(
            model,
            _context(),
            SimpleStreamOptions(api_key=token, client=fake_client, reasoning="xhigh"),
        ).result()

        assert captured["payload"]["reasoning"] == {"effort": "high", "summary": "auto"}

    asyncio.run(run())
