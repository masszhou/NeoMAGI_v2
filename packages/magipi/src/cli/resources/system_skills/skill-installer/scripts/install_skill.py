#!/usr/bin/env python3
"""Materialize a skill directory into the workspace .magipi/skills/ area.

Copies a candidate skill (typically from a tmp/skill-installer/ checkout) into
`.magipi/skills/<name>/` and optionally wires the skillEnv credentials protocol:
`resources.skillEnv.<name>` in `.magipi/settings.json` plus an empty-value secrets
placeholder at `.magipi/secrets/<name>.env` (0600). Never writes secret values.
Stdlib-only; all writes stay inside the workspace.

Usage:
    python3 install_skill.py <source-skill-dir> [--name NAME] [--env VAR1,VAR2] [--force]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="directory containing the skill's SKILL.md")
    parser.add_argument("--name", default=None, help="installed skill name (default: source dir name)")
    parser.add_argument("--workspace", default=".", help="workspace root (default: current directory)")
    parser.add_argument("--env", default="", help="comma-separated env var names the skill requires")
    parser.add_argument("--force", action="store_true", help="replace an existing installed skill")
    args = parser.parse_args()

    source = Path(args.source)
    workspace = Path(args.workspace)
    name = args.name or source.name

    if not (source / "SKILL.md").is_file():
        print(f"error: {source} does not contain SKILL.md", file=sys.stderr)
        return 1
    if len(name) > MAX_NAME_LENGTH or not NAME_RE.match(name):
        print(
            f"error: invalid skill name {name!r} (lowercase a-z/0-9 and single hyphens, "
            f"max {MAX_NAME_LENGTH} chars); pass a valid --name",
            file=sys.stderr,
        )
        return 1

    destination = workspace / ".magipi" / "skills" / name
    if ".system" in destination.parts[-3:]:
        print("error: refusing to install into the host-managed .system area", file=sys.stderr)
        return 1
    if destination.exists():
        if not args.force:
            print(f"error: {destination} already exists (use --force to replace)", file=sys.stderr)
            return 1
        shutil.rmtree(destination)

    shutil.copytree(source, destination, symlinks=False, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
    print(f"installed skill {name!r} -> {destination}")

    env_vars = [item.strip() for item in args.env.split(",") if item.strip()]
    if env_vars:
        configure_skill_env(workspace, name, env_vars)

    print("\nnext steps:")
    print(f"1. adapt {destination}/SKILL.md to the magipi protocol (execution boundary, setup check, credentials, output hygiene, failure mode)")
    print("2. validate: python3 .magipi/skills/.system/skill-creator/scripts/validate_skill.py " + str(destination))
    if env_vars:
        print(f"3. user fills real values into .magipi/secrets/{name}.env (values stay empty until then; empty values are inert)")
    print(f"{'4' if env_vars else '3'}. user runs /reload to activate the skill")
    return 0


def configure_skill_env(workspace: Path, name: str, env_vars: list[str]) -> None:
    secrets_dir = workspace / ".magipi" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    secrets_dir.chmod(0o700)
    env_file = secrets_dir / f"{name}.env"
    if not env_file.exists():
        lines = [
            f"# Secrets for skill {name!r}. Fill in real values; this file must stay gitignored.",
            *[f"{var}=" for var in env_vars],
            "",
        ]
        env_file.write_text("\n".join(lines), encoding="utf-8")
    env_file.chmod(0o600)

    settings_file = workspace / ".magipi" / "settings.json"
    settings: dict = {}
    if settings_file.is_file():
        try:
            settings = json.loads(settings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"error: {settings_file} is not valid JSON; configure skillEnv manually", file=sys.stderr)
            return
    resources = settings.setdefault("resources", {})
    skill_env = resources.setdefault("skillEnv", {})
    existing_allow = skill_env.get(name, {}).get("allow", [])
    merged_allow = sorted(set(existing_allow) | set(env_vars))
    skill_env[name] = {"envFile": f".magipi/secrets/{name}.env", "allow": merged_allow}
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"configured resources.skillEnv.{name} (allow: {', '.join(merged_allow)})")
    print(f"created placeholder {env_file} (0600); values are EMPTY on purpose")

    gitignore = workspace / ".gitignore"
    needs_hint = True
    if gitignore.is_file():
        content = gitignore.read_text(encoding="utf-8", errors="replace")
        if ".magipi/secrets" in content:
            needs_hint = False
    if needs_hint:
        print("note: add '.magipi/secrets/' to the workspace .gitignore before committing")


if __name__ == "__main__":
    raise SystemExit(main())
