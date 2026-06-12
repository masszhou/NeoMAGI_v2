#!/usr/bin/env python3
"""Initialize a new magipi skill directory with a protocol-conformant template.

Stdlib-only; safe to run via magipi's governed bash tool. Writes only inside the
target path (workspace-relative by default).

Usage:
    python3 init_skill.py <skill-name> --path .magipi/skills [--resources scripts,references,assets]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64

SKILL_TEMPLATE = """\
---
name: {name}
description: TODO. What this skill does AND when to use it (concrete triggers). Max 1024 chars; this is the only text seen before the skill fires.
---
# {title}

TODO: imperative instructions for the agent executing this skill.
Paths are relative to this skill's directory; `{{baseDir}}` resolves to it under
`/skill:` expansion.

## MagiPi execution boundary

Run every command through magipi's governed `bash` tool. Keep all outputs inside the
workspace (no redirects to /dev/null or /tmp). Do not edit shell profiles; credentials
come only through the skillEnv grant.

## Setup check

```bash
echo "TODO: verify dependencies, e.g.: command -v node && echo found || echo missing"
```

## Failure mode

If a dependency or credential is unavailable, stop and report the missing prerequisite
plus the next setup command. Do not guess or switch to another service without user
approval.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="skill name (lowercase a-z, 0-9, hyphens)")
    parser.add_argument("--path", default=".magipi/skills", help="parent directory for the new skill")
    parser.add_argument(
        "--resources",
        default="",
        help="comma-separated optional dirs to create: scripts,references,assets",
    )
    args = parser.parse_args()

    problems = name_problems(args.name)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    parent = Path(args.path)
    if parent.name == ".system" or ".system" in parent.parts:
        print("error: .magipi/skills/.system is host-managed; install skills directly under .magipi/skills", file=sys.stderr)
        return 1
    skill_dir = parent / args.name
    if skill_dir.exists():
        print(f"error: {skill_dir} already exists", file=sys.stderr)
        return 1

    resources = [item.strip() for item in args.resources.split(",") if item.strip()]
    unknown = [item for item in resources if item not in {"scripts", "references", "assets"}]
    if unknown:
        print(f"error: unknown resource dirs: {', '.join(unknown)}", file=sys.stderr)
        return 1

    skill_dir.mkdir(parents=True)
    title = args.name.replace("-", " ").title()
    (skill_dir / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(name=args.name, title=title), encoding="utf-8"
    )
    for resource in resources:
        (skill_dir / resource).mkdir()
    print(f"initialized skill at {skill_dir}")
    print("next: edit SKILL.md (frontmatter description first), then run validate_skill.py")
    return 0


def name_problems(name: str) -> list[str]:
    problems: list[str] = []
    if len(name) > MAX_NAME_LENGTH:
        problems.append(f"name exceeds {MAX_NAME_LENGTH} characters")
    if not NAME_RE.match(name):
        problems.append(
            "name must be lowercase a-z/0-9 with single hyphens, no leading/trailing hyphen"
        )
    return problems


if __name__ == "__main__":
    raise SystemExit(main())
