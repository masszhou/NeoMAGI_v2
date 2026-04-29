from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ai_provider.api_registry import stream_simple
from ai_provider.model_registry import get_model
from ai_provider.providers.anthropic import build_anthropic_messages_params, get_cache_control, stream_anthropic_messages
from ai_provider.runtime_types import SimpleStreamOptions, StreamOptions
from ai_provider.types import Context, Tool, UserMessage

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "pi_compat"


class FakeAnthropicMessages:
    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.last_payload: dict | None = None

    def stream(self, **payload):
        self.last_payload = payload
        return self.events


class FakeAnthropicClient:
    def __init__(self, events: list[dict]) -> None:
        self.messages = FakeAnthropicMessages(events)


def _json_fixture(scene: str, file_name: str = "fixture.json") -> dict:
    return json.loads((FIXTURE_ROOT / scene / file_name).read_text())


def _stream_fixture(scene: str) -> dict:
    return json.loads((FIXTURE_ROOT / scene / "events.json").read_text())


def _context() -> Context:
    return Context(
        systemPrompt="sys",
        messages=[UserMessage(content="hello", timestamp=1)],
        tools=[
            Tool(
                name="read",
                description="Read a file",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            )
        ],
    )


def test_anthropic_cache_control_rules(monkeypatch) -> None:
    monkeypatch.delenv("PI_CACHE_RETENTION", raising=False)
    fixture = _json_fixture("anthropic_cache_long")

    assert get_cache_control("https://api.anthropic.com", "short") == {"type": "ephemeral"}
    assert get_cache_control("https://api.anthropic.com", fixture["cacheRetention"]) == fixture["directExpected"][
        "cacheControl"
    ]
    assert get_cache_control("https://proxy.example.com", fixture["cacheRetention"]) == fixture["proxyExpected"][
        "cacheControl"
    ]
    assert get_cache_control("https://api.anthropic.com", "none") is None


def test_anthropic_payload_marks_system_last_tool_and_last_user() -> None:
    fixture = _json_fixture("anthropic_cache_short")
    expected = fixture["expected"]
    model = get_model("anthropic", "claude-haiku-4-5-20251001")
    payload = build_anthropic_messages_params(
        model,
        _context(),
        StreamOptions(cache_retention=fixture["cacheRetention"]),
    )

    assert payload["system"][0]["cache_control"] == expected["systemCacheControl"]
    assert payload["tools"][-1]["cache_control"] == expected["lastToolCacheControl"]
    assert payload["messages"][-1]["content"][-1]["cache_control"] == expected["lastUserCacheControl"]


def test_anthropic_cache_none_forbids_cache_markers() -> None:
    fixture = _json_fixture("anthropic_cache_none")
    model = get_model("anthropic", "claude-haiku-4-5-20251001")
    payload = build_anthropic_messages_params(
        model,
        _context(),
        StreamOptions(cache_retention=fixture["cacheRetention"]),
    )

    for forbidden in fixture["forbiddenKeys"]:
        assert forbidden not in payload["system"][0]
        assert forbidden not in payload["tools"][-1]
        assert forbidden not in payload["messages"][-1]["content"][-1]


def test_anthropic_payload_omits_empty_system_prompt() -> None:
    model = get_model("anthropic", "claude-haiku-4-5-20251001")
    payload = build_anthropic_messages_params(
        model,
        Context(systemPrompt="", messages=[UserMessage(content="hello", timestamp=1)]),
        StreamOptions(cache_retention="none"),
    )

    assert "system" not in payload


def test_anthropic_stream_text_fixture() -> None:
    async def run() -> None:
        fixture = _stream_fixture("provider_stream_text")
        expected = fixture["expected"]
        model = get_model(fixture["provider"], fixture["model"])
        fake = FakeAnthropicClient(fixture["providerEvents"])
        stream = stream_anthropic_messages(model, _context(), StreamOptions(client=fake))
        events = [event.type async for event in stream]
        result = await stream.result()

        assert events == expected["eventTypes"]
        assert result.response_id == expected["responseId"]
        assert result.stop_reason == expected["stopReason"]
        assert result.content[0].text == expected["text"]
        assert result.usage.input == expected["usage"]["input"]
        assert result.usage.output == expected["usage"]["output"]

    asyncio.run(run())


def test_anthropic_stream_text_and_tool_call() -> None:
    async def run() -> None:
        fixture = _stream_fixture("provider_stream_tool_call")
        expected = fixture["expected"]
        model = get_model(fixture["provider"], fixture["model"])
        fake = FakeAnthropicClient(fixture["providerEvents"])
        stream = stream_anthropic_messages(model, _context(), StreamOptions(client=fake))
        events = [event.type async for event in stream]
        result = await stream.result()

        assert events == expected["eventTypes"]
        assert result.response_id == expected["responseId"]
        assert result.stop_reason == expected["stopReason"]
        assert result.content[0].text == expected["text"]
        assert result.content[1].id == expected["toolCall"]["id"]
        assert result.content[1].name == expected["toolCall"]["name"]
        assert result.content[1].arguments == expected["toolCall"]["arguments"]
        assert result.usage.cache_read == expected["usage"]["cacheRead"]
        assert result.usage.cache_write == expected["usage"]["cacheWrite"]
        assert fake.messages.last_payload is not None

    asyncio.run(run())


def test_anthropic_stream_simple_sets_thinking_budget() -> None:
    async def run() -> None:
        model = get_model("anthropic", "claude-haiku-4-5-20251001")
        fake = FakeAnthropicClient([{"type": "message_stop"}])
        await stream_simple(
            model,
            _context(),
            SimpleStreamOptions(client=fake, reasoning="low", thinking_budgets={"low": 1234}),
        ).result()

        payload = fake.messages.last_payload
        assert payload["max_tokens"] == 33234
        assert payload["thinking"] == {"type": "enabled", "budget_tokens": 1234}
        assert "metadata" not in payload

    asyncio.run(run())


def test_anthropic_stream_simple_omits_thinking_for_non_reasoning_model() -> None:
    async def run() -> None:
        model = get_model("anthropic", "claude-haiku-4-5-20251001")
        fake = FakeAnthropicClient([{"type": "message_stop"}])
        await stream_simple(model, _context(), SimpleStreamOptions(client=fake)).result()
        assert "thinking" not in fake.messages.last_payload

    asyncio.run(run())


def test_anthropic_stream_simple_ignores_reasoning_for_non_reasoning_model() -> None:
    async def run() -> None:
        model = get_model("anthropic", "claude-haiku-4-5-20251001").model_copy(deep=True)
        model.reasoning = False
        fake = FakeAnthropicClient([{"type": "message_stop"}])
        await stream_simple(model, _context(), SimpleStreamOptions(client=fake, reasoning="low")).result()
        assert "thinking" not in fake.messages.last_payload
        assert fake.messages.last_payload["max_tokens"] == 32000

    asyncio.run(run())
