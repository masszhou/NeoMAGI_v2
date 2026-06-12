#!/usr/bin/env python3
"""Validate a magipi skill directory against the magipi skill protocol.

Mirrors the checks magipi's resource loader applies at discovery time, so a clean
validation here means the skill will load without warnings. Stdlib-only, read-only.

Usage:
    python3 validate_skill.py <path/to/skill-dir>
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Same pattern the loader uses to flag shell snippets whose redirect targets escape
# the workspace and would be blocked by shell policy at execution time.
POLICY_INCOMPATIBLE_REDIRECT_RE = re.compile(r"[12&]?>+\s*/(?:dev/null|tmp/)")
RELATIVE_REF_RE = re.compile(r"(?:\]\(|`)((?:scripts|references|assets)/[A-Za-z0-9_./-]+)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_dir", help="path to the skill directory")
    args = parser.parse_args()

    skill_dir = Path(args.skill_dir)
    errors: list[str] = []
    warnings: list[str] = []

    skill_file = skill_dir / "SKILL.md"
    if not skill_dir.is_dir():
        errors.append(f"{skill_dir} is not a directory")
    elif not skill_file.is_file():
        errors.append(f"{skill_file} does not exist")
    else:
        metadata, body = split_frontmatter(skill_file.read_text(encoding="utf-8"))
        check_frontmatter(metadata, skill_dir.name, errors, warnings)
        check_body(body, skill_dir, errors, warnings)

    for message in errors:
        print(f"ERROR: {message}")
    for message in warnings:
        print(f"WARNING: {message}")
    if not errors and not warnings:
        print("OK: skill validates cleanly")
    elif not errors:
        print("OK with warnings")
    return 1 if errors else 0


def check_frontmatter(
    metadata: dict[str, object],
    dir_name: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    if not metadata:
        errors.append("missing or unparseable YAML frontmatter (--- blocks)")
        return
    name = str(metadata.get("name") or "")
    description = metadata.get("description")
    if not name:
        errors.append("frontmatter is missing required field: name")
    else:
        if name != dir_name:
            errors.append(f'name "{name}" does not match directory name "{dir_name}"')
        if len(name) > MAX_NAME_LENGTH:
            errors.append(f"name exceeds {MAX_NAME_LENGTH} characters")
        if not NAME_RE.match(name):
            errors.append("name must be lowercase a-z/0-9 with single hyphens, no leading/trailing hyphen")
    if not isinstance(description, str) or not description.strip():
        errors.append("frontmatter is missing required field: description")
    elif len(description.strip()) > MAX_DESCRIPTION_LENGTH:
        errors.append(
            f"description exceeds {MAX_DESCRIPTION_LENGTH} characters ({len(description.strip())})"
        )
    known = {"name", "description", "disable-model-invocation"}
    extra = sorted(set(map(str, metadata)) - known)
    if extra:
        warnings.append(f"frontmatter keys ignored by magipi: {', '.join(extra)} (move info into the body or drop)")


def check_body(body: str, skill_dir: Path, errors: list[str], warnings: list[str]) -> None:
    if not body.strip():
        errors.append("SKILL.md body is empty")
        return
    redirects = sorted(set(match.strip() for match in POLICY_INCOMPATIBLE_REDIRECT_RE.findall(body)))
    if redirects:
        warnings.append(
            "body contains policy-incompatible shell redirect(s) "
            f"({', '.join(redirects)}); shell policy blocks redirect targets outside the "
            "workspace - rewrite to workspace-relative paths or drop the redirect"
        )
    if len(body.splitlines()) > 500:
        warnings.append("body exceeds 500 lines; move detail into references/")
    for reference in sorted(set(RELATIVE_REF_RE.findall(body))):
        target = skill_dir / reference
        if not target.exists():
            warnings.append(f"body references missing file: {reference}")


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            metadata: dict[str, object] = {}
            for raw in lines[1:index]:
                line = raw.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                metadata[key.strip()] = parse_scalar(value.strip())
            return metadata, "\n".join(lines[index + 1 :])
    return {}, text


def parse_scalar(value: str) -> object:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
