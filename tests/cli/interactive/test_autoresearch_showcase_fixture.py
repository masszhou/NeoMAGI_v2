from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

from agent_core import Agent, AgentOptions
from ai_provider.model_registry import get_model
from ai_provider.providers.faux import faux_tool_call, stream_faux
from ai_provider.runtime_types import SimpleStreamOptions
from ai_provider.types import Context, Model
from cli.extensions.loader import load_extensions
from cli.extensions.runner import ExtensionRunner
from cli.interactive.runtime import InteractiveAgentRuntime
from cli.resources.loader import ResourceLoader
from cli.resources.prompt_templates import expand_prompt_template
from cli.resources.skills import expand_skill_command
from policy.audit import InMemoryAuditSink


REPO_ROOT = Path(__file__).resolve().parents[3]
SHOWCASE_WORKSPACE = REPO_ROOT / "showcase" / "qmd_autoresearch_mini" / "workspace"
EXPECTED_EXTENSION_TOOLS = ["init_experiment", "run_experiment", "log_experiment"]
INIT_ARGS = {"objective": "M2 skill-driven baseline", "command": "bash autoresearch.sh"}
RUN_ARGS = {
    "trial_id": "baseline",
    "hypothesis": "",
    "changes": "",
    "command": "bash autoresearch.sh",
}
RESTART_NOTE = "Skill-driven baseline completed through runtime tool calls."


def test_autoresearch_showcase_fixture_loads_extension_skill_and_prompt(tmp_path: Path) -> None:
    async def run() -> None:
        loader = ResourceLoader(cwd=SHOWCASE_WORKSPACE, agent_dir=tmp_path / ".magipi" / "agent")
        await loader.reload()
        first_snapshot = loader.snapshot
        first_extensions = await load_extensions([resource.path for resource in first_snapshot.extensions], cwd=SHOWCASE_WORKSPACE)
        first_runner = ExtensionRunner(first_extensions.runtime)

        await loader.reload()
        second_snapshot = loader.snapshot
        second_extensions = await load_extensions([resource.path for resource in second_snapshot.extensions], cwd=SHOWCASE_WORKSPACE)
        second_runner = ExtensionRunner(second_extensions.runtime)

        extension_names = [resource.name for resource in second_snapshot.extensions]
        skill_names = [skill.name for skill in second_snapshot.skills]
        prompt_names = [prompt.name for prompt in second_snapshot.prompts]
        tool_names = [tool.name for tool in second_runner.get_all_registered_tools()]
        expanded_skill = expand_skill_command("/skill:autoresearch-mini score", list(second_snapshot.skills))
        expanded_prompt = expand_prompt_template("/autoresearch-next score", list(second_snapshot.prompts))

        assert extension_names == ["autoresearch"]
        assert "autoresearch-mini" in skill_names
        assert "autoresearch-next" in prompt_names
        assert sorted(tool_names) == ["init_experiment", "log_experiment", "recover_experiment", "run_experiment"]
        assert expanded_skill is not None and "## Restart Note" in expanded_skill
        assert expanded_prompt is not None and "Optional focus: score" in expanded_prompt
        assert sorted(tool.name for tool in first_runner.get_all_registered_tools()) == sorted(tool_names)
        assert not any(diagnostic.severity == "error" for diagnostic in second_extensions.diagnostics)

    asyncio.run(run())


def test_autoresearch_skill_path_drives_extension_tools_to_baseline_jsonl(tmp_path: Path) -> None:
    workspace = _copy_workspace(tmp_path)
    _git_init_with_commit(workspace)
    provider_contexts: list[Context] = []
    audit = InMemoryAuditSink()
    runtime = InteractiveAgentRuntime(
        cwd=workspace,
        agent_factory=_baseline_agent_factory(provider_contexts),
        audit_sink=audit,
    )
    try:
        runtime.submit("/skill:autoresearch-mini baseline only")
        events = _drain_until_idle(runtime)
    finally:
        runtime.shutdown()

    _assert_skill_provider_context(provider_contexts[0])
    _assert_baseline_tool_calls(events, audit)
    _assert_baseline_jsonl(workspace)


def _baseline_agent_factory(provider_contexts: list[Context]):
    stream_fn = partial(_baseline_stream, provider_contexts)

    def agent_factory(options: AgentOptions) -> Agent:
        return Agent(replace(options, model=get_model("faux", "faux-1"), stream_fn=stream_fn))

    return agent_factory


def _baseline_stream(
    provider_contexts: list[Context],
    model: Model,
    context: Context,
    _options: SimpleStreamOptions | None = None,
):
    provider_contexts.append(context)
    response = _baseline_response(context)
    return stream_faux(model, context, SimpleStreamOptions(metadata={"response": response}))


def _baseline_response(context: Context) -> str | list[Any]:
    last_message = context.messages[-1] if context.messages else None
    if getattr(last_message, "role", None) != "toolResult":
        return [faux_tool_call("init_experiment", INIT_ARGS, id="call_init")]
    if getattr(last_message, "tool_name", "") == "init_experiment":
        return [faux_tool_call("run_experiment", RUN_ARGS, id="call_run")]
    if getattr(last_message, "tool_name", "") == "run_experiment":
        log_args = {"run_result": last_message.details, "status": "baseline", "restart_note": RESTART_NOTE}
        return [faux_tool_call("log_experiment", log_args, id="call_log")]
    return "baseline logged"


def _assert_skill_provider_context(first_context: Context) -> None:
    first_context_text = first_context.model_dump_json(by_alias=True, exclude_none=True)
    provider_tool_names = {tool.name for tool in first_context.tools or []}
    assert "QMD Autoresearch Mini" in first_context_text
    assert "Do not claim that an experiment" in first_context_text
    assert set(EXPECTED_EXTENSION_TOOLS).issubset(provider_tool_names)


def _assert_baseline_tool_calls(events: list[Any], audit: InMemoryAuditSink) -> None:
    runtime_tool_calls = [event.tool_name for event in events if getattr(event, "type", None) == "tool_execution_start"]
    audit_tool_calls = [record.tool_name for record in audit.records if record.tool_name in EXPECTED_EXTENSION_TOOLS]
    assert runtime_tool_calls[:3] == EXPECTED_EXTENSION_TOOLS
    assert audit_tool_calls == EXPECTED_EXTENSION_TOOLS


def _assert_baseline_jsonl(workspace: Path) -> None:
    entries = _read_jsonl(workspace / "autoresearch.jsonl")
    assert [entry["status"] for entry in entries] == ["baseline"]
    assert entries[0]["metrics"]["score"] == 0.696667
    assert entries[0]["restart_note"] == RESTART_NOTE


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _copy_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "qmd-mini"
    shutil.copytree(SHOWCASE_WORKSPACE, workspace)
    (workspace / ".gitignore").write_text(
        "\n".join(
            [
                "__pycache__/",
                "*.py[cod]",
                ".pytest_cache/",
                "autoresearch.md",
                "autoresearch.sh",
                "autoresearch.jsonl",
                "autoresearch.checks.sh",
                "autoresearch-artifacts/",
                "exports/",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return workspace


def _git_init_with_commit(workspace: Path) -> None:
    _git(workspace, "init", "-q")
    _git(workspace, "checkout", "-q", "-b", "scratch/autoresearch-mini-m2")
    _git(workspace, "config", "user.name", "Manual Test")
    _git(workspace, "config", "user.email", "manual@example.invalid")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", "initial mini fixture")


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=workspace, text=True, capture_output=True, check=True)


def _drain_until_idle(runtime: InteractiveAgentRuntime, *, timeout: float = 5.0) -> list[Any]:
    deadline = time.monotonic() + timeout
    events: list[Any] = []
    while time.monotonic() < deadline:
        events.extend(runtime.drain_events())
        if not runtime.state.is_running and any(getattr(event, "type", None) == "agent_end" for event in events):
            events.extend(runtime.drain_events())
            return events
        time.sleep(0.01)
    raise AssertionError("runtime did not become idle")
