"""W4 business renderer snapshots."""

from __future__ import annotations

from agent_core.types import (
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from ai_provider.types import (
    AssistantMessage,
    StreamTextDelta,
    TextContent,
    ToolResultMessage,
    Usage,
    UsageCost,
    UserMessage,
)
from cli.core.session_types import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
)
from cli.interactive.components import (
    AssistantMessageComponent,
    BashExecutionComponent,
    BranchSummaryComponent,
    CompactionSummaryComponent,
    CustomMessageComponent,
    StatusComponent,
    ToolExecutionComponent,
    ToolResultComponent,
    UserMessageComponent,
)
from cli.interactive.tool_renderer_registry import (
    ToolRenderContext,
    ToolRendererRegistry,
    generic_tool_renderer,
)


def _zero_usage() -> Usage:
    return Usage(
        input=0,
        output=0,
        cacheRead=0,
        cacheWrite=0,
        totalTokens=0,
        cost=UsageCost(input=0, output=0, cacheRead=0, cacheWrite=0, total=0),
    )


def test_user_message_renders_role_header_and_text() -> None:
    msg = UserMessage(role="user", content="hello", timestamp=1)
    out = UserMessageComponent(msg).render(40)
    assert any("user" in line for line in out)
    assert any("hello" in line for line in out)


def test_assistant_message_streaming_text_accumulates() -> None:
    comp = AssistantMessageComponent()
    base_partial = AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text="")],
        api="x",
        provider="x",
        model="x",
        usage=_zero_usage(),
        stopReason="stop",
        timestamp=1,
    )
    comp.apply(StreamTextDelta(contentIndex=0, delta="Hi", partial=base_partial))
    comp.apply(StreamTextDelta(contentIndex=0, delta=" world", partial=base_partial))
    out = "\n".join(comp.render(40))
    assert "Hi world" in out


def test_assistant_message_initial_error_renders_error_text() -> None:
    msg = AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text="")],
        api="openai-responses",
        provider="openai",
        model="gpt-4o-mini",
        usage=_zero_usage(),
        stopReason="error",
        errorMessage="missing API key for provider 'openai'",
        timestamp=1,
    )
    out = "\n".join(AssistantMessageComponent(msg).render(80))
    assert "missing API key" in out


def test_tool_result_renders_error_glyph_when_is_error() -> None:
    msg = ToolResultMessage(
        role="toolResult",
        toolCallId="c1",
        toolName="read",
        content=[TextContent(type="text", text="boom")],
        isError=True,
        timestamp=1,
    )
    out = "\n".join(ToolResultComponent(msg).render(60))
    assert "✗" in out
    assert "read" in out


def test_bash_execution_excluded_from_context_marker() -> None:
    msg = BashExecutionMessage(
        role="bashExecution",
        command="ls",
        output="a\nb",
        cancelled=False,
        truncated=False,
        timestamp=1,
        excludeFromContext=True,
    )
    out = "\n".join(BashExecutionComponent(msg).render(60))
    assert "[no-context]" in out


def test_custom_message_skipped_when_display_false() -> None:
    msg = CustomMessage(
        role="custom",
        customType="dbg",
        content="hidden",
        display=False,
        timestamp=1,
    )
    assert CustomMessageComponent(msg).render(40) == []


def test_branch_summary_renders_from_id() -> None:
    msg = BranchSummaryMessage(
        role="branchSummary",
        summary="a branch was summarised",
        fromId="01abc",
        timestamp=1,
    )
    out = "\n".join(BranchSummaryComponent(msg).render(60))
    assert "01abc" in out


def test_compaction_summary_renders_tokens_before() -> None:
    msg = CompactionSummaryMessage(
        role="compactionSummary",
        summary="compacted!",
        tokensBefore=42,
        timestamp=1,
    )
    out = "\n".join(CompactionSummaryComponent(msg).render(60))
    assert "42" in out
    assert "compacted!" in out


def test_status_notifications_render_with_color_marker() -> None:
    s = StatusComponent()
    s.push_notification("hello", level="warn")
    out = "\n".join(s.render(40))
    assert "hello" in out


def test_generic_tool_renderer_includes_duration_after_end() -> None:
    ctx = ToolRenderContext(
        tool_name="read",
        tool_call_id="c1",
        args={"path": "x"},
        partial_result=None,
        result={"content": [{"type": "text", "text": "ok"}]},
        is_error=False,
        is_partial=False,
        started_at_ms=1000,
        last_update_at_ms=None,
        ended_at_ms=1250,
    )
    out = "\n".join(generic_tool_renderer(ctx, 60))
    assert "duration: 250 ms" in out
    assert "[ok]" in out


def test_registry_falls_back_to_generic_when_no_specific_registered() -> None:
    """Plan §W4 — fallback path is generic until M5 registers tool-specific
    renderers from ``src/cli/tools/``."""

    reg = ToolRendererRegistry()
    ts = [0]

    def clock() -> int:
        ts[0] += 100
        return ts[0]

    comp = ToolExecutionComponent(
        tool_call_id="c1",
        tool_name="read",
        args={"path": "x"},
        registry=reg,
        clock=clock,
    )
    comp.update({"content": [{"type": "text", "text": "partial"}]})
    out = "\n".join(comp.render(60))
    assert "partial" in out


def test_registry_uses_specific_renderer_when_registered() -> None:
    reg = ToolRendererRegistry()

    def my_renderer(_ctx, _w):
        return ["MY-CUSTOM-RENDERER"]

    reg.register("read", my_renderer)
    comp = ToolExecutionComponent(
        tool_call_id="c1",
        tool_name="read",
        args={},
        registry=reg,
    )
    out = comp.render(60)
    assert any("MY-CUSTOM-RENDERER" in line for line in out)


def test_generic_renderer_emits_only_supported_event_fields() -> None:
    """Plan §W4 — `truncated` is intentionally NOT a generic-renderer
    field because ``ToolExecutionEndEvent`` has no ``truncated`` attribute.
    The renderer must not invent it."""

    end = ToolExecutionEndEvent(
        toolCallId="c", toolName="read", result={"content": []}, isError=False
    )
    assert not hasattr(end, "truncated")
    # And the registry context dataclass also has no ``truncated`` knob.
    assert "truncated" not in ToolRenderContext.__dataclass_fields__
    # `start` and `update` events both declare the canonical fields.
    start = ToolExecutionStartEvent(toolCallId="c", toolName="read", args={})
    update = ToolExecutionUpdateEvent(
        toolCallId="c", toolName="read", args={}, partialResult={}
    )
    assert start.tool_call_id == "c"
    assert update.partial_result == {}
