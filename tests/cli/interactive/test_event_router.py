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
    MessageListComponent,
    StatusComponent,
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
FORBIDDEN_PROTOCOL_MODULES = ("agent_core", "cli.core", "ai_provider")


def _walk_py(root: Path):
    for path in root.rglob("*.py"):
        yield path


def test_src_tui_does_not_import_protocol_modules() -> None:
    bad: list[tuple[Path, str]] = []
    for path in _walk_py(REPO / "src" / "tui"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                top = module.split(".")[0]
                second = ".".join(module.split(".")[:2])
                if top in FORBIDDEN_PROTOCOL_MODULES or second in FORBIDDEN_PROTOCOL_MODULES:
                    bad.append((path, module))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    top = name.split(".")[0]
                    second = ".".join(name.split(".")[:2])
                    if top in FORBIDDEN_PROTOCOL_MODULES or second in FORBIDDEN_PROTOCOL_MODULES:
                        bad.append((path, name))
    assert not bad, f"src/tui imports protocol modules: {bad}"


def test_neither_tui_nor_interactive_define_pydantic_models() -> None:
    """Substring scan: rg-style. No ``BaseModel`` or ``_PiModel`` subclassing
    in ``src/tui`` or ``src/cli/interactive``."""

    bad: list[Path] = []
    for root in (REPO / "src" / "tui", REPO / "src" / "cli" / "interactive"):
        for path in _walk_py(root):
            text = path.read_text()
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        # base could be Name('BaseModel') or Attribute(value=Name('pydantic'), attr='BaseModel')
                        names: list[str] = []
                        if isinstance(base, ast.Name):
                            names.append(base.id)
                        elif isinstance(base, ast.Attribute):
                            names.append(base.attr)
                        if {"BaseModel", "_PiModel"} & set(names):
                            bad.append(path)
    assert not bad, f"forbidden pydantic models in: {bad}"
