from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from cli.resources.loader import ResourceLoader
from cli.resources.system_skills import (
    sync_system_skills,
    system_skills_source_root,
    workspace_system_skills_dir,
)

SYSTEM_SKILL_NAMES = ["skill-creator", "skill-installer"]


def _make_source(tmp_path: Path, skills: dict[str, str]) -> Path:
    source = tmp_path / "package_source"
    for name, body in skills.items():
        skill_dir = source / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {body}\n---\n{body}\n",
            encoding="utf-8",
        )
    return source


def test_sync_materializes_and_is_idempotent(tmp_path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    source = _make_source(tmp_path, {"alpha": "First."})

    diagnostics = sync_system_skills(cwd, enabled=True, source_root=source)
    assert diagnostics == []
    destination = workspace_system_skills_dir(cwd)
    assert (destination / "alpha" / "SKILL.md").is_file()
    assert (destination / "README.md").is_file()

    before = (destination / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert sync_system_skills(cwd, enabled=True, source_root=source) == []
    assert (destination / "alpha" / "SKILL.md").read_text(encoding="utf-8") == before


def test_sync_updates_overwrites_local_edits_and_removes_stale(tmp_path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    source = _make_source(tmp_path, {"alpha": "First.", "beta": "Second."})
    sync_system_skills(cwd, enabled=True, source_root=source)

    destination = workspace_system_skills_dir(cwd)
    (destination / "alpha" / "SKILL.md").write_text("tampered", encoding="utf-8")
    (source / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: Updated.\n---\nUpdated.\n", encoding="utf-8"
    )
    new_source = _make_source(tmp_path / "v2", {"alpha": "First.", "gamma": "Third."})

    sync_system_skills(cwd, enabled=True, source_root=new_source)
    assert "First." in (destination / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert (destination / "gamma").is_dir()
    assert not (destination / "beta").exists()


def test_sync_disabled_removes_destination(tmp_path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    source = _make_source(tmp_path, {"alpha": "First."})
    sync_system_skills(cwd, enabled=True, source_root=source)
    assert workspace_system_skills_dir(cwd).exists()

    assert sync_system_skills(cwd, enabled=False, source_root=source) == []
    assert not workspace_system_skills_dir(cwd).exists()


def test_sync_missing_source_reports_diagnostic(tmp_path) -> None:
    cwd = tmp_path / "ws"
    cwd.mkdir()
    diagnostics = sync_system_skills(cwd, enabled=True, source_root=tmp_path / "missing")
    assert len(diagnostics) == 1
    assert "missing or empty" in diagnostics[0].message


def test_packaged_system_skills_exist_and_load(tmp_path) -> None:
    source = system_skills_source_root()
    for name in SYSTEM_SKILL_NAMES:
        assert (source / name / "SKILL.md").is_file()

    cwd = tmp_path / "ws"
    cwd.mkdir()
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    loader = ResourceLoader(cwd=cwd, agent_dir=agent_dir)
    asyncio.run(loader.reload())

    names = [skill.name for skill in loader.get_skills()]
    for name in SYSTEM_SKILL_NAMES:
        assert name in names
    by_name = {skill.name: skill for skill in loader.get_skills()}
    for name in SYSTEM_SKILL_NAMES:
        assert by_name[name].source is not None
        assert by_name[name].source.scope == "system"
        assert by_name[name].prompt_location.startswith(".magipi/skills/.system/")


def test_workspace_skill_overrides_system_skill(tmp_path) -> None:
    cwd = tmp_path / "ws"
    override = cwd / ".magipi" / "skills" / "skill-creator"
    override.mkdir(parents=True)
    override_skill = "---\nname: skill-creator\ndescription: Custom override.\n---\nCustom.\n"
    (override / "SKILL.md").write_text(override_skill, encoding="utf-8")
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    loader = ResourceLoader(cwd=cwd, agent_dir=agent_dir)
    asyncio.run(loader.reload())

    by_name = {skill.name: skill for skill in loader.get_skills()}
    assert by_name["skill-creator"].description == "Custom override."
    assert by_name["skill-creator"].source is not None
    assert by_name["skill-creator"].source.scope == "project"
    assert any(
        diagnostic.type == "collision" and diagnostic.name == "skill-creator"
        for diagnostic in loader.snapshot.diagnostics
    )


def test_settings_opt_out_disables_system_skills(tmp_path) -> None:
    cwd = tmp_path / "ws"
    (cwd / ".magipi").mkdir(parents=True)
    (cwd / ".magipi" / "settings.json").write_text(
        json.dumps({"resources": {"systemSkills": False}}), encoding="utf-8"
    )
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    loader = ResourceLoader(cwd=cwd, agent_dir=agent_dir)
    asyncio.run(loader.reload())

    assert loader.get_skills() == ()
    assert not workspace_system_skills_dir(cwd).exists()


def test_packaged_system_skills_validate_cleanly(tmp_path) -> None:
    validator = system_skills_source_root() / "skill-creator" / "scripts" / "validate_skill.py"
    for name in SYSTEM_SKILL_NAMES:
        completed = subprocess.run(
            [sys.executable, str(validator), str(system_skills_source_root() / name)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "WARNING" not in completed.stdout, completed.stdout


def test_init_skill_script_creates_valid_skill(tmp_path) -> None:
    creator_scripts = system_skills_source_root() / "skill-creator" / "scripts"
    completed = subprocess.run(
        [
            sys.executable,
            str(creator_scripts / "init_skill.py"),
            "demo-skill",
            "--path",
            str(tmp_path / "skills"),
            "--resources",
            "scripts,references",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    skill_dir = tmp_path / "skills" / "demo-skill"
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "scripts").is_dir()
    assert (skill_dir / "references").is_dir()

    validated = subprocess.run(
        [sys.executable, str(creator_scripts / "validate_skill.py"), str(skill_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr

    rejected = subprocess.run(
        [sys.executable, str(creator_scripts / "init_skill.py"), "Bad_Name", "--path", str(tmp_path / "skills")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rejected.returncode == 1


def test_install_skill_script_installs_and_configures_env(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    checkout = tmp_path / "checkout" / "fancy-skill"
    checkout.mkdir(parents=True)
    (checkout / "SKILL.md").write_text(
        "---\nname: fancy-skill\ndescription: Fancy.\n---\nFancy body.\n",
        encoding="utf-8",
    )
    installer = system_skills_source_root() / "skill-installer" / "scripts" / "install_skill.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(installer),
            str(checkout),
            "--workspace",
            str(workspace),
            "--env",
            "FANCY_API_KEY",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    installed = workspace / ".magipi" / "skills" / "fancy-skill"
    assert (installed / "SKILL.md").is_file()

    settings = json.loads((workspace / ".magipi" / "settings.json").read_text(encoding="utf-8"))
    skill_env = settings["resources"]["skillEnv"]["fancy-skill"]
    assert skill_env["envFile"] == ".magipi/secrets/fancy-skill.env"
    assert skill_env["allow"] == ["FANCY_API_KEY"]
    env_file = workspace / ".magipi" / "secrets" / "fancy-skill.env"
    assert "FANCY_API_KEY=" in env_file.read_text(encoding="utf-8")
    assert (env_file.stat().st_mode & 0o777) == 0o600

    rerun = subprocess.run(
        [sys.executable, str(installer), str(checkout), "--workspace", str(workspace)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert rerun.returncode == 1
    assert "already exists" in rerun.stderr


def test_fetch_repo_script_reuses_local_checkout(tmp_path) -> None:
    workspace = tmp_path / "ws"
    checkout = workspace / "tmp" / "skill-installer" / "octo__demo" / "default"
    skill_dir = checkout / "skills" / "neat-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: neat-skill\ndescription: Neat helper for tests.\n---\nNeat.\n",
        encoding="utf-8",
    )
    fetcher = (
        system_skills_source_root() / "skill-installer" / "scripts" / "fetch_repo.py"
    )

    completed = subprocess.run(
        [sys.executable, str(fetcher), "octo/demo"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=workspace,
    )
    assert completed.returncode == 0, completed.stderr
    assert "reusing existing checkout" in completed.stdout
    assert "neat-skill" in completed.stdout
    assert "Neat helper for tests." in completed.stdout
