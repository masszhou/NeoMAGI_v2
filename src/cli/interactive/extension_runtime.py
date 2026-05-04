"""Extension/resource runtime methods kept out of the main TUI bridge."""

from __future__ import annotations

import asyncio
import inspect
import shlex
import time
import uuid
from datetime import date
from typing import Any

from agent_core.types import AfterToolCallResult, BeforeToolCallResult
from ai_provider.runtime_types import ProviderResponse
from ai_provider.types import Model, TextContent, UserMessage
from cli.core.session_types import CustomMessage, MessageEndEvent, MessageStartEvent
from cli.extensions.event_types import BeforeAgentStartEvent, InputEvent
from cli.extensions.loader import load_extensions
from cli.extensions.runner import ExtensionRunner
from cli.extensions.tools import create_extension_tools
from cli.extensions.ui import NoopExtensionUIContext
from cli.slash_commands.registry import PI_BUILTIN_COMMANDS
from cli.tools.bash import create_bash_tool_definition
from cli.tools.wrapper import ToolRuntime, wrap_tool_definition
from cli.resources import (
    ResourceLoader,
    SystemPromptParts,
    build_system_prompt,
    expand_prompt_template,
    expand_skill_command,
)

_DEFAULT_SYSTEM_PROMPT = "You are a helpful coding assistant."


def _now_ms() -> int:
    return int(time.time() * 1000)


class ExtensionRuntimeMixin:
    def _initialize_extension_runtime(self) -> None:
        self._extension_ui_context = NoopExtensionUIContext()
        self._resource_loader = ResourceLoader(cwd=self._cwd)
        self._extension_runner: ExtensionRunner | None = None
        self._extension_diagnostics: list[str] = []
        asyncio.run(self._reload_resources("startup"))

    def bind_extension_ui_context(self, ui: Any) -> None:
        self._extension_ui_context = ui
        if self._extension_runner is not None:
            self._extension_runner.set_ui_context(ui)
            self._extension_runner.runtime.actions["ui"] = ui

    def reload_resources(self) -> str:
        with self._lock:
            self._ensure_open()
            if self._is_running_locked():
                raise RuntimeError("reload is not available while streaming or tools are running")
        self._emit_extension_session_shutdown("reload")
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

    def get_custom_message_renderer(self, custom_type: str):
        if self._extension_runner is None:
            return None
        return self._extension_runner.get_message_renderer(custom_type)

    def resource_command_items(self) -> list[tuple[str, str | None]]:
        snapshot = self._resource_loader.snapshot
        items: list[tuple[str, str | None]] = []
        for prompt in snapshot.prompts:
            detail = prompt.description or "Prompt template"
            if prompt.argument_hint:
                detail = f"{detail} {prompt.argument_hint}"
            items.append((f"/{prompt.name}", detail))
        if self._resource_loader.settings.enable_skill_commands:
            for skill in snapshot.skills:
                items.append((f"/skill:{skill.name}", skill.description))
        return items

    def extension_command_name(self, text: str) -> str | None:
        if not text.startswith("/") or self._extension_runner is None:
            return None
        name = text[1:].split(maxsplit=1)[0] if text[1:].strip() else ""
        if name and name in self._extension_runner.get_registered_commands():
            return name
        return None

    def prepare_queued_prompt(self, text: str) -> str:
        name = self.extension_command_name(text)
        if name is not None:
            raise RuntimeError(
                f"extension command /{name} cannot be queued while streaming; run it while idle"
            )
        return self.expand_resource_command(text) or text

    def run_extension_command(self, name: str, args: list[str], raw: str) -> None:
        if self._extension_runner is None:
            raise RuntimeError("extension runtime is not available")
        command = self._extension_runner.get_command(name)
        if command is None:
            raise RuntimeError(f"extension command not found: /{name}")
        handler = command.get("handler") or command.get("execute")
        if not callable(handler):
            raise RuntimeError(f"extension command /{name} has no handler")
        result = handler(
            {
                **self._extension_runner.create_command_context(),
                "name": name,
                "args": args,
                "raw": raw,
                "runtime": self,
            }
        )
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
        if self._resource_loader.settings.enable_skill_commands:
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

    async def _before_agent_start_messages(self, prompt: str) -> tuple[list[Any], str]:
        if self._extension_runner is None:
            return [], self._agent.state.system_prompt
        messages, system_prompt = await self._extension_runner.emit_before_agent_start(
            BeforeAgentStartEvent(
                prompt=prompt,
                systemPrompt=self._agent.state.system_prompt,
                systemPromptOptions={
                    "cwd": str(self._cwd),
                    "skills": [skill.name for skill in self._resource_loader.snapshot.skills],
                    "promptTemplates": [prompt_template.name for prompt_template in self._resource_loader.snapshot.prompts],
                },
            )
        )
        return [_message_from_extension(item) for item in messages], system_prompt

    async def _transform_context(self, messages: list[Any], _signal: asyncio.Event | None) -> list[Any]:
        if self._extension_runner is None:
            return messages
        return await self._extension_runner.emit_context(messages)

    async def _before_provider_request(self, payload: Any, model: Model) -> Any:
        if self._extension_runner is None:
            return payload
        event = {"type": "before_provider_request", "payload": payload, "model": model}
        for result in await self._extension_runner.emit(event):
            if isinstance(result, dict) and "payload" in result:
                event["payload"] = result["payload"]
        return event["payload"]

    async def _after_provider_response(self, response: ProviderResponse, model: Model) -> None:
        if self._extension_runner is None:
            return
        await self._extension_runner.emit(
            {
                "type": "after_provider_response",
                "status": response.status,
                "headers": dict(response.headers),
                "model": model,
            }
        )

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
            set_active_tools=self._extension_set_active_tools,
            send_user_message=self._extension_send_user_message,
            send_message=self._extension_send_message,
            append_entry=self._extension_append_entry,
            set_session_name=self._extension_set_session_name,
            get_session_name=self._extension_get_session_name,
            set_label=self._extension_set_label,
            exec=self._extension_exec,
            get_thinking_level=lambda: self._thinking_level,
            set_thinking_level=self._extension_set_thinking_level,
            ui=self._extension_ui_context,
        )
        runner.set_ui_context(self._extension_ui_context)
        runner.diagnose_command_collisions(
            reserved={name for name, _description, _milestone in PI_BUILTIN_COMMANDS}
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
        await runner.emit({"type": "session_start", "reason": reason, "previousSessionFile": None})

    def _emit_extension_session_shutdown(self, reason: str) -> None:
        runner = self._extension_runner
        if runner is None:
            return
        event = {"type": "session_shutdown", "reason": reason, "targetSessionFile": None}
        loop = getattr(self, "_loop", None)
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(runner.emit(event), loop)
            future.result(timeout=5.0)
            return
        asyncio.run(runner.emit(event))

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
        custom_type = message.get("customType") or message.get("custom_type")
        if custom_type is not None or message.get("role") == "custom":
            custom = CustomMessage(
                customType=str(custom_type or "custom"),
                content=_custom_message_content(message.get("content", "")),
                display=bool(message.get("display", True)),
                details=message.get("details"),
                timestamp=_now_ms(),
            )
            self._emit_session_event(MessageStartEvent(message=custom))
            self._emit_session_event(MessageEndEvent(message=custom))
            self._agent.state.messages.append(custom)
            return
        content = message.get("content")
        if isinstance(content, str):
            self.submit(content)

    def _extension_append_entry(self, custom_type: str, data: Any | None = None) -> None:
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        entry = self._session_manager.append_custom_entry(
            self._durable_session.id,
            str(custom_type),
            data,
        )
        refreshed = self._session_manager.repository.get_session(self._durable_session.id)
        if refreshed is not None:
            self._durable_session = refreshed
        self._session_context_messages = self._load_session_context_messages()
        if entry is not None:
            self._notify_wake()

    def _extension_set_session_name(self, name: str) -> None:
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        self._durable_session = self._session_manager.rename_session(self._durable_session.id, name)
        self._notify_wake()

    def _extension_get_session_name(self) -> str | None:
        return self._durable_session.display_name if self._durable_session is not None else None

    def _extension_set_label(self, entry_id: str, label: str | None = None) -> None:
        if self._session_manager is None or self._durable_session is None:
            raise RuntimeError("durable session manager is not available")
        self._session_manager.label_entry(self._durable_session.id, entry_id, label)
        refreshed = self._session_manager.repository.get_session(self._durable_session.id)
        if refreshed is not None:
            self._durable_session = refreshed
        self._notify_wake()

    async def _extension_exec(
        self,
        command: str,
        args: list[str] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        argv = [command, *(args or [])]
        shell_command = shlex.join([str(item) for item in argv]) if args else str(command)
        tool = wrap_tool_definition(
            create_bash_tool_definition(artifact_store=self._artifact_store),
            ToolRuntime(
                cwd=str(self._cwd),
                runtime_session_id=self._runtime_session_id,
                run_id=self._mint_run_id(),
                actor="extension",
                audit_sink=self._audit_sink,
            ),
        )
        payload: dict[str, Any] = {"command": shell_command}
        if isinstance(options, dict) and options.get("timeout") is not None:
            payload["timeout"] = options["timeout"]
        result = await tool.execute(f"extension-exec-{uuid.uuid4()}", payload, None, None)
        details = result.details if isinstance(result.details, dict) else {}
        truncation = details.get("truncation") if isinstance(details.get("truncation"), dict) else {}
        return {
            "output": _tool_text(result),
            "exitCode": details.get("exitCode"),
            "cancelled": bool(details.get("cancelled")),
            "truncated": bool(truncation.get("truncated")),
            "fullOutputPath": details.get("fullOutputPath"),
            "isError": bool(result.is_error),
            "details": details,
        }

    def _extension_set_active_tools(self, tool_names: list[str]) -> None:
        self._active_tool_names = {str(name) for name in tool_names}
        with self._lock:
            if getattr(self, "_agent", None) is not None:
                self._generation += 1
                self._agent = self._build_agent(self._generation)
                self._enqueue_queue_update_locked()
        self._notify_wake()

    def _extension_set_thinking_level(self, level: str) -> None:
        self._thinking_level = level  # validated when CLI/user-facing model switching lands in M9
        with self._lock:
            if getattr(self, "_agent", None) is not None:
                self._generation += 1
                self._agent = self._build_agent(self._generation)
                self._enqueue_queue_update_locked()
        self._notify_wake()


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


def _custom_message_content(value: Any) -> str | list[dict[str, Any]]:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [
            block.model_dump(by_alias=True, exclude_none=True)
            if hasattr(block, "model_dump")
            else dict(block)
            for block in value
            if isinstance(block, dict) or hasattr(block, "model_dump")
        ]
    return str(value)


def _message_from_extension(value: dict[str, Any]) -> Any:
    role = value.get("role")
    custom_type = value.get("customType") or value.get("custom_type")
    if role == "custom" or custom_type is not None:
        return CustomMessage(
            customType=str(custom_type or "custom"),
            content=_custom_message_content(value.get("content", "")),
            display=bool(value.get("display", True)),
            details=value.get("details"),
            timestamp=_now_ms(),
        )
    if role == "user":
        content = value.get("content", "")
        text = content if isinstance(content, str) else str(content)
        return UserMessage(content=[TextContent(text=text)], timestamp=_now_ms())
    return value


def _tool_text(result: Any) -> str:
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, TextContent):
            parts.append(block.text)
        elif getattr(block, "type", None) == "text":
            parts.append(str(block.text))
    return "\n".join(parts)


__all__ = ["ExtensionRuntimeMixin"]
