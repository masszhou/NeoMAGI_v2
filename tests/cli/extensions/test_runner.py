from __future__ import annotations

import asyncio

from cli.extensions.loader import load_extension_from_factory
from cli.extensions.runner import ExtensionRunner
from cli.extensions.runtime import create_extension_runtime
from cli.extensions.types import BeforeAgentStartEvent, InputEvent
from cli.extensions.ui import NoopExtensionUIContext


def _runner_from_setups(*setups):
    runtime = create_extension_runtime()
    for index, setup in enumerate(setups):
        asyncio.run(load_extension_from_factory(setup, name=f"ext-{index}", runtime=runtime))
    return ExtensionRunner(runtime)


def test_context_handlers_chain_messages() -> None:
    def first(api) -> None:
        api.on("context", lambda event: {"messages": [*event["messages"], "first"]})

    def second(api) -> None:
        api.on("context", lambda event: {"messages": [*event["messages"], "second"]})

    runner = _runner_from_setups(first, second)

    assert asyncio.run(runner.emit_context(["base"])) == ["base", "first", "second"]


def test_tool_call_mutation_visible_and_block_short_circuits() -> None:
    calls: list[str] = []

    def first(api) -> None:
        def handler(event):
            event["input"]["path"] = "patched"
            calls.append("first")

        api.on("tool_call", handler)

    def second(api) -> None:
        def handler(event):
            calls.append(event["input"]["path"])
            return {"block": True, "reason": "blocked"}

        api.on("tool_call", handler)

    def third(api) -> None:
        api.on("tool_call", lambda _event: calls.append("third"))

    runner = _runner_from_setups(first, second, third)
    result = asyncio.run(
        runner.emit_tool_call({"type": "tool_call", "toolCallId": "1", "toolName": "write", "input": {}})
    )

    assert calls == ["first", "patched"]
    assert result is not None
    assert result.reason == "blocked"


def test_tool_result_patches_accumulate() -> None:
    def first(api) -> None:
        api.on("tool_result", lambda _event: {"details": {"a": 1}})

    def second(api) -> None:
        api.on("tool_result", lambda _event: {"content": [{"type": "text", "text": "patched"}], "isError": True})

    runner = _runner_from_setups(first, second)
    event = {
        "type": "tool_result",
        "toolCallId": "1",
        "toolName": "read",
        "input": {},
        "content": [{"type": "text", "text": "old"}],
        "isError": False,
    }

    patched = asyncio.run(runner.emit_tool_result(event))

    assert patched["details"] == {"a": 1}
    assert patched["content"][0].text == "patched"
    assert patched["isError"] is True


def test_user_bash_first_result_wins() -> None:
    def first(api) -> None:
        api.on(
            "user_bash",
            lambda _event: {"result": {"output": "one", "exitCode": 0, "cancelled": False, "truncated": False}},
        )

    def second(api) -> None:
        api.on(
            "user_bash",
            lambda _event: {"result": {"output": "two", "exitCode": 0, "cancelled": False, "truncated": False}},
        )

    runner = _runner_from_setups(first, second)
    result = asyncio.run(runner.emit_user_bash({"type": "user_bash", "command": "echo", "excludeFromContext": False, "cwd": "."}))

    assert result is not None
    assert result.result is not None
    assert result.result.output == "one"


def test_before_agent_start_chains_system_prompt_and_messages() -> None:
    def first(api) -> None:
        api.on("before_agent_start", lambda _event: {"message": {"role": "user"}, "systemPrompt": "first"})

    def second(api) -> None:
        api.on("before_agent_start", lambda event: {"systemPrompt": event.system_prompt + "+second"})

    runner = _runner_from_setups(first, second)
    messages, system_prompt = asyncio.run(
        runner.emit_before_agent_start(
            BeforeAgentStartEvent(prompt="hello", systemPrompt="base", systemPromptOptions={})
        )
    )

    assert messages == [{"role": "user"}]
    assert system_prompt == "first+second"


def test_input_transform_and_handled_short_circuit() -> None:
    def first(api) -> None:
        api.on("input", lambda event: {"action": "transform", "text": event.text + " one"})

    def second(api) -> None:
        api.on("input", lambda _event: {"action": "handled"})

    runner = _runner_from_setups(first, second)
    result = asyncio.run(runner.emit_input(InputEvent(text="base", source="interactive")))

    assert result.action == "handled"


def test_resources_discover_collects_paths(tmp_path) -> None:
    skill_path = tmp_path / "skills"

    def setup(api) -> None:
        api.on("resources_discover", lambda _event: {"skillPaths": [str(skill_path)]})

    runner = _runner_from_setups(setup)
    paths = asyncio.run(runner.emit_resources_discover(str(tmp_path), "reload"))

    assert paths.skills == (skill_path.resolve(),)


def test_handler_exceptions_become_diagnostics() -> None:
    def setup(api) -> None:
        def fail(_event):
            raise RuntimeError("boom")

        api.on("context", fail)

    runner = _runner_from_setups(setup)

    assert asyncio.run(runner.emit_context([])) == []
    assert any("boom" in diagnostic.message for diagnostic in runner.diagnostics)


def test_runner_exposes_noop_ui_context() -> None:
    runner = _runner_from_setups(lambda _api: None)
    context = runner.create_context()

    assert isinstance(context["ui"], NoopExtensionUIContext)
    assert context["has_ui"] is False
