from __future__ import annotations

import time

from agent_core import Agent
from ai_provider.auth_storage import AUTH_PATH_ENV
from cli.interactive.runtime import InteractiveAgentRuntime


def _drain_until_idle(runtime: InteractiveAgentRuntime, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    events = []
    while time.monotonic() < deadline:
        events.extend(runtime.drain_events())
        if not runtime.state.is_running and any(event.type == "agent_end" for event in events):
            events.extend(runtime.drain_events())
            return events
        time.sleep(0.01)
    raise AssertionError("runtime did not become idle")


def test_runtime_submit_queues_validated_session_events() -> None:
    runtime = InteractiveAgentRuntime()
    try:
        runtime.submit("hello")
        events = _drain_until_idle(runtime)
    finally:
        runtime.shutdown()

    event_types = [event.type for event in events]
    assert event_types[0] == "queue_update"
    assert event_types[-1] == "queue_update"
    assert "message_start" in event_types
    assert "message_update" in event_types
    assert "agent_end" in event_types
    assert runtime.state.queued_steering == ()
    assert runtime.state.queued_follow_up == ()
    assert runtime.state.provider_cache_affinity_id


def test_runtime_missing_provider_credentials_becomes_assistant_error(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv(AUTH_PATH_ENV, str(tmp_path / "auth.json"))
    runtime = InteractiveAgentRuntime(model_ref="openai/gpt-4o-mini")
    try:
        runtime.submit("hello")
        events = _drain_until_idle(runtime)
    finally:
        runtime.shutdown()

    ended_messages = [
        event.message for event in events if event.type == "message_end"
    ]
    assert ended_messages[-1].role == "assistant"
    assert ended_messages[-1].error_message
    assert "missing API key" in ended_messages[-1].error_message


def test_runtime_supplies_default_system_prompt_for_codex() -> None:
    captured = []

    def factory(options):
        captured.append(options)
        return Agent(options)

    runtime = InteractiveAgentRuntime(
        model_ref="openai-codex/gpt-5.3-codex",
        agent_factory=factory,
    )
    try:
        assert captured[0].system_prompt == "You are a helpful coding assistant."
    finally:
        runtime.shutdown()


def test_runtime_reset_mints_new_session_and_drops_old_queue() -> None:
    runtime = InteractiveAgentRuntime()
    try:
        before = runtime.state.runtime_session_id
        runtime.steer("queued while idle becomes submit")
        _drain_until_idle(runtime)
        runtime.reset()
        after = runtime.state.runtime_session_id
    finally:
        runtime.shutdown()

    assert before != after
    assert runtime.state.queued_steering == ()
    assert runtime.state.queued_follow_up == ()
