"""W4 event router contract tests."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agent_core.types import AgentEventAdapter
from ai_provider.types import AssistantMessageEventAdapter
from cli.core.session_types import AgentSessionEventAdapter
from cli.interactive.components import (
    AssistantMessageComponent,
    BranchSummaryComponent,
    CompactionSummaryComponent,
    MessageListComponent,
    StatusComponent,
    ToolExecutionComponent,
    ToolResultComponent,
)
from cli.interactive.event_router import EventRouter
from cli.interactive.tool_renderer_registry import ToolRendererRegistry

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "pi_compat"

# W5 deliverable table — 7 fixtures the M1 router must accept.
M1_FIXTURES = (
    "assistant_text_delta",
    "assistant_thinking_delta",
    "tool_execution_success",
    "parallel_tools",
    "compaction",
    "branch_summary",
    "abort_during_stream",
    "abort_during_tool",
)


def _make_router() -> tuple[EventRouter, MessageListComponent, StatusComponent]:
    ml = MessageListComponent()
    st = StatusComponent()
    reg = ToolRendererRegistry()
    return EventRouter(ml, st, reg), ml, st


def _validate(raw: dict) -> object:
    type_field = raw.get("type")
    assistant_types = {
        "start",
        "text_start",
        "text_delta",
        "text_end",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_end",
        "done",
        "error",
    }
    if type_field in assistant_types:
        return AssistantMessageEventAdapter.validate_python(raw)
    try:
        return AgentSessionEventAdapter.validate_python(raw)
    except Exception:
        return AgentEventAdapter.validate_python(raw)


@pytest.mark.parametrize("fixture", M1_FIXTURES)
def test_each_event_routes_without_error(fixture: str) -> None:
    router, _, _ = _make_router()
    raw_events = (FIXTURE_ROOT / fixture / "events.jsonl").read_text().splitlines()
    for line in raw_events:
        if not line.strip():
            continue
        event = _validate(json.loads(line))
        router.route(event)  # must not raise


def test_top_level_assistant_text_path_creates_assistant_component() -> None:
    router, ml, _ = _make_router()
    events = (
        FIXTURE_ROOT / "assistant_text_delta" / "events.jsonl"
    ).read_text().splitlines()
    for line in events:
        router.route(_validate(json.loads(line)))
    assert any(isinstance(c, AssistantMessageComponent) for c in ml.children)


def test_top_level_assistant_thinking_path_creates_assistant_component() -> None:
    router, ml, _ = _make_router()
    events = (
        FIXTURE_ROOT / "assistant_thinking_delta" / "events.jsonl"
    ).read_text().splitlines()
    for line in events:
        router.route(_validate(json.loads(line)))
    assert any(isinstance(c, AssistantMessageComponent) for c in ml.children)


def test_compaction_fixture_routes_summary_component() -> None:
    router, ml, _ = _make_router()
    for line in (FIXTURE_ROOT / "compaction" / "events.jsonl").read_text().splitlines():
        router.route(_validate(json.loads(line)))
    assert any(isinstance(c, CompactionSummaryComponent) for c in ml.children)


def test_branch_summary_fixture_routes_summary_component() -> None:
    router, ml, _ = _make_router()
    for line in (FIXTURE_ROOT / "branch_summary" / "events.jsonl").read_text().splitlines():
        router.route(_validate(json.loads(line)))
    assert any(isinstance(c, BranchSummaryComponent) for c in ml.children)


def test_tool_result_message_does_not_duplicate_completed_tool_log() -> None:
    from agent_core.types import ToolExecutionEndEvent, ToolExecutionStartEvent
    from ai_provider.types import TextContent, ToolResultMessage
    from cli.core.session_types import MessageStartEvent

    router, ml, _ = _make_router()
    router.route(ToolExecutionStartEvent(toolCallId="c1", toolName="read", args={"path": "README.md"}))
    router.route(
        ToolExecutionEndEvent(
            toolCallId="c1",
            toolName="read",
            result={"content": [{"type": "text", "text": "# title"}]},
            isError=False,
        )
    )
    router.route(
        MessageStartEvent(
            message=ToolResultMessage(
                toolCallId="c1",
                toolName="read",
                content=[TextContent(text="# title")],
                isError=False,
                timestamp=1,
            )
        )
    )

    assert sum(isinstance(child, ToolExecutionComponent) for child in ml.children) == 1
    assert not any(isinstance(child, ToolResultComponent) for child in ml.children)


def test_unknown_event_type_raises_runtime_error() -> None:
    router, _, _ = _make_router()

    class _Bogus:
        type = "made_up_event"

    with pytest.raises(RuntimeError, match="contract violation"):
        router.route(_Bogus())


# -------------------------------------------------------------------- #
# Static guards (plan §完成标准 #9): ``src/tui`` and                   #
# ``src/cli/interactive`` must NOT define pydantic message / event     #
# models, and ``src/tui`` must NOT import protocol types.              #
# -------------------------------------------------------------------- #

REPO = Path(__file__).resolve().parents[3]
FORBIDDEN_PROTOCOL_MODULES = ("agent_core", "cli.core", "cli.tools", "policy", "ai_provider")


def _walk_py(root: Path):
    for path in root.rglob("*.py"):
        yield path


def _imported_module_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


def _top_import_keys(module: str) -> tuple[str, str]:
    parts = module.split(".")
    return parts[0], ".".join(parts[:2])


def test_src_tui_does_not_import_protocol_modules() -> None:
    bad: list[tuple[Path, str]] = []
    for path in _walk_py(REPO / "src" / "tui"):
        for module in _imported_module_names(path):
            top, second = _top_import_keys(module)
            if top in FORBIDDEN_PROTOCOL_MODULES or second in FORBIDDEN_PROTOCOL_MODULES:
                bad.append((path, module))
    assert not bad, f"src/tui imports protocol modules: {bad}"


def _pydantic_base_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def test_neither_tui_nor_interactive_define_pydantic_models() -> None:
    """Substring scan: rg-style. No ``BaseModel`` or ``_PiModel`` subclassing
    in ``src/tui`` or ``src/cli/interactive``."""

    bad: list[Path] = []
    for root in (REPO / "src" / "tui", REPO / "src" / "cli" / "interactive"):
        for path in _walk_py(root):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and {"BaseModel", "_PiModel"} & _pydantic_base_names(node):
                    bad.append(path)
    assert not bad, f"forbidden pydantic models in: {bad}"


def test_agent_end_appends_run_divider_with_elapsed_time() -> None:
    from agent_core.types import AgentEndEvent, AgentStartEvent
    from cli.interactive.components import RunDividerComponent

    now = 100.0

    def clock() -> float:
        return now

    ml = MessageListComponent()
    st = StatusComponent()
    reg = ToolRendererRegistry()
    router = EventRouter(ml, st, reg, clock=clock)
    router.route(AgentStartEvent())
    now = 169.0
    router.route(AgentEndEvent(messages=[]))

    assert any(isinstance(c, RunDividerComponent) for c in ml.children)
    rendered = "\n".join(ml.render(80))
    assert "Worked for 1m 09s" in rendered
