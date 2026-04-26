from __future__ import annotations

import asyncio

from ai_provider.model_registry import get_model
from ai_provider.providers.openai_completions import build_openai_completions_params, stream_openai_completions
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage


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


def _context() -> Context:
    return Context(systemPrompt="sys", messages=[UserMessage(content="hello", timestamp=1)])


def test_openai_completions_direct_prompt_cache() -> None:
    model = get_model("openai", "gpt-4o-mini-chat-completions")
    payload, headers = build_openai_completions_params(
        model,
        _context(),
        StreamOptions(cache_retention="long", session_id="session-1"),
    )

    assert payload["prompt_cache_key"] == "session-1"
    assert payload["prompt_cache_retention"] == "24h"
    assert "cache_control" not in str(payload)
    assert headers == {}


def test_openai_completions_compatible_affinity_headers() -> None:
    model = get_model("opencode", "glm-5")
    payload, headers = build_openai_completions_params(
        model,
        _context(),
        StreamOptions(cache_retention="long", session_id="session-1"),
    )

    assert "prompt_cache_key" not in payload
    assert headers["session_id"] == "session-1"
    assert headers["x-client-request-id"] == "session-1"
    assert headers["x-session-affinity"] == "session-1"


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
        stream = stream_openai_completions(model, _context(), StreamOptions(client=fake))
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
