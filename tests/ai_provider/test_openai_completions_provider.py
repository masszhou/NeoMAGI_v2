from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ai_provider.api_registry import stream_simple
from ai_provider.model_registry import get_model
from ai_provider.providers.openai_completions import build_openai_completions_params, stream_openai_completions
from ai_provider.runtime_types import SimpleStreamOptions, StreamOptions
from ai_provider.types import AssistantMessage, Context, TextContent, Tool, ToolCall, Usage, UserMessage

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "pi_compat"


class FakeChatCompletions:
    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks
        self.last_payload: dict | None = None
        self.last_headers: dict | None = None

    def create(self, **kwargs):
        self.last_headers = kwargs.pop("extra_headers", None)
        self.last_payload = kwargs
        return self.chunks


class FakeChat:
    def __init__(self, chunks: list[dict]) -> None:
        self.completions = FakeChatCompletions(chunks)


class FakeOpenAIClient:
    def __init__(self, chunks: list[dict]) -> None:
        self.chat = FakeChat(chunks)


def _context(tools: list[Tool] | None = None) -> Context:
    return Context(systemPrompt="sys", messages=[UserMessage(content="hello", timestamp=1)], tools=tools)


def _read_tool() -> Tool:
    return Tool(name="read", description="Read a file", parameters={"type": "object"})


def _fixture(scene: str) -> dict:
    return json.loads((FIXTURE_ROOT / scene / "fixture.json").read_text())


def test_openai_completions_direct_prompt_cache() -> None:
    fixture = _fixture("openai_completions_prompt_cache")["directOpenAI"]
    model = get_model("openai", "gpt-4o-mini-chat-completions")
    payload, headers = build_openai_completions_params(
        model,
        _context(),
        StreamOptions(cache_retention=fixture["cacheRetention"], session_id=fixture["sessionId"]),
    )

    for key, value in fixture["expectedPayload"].items():
        assert payload[key] == value
    assert "cache_control" not in str(payload)
    assert headers == {}


def test_openai_completions_tool_strict_defaults_to_false_when_supported() -> None:
    model = get_model("openai", "gpt-4o-mini-chat-completions")
    payload, _ = build_openai_completions_params(model, _context([_read_tool()]), StreamOptions())
    assert payload["tools"][0]["function"]["strict"] is False

    model = model.model_copy(deep=True)
    model.compat = {"supportsStrictMode": False}
    payload, _ = build_openai_completions_params(model, _context([_read_tool()]), StreamOptions())
    assert "strict" not in payload["tools"][0]["function"]


def test_openai_completions_compatible_affinity_headers() -> None:
    fixture = _fixture("openai_completions_prompt_cache")["compatibleProvider"]
    model = get_model("opencode", "glm-5")
    payload, headers = build_openai_completions_params(
        model,
        _context(),
        StreamOptions(cache_retention="long", session_id=fixture["sessionId"]),
    )

    assert "prompt_cache_key" not in payload
    for key, value in fixture["expectedHeaders"].items():
        assert headers[key] == value


def test_openai_completions_cache_none_forbids_cache_and_affinity() -> None:
    model = get_model("opencode", "glm-5")
    payload, headers = build_openai_completions_params(
        model,
        _context(),
        StreamOptions(cache_retention="none", session_id="session-1"),
    )

    forbidden = {"prompt_cache_key", "prompt_cache_retention", "session_id", "x-client-request-id", "x-session-affinity"}
    assert forbidden.isdisjoint(payload)
    assert forbidden.isdisjoint(headers)


def test_openai_completions_stream_text_thinking_tool_and_usage() -> None:
    async def run() -> None:
        model = get_model("openai", "gpt-4o-mini-chat-completions")
        fake = FakeOpenAIClient(
            [
                {"choices": [{"delta": {"reasoning_content": "think"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "read", "arguments": "{\"path\":\"README.md\"}"},
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 5,
                        "prompt_tokens_details": {"cached_tokens": 8, "cache_write_tokens": 3},
                    },
                },
            ]
        )
        stream = stream_openai_completions(model, _context([_read_tool()]), StreamOptions(client=fake))
        events = [event.type async for event in stream]
        result = await stream.result()

        assert events == [
            "start",
            "thinking_start",
            "thinking_delta",
            "text_start",
            "text_delta",
            "toolcall_start",
            "toolcall_delta",
            "toolcall_end",
            "thinking_end",
            "text_end",
            "done",
        ]
        assert result.stop_reason == "toolUse"
        assert result.content[0].thinking == "think"
        assert result.content[0].thinking_signature == "reasoning_content"
        assert result.content[1].text == "hi"
        assert result.content[2].arguments == {"path": "README.md"}
        assert result.usage.input == 12
        assert result.usage.cache_read == 5
        assert result.usage.cache_write == 3
        assert fake.chat.completions.last_payload["stream_options"] == {"include_usage": True}

    asyncio.run(run())


def test_openai_completions_multichunk_tool_call_ends_once() -> None:
    async def run() -> None:
        model = get_model("openai", "gpt-4o-mini-chat-completions")
        fake = FakeOpenAIClient(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "read",
                                            "arguments": "{\"path\":\"README.md\"}",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {"tool_calls": [{"index": 0, "function": {"arguments": " "}}]},
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            ]
        )
        stream = stream_openai_completions(model, _context([_read_tool()]), StreamOptions(client=fake))
        events = [event.type async for event in stream]
        result = await stream.result()

        assert events == ["start", "toolcall_start", "toolcall_delta", "toolcall_delta", "toolcall_end", "done"]
        assert events.count("toolcall_end") == 1
        assert result.content[0].arguments == {"path": "README.md"}

    asyncio.run(run())


def test_openai_completions_reasoning_details_update_thought_signature() -> None:
    async def run() -> None:
        model = get_model("openai", "gpt-4o-mini-chat-completions")
        detail = {"type": "reasoning.encrypted", "id": "call_1", "data": "opaque"}
        fake = FakeOpenAIClient(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "read", "arguments": "{\"path\":\"README.md\"}"},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {"choices": [{"delta": {"reasoning_details": [detail]}, "finish_reason": "tool_calls"}]},
            ]
        )
        result = await stream_openai_completions(
            model,
            _context([_read_tool()]),
            StreamOptions(client=fake),
        ).result()

        assert json.loads(result.content[0].thought_signature) == detail

    asyncio.run(run())


def test_openai_completions_replays_tool_reasoning_details() -> None:
    model = get_model("openai", "gpt-4o-mini-chat-completions")
    detail = {"type": "reasoning.encrypted", "id": "call_1", "data": "opaque"}
    assistant = AssistantMessage(
        content=[
            TextContent(text="using tool"),
            ToolCall(id="call_1", name="read", arguments={"path": "README.md"}, thoughtSignature=json.dumps(detail)),
        ],
        api="openai-completions",
        provider="openai",
        model=model.id,
        usage=Usage(input=0, output=0, cacheRead=0, cacheWrite=0, totalTokens=0),
        stopReason="toolUse",
        timestamp=1,
    )
    payload, _ = build_openai_completions_params(model, Context(messages=[assistant]), StreamOptions())
    assert payload["messages"][0]["reasoning_details"] == [detail]


def test_openai_completions_stream_simple_sets_reasoning_effort() -> None:
    async def run() -> None:
        model = get_model("opencode", "glm-5")
        model = model.model_copy(deep=True)
        model.compat = {"supportsReasoningEffort": True}
        fake = FakeOpenAIClient([{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}])

        result = await stream_simple(
            model,
            _context(),
            SimpleStreamOptions(client=fake, reasoning="xhigh"),
        ).result()

        assert result.content[0].text == "ok"
        assert fake.chat.completions.last_payload["reasoning_effort"] == "high"

    asyncio.run(run())
