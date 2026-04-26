from __future__ import annotations

import asyncio

from ai_provider.model_registry import get_model
from ai_provider.providers.anthropic import build_anthropic_messages_params, get_cache_control, stream_anthropic_messages
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, Tool, UserMessage


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


ANTHROPIC_TEXT_TOOL_EVENTS = [
    {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 0,
                "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 3,
            },
        },
    },
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
    {"type": "content_block_stop", "index": 0},
    {
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "tool_use", "id": "call_1", "name": "read", "input": {}},
    },
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": "{\"path\":\"README.md\"}"},
    },
    {"type": "content_block_stop", "index": 1},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use"},
        "usage": {
            "input_tokens": 10,
            "output_tokens": 4,
            "cache_read_input_tokens": 2,
            "cache_creation_input_tokens": 3,
        },
    },
    {"type": "message_stop"},
]


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
    assert get_cache_control("https://api.anthropic.com", "short") == {"type": "ephemeral"}
    assert get_cache_control("https://api.anthropic.com", "long") == {
        "type": "ephemeral",
        "ttl": "1h",
    }
    assert get_cache_control("https://proxy.example.com", "long") == {"type": "ephemeral"}
    assert get_cache_control("https://api.anthropic.com", "none") is None


def test_anthropic_payload_marks_system_last_tool_and_last_user() -> None:
    model = get_model("anthropic", "claude-3-5-haiku-20241022")
    payload = build_anthropic_messages_params(model, _context(), StreamOptions())

    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert payload["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_cache_none_forbids_cache_markers() -> None:
    model = get_model("anthropic", "claude-3-5-haiku-20241022")
    payload = build_anthropic_messages_params(model, _context(), StreamOptions(cache_retention="none"))

    assert "cache_control" not in payload["system"][0]
    assert "cache_control" not in payload["tools"][-1]
    assert "cache_control" not in payload["messages"][-1]["content"][-1]


def test_anthropic_stream_text_and_tool_call() -> None:
    async def run() -> None:
        model = get_model("anthropic", "claude-3-5-haiku-20241022")
        fake = FakeAnthropicClient(ANTHROPIC_TEXT_TOOL_EVENTS)
        stream = stream_anthropic_messages(model, _context(), StreamOptions(client=fake))
        events = [event.type async for event in stream]
        result = await stream.result()

        assert events == [
            "start",
            "text_start",
            "text_delta",
            "text_end",
            "toolcall_start",
            "toolcall_delta",
            "toolcall_end",
            "done",
        ]
        assert result.response_id == "msg_1"
        assert result.stop_reason == "toolUse"
        assert result.content[0].text == "hi"
        assert result.content[1].arguments == {"path": "README.md"}
        assert result.usage.cache_read == 2
        assert fake.messages.last_payload is not None

    asyncio.run(run())
