"""W5 playback harness end-to-end tests against the 7 M1 fixtures."""

from __future__ import annotations

from pathlib import Path

from cli.interactive.app import InteractiveController
from cli.interactive.components import (
    AssistantMessageComponent,
    CompactionSummaryComponent,
    ToolExecutionComponent,
)
from cli.interactive.playback import PlaybackHarness
from tui.app import TUIApp
from tui.editor import EditorState

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "pi_compat"


def _play(name: str) -> InteractiveController:
    app = TUIApp()
    controller = InteractiveController(tui_app=app)
    controller.bootstrap()
    harness = PlaybackHarness(FIXTURE_ROOT / name, controller=controller)
    harness.play_sync()
    return controller


def test_assistant_text_delta_final_message_equals_hello_world() -> None:
    c = _play("assistant_text_delta")
    assistants = [
        child for child in c.messages.children if isinstance(child, AssistantMessageComponent)
    ]
    assert len(assistants) == 1
    text_blocks = [s.text for s in assistants[0]._slots if s.kind == "text"]
    assert "Hello, world." in "".join(text_blocks)


def test_assistant_thinking_delta_thinking_block_accumulates() -> None:
    c = _play("assistant_thinking_delta")
    assistants = [
        child for child in c.messages.children if isinstance(child, AssistantMessageComponent)
    ]
    assert len(assistants) == 1
    thinking_blocks = [s.text for s in assistants[0]._slots if s.kind == "thinking"]
    assert "Let me think about this carefully." in "".join(thinking_blocks)


def test_tool_execution_success_renders_tool_name_and_result() -> None:
    c = _play("tool_execution_success")
    tools = [
        child for child in c.messages.children if isinstance(child, ToolExecutionComponent)
    ]
    assert len(tools) == 1
    out = "\n".join(tools[0].render(80))
    assert "read" in out
    assert "main.py" in out


def test_parallel_tools_emits_two_streaming_components() -> None:
    c = _play("parallel_tools")
    tools = [
        child for child in c.messages.children if isinstance(child, ToolExecutionComponent)
    ]
    assert len(tools) == 2
    assert {t.tool_name for t in tools} == {"read", "grep"}


def test_compaction_renders_summary_component_with_text_kept() -> None:
    c = _play("compaction")
    summaries = [
        child
        for child in c.messages.children
        if isinstance(child, CompactionSummaryComponent)
    ]
    assert len(summaries) == 1
    out = "\n".join(summaries[0].render(120))
    assert "Initial response sent." in out


def test_abort_during_stream_keeps_partial_text_and_returns_editor_to_idle() -> None:
    c = _play("abort_during_stream")
    assistants = [
        child for child in c.messages.children if isinstance(child, AssistantMessageComponent)
    ]
    assert len(assistants) == 1
    assistant = assistants[0]
    # Partial text must still be visible.
    text = "".join(s.text for s in assistant._slots if s.kind == "text")
    assert "first half" in text
    assert assistant.aborted is True
    assert c.editor.state == EditorState.IDLE


def test_abort_during_tool_marks_tool_aborted_and_returns_editor_to_idle() -> None:
    c = _play("abort_during_tool")
    tools = [
        child for child in c.messages.children if isinstance(child, ToolExecutionComponent)
    ]
    assert len(tools) == 1
    assert tools[0].aborted is True
    assert c.editor.state == EditorState.IDLE


# Removed: a parametrized "at least one component was created" smoke. Each
# of the 7 M1 fixtures already has a named, behavioral test above asserting
# the actual end-state (final text, tool name, abort flag, summary text…).
# A weaker "plays without raising" smoke would just inflate the case count
# while adding no protection that the named tests don't already provide.
