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
    RunDividerComponent,
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
from tui.width import strip_ansi, visible_width


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


def test_user_message_renders_resource_command_display_metadata() -> None:
    msg = UserMessage(
        role="user",
        content="expanded skill body",
        timestamp=1,
        resourceCommand={
            "display": "/skill:reviewer target.py",
            "displayMode": "compact",
        },
    )

    out = "\n".join(UserMessageComponent(msg).render(80))

    assert "/skill:reviewer target.py" in out
    assert "expanded skill body" not in out


def test_user_message_verbose_resource_command_renders_provider_content() -> None:
    msg = UserMessage(
        role="user",
        content="expanded skill body",
        timestamp=1,
        resourceCommand={
            "display": "/skill:reviewer target.py",
            "displayMode": "expanded",
        },
    )

    out = "\n".join(UserMessageComponent(msg).render(80))

    assert "expanded skill body" in out
    assert "/skill:reviewer target.py" not in out


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


def test_tool_result_renders_error_header_when_is_error() -> None:
    msg = ToolResultMessage(
        role="toolResult",
        toolCallId="c1",
        toolName="read",
        content=[TextContent(type="text", text="boom")],
        isError=True,
        timestamp=1,
    )
    out = "\n".join(ToolResultComponent(msg).render(60))
    assert "⏺ Read read [error]" in out
    assert "└ boom" in out


def test_read_tool_result_renders_line_number_gutter_when_wide() -> None:
    msg = ToolResultMessage(
        role="toolResult",
        toolCallId="c1",
        toolName="read",
        content=[TextContent(type="text", text="ten\neleven\ntwelve\n\n[2 more lines in file.]")],
        details={
            "path": "README.md",
            "lineStart": 10,
            "lineEnd": 12,
            "totalLines": 14,
            "outputLines": 3,
        },
        isError=False,
        timestamp=1,
    )

    out = "\n".join(ToolResultComponent(msg).render(80))

    assert "README.md:10-12" in out
    assert "10 | ten" in out
    assert "11 | eleven" in out
    assert "12 | twelve" in out
    assert "[2 more lines in file.]" in out


def test_read_tool_result_without_line_metadata_uses_generic_renderer() -> None:
    msg = ToolResultMessage(
        role="toolResult",
        toolCallId="c1",
        toolName="read",
        content=[TextContent(type="text", text="plain legacy output")],
        details={"path": "README.md"},
        isError=False,
        timestamp=1,
    )

    out = "\n".join(ToolResultComponent(msg).render(80))

    assert "plain legacy output" in out
    assert "README.md:" not in out
    assert "1 |" not in out


def test_compact_tool_result_renderer_does_not_mutate_provider_visible_content() -> None:
    msg = ToolResultMessage(
        role="toolResult",
        toolCallId="c1",
        toolName="read",
        content=[TextContent(type="text", text="plain provider output")],
        details={"path": "README.md"},
        isError=False,
        timestamp=1,
    )

    ToolResultComponent(msg).render(80)

    assert msg.content[0].text == "plain provider output"
    assert "⏺" not in msg.content[0].text
    assert "└" not in msg.content[0].text


def test_read_tool_result_narrow_width_uses_short_preview() -> None:
    msg = ToolResultMessage(
        role="toolResult",
        toolCallId="c1",
        toolName="read",
        content=[TextContent(type="text", text="first\nsecond")],
        details={
            "path": "README.md",
            "lineStart": 1,
            "lineEnd": 2,
            "totalLines": 2,
            "outputLines": 2,
        },
        isError=False,
        timestamp=1,
    )

    out = "\n".join(ToolResultComponent(msg).render(49))

    assert "first" in out
    assert "README.md:1-2" in out
    assert "second" not in out
    assert "⋮" in out
    assert "1 | first" not in out


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
    assert "⏺ Ran [!] ls" in out


def test_bash_execution_summarises_multiline_command_as_single_rows() -> None:
    msg = BashExecutionMessage(
        role="bashExecution",
        command="cat > weather.py <<'PY'\n#!/usr/bin/env python3\nprint('ok')\nPY",
        output="created:weather.py",
        cancelled=False,
        truncated=False,
        timestamp=1,
    )

    rows = BashExecutionComponent(msg).render(80)
    out = "\n".join(rows)

    assert all("\n" not in row and "\r" not in row for row in rows)
    assert "⏺ Ran [user] cat > weather.py <<'PY' (+3 lines)" in out
    assert "#!/usr/bin/env python3" not in out


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


def test_transcript_components_use_shared_marker_not_legacy_bar() -> None:
    components = [
        UserMessageComponent(UserMessage(content="hello", timestamp=1)),
        AssistantMessageComponent(
            AssistantMessage(
                role="assistant",
                content=[TextContent(text="hi")],
                api="openai-responses",
                provider="openai",
                model="gpt-4o-mini",
                usage=_zero_usage(),
                stopReason="stop",
                timestamp=2,
            )
        ),
        BranchSummaryComponent(
            BranchSummaryMessage(
                role="branchSummary",
                summary="branch",
                fromId="01abc",
                timestamp=3,
            )
        ),
        CompactionSummaryComponent(
            CompactionSummaryMessage(
                role="compactionSummary",
                summary="compact",
                tokensBefore=42,
                timestamp=4,
            )
        ),
    ]

    rendered = "\n".join(row for component in components for row in component.render(80))

    assert "⏺ user" in rendered
    assert "⏺ assistant" in rendered
    assert "⏺ branch summary" in rendered
    assert "⏺ compaction summary" in rendered
    assert "▎" not in rendered


def test_status_notifications_render_with_color_marker() -> None:
    s = StatusComponent()
    s.push_notification("hello", level="warn")
    out = "\n".join(s.render(40))
    assert "hello" in out


def test_status_notification_lane_stays_below_status_row() -> None:
    s = StatusComponent()
    s.push_notification("one\ntwo", level="info")

    rows = s.render(40)

    assert len(rows) == 3
    assert all("\n" not in row for row in rows)
    assert rows[0].strip() == ""
    assert "● one" in rows[1]
    assert "  two" in rows[2]


def test_run_divider_renders_elapsed_time_as_single_row() -> None:
    rows = RunDividerComponent(elapsed_ms=69_000).render(80)

    assert len(rows) == 1
    assert "Worked for 1m 09s" in rows[0]
    assert all("\n" not in row and "\r" not in row for row in rows)


def test_run_divider_narrow_width_does_not_overflow() -> None:
    rows = RunDividerComponent(elapsed_ms=69_000).render(12)

    assert len(rows) == 1
    assert all("\n" not in row and "\r" not in row for row in rows)


def test_run_divider_deferred_render_avoids_full_width_rule() -> None:
    rows = RunDividerComponent(elapsed_ms=6_000).render_deferred(80)

    assert len(rows) == 1
    assert "Worked for 6s" in rows[0]
    assert "─" in strip_ansi(rows[0])
    assert visible_width(rows[0].rstrip()) <= 36


def test_status_split_renderers_support_bottom_notification_lane() -> None:
    s = StatusComponent()
    s.push_notification("saved", level="info")

    status_rows = s.render_status(40)
    notification_rows = s.render_notifications(40)

    assert status_rows == []
    assert len(notification_rows) == 1
    assert "● saved" in notification_rows[0]


def test_generic_tool_renderer_omits_fast_success_status_duration() -> None:
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
    assert "⏺ Ran read" in out
    assert "duration: 250 ms" not in out
    assert "[ok]" not in out


def test_registered_read_renderer_keeps_partial_preview() -> None:
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


def test_registered_read_renderer_uses_compact_final_preview() -> None:
    comp = ToolExecutionComponent(
        tool_call_id="c1",
        tool_name="read",
        args={"path": "README.md"},
        registry=ToolRendererRegistry(),
    )
    comp.end(
        {
            "content": [{"type": "text", "text": "first\nsecond"}],
            "details": {
                "path": "README.md",
                "lineStart": 1,
                "lineEnd": 2,
                "outputLines": 2,
            },
        },
        is_error=False,
    )

    out = "\n".join(comp.render(80))

    assert "⏺ Read README.md:1-2" in out
    assert "└ first" in out
    assert "1 | first" not in out


def test_edit_renderer_shows_path_stats_and_small_diff_preview() -> None:
    comp = ToolExecutionComponent(
        tool_call_id="c1",
        tool_name="edit",
        args={"path": "app.py"},
        registry=ToolRendererRegistry(),
    )
    comp.end(
        {
            "content": [{"type": "text", "text": "Successfully replaced 1 block."}],
            "details": {
                "path": "app.py",
                "unifiedDiff": "--- app.py\n+++ app.py\n@@ -1,2 +1,2 @@\n-old\n+new\n keep\n",
            },
        },
        is_error=False,
    )

    out = "\n".join(comp.render(80))

    assert "⏺ Edited app.py (+1 -1)" in out
    assert "+new" in out
    assert "-old" in out


def test_write_renderer_shows_path_and_result_preview() -> None:
    comp = ToolExecutionComponent(
        tool_call_id="c1",
        tool_name="write",
        args={"path": "notes.md"},
        registry=ToolRendererRegistry(),
    )
    comp.end(
        {
            "content": [{"type": "text", "text": "Successfully wrote 12 bytes to notes.md."}],
            "details": {"path": "notes.md"},
        },
        is_error=False,
    )

    out = "\n".join(comp.render(80))

    assert "⏺ Wrote notes.md" in out
    assert "└ Successfully wrote" in out


def test_bash_renderer_summarises_multiline_command_as_single_rows() -> None:
    comp = ToolExecutionComponent(
        tool_call_id="c1",
        tool_name="bash",
        args={
            "command": "cat > weather.py <<'PY'\n#!/usr/bin/env python3\nprint('ok')\nPY",
        },
        registry=ToolRendererRegistry(),
    )

    rows = comp.render(80)
    out = "\n".join(rows)

    assert all("\n" not in row and "\r" not in row for row in rows)
    assert "⏺ Ran cat > weather.py <<'PY' (+3 lines) [running]" in out
    assert "#!/usr/bin/env python3" not in out


def test_registry_falls_back_to_generic_for_unknown_tools() -> None:
    reg = ToolRendererRegistry()
    comp = ToolExecutionComponent(
        tool_call_id="c1",
        tool_name="custom_tool",
        args={"x": 1},
        registry=reg,
    )
    out = "\n".join(comp.render(60))
    assert "custom_tool" in out
    assert "└ (no output yet)" in out


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
