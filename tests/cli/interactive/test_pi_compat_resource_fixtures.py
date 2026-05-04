from __future__ import annotations

from pathlib import Path

from cli.core.session_manager import SessionManager
from cli.interactive.runtime import InteractiveAgentRuntime
from storage.in_memory_session_repository import InMemorySessionRepository

FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "pi_compat"


def test_project_extension_fixture_registers_executable_command() -> None:
    cwd = FIXTURE_ROOT / "extensions" / "project_command"
    repo = InMemorySessionRepository()
    manager = SessionManager(repo)
    runtime = InteractiveAgentRuntime(cwd=cwd, session_manager=manager)
    try:
        session_id = runtime.state.durable_session_id
        assert session_id is not None
        runtime.run_extension_command("fixture_status", ["ok"], "/fixture_status ok")
        entries = repo.list_entries(session_id)
    finally:
        runtime.shutdown()

    assert entries[-1].payload.type == "custom"
    assert entries[-1].payload.custom_type == "fixture_command"
    assert entries[-1].payload.data == {"args": ["ok"]}


def test_project_skill_fixture_expands_skill_command() -> None:
    cwd = FIXTURE_ROOT / "skills" / "project_skill"
    runtime = InteractiveAgentRuntime(cwd=cwd)
    try:
        expanded = runtime.expand_resource_command("/skill:research-protocol qmd")
    finally:
        runtime.shutdown()

    assert expanded is not None
    assert 'name="research-protocol"' in expanded
    assert "propose one hypothesis" in expanded
    assert "User arguments: qmd" in expanded


def test_project_prompt_fixture_expands_prompt_template() -> None:
    cwd = FIXTURE_ROOT / "prompt_templates" / "project_prompt"
    runtime = InteractiveAgentRuntime(cwd=cwd)
    try:
        expanded = runtime.expand_resource_command("/investigate qmd")
    finally:
        runtime.shutdown()

    assert expanded == "Investigate qmd and report the smallest useful next experiment.\n"
