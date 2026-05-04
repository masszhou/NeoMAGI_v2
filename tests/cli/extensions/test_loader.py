from __future__ import annotations

import asyncio

from cli.extensions import RuntimeNotInitializedError, load_extension_from_factory, load_extensions
from cli.extensions.event_bus import ExtensionEventBus
from cli.extensions.runtime import create_extension_runtime


def test_load_extension_from_factory_registers_surface(tmp_path) -> None:
    def setup(api) -> None:
        api.register_command("hello", {"description": "Hello"})
        api.register_flag("enabled", {"description": "Enabled", "type": "boolean", "default": True})
        api.register_provider("demo", {"baseUrl": "https://example.test", "models": []})
        api.on("context", lambda event: {"messages": [*event["messages"], {"role": "user"}]})

    runtime = create_extension_runtime()
    extension = asyncio.run(load_extension_from_factory(setup, name="demo", cwd=tmp_path, runtime=runtime))

    assert extension.commands["hello"]["description"] == "Hello"
    assert extension.flag_values["enabled"] is True
    assert "demo" in extension.providers
    assert len(extension.handlers["context"]) == 1
    assert any("not applied in M8" in diagnostic.message for diagnostic in extension.diagnostics)


def test_live_actions_fail_during_setup(tmp_path) -> None:
    def setup(api) -> None:
        try:
            api.send_message({"role": "user", "content": "nope"})
        except RuntimeNotInitializedError:
            raise

    result = asyncio.run(load_extensions([], cwd=tmp_path))
    extension = asyncio.run(
        load_extension_from_factory(setup, name="bad", cwd=tmp_path, runtime=result.runtime)
    )

    assert any("runtime not initialized" in diagnostic.message for diagnostic in extension.diagnostics)


def test_extension_file_reload_uses_new_module_contents(tmp_path) -> None:
    extension_file = tmp_path / "demo.py"
    extension_file.write_text(
        "def setup(api):\n    api.register_command('demo', {'description': 'v1'})\n",
        encoding="utf-8",
    )
    first = asyncio.run(load_extensions([extension_file], cwd=tmp_path))
    extension_file.write_text(
        "def setup(api):\n    api.register_command('demo', {'description': 'v2'})\n",
        encoding="utf-8",
    )
    second = asyncio.run(load_extensions([extension_file], cwd=tmp_path))

    assert first.runtime.extensions[0].commands["demo"]["description"] == "v1"
    assert second.runtime.extensions[0].commands["demo"]["description"] == "v2"


def test_event_bus_ping_pong_diagnostics() -> None:
    bus = ExtensionEventBus()
    seen: list[object] = []
    unsubscribe = bus.on("ping", seen.append)

    bus.emit("ping", {"ok": True})
    unsubscribe()
    bus.emit("ping", {"ok": False})

    assert seen == [{"ok": True}]
    assert bus.diagnostics == []


def test_live_actions_resolve_after_runtime_bind(tmp_path) -> None:
    seen: list[object] = []

    def setup(api) -> None:
        api.on("context", lambda _event: api.get_commands())

    runtime = create_extension_runtime()
    extension = asyncio.run(load_extension_from_factory(setup, name="actions", cwd=tmp_path, runtime=runtime))
    runtime.actions["get_commands"] = lambda: [{"name": "hello"}]
    runtime.bound = True

    result = extension.handlers["context"][0]({"type": "context", "messages": []})
    seen.append(result)

    assert seen == [[{"name": "hello"}]]
