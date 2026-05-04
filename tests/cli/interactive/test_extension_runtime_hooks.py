from __future__ import annotations

import asyncio

from cli.interactive.runtime import InteractiveAgentRuntime
from policy.audit import InMemoryAuditSink


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
