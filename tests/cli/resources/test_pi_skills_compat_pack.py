from __future__ import annotations

import asyncio
from pathlib import Path

from cli.resources.frontmatter import split_frontmatter
from cli.resources.loader import ResourceLoader
from cli.resources.skills import expand_skill_command
from policy.shell_policy import decide_shell_access
from policy.types import PolicyRequest

REPO_ROOT = Path(__file__).resolve().parents[3]
SHOWCASE_ROOT = REPO_ROOT / "showcase" / "pi_skills_compat"
PACK_ROOT = SHOWCASE_ROOT / "skills" / "pi-skills"
EXPECTED_SKILLS = [
    "brave-search",
    "browser-tools",
    "gmcli",
    "transcribe",
    "vscode",
    "youtube-transcript",
]


def test_resource_loader_discovers_generated_nested_pack(tmp_path) -> None:
    cwd = tmp_path / "repo"
    agent_dir = tmp_path / "home" / ".pi" / "agent"
    cwd.mkdir()
    agent_dir.mkdir(parents=True)

    loader = ResourceLoader(
        cwd=cwd,
        agent_dir=agent_dir,
        explicit_skills=(SHOWCASE_ROOT / "skills",),
    )
    asyncio.run(loader.reload())

    assert [skill.name for skill in loader.get_skills()] == EXPECTED_SKILLS
    assert not [diagnostic for diagnostic in loader.snapshot.diagnostics if diagnostic.type == "error"]


def test_skill_command_expansion_resolves_base_dir_for_generated_pack() -> None:
    loader = ResourceLoader(
        cwd=REPO_ROOT,
        agent_dir=REPO_ROOT / ".tmp" / "test-agent",
        explicit_skills=(PACK_ROOT,),
    )
    asyncio.run(loader.reload())

    expanded = expand_skill_command("/skill:brave-search docs", list(loader.get_skills())) or ""

    assert f"{PACK_ROOT / 'brave-search' / 'search.js'}" in expanded
    assert "{baseDir}" not in expanded
    assert "User arguments: docs" in expanded


def test_generated_skills_have_frontmatter_and_expected_helpers() -> None:
    for name in EXPECTED_SKILLS:
        metadata, body = split_frontmatter((PACK_ROOT / name / "SKILL.md").read_text(encoding="utf-8"))
        assert metadata["name"] == name
        assert isinstance(metadata["description"], str)
        assert metadata["description"].strip()
        assert "Derived from badlogic/pi-skills@" in body

    helper_files = [
        "brave-search/search.js",
        "brave-search/content.js",
        "brave-search/package.json",
        "brave-search/package-lock.json",
        "browser-tools/browser-start.js",
        "browser-tools/browser-cookies.js",
        "browser-tools/package.json",
        "browser-tools/package-lock.json",
        "transcribe/transcribe.sh",
        "youtube-transcript/transcript.js",
        "youtube-transcript/package.json",
    ]
    for relative in helper_files:
        assert (PACK_ROOT / relative).is_file()


def test_generated_high_risk_gates_are_concrete() -> None:
    browser = _section("browser-tools", "Sensitive operations")
    gmcli = _section("gmcli", "Sensitive operations")
    transcribe = _section("transcribe", "Sensitive operations")

    assert "--profile" in browser
    assert "browser-cookies.js" in browser
    assert "send" in gmcli
    assert "drafts" in gmcli
    assert "attachment download risk" in gmcli
    assert "upload to Groq" in transcribe
    assert "transcribe.sh <audio-file>" in transcribe


def test_generated_setup_checks_do_not_leak_secret_values() -> None:
    brave = (PACK_ROOT / "brave-search" / "SKILL.md").read_text(encoding="utf-8")
    transcribe = (PACK_ROOT / "transcribe" / "SKILL.md").read_text(encoding="utf-8")

    assert "echo $BRAVE_API_KEY" not in brave
    assert "echo $GROQ_API_KEY" not in transcribe
    assert ">/dev/null" not in brave
    assert ">/dev/null" not in transcribe
    assert 'test -n "${BRAVE_API_KEY:-}"' in brave
    assert 'test -n "${GROQ_API_KEY:-}"' in transcribe
    assert "resources.skillEnv.brave-search" in brave
    assert "resources.skillEnv.transcribe" in transcribe


def test_generated_setup_checks_are_shell_policy_compatible(tmp_path) -> None:
    for name in EXPECTED_SKILLS:
        for command in _setup_check_commands(name):
            decision = decide_shell_access(
                PolicyRequest(
                    toolName="bash",
                    args={"command": command},
                    cwd=str(tmp_path),
                    actor="model",
                )
            )
            assert decision.effect == "allow", (name, command, decision.reason)


def _section(skill_name: str, heading: str) -> str:
    text = (PACK_ROOT / skill_name / "SKILL.md").read_text(encoding="utf-8")
    marker = f"## {heading}\n\n"
    start = text.index(marker) + len(marker)
    end = text.find("\n## ", start)
    return text[start:] if end == -1 else text[start:end]


def _setup_check_commands(skill_name: str) -> list[str]:
    section = _section(skill_name, "Setup check")
    start = section.index("```bash") + len("```bash")
    end = section.index("```", start)
    return [line.strip() for line in section[start:end].splitlines() if line.strip()]
