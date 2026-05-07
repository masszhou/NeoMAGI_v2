from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import Future

from cli.core.session_manager import SessionManager
from cli.interactive.app import InteractiveController
from cli.interactive.runtime import InteractiveAgentRuntime
from policy.audit import InMemoryAuditSink
from storage.in_memory_session_repository import InMemorySessionRepository
from tui.app import TUIApp


def _drain_until_idle(runtime: InteractiveAgentRuntime, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    events = []
    while time.monotonic() < deadline:
        events.extend(runtime.drain_events())
        if not runtime.state.is_running and any(getattr(event, "type", None) == "agent_end" for event in events):
            events.extend(runtime.drain_events())
            return events
        time.sleep(0.01)
    raise AssertionError("runtime did not become idle")


def test_runtime_loads_extension_tool_through_governed_path(tmp_path) -> None:
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    (tmp_path / ".pi" / "extensions" / "tool_ext.py").write_text(
        """
async def execute(args, tool_context, signal, on_update):
    return {"content": [{"type": "text", "text": "echo " + args["text"]}], "details": {"ok": True}}

def setup(api):
    api.register_tool({
        "name": "echo_ext",
        "label": "Echo",
        "description": "Echo extension tool",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "execute": execute,
    })
""",
        encoding="utf-8",
    )
    audit = InMemoryAuditSink()
    runtime = InteractiveAgentRuntime(cwd=tmp_path, audit_sink=audit)
    try:
        tool = next(tool for tool in runtime._agent.tools if tool.name == "echo_ext")  # noqa: SLF001
        result = asyncio.run(tool.execute("call-ext", {"text": "hello"}, None, None))
    finally:
        runtime.shutdown()

    assert result.content == [{"type": "text", "text": "echo hello"}]
    assert audit.records[-1].actor == "extension"
    assert audit.records[-1].tool_name == "echo_ext"
    assert audit.records[-1].policy_decision.effect == "allow"


def test_runtime_extension_tool_call_and_result_hooks(tmp_path) -> None:
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    (tmp_path / ".pi" / "extensions" / "hooks.py").write_text(
        """
def setup(api):
    def before(event):
        event["input"]["path"] = "patched.txt"
    def after(event):
        return {"content": [{"type": "text", "text": event["input"]["path"]}], "isError": False}
    api.on("tool_call", before)
    api.on("tool_result", after)
""",
        encoding="utf-8",
    )
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        args = {}
        before = asyncio.run(
            runtime._before_tool_call(  # noqa: SLF001
                _BeforeContext({"id": "1", "name": "read"}, args),
                None,
            )
        )
        after = asyncio.run(
            runtime._after_tool_call(  # noqa: SLF001
                _AfterContext({"id": "1", "name": "read"}, args),
                None,
            )
        )
    finally:
        runtime.shutdown()

    assert before is None
    assert args == {"path": "patched.txt"}
    assert after is not None
    assert after.content == [{"type": "text", "text": "patched.txt"}]
    assert after.is_error is False


def test_runtime_extension_user_bash_intercepts(tmp_path) -> None:
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    (tmp_path / ".pi" / "extensions" / "bash_ext.py").write_text(
        """
def setup(api):
    api.on("user_bash", lambda event: {
        "result": {"output": "intercepted " + event.command, "exitCode": 0, "cancelled": False, "truncated": False}
    })
""",
        encoding="utf-8",
    )
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        result = asyncio.run(runtime._resolve_user_bash_result("echo hi", False))  # noqa: SLF001
    finally:
        runtime.shutdown()

    assert result.output == "intercepted echo hi"


def test_runtime_reload_refreshes_extension_commands(tmp_path) -> None:
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    extension = tmp_path / ".pi" / "extensions" / "cmd.py"
    extension.write_text(
        "def setup(api):\n    api.register_command('hello', {'description': 'v1', 'handler': lambda ctx: None})\n",
        encoding="utf-8",
    )
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        assert runtime.extension_commands()["hello"]["description"] == "v1"
        extension.write_text(
            "def setup(api):\n    api.register_command('hello', {'description': 'v2', 'handler': lambda ctx: None})\n",
            encoding="utf-8",
        )
        summary = runtime.reload_resources()
        assert runtime.extension_commands()["hello"]["description"] == "v2"
    finally:
        runtime.shutdown()

    assert "extensions=1" in summary


def test_runtime_reports_extension_command_collisions(tmp_path) -> None:
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    (tmp_path / ".pi" / "extensions" / "a_first.py").write_text(
        "def setup(api):\n    api.register_command('hello', {'description': 'first', 'handler': lambda ctx: None})\n",
        encoding="utf-8",
    )
    (tmp_path / ".pi" / "extensions" / "b_second.py").write_text(
        "def setup(api):\n    api.register_command('hello', {'description': 'second', 'handler': lambda ctx: None})\n",
        encoding="utf-8",
    )
    (tmp_path / ".pi" / "extensions" / "c_builtin.py").write_text(
        "def setup(api):\n    api.register_command('reload', {'description': 'shadow', 'handler': lambda ctx: None})\n",
        encoding="utf-8",
    )
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        commands = runtime.extension_commands()
        diagnostics = runtime._extension_diagnostics  # noqa: SLF001
        footer = runtime.footer_summary
    finally:
        runtime.shutdown()

    assert commands["hello"]["description"] == "first"
    assert "diagnostics=2" in footer
    assert any("duplicate extension command /hello" in message for message in diagnostics)
    assert any("extension command /reload conflicts with builtin" in message for message in diagnostics)


def test_reload_command_rejects_while_streaming(tmp_path) -> None:
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    controller = InteractiveController(tui_app=TUIApp(), runtime=runtime)
    pending: Future[None] = Future()
    try:
        controller.bootstrap()
        runtime._active_future = pending  # noqa: SLF001
        assert controller._slash_registry is not None  # noqa: SLF001

        handled = controller._slash_registry.parse_and_dispatch("/reload", controller)  # noqa: SLF001

        assert handled is True
        assert any(
            "streaming or tools are running" in note.text
            for note in controller.status._notifications  # noqa: SLF001
        )
    finally:
        runtime._active_future = None  # noqa: SLF001
        runtime.shutdown()


def test_runtime_expands_skill_and_prompt_template_commands(tmp_path) -> None:
    (tmp_path / ".pi" / "skills" / "reviewer").mkdir(parents=True)
    (tmp_path / ".pi" / "prompts").mkdir(parents=True)
    (tmp_path / ".pi" / "skills" / "reviewer" / "SKILL.md").write_text(
        "---\nname: reviewer\ndescription: Review files.\n---\nRead carefully.\n",
        encoding="utf-8",
    )
    (tmp_path / ".pi" / "prompts" / "ask.md").write_text("Question: $1", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        skill = runtime.expand_resource_command("/skill:reviewer target.py")
        prompt = runtime.expand_resource_command("/ask topic")
    finally:
        runtime.shutdown()

    assert skill is not None and "Read carefully." in skill
    assert "User arguments: target.py" in skill
    assert prompt == "Question: topic"


def test_runtime_prepares_resource_command_display_metadata_and_skill_env(tmp_path) -> None:
    (tmp_path / ".pi" / "skills" / "brave-search").mkdir(parents=True)
    (tmp_path / ".pi" / "skills" / "brave-search" / "SKILL.md").write_text(
        "---\nname: brave-search\ndescription: Search the web.\n---\nRun search.\n",
        encoding="utf-8",
    )
    (tmp_path / ".pi" / "settings.json").write_text(
        json.dumps(
            {
                "resources": {
                    "skillEnv": {
                        "brave-search": {
                            "envFile": ".env.brave",
                            "allow": ["BRAVE_API_KEY"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env.brave").write_text('BRAVE_API_KEY="FAKE_BRAVE_SECRET_VALUE"\n', encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        prepared = runtime.prepare_prompt_submission("/skill:brave-search today")
        message = runtime._user_message(prepared)  # noqa: SLF001
    finally:
        runtime.shutdown()

    assert prepared.display_text == "/skill:brave-search today"
    assert "Run search." in prepared.provider_text
    assert prepared.skill_env_grant is not None
    assert prepared.skill_env_grant.names == ("BRAVE_API_KEY",)
    assert prepared.skill_env_grant.env["BRAVE_API_KEY"] == "FAKE_BRAVE_SECRET_VALUE"
    assert message.resourceCommand["display"] == "/skill:brave-search today"
    assert message.content[0].text == prepared.provider_text


def test_runtime_skill_env_missing_file_reports_non_secret_setup_error(tmp_path) -> None:
    (tmp_path / ".pi" / "skills" / "brave-search").mkdir(parents=True)
    (tmp_path / ".pi" / "skills" / "brave-search" / "SKILL.md").write_text(
        "---\nname: brave-search\ndescription: Search the web.\n---\nRun search.\n",
        encoding="utf-8",
    )
    (tmp_path / ".pi" / "settings.json").write_text(
        json.dumps(
            {
                "resources": {
                    "skillEnv": {
                        "brave-search": {
                            "envFile": ".env.brave",
                            "allow": ["BRAVE_API_KEY"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        prepared = runtime.prepare_prompt_submission("/skill:brave-search today")
    finally:
        runtime.shutdown()

    assert prepared.setup_error is not None
    assert "BRAVE_API_KEY" not in prepared.setup_error
    assert "missing envFile '.env.brave'" in prepared.setup_error


def test_runtime_respects_disabled_skill_commands_for_expand_and_autocomplete(tmp_path) -> None:
    (tmp_path / ".pi" / "skills" / "reviewer").mkdir(parents=True)
    (tmp_path / ".pi" / "prompts").mkdir(parents=True)
    (tmp_path / ".pi" / "settings.json").write_text(
        '{"enableSkillCommands": false}',
        encoding="utf-8",
    )
    (tmp_path / ".pi" / "skills" / "reviewer" / "SKILL.md").write_text(
        "---\nname: reviewer\ndescription: Review files.\n---\nRead carefully.\n",
        encoding="utf-8",
    )
    (tmp_path / ".pi" / "prompts" / "ask.md").write_text("Question: $1", encoding="utf-8")
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        skill = runtime.expand_resource_command("/skill:reviewer target.py")
        prompt = runtime.expand_resource_command("/ask topic")
        items = runtime.resource_command_items()
    finally:
        runtime.shutdown()

    assert skill is None
    assert prompt == "Question: topic"
    assert ("/skill:reviewer", "Review files.") not in items
    assert any(item[0] == "/ask" for item in items)


def test_runtime_extension_set_active_tools_refreshes_tools_without_generation_bump(tmp_path) -> None:
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    (tmp_path / ".pi" / "extensions" / "tools.py").write_text(
        """
def setup(api):
    api.register_command("read_only", {
        "description": "read",
        "handler": lambda _ctx: api.set_active_tools(["read"]),
    })
""",
        encoding="utf-8",
    )
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        generation = runtime._generation  # noqa: SLF001
        assert "bash" in {tool.name for tool in runtime._agent.tools}  # noqa: SLF001

        runtime.run_extension_command("read_only", [], "/read_only")

        tool_names = {tool.name for tool in runtime._agent.tools}  # noqa: SLF001
        updated_generation = runtime._generation  # noqa: SLF001
    finally:
        runtime.shutdown()

    assert updated_generation == generation
    assert tool_names == {"read"}


def test_runtime_input_event_can_transform_or_handle(tmp_path) -> None:
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    (tmp_path / ".pi" / "extensions" / "input_ext.py").write_text(
        """
def setup(api):
    api.on("input", lambda event: {"action": "transform", "text": event.text + " transformed"})
""",
        encoding="utf-8",
    )
    runtime = InteractiveAgentRuntime(cwd=tmp_path)
    try:
        transformed = asyncio.run(runtime._apply_input_event("base"))  # noqa: SLF001
    finally:
        runtime.shutdown()

    assert transformed == "base transformed"


def test_runtime_extension_actions_append_custom_entries_and_messages(tmp_path) -> None:
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    (tmp_path / ".pi" / "extensions" / "custom.py").write_text(
        """
def setup(api):
    def emit(_ctx):
        api.append_entry("research_note", {"metric": 0.72})
        api.send_message({"customType": "status_panel", "content": "hello", "display": True})
    api.register_command("emit_custom", {"description": "emit", "handler": emit})
""",
        encoding="utf-8",
    )
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    runtime = InteractiveAgentRuntime(cwd=tmp_path, session_manager=manager)
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        runtime.run_extension_command("emit_custom", [], "/emit_custom")
        events = runtime.drain_events()
        entries = repo.list_entries(session_id)
    finally:
        runtime.shutdown()

    assert [entry.payload.type for entry in entries] == ["custom", "custom_message"]
    assert any(getattr(event, "type", None) == "message_start" for event in events)
    assert manager.build_session_context(session_id).messages[0].custom_type == "status_panel"


def test_runtime_extension_exec_uses_governed_bash_path(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    (tmp_path / ".pi" / "extensions" / "exec_ext.py").write_text(
        """
def setup(api):
    async def run(_ctx):
        result = await api.exec('printf "${SSH_AUTH_SOCK-unset}"', [], {"timeout": 1})
        api.append_entry("exec_result", result)
    api.register_command("exec_check", {"description": "exec", "handler": run})
""",
        encoding="utf-8",
    )
    audit = InMemoryAuditSink()
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    runtime = InteractiveAgentRuntime(cwd=tmp_path, audit_sink=audit, session_manager=manager)
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        runtime.run_extension_command("exec_check", [], "/exec_check")
        entries = repo.list_entries(session_id)
    finally:
        runtime.shutdown()

    assert entries[-1].payload.type == "custom"
    assert entries[-1].payload.data["output"] == "unset"
    assert audit.records[-1].actor == "extension"
    assert audit.records[-1].tool_name == "bash"
    assert audit.records[-1].args["commandPreview"] == 'printf "${SSH_AUTH_SOCK-unset}"'


def test_runtime_extension_before_agent_start_and_provider_hooks(tmp_path) -> None:
    (tmp_path / ".pi" / "extensions").mkdir(parents=True)
    (tmp_path / ".pi" / "extensions" / "agent_hooks.py").write_text(
        """
def setup(api):
    def before_agent(event):
        return {
            "message": {"customType": "preflight", "content": "ready", "display": True},
            "systemPrompt": event.system_prompt + "\\nEXTENSION_SYSTEM",
        }

    def agent_start(_event):
        api.append_entry("agent_start", True)

    def before_payload(event):
        api.append_entry("provider_system_prompt", event["payload"]["context"].system_prompt)

    def after_response(event):
        api.append_entry("provider_response", {"status": event["status"]})

    api.on("before_agent_start", before_agent)
    api.on("agent_start", agent_start)
    api.on("before_provider_request", before_payload)
    api.on("after_provider_response", after_response)
""",
        encoding="utf-8",
    )
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    runtime = InteractiveAgentRuntime(cwd=tmp_path, session_manager=manager)
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        runtime.submit("hello")
        events = _drain_until_idle(runtime)
        entries = repo.list_entries(session_id)
    finally:
        runtime.shutdown()

    assert any(
        getattr(event, "type", None) == "message_start"
        and getattr(event.message, "custom_type", None) == "preflight"
        for event in events
    )
    custom_entries = [entry.payload for entry in entries if entry.payload.type == "custom"]
    assert [entry.custom_type for entry in custom_entries] == [
        "agent_start",
        "provider_response",
        "provider_system_prompt",
    ]
    assert "EXTENSION_SYSTEM" in custom_entries[-1].data


class _BeforeContext:
    def __init__(self, tool_call, args):
        self.tool_call = tool_call
        self.args = args
        self.assistant_message = None


class _AfterContext:
    def __init__(self, tool_call, args):
        self.tool_call = tool_call
        self.args = args
        self.assistant_message = None
        self.result = _ToolResult()
        self.is_error = True


class _ToolResult:
    content = [{"type": "text", "text": "old"}]
    details = {}
