"""Extension/resource runtime methods kept out of the main TUI bridge."""

from __future__ import annotations

import asyncio
import inspect
from datetime import date
from typing import Any

from agent_core.types import AfterToolCallResult, BeforeToolCallResult
from cli.extensions.event_types import InputEvent
from cli.extensions.loader import load_extensions
from cli.extensions.runner import ExtensionRunner
from cli.extensions.tools import create_extension_tools
from cli.resources import (
    ResourceLoader,
    SystemPromptParts,
    build_system_prompt,
    expand_prompt_template,
    expand_skill_command,
)

_DEFAULT_SYSTEM_PROMPT = "You are a helpful coding assistant."


class ExtensionRuntimeMixin:
    def _initialize_extension_runtime(self) -> None:
        self._resource_loader = ResourceLoader(cwd=self._cwd)
        self._extension_runner: ExtensionRunner | None = None
        self._extension_diagnostics: list[str] = []
        asyncio.run(self._reload_resources("startup"))

    def reload_resources(self) -> str:
        with self._lock:
            self._ensure_open()
            if self._is_running_locked():
                raise RuntimeError("reload is not available while streaming or tools are running")
        future = asyncio.run_coroutine_threadsafe(self._reload_resources("reload"), self._loop)
        future.result(timeout=5.0)
        with self._lock:
            self._generation += 1
            self._agent = self._build_agent(self._generation)
            self._enqueue_queue_update_locked()
        snapshot = self._resource_loader.snapshot
        extension_count = (
            len(self._extension_runner.runtime.extensions) if self._extension_runner is not None else 0
        )
        diagnostics_count = len(snapshot.diagnostics) + len(self._extension_diagnostics)
        return (
            f"reloaded resources: extensions={extension_count} skills={len(snapshot.skills)} "
            f"prompts={len(snapshot.prompts)} diagnostics={diagnostics_count}"
        )

    def extension_commands(self) -> dict[str, dict[str, Any]]:
        if self._extension_runner is None:
            return {}
        return self._extension_runner.get_registered_commands()

    def run_extension_command(self, name: str, args: list[str], raw: str) -> None:
        if self._extension_runner is None:
            raise RuntimeError("extension runtime is not available")
        command = self._extension_runner.get_command(name)
        if command is None:
            raise RuntimeError(f"extension command not found: /{name}")
        handler = command.get("handler") or command.get("execute")
        if not callable(handler):
            raise RuntimeError(f"extension command /{name} has no handler")
        result = handler({"name": name, "args": args, "raw": raw, "runtime": self})
        if inspect.isawaitable(result):
            future = asyncio.run_coroutine_threadsafe(result, self._loop)
            future.result(timeout=5.0)

    def _build_extension_tools(self, *, run_id_provider=None):
        if self._extension_runner is None:
            return []
        return create_extension_tools(
            self._extension_runner.runtime.extensions,
            cwd=str(self._cwd),
            runtime_session_id=self._runtime_session_id,
            run_id_provider=run_id_provider,
            audit_sink=self._audit_sink,
        )

    def _build_system_prompt(self) -> str:
        snapshot = self._resource_loader.snapshot
        return build_system_prompt(
            SystemPromptParts(
                base_prompt=snapshot.system_prompt or _DEFAULT_SYSTEM_PROMPT,
                append_prompts=snapshot.append_system_prompts,
                context_files=snapshot.context_files,
                skills=snapshot.skills,
                active_tools=tuple(tool.name for tool in self._build_tools()),
                cwd=str(self._cwd),
                current_date=date.today(),
            )
        )

    def expand_resource_command(self, text: str) -> str | None:
        snapshot = self._resource_loader.snapshot
        skill = expand_skill_command(text, list(snapshot.skills))
        if skill is not None:
            return skill
        return expand_prompt_template(text, list(snapshot.prompts))

    async def _apply_input_event(self, text: str) -> str | None:
        if self._extension_runner is None:
            return text
        result = await self._extension_runner.emit_input(InputEvent(text=text, source="interactive"))
        action = getattr(result, "action", None)
        if action == "handled":
            return None
        return getattr(result, "text", text)

    async def _transform_context(self, messages: list[Any], _signal: asyncio.Event | None) -> list[Any]:
        if self._extension_runner is None:
            return messages
        return await self._extension_runner.emit_context(messages)

    async def _before_tool_call(self, context: Any, _signal: asyncio.Event | None) -> BeforeToolCallResult | None:
        if self._extension_runner is None:
            return None
        tool_call = dict(context.tool_call)
        event = {
            "type": "tool_call",
            "toolCallId": tool_call.get("id", ""),
            "toolName": tool_call.get("name", ""),
            "assistantMessage": context.assistant_message,
            "input": context.args,
        }
        result = await self._extension_runner.emit_tool_call(event)
        if result is not None and result.block:
            return BeforeToolCallResult(block=True, reason=result.reason)
        return None

    async def _after_tool_call(self, context: Any, _signal: asyncio.Event | None) -> AfterToolCallResult | None:
        if self._extension_runner is None:
            return None
        tool_call = dict(context.tool_call)
        result = context.result
        event = {
            "type": "tool_result",
            "toolCallId": tool_call.get("id", ""),
            "toolName": tool_call.get("name", ""),
            "input": context.args,
            "content": result.content,
            "details": result.details,
            "isError": context.is_error,
        }
        patched = await self._extension_runner.emit_tool_result(event)
        return AfterToolCallResult(
            content=_content_blocks_to_dicts(patched.get("content")),
            details=patched.get("details"),
            isError=patched.get("isError"),
        )

    async def _reload_resources(self, reason: str) -> None:
        await self._resource_loader.reload()
        loaded = await load_extensions(
            [info.path for info in self._resource_loader.get_extensions()],
            cwd=self._cwd,
        )
        runner = ExtensionRunner(loaded.runtime)
        runner.bind_core(
            get_commands=lambda: list(runner.get_registered_commands().values()),
            get_active_tools=lambda: [tool.name for tool in self._build_tools()],
            get_all_tools=lambda: [tool.to_agent_tool_spec() for tool in self._build_tools()],
            send_user_message=self._extension_send_user_message,
            send_message=self._extension_send_message,
        )
        contributed = await runner.emit_resources_discover(str(self._cwd), reason)
        if contributed.skills or contributed.prompts or contributed.themes:
            self._resource_loader.extend_resources(contributed)
            await self._resource_loader.reload()
        self._extension_runner = runner
        self._extension_diagnostics = [
            diagnostic.message
            for diagnostic in [*loaded.diagnostics, *runner.diagnostics]
        ]

    def _extension_send_user_message(self, content: Any, _options: dict[str, Any] | None = None) -> None:
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(
                block.text if hasattr(block, "text") else str(block.get("text", ""))
                for block in content
                if (hasattr(block, "text") or isinstance(block, dict))
            )
        else:
            text = str(content)
        if text.strip():
            self.submit(text)

    def _extension_send_message(
        self,
        message: dict[str, Any],
        _options: dict[str, Any] | None = None,
    ) -> None:
        content = message.get("content")
        if isinstance(content, str):
            self.submit(content)


def _content_blocks_to_dicts(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    blocks: list[dict[str, Any]] = []
    for block in value:
        if isinstance(block, dict):
            blocks.append(block)
        elif hasattr(block, "model_dump"):
            blocks.append(block.model_dump(by_alias=True, exclude_none=True))
    return blocks


__all__ = ["ExtensionRuntimeMixin"]
