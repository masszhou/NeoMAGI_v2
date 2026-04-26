from __future__ import annotations

import asyncio

from ai_provider.model_registry import get_model
from ai_provider.providers._shared import initial_message
from ai_provider.streaming import create_assistant_message_event_stream
from ai_provider.types import StreamDone, StreamTextDelta, StreamTextStart, TextContent


def test_stream_result_matches_done_message() -> None:
    async def run() -> None:
        model = get_model("faux", "faux-1")
        message = initial_message(model)
        stream = create_assistant_message_event_stream(message)
        message.content.append(TextContent(text=""))
        stream.push(StreamTextStart(contentIndex=0, partial=message.model_copy(deep=True)))
        message.content[0].text = "hi"
        stream.push(StreamTextDelta(contentIndex=0, delta="hi", partial=message.model_copy(deep=True)))
        stream.push(StreamDone(reason="stop", message=message))

        events = [event async for event in stream]
        result = await stream.result()
        assert events[-1].message is message
        assert result.content[0].text == "hi"

    asyncio.run(run())


def test_stream_close_emits_aborted_error() -> None:
    async def run() -> None:
        model = get_model("faux", "faux-1")
        message = initial_message(model)
        stream = create_assistant_message_event_stream(message)
        stream.close()

        events = [event async for event in stream]
        result = await stream.result()
        assert events[-1].type == "error"
        assert events[-1].reason == "aborted"
        assert result.stop_reason == "aborted"

    asyncio.run(run())

