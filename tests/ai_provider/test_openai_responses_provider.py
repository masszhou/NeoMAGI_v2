from __future__ import annotations

import asyncio

from ai_provider.model_registry import get_model
from ai_provider.providers.openai_responses import (
    build_openai_responses_params,
    decode_text_signature,
    encode_text_signature,
    stream_openai_responses,
)
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, Tool, UserMessage


class FakeResponses:
    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.last_payload: dict | None = None
        self.last_headers: dict | None = None

    def create(self, **kwargs):
        self.last_headers = kwargs.pop("extra_headers", None)
        self.last_payload = kwargs
        return self.events


class FakeOpenAIClient:
    def __init__(self, events: list[dict]) -> None:
        self.responses = FakeResponses(events)


OPENAI_RESPONSES_TEXT_TOOL_EVENTS = [
    {"type": "response.created", "response": {"id": "resp_1"}},
    {"type": "response.output_item.added", "item": {"type": "message", "id": "msg_item_1"}},
    {"type": "response.output_text.delta", "delta": "hi"},
    {
        "type": "response.output_item.added",
        "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read"},
    },
    {"type": "response.function_call_arguments.delta", "call_id": "call_1", "delta": "{\"path\":\""},
    {"type": "response.function_call_arguments.delta", "call_id": "call_1", "delta": "README.md\"}"},
    {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "read",
            "arguments": "{\"path\":\"README.md\"}",
        },
    },
    {
        "type": "response.completed",
        "response": {
            "id": "resp_1",
            "usage": {
                "input_tokens": 20,
                "output_tokens": 5,
                "input_tokens_details": {"cached_tokens": 7},
            },
        },
    },
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


def test_text_signature_round_trip() -> None:
    signature = encode_text_signature("item_1", "final_answer")
    assert decode_text_signature(signature) == {"v": 1, "id": "item_1", "phase": "final_answer"}


def test_openai_responses_prompt_cache_fields_and_headers() -> None:
    model = get_model("openai", "gpt-4o-mini")
    payload, headers = build_openai_responses_params(
        model,
        _context(),
        StreamOptions(cache_retention="long", session_id="session-1"),
    )

    assert payload["prompt_cache_key"] == "session-1"
    assert payload["prompt_cache_retention"] == "24h"
    assert headers["session_id"] == "session-1"
    assert headers["x-client-request-id"] == "session-1"
    assert "cache_control" not in str(payload)


def test_openai_responses_cache_none_forbids_cache_fields() -> None:
    model = get_model("openai", "gpt-4o-mini")
    payload, headers = build_openai_responses_params(
        model,
        _context(),
        StreamOptions(cache_retention="none", session_id="session-1"),
    )

    forbidden = {"prompt_cache_key", "prompt_cache_retention", "session_id", "x-client-request-id"}
    assert forbidden.isdisjoint(payload)
    assert forbidden.isdisjoint(headers)


def test_openai_responses_stream_text_and_tool_call() -> None:
    async def run() -> None:
        model = get_model("openai", "gpt-4o-mini")
        fake = FakeOpenAIClient(OPENAI_RESPONSES_TEXT_TOOL_EVENTS)
        stream = stream_openai_responses(
            model,
            _context(),
            StreamOptions(client=fake, cache_retention="short", session_id="session-1"),
        )
        events = [event.type async for event in stream]
        result = await stream.result()

        assert events == [
            "start",
            "text_start",
            "text_delta",
            "toolcall_start",
            "toolcall_delta",
            "toolcall_delta",
            "toolcall_end",
            "text_end",
            "done",
        ]
        assert result.response_id == "resp_1"
        assert result.stop_reason == "toolUse"
        assert result.content[0].text == "hi"
        assert result.content[1].arguments == {"path": "README.md"}
        assert result.usage.input == 13
        assert fake.responses.last_payload["prompt_cache_key"] == "session-1"
        assert fake.responses.last_headers["session_id"] == "session-1"

    asyncio.run(run())
