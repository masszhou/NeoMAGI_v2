from __future__ import annotations

import json
from pathlib import Path

from ai_provider.convert import clone_assistant_message, clone_context
from ai_provider.model_registry import get_model
from ai_provider.providers.anthropic import build_anthropic_messages_params
from ai_provider.providers.openai_completions import build_openai_completions_params
from ai_provider.runtime_types import StreamOptions
from ai_provider.types import (
    AssistantMessage,
    Context,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
    UserMessage,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "pi_compat"


def test_opaque_fields_survive_cross_provider_handoff_without_mutating_source() -> None:
    fixture = json.loads((FIXTURE_ROOT / "cross_provider_handoff_opaque" / "fixture.json").read_text())
    opaque = fixture["opaqueFields"]
    original = AssistantMessage(
        content=[
            ThinkingContent(thinking="hidden", thinkingSignature=opaque["thinkingSignature"]),
            ToolCall(
                id="call_1",
                name="read",
                arguments={"path": "README.md"},
                thoughtSignature=opaque["thoughtSignature"],
            ),
            TextContent(text="answer", textSignature=opaque["textSignature"]),
        ],
        api="anthropic-messages",
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        responseId=opaque["responseId"],
        usage=Usage(input=1, output=1, cacheRead=1, cacheWrite=1, totalTokens=4),
        stopReason="toolUse",
        timestamp=1,
    )
    context = Context(messages=[UserMessage(content="next", timestamp=2), original])

    cloned = clone_assistant_message(original)
    assert cloned.response_id == opaque["responseId"]
    assert cloned.content[0].thinking_signature == opaque["thinkingSignature"]
    assert cloned.content[1].thought_signature == opaque["thoughtSignature"]
    assert cloned.content[2].text_signature == opaque["textSignature"]

    openai_model = get_model("openai", "gpt-4o-mini-chat-completions")
    anthropic_model = get_model("anthropic", "claude-haiku-4-5-20251001")
    openai_payload, _ = build_openai_completions_params(openai_model, clone_context(context), StreamOptions())
    anthropic_payload = build_anthropic_messages_params(anthropic_model, clone_context(context), StreamOptions())

    assert openai_payload["messages"][1]["tool_calls"][0]["id"] == "call_1"
    assert anthropic_payload["messages"][1]["content"][0]["signature"] == opaque["thinkingSignature"]
    assert original.content[1].arguments == {"path": "README.md"}
    assert original.response_id == opaque["responseId"]
