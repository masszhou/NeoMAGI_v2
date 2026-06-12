#!/usr/bin/env python3
"""Fetch a skill repository once into the workspace tmp/ area and list skill candidates.

Idempotent: if the checkout directory already exists, it is reused without touching the
network (one download per repo per workspace; re-run freely). Stdlib-only; all writes
stay inside the workspace.

Usage:
    python3 fetch_repo.py <repo> [--ref REF] [--dest tmp/skill-installer]

<repo> accepts:
    owner/repo
    https://github.com/owner/repo[.git]
    https://github.com/owner/repo/tree/<ref>[/sub/path]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

GITHUB_URL_RE = re.compile(
    r"^(?:https?://github\.com/)?(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/]+)(?:/(?P<subpath>.+))?)?/?$"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", help="owner/repo or GitHub URL")
    parser.add_argument("--ref", default=None, help="branch, tag, or commit (default: repo default branch)")
    parser.add_argument("--dest", default="tmp/skill-installer", help="checkout parent directory")
    parser.add_argument("--refresh", action="store_true", help="delete any existing checkout and re-download")
    args = parser.parse_args()

    match = GITHUB_URL_RE.match(args.repo.strip())
    if not match:
        print(f"error: cannot parse repo reference: {args.repo}", file=sys.stderr)
        return 1
    owner, repo = match.group("owner"), match.group("repo")
    ref = args.ref or match.group("ref")
    subpath = match.group("subpath")

    checkout = Path(args.dest) / f"{owner}__{repo}" / (ref or "default")
    if args.refresh and checkout.exists():
        shutil.rmtree(checkout)
    if checkout.exists() and any(checkout.iterdir()):
        print(f"reusing existing checkout: {checkout}")
    else:
        if not fetch(owner, repo, ref, checkout):
            return 1
        print(f"fetched {owner}/{repo}" + (f"@{ref}" if ref else "") + f" -> {checkout}")

    report(checkout, subpath)
    return 0


def fetch(owner: str, repo: str, ref: str | None, checkout: Path) -> bool:
    checkout.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{owner}/{repo}.git"
    command = ["git", "clone", "--depth", "1", "--single-branch"]
    if ref:
        command += ["--branch", ref]
    command += [url, str(checkout)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if completed.returncode == 0:
            return True
        print(f"git clone failed ({completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else 'unknown'}); trying tarball", file=sys.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"git unavailable ({exc}); trying tarball", file=sys.stderr)
    if checkout.exists():
        shutil.rmtree(checkout, ignore_errors=True)
    return fetch_tarball(owner, repo, ref, checkout)


def fetch_tarball(owner: str, repo: str, ref: str | None, checkout: Path) -> bool:
    refs = [ref] if ref else ["HEAD"]
    for candidate in refs:
        url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{candidate}"
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                with tempfile.NamedTemporaryFile(suffix=".tar.gz", dir=str(checkout.parent)) as handle:
                    shutil.copyfileobj(response, handle)
                    handle.flush()
                    extract_tarball(Path(handle.name), checkout)
            return True
        except (urllib.error.URLError, urllib.error.HTTPError, tarfile.TarError, OSError) as exc:
            print(f"error: tarball download failed for {url}: {exc}", file=sys.stderr)
    return False


def extract_tarball(archive: Path, checkout: Path) -> None:
    checkout.mkdir(parents=True, exist_ok=True)
    resolved_root = checkout.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        members = []
        for member in tar.getmembers():
            if not (member.isfile() or member.isdir()):
                continue  # skip links and special files
            parts = Path(member.name).parts
            if len(parts) <= 1:
                continue  # strip the top-level "<repo>-<ref>/" component
            relative = Path(*parts[1:])
            target = (checkout / relative).resolve()
            if not str(target).startswith(str(resolved_root)):
                raise tarfile.TarError(f"archive member escapes checkout: {member.name}")
            member.name = str(relative)
            members.append(member)
        tar.extractall(checkout, members=members, filter="data")


def report(checkout: Path, subpath: str | None) -> None:
    scan_root = checkout / subpath if subpath else checkout
    if subpath and not scan_root.exists():
        print(f"note: requested subpath does not exist in checkout: {subpath}")
        scan_root = checkout
    candidates = find_skill_candidates(scan_root)
    if candidates:
        print(f"\nskill candidates ({len(candidates)}):")
        for skill_file in candidates:
            name, description = skill_summary(skill_file)
            print(f"- {skill_file.parent.relative_to(checkout)}  name={name!r}")
            print(f"  description: {description}")
    else:
        print("\nno SKILL.md files found.")
    hints = layout_hints(checkout)
    if hints:
        print("\nlayout hints:")
        for hint in hints:
            print(f"- {hint}")


def find_skill_candidates(root: Path) -> list[Path]:
    ignored = {".git", "node_modules", "__pycache__", ".venv"}
    results: list[Path] = []
    for path in sorted(root.rglob("SKILL.md")):
        if not any(part in ignored for part in path.parts):
            results.append(path)
    return results


def skill_summary(skill_file: Path) -> tuple[str, str]:
    name, description = skill_file.parent.name, ""
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return name, "(unreadable)"
    if text.startswith("---"):
        for line in text.splitlines()[1:40]:
            if line.strip() == "---":
                break
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip().strip("\"'")
            if line.startswith("description:"):
                description = line.split(":", 1)[1].strip().strip("\"'")
    return name, (description[:160] + "..." if len(description) > 160 else description or "(none)")


def layout_hints(checkout: Path) -> list[str]:
    hints: list[str] = []
    checks = {
        ".claude/skills": "Claude Code project-skill layout",
        ".claude-plugin": "Claude Code plugin/marketplace layout",
        "skills/.system": "openai/skills-style system+curated collection",
        "commands": "slash-command prompt directory (maps to .magipi/prompts, not skills)",
        "prompts": "prompt template directory (maps to .magipi/prompts, not skills)",
    }
    for relative, label in checks.items():
        if (checkout / relative).exists():
            hints.append(f"{relative}/ -> {label}")
    if (checkout / "mcp.json").exists() or (checkout / ".mcp.json").exists():
        hints.append("mcp config found -> MCP servers are not installable as magipi skills")
    for meta in ("package.json", "pyproject.toml", "requirements.txt"):
        if (checkout / meta).exists():
            hints.append(f"{meta} present -> check runtime dependencies before adapting")
    readme = checkout / "README.md"
    if readme.is_file():
        hints.append("README.md present -> read it for credentials/dependency notes")
    try:
        manifest = checkout / ".claude-plugin" / "marketplace.json"
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            count = len(data.get("plugins", []))
            hints.append(f"marketplace.json lists {count} plugin(s)")
    except (OSError, json.JSONDecodeError):
        pass
    return hints


if __name__ == "__main__":
    raise SystemExit(main())
