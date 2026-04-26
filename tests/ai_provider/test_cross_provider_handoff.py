from __future__ import annotations

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


def test_opaque_fields_survive_cross_provider_handoff_without_mutating_source() -> None:
    original = AssistantMessage(
        content=[
            ThinkingContent(thinking="hidden", thinkingSignature="think_sig"),
            ToolCall(id="call_1", name="read", arguments={"path": "README.md"}, thoughtSignature="thought_sig"),
            TextContent(text="answer", textSignature="text_sig"),
        ],
        api="anthropic-messages",
        provider="anthropic",
        model="claude-3-5-haiku-20241022",
        responseId="resp_1",
        usage=Usage(input=1, output=1, cacheRead=1, cacheWrite=1, totalTokens=4),
        stopReason="toolUse",
        timestamp=1,
    )
    context = Context(messages=[UserMessage(content="next", timestamp=2), original])

    cloned = clone_assistant_message(original)
    assert cloned.response_id == "resp_1"
    assert cloned.content[0].thinking_signature == "think_sig"
    assert cloned.content[1].thought_signature == "thought_sig"
    assert cloned.content[2].text_signature == "text_sig"

    openai_model = get_model("openai", "gpt-4o-mini-chat-completions")
    anthropic_model = get_model("anthropic", "claude-3-5-haiku-20241022")
    openai_payload, _ = build_openai_completions_params(openai_model, clone_context(context), StreamOptions())
    anthropic_payload = build_anthropic_messages_params(anthropic_model, clone_context(context), StreamOptions())

    assert openai_payload["messages"][1]["tool_calls"][0]["id"] == "call_1"
    assert anthropic_payload["messages"][1]["content"][0]["signature"] == "think_sig"
    assert original.content[1].arguments == {"path": "README.md"}
    assert original.response_id == "resp_1"

