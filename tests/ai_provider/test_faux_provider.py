from __future__ import annotations

import asyncio

from ai_provider.model_registry import get_model
from ai_provider.providers.faux import faux_thinking, faux_tool_call, stream_faux
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import Context, UserMessage


def _context(text: str = "hi") -> Context:
    return Context(messages=[UserMessage(content=text, timestamp=1)])


def test_faux_provider_streams_text_thinking_and_tool_call() -> None:
    async def run() -> None:
        model = get_model("faux", "faux-1")
        stream = stream_faux(
            model,
            _context(),
            StreamOptions(
                metadata={
                    "response": [
                        faux_thinking("think"),
                        faux_tool_call("read", {"path": "README.md"}),
                    ]
                }
            ),
        )
        events = [event.type async for event in stream]
        result = await stream.result()

        assert events == [
            "start",
            "thinking_start",
            "thinking_delta",
            "thinking_end",
            "toolcall_start",
            "toolcall_delta",
            "toolcall_delta",
            "toolcall_delta",
            "toolcall_delta",
            "toolcall_end",
            "done",
        ]
        assert result.stop_reason == "toolUse"
        assert result.content[1].arguments == {"path": "README.md"}

    asyncio.run(run())


def test_faux_provider_error_and_abort_paths() -> None:
    async def run() -> None:
        model = get_model("faux", "faux-1")
        error_stream = stream_faux(model, _context(), StreamOptions(metadata={"error": "boom"}))
        error = await error_stream.result()
        assert error.stop_reason == "error"
        assert error.error_message == "boom"

        abort_stream = stream_faux(model, _context(), StreamOptions(metadata={"abort": True}))
        aborted = await abort_stream.result()
        assert aborted.stop_reason == "aborted"

    asyncio.run(run())


def test_faux_prompt_cache_simulates_read_and_write() -> None:
    async def run() -> None:
        model = get_model("faux", "faux-1")
        first = await stream_faux(
            model,
            _context("same prefix one"),
            StreamOptions(session_id="session-cache", metadata={"response": "ok"}),
        ).result()
        second = await stream_faux(
            model,
            _context("same prefix two"),
            StreamOptions(session_id="session-cache", metadata={"response": "ok"}),
        ).result()
        none = await stream_faux(
            model,
            _context("same prefix three"),
            StreamOptions(
                session_id="session-cache",
                cache_retention="none",
                metadata={"response": "ok"},
            ),
        ).result()

        assert first.usage.cache_write > 0
        assert second.usage.cache_read > 0
        assert none.usage.cache_read == 0
        assert none.usage.cache_write == 0

    asyncio.run(run())

