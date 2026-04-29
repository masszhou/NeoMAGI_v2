from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any

from agent_core import Agent, AgentOptions
from ai_provider.auth_storage import AUTH_PATH_ENV
from ai_provider.providers.faux import faux_assistant_message
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.streaming import create_assistant_message_event_stream
from ai_provider.types import Model, StreamDone, StreamStart, StreamTextDelta, TextContent
from cli.interactive.app import InteractiveController
from cli.interactive.components import AssistantMessageComponent, UserMessageComponent
from cli.interactive.runtime import InteractiveAgentRuntime
from tui.app import TUIApp
from tui.editor import EditorState, EditorSubmission
from tui.keymap import Action


def _slow_stream(model: Model, _context: Any, options: SimpleStreamOptions | None = None):
    options = options or SimpleStreamOptions()
    partial = faux_assistant_message("", model)
    stream = create_assistant_message_event_stream(initial=partial)

    async def run() -> None:
        stream.push(StreamStart(partial=partial.model_copy(deep=True)))
        await asyncio.sleep(0.2)
        if options.signal is not None and options.signal.is_set():
            stream.close()
            return
        partial.content = [TextContent(text="slow")]
        stream.push(
            StreamTextDelta(
                contentIndex=0,
                delta="slow",
                partial=partial.model_copy(deep=True),
            )
        )
        final = faux_assistant_message("slow", model)
        stream.push(StreamDone(reason="stop", message=final))

    asyncio.create_task(run())
    return stream


def _agent_factory(options: AgentOptions) -> Agent:
    return Agent(replace(options, stream_fn=_slow_stream))


def _controller_with_runtime() -> tuple[TUIApp, InteractiveController, InteractiveAgentRuntime]:
    app = TUIApp()
    runtime = InteractiveAgentRuntime(agent_factory=_agent_factory)
    controller = InteractiveController(tui_app=app, runtime=runtime)
    controller.bootstrap()
    return app, controller, runtime


def _drain_controller(controller: InteractiveController, runtime: InteractiveAgentRuntime) -> None:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        controller._drain_runtime_events()  # noqa: SLF001
        if not runtime.state.is_running:
            time.sleep(0.02)
            controller._drain_runtime_events()  # noqa: SLF001
            return
        time.sleep(0.01)
    raise AssertionError("controller did not drain runtime")


def test_idle_submit_renders_user_and_assistant_then_returns_idle() -> None:
    _app, controller, runtime = _controller_with_runtime()
    try:
        controller._handle_runtime_submit(  # noqa: SLF001
            EditorSubmission("hello", EditorState.IDLE)
        )
        assert controller.editor.state == EditorState.STREAMING
        _drain_controller(controller, runtime)
    finally:
        runtime.shutdown()

    assert controller.editor.state == EditorState.IDLE
    assert any(isinstance(child, UserMessageComponent) for child in controller.messages.children)
    assert any(
        isinstance(child, AssistantMessageComponent)
        for child in controller.messages.children
    )


def test_missing_provider_credentials_render_visible_assistant_error(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(AUTH_PATH_ENV, str(tmp_path / "auth.json"))
    app = TUIApp()
    runtime = InteractiveAgentRuntime(model_ref="openai/gpt-4o-mini")
    controller = InteractiveController(tui_app=app, runtime=runtime)
    controller.bootstrap()
    try:
        controller._handle_runtime_submit(  # noqa: SLF001
            EditorSubmission("hello", EditorState.IDLE)
        )
        _drain_controller(controller, runtime)
    finally:
        runtime.shutdown()

    rendered = "\n".join(
        "\n".join(child.render(100))
        for child in controller.messages.children
        if isinstance(child, AssistantMessageComponent)
    )
    assert "missing API key" in rendered


def test_abort_moves_through_aborting_and_settles_idle() -> None:
    _app, controller, runtime = _controller_with_runtime()
    try:
        controller._handle_runtime_submit(  # noqa: SLF001
            EditorSubmission("hello", EditorState.IDLE)
        )
        assert controller.editor.state == EditorState.STREAMING
        controller.handle_abort()
        assert controller.editor.state == EditorState.ABORTING
        _drain_controller(controller, runtime)
    finally:
        runtime.shutdown()

    assert controller.editor.state == EditorState.IDLE


def test_submit_while_aborting_waits_for_abort_settlement() -> None:
    _app, controller, runtime = _controller_with_runtime()
    try:
        controller._handle_runtime_submit(  # noqa: SLF001
            EditorSubmission("hello", EditorState.IDLE)
        )
        controller.handle_abort()
        assert controller.editor.state == EditorState.ABORTING

        controller._handle_runtime_submit(  # noqa: SLF001
            EditorSubmission("too soon", EditorState.ABORTING)
        )

        assert controller.editor.state == EditorState.ABORTING
        assert any(
            "waiting for abort" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )
        _drain_controller(controller, runtime)
    finally:
        runtime.shutdown()

    assert controller.editor.state == EditorState.IDLE


def test_streaming_submit_and_followup_action_update_queue_status() -> None:
    _app, controller, runtime = _controller_with_runtime()
    try:
        controller._handle_runtime_submit(  # noqa: SLF001
            EditorSubmission("hello", EditorState.IDLE)
        )
        controller._handle_runtime_submit(  # noqa: SLF001
            EditorSubmission("steer", EditorState.STREAMING)
        )
        controller.editor.buffer.insert("follow")
        controller._handle_runtime_action(Action.QUEUE_FOLLOWUP)  # noqa: SLF001
        controller._drain_runtime_events()  # noqa: SLF001

        assert controller.status.queue.steering == ["steer"]
        assert controller.status.queue.follow_up == ["follow"]

        controller.handle_abort()
        _drain_controller(controller, runtime)
    finally:
        runtime.shutdown()

    assert controller.status.queue.steering == []
    assert controller.status.queue.follow_up == []


def test_idle_followup_action_starts_normal_prompt() -> None:
    _app, controller, runtime = _controller_with_runtime()
    try:
        controller.editor.buffer.insert("hello")
        controller._handle_runtime_action(Action.QUEUE_FOLLOWUP)  # noqa: SLF001
        assert controller.editor.state == EditorState.STREAMING
        _drain_controller(controller, runtime)
    finally:
        runtime.shutdown()

    assert controller.editor.state == EditorState.IDLE
    assert any(isinstance(child, UserMessageComponent) for child in controller.messages.children)


def test_new_resets_runtime_and_message_list() -> None:
    _app, controller, runtime = _controller_with_runtime()
    try:
        controller._handle_runtime_submit(  # noqa: SLF001
            EditorSubmission("hello", EditorState.IDLE)
        )
        _drain_controller(controller, runtime)
        before = runtime.state.runtime_session_id
        assert controller.messages.children
        controller.reset_session()
        after = runtime.state.runtime_session_id
    finally:
        runtime.shutdown()

    assert before != after
    assert controller.messages.children == []
    assert controller.editor.state == EditorState.IDLE


def test_new_while_streaming_aborts_and_drops_old_events() -> None:
    _app, controller, runtime = _controller_with_runtime()
    try:
        controller._handle_runtime_submit(  # noqa: SLF001
            EditorSubmission("hello", EditorState.IDLE)
        )
        assert runtime.state.is_running
        before = runtime.state.runtime_session_id

        controller.reset_session()
        after = runtime.state.runtime_session_id
        time.sleep(0.25)
        controller._drain_runtime_events()  # noqa: SLF001
    finally:
        runtime.shutdown()

    assert before != after
    assert controller.messages.children == []
    assert controller.editor.state == EditorState.IDLE
    assert any(
        "previous run aborted" in note.text
        for note in controller.status._notifications  # noqa: SLF001
    )


def test_quit_shutdown_stops_runtime_before_exit() -> None:
    app, controller, runtime = _controller_with_runtime()
    controller._handle_runtime_submit(  # noqa: SLF001
        EditorSubmission("hello", EditorState.IDLE)
    )
    assert runtime.state.is_running
    app._running = True  # noqa: SLF001

    controller.exit()

    assert app._running is False  # noqa: SLF001
    assert runtime.state.is_running is False
    assert runtime._thread.is_alive() is False  # noqa: SLF001
